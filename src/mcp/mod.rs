use anyhow::{Context, Result};
use serde_json::{json, Value};
use std::io::{self, BufRead, Write};
use std::path::PathBuf;

use crate::graph::CodeGraph;
use crate::parser::Scanner;
use crate::storage::{self, KnowledgeStore, LocalStorage};

/// Run the MCP server over stdio (JSON-RPC).
pub async fn serve(project_path: PathBuf) -> Result<()> {
    let mut server = McpServer::new(project_path)?;
    server.run().await
}

struct McpServer {
    project_path: PathBuf,
    graph: Option<CodeGraph>,
    store: LocalStorage,
    last_refresh_check: std::time::Instant,
}

const REFRESH_THROTTLE_SECS: u64 = 3;
const MAX_INCREMENTAL_FILES: usize = 50;

impl McpServer {
    fn new(project_path: PathBuf) -> Result<Self> {
        let temper = project_path.join(".temper");
        std::fs::create_dir_all(&temper)?;
        let store = LocalStorage::open(&temper.join("knowledge.db"))?;
        Ok(Self {
            project_path,
            graph: None,
            store,
            last_refresh_check: std::time::Instant::now(),
        })
    }

    fn temper_dir(&self) -> PathBuf {
        self.project_path.join(".temper")
    }

    /// Ensure graph is loaded AND up-to-date via git diff on-demand.
    fn ensure_graph(&mut self) -> Result<&CodeGraph> {
        let first_load = self.graph.is_none();

        // Step 1: Load graph if not yet loaded (lazy init)
        if self.graph.is_none() {
            let graph_path = self.temper_dir().join("graph.json");
            if graph_path.exists() {
                self.graph = Some(CodeGraph::load(&graph_path)?);
                eprintln!("[temper] Graph loaded: {} nodes", self.graph.as_ref().unwrap().nodes.len());
            } else {
                eprintln!("[temper] No graph found, scanning...");
                let scanner = Scanner::new(&self.project_path);
                let graph = scanner.full_scan()?;
                graph.save(&graph_path)?;
                self.graph = Some(graph);
            }
        }

        // Step 2: On-demand refresh via git status
        // Always check on first load; throttle on subsequent calls
        let should_check = first_load
            || self.last_refresh_check.elapsed().as_secs() >= REFRESH_THROTTLE_SECS;

        if should_check {
            self.last_refresh_check = std::time::Instant::now();
            self.try_incremental_refresh()?;
        }

        Ok(self.graph.as_ref().unwrap())
    }

    /// Check git status for changed files and incrementally update graph.
    fn try_incremental_refresh(&mut self) -> Result<()> {
        let changed = get_changed_files_git_status(&self.project_path);
        if changed.is_empty() {
            return Ok(());
        }

        if changed.len() >= MAX_INCREMENTAL_FILES {
            eprintln!(
                "[temper] {} files changed, graph may be stale. Run rescan_code for full update.",
                changed.len()
            );
            return Ok(());
        }

        eprintln!("[temper] Incremental refresh: {} files changed", changed.len());
        let scanner = Scanner::new(&self.project_path);
        if let Some(existing) = self.graph.take() {
            let updated = scanner.incremental_update(existing, &changed)?;
            updated.save(&self.temper_dir().join("graph.json"))?;
            self.graph = Some(updated);
        }

        Ok(())
    }

    async fn run(&mut self) -> Result<()> {
        let stdin = io::stdin();
        let stdout = io::stdout();
        let reader = stdin.lock();
        let mut writer = stdout.lock();

        for line in reader.lines() {
            let line = line?;
            if line.trim().is_empty() {
                continue;
            }

            let request: Value = match serde_json::from_str(&line) {
                Ok(v) => v,
                Err(e) => {
                    eprintln!("[temper] Invalid JSON: {}", e);
                    continue;
                }
            };

            let response = self.handle_request(&request);

            let response_str = serde_json::to_string(&response)?;
            writeln!(writer, "{}", response_str)?;
            writer.flush()?;
        }

        Ok(())
    }

    fn handle_request(&mut self, request: &Value) -> Value {
        let method = request["method"].as_str().unwrap_or("");
        let id = request.get("id").cloned();
        let params = request.get("params").cloned().unwrap_or(json!({}));

        let result = match method {
            "initialize" => self.handle_initialize(&params),
            "tools/list" => self.handle_tools_list(),
            "tools/call" => self.handle_tools_call(&params),
            "notifications/initialized" => return json!(null), // no response needed
            _ => Err(anyhow::anyhow!("Unknown method: {}", method)),
        };

        match result {
            Ok(value) => {
                let mut resp = json!({
                    "jsonrpc": "2.0",
                    "result": value,
                });
                if let Some(id) = id {
                    resp["id"] = id;
                }
                resp
            }
            Err(e) => {
                let mut resp = json!({
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32603,
                        "message": e.to_string(),
                    },
                });
                if let Some(id) = id {
                    resp["id"] = id;
                }
                resp
            }
        }
    }

    fn handle_initialize(&self, _params: &Value) -> Result<Value> {
        Ok(json!({
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {}
            },
            "serverInfo": {
                "name": "temper",
                "version": env!("CARGO_PKG_VERSION")
            }
        }))
    }

    fn handle_tools_list(&self) -> Result<Value> {
        Ok(json!({
            "tools": [
                {
                    "name": "search_code",
                    "description": "Find related files, functions, and dependencies via AST code graph. Returns direct matches + impact chain (what else is affected). Use this before modifying code to understand the blast radius.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query — function name, file name, module name, or concept"
                            }
                        },
                        "required": ["query"]
                    }
                },
                {
                    "name": "get_file_context",
                    "description": "Get full context for a file: who imports it, what it imports, exported symbols, and any attached knowledge.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": "Relative file path"
                            }
                        },
                        "required": ["file_path"]
                    }
                },
                {
                    "name": "rescan_code",
                    "description": "Force rescan the project code graph. Use after creating new files or making significant structural changes.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {}
                    }
                },
                {
                    "name": "define_module",
                    "description": "Create or update a module boundary. Modules are tags/groups on files — a file can belong to multiple modules. Use glob patterns for paths.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "name": { "type": "string", "description": "Module name (e.g. 'web-server/user')" },
                            "description": { "type": "string", "description": "What this module does" },
                            "paths": { "type": "array", "items": { "type": "string" }, "description": "Glob patterns for included files" },
                            "tags": { "type": "array", "items": { "type": "string" }, "description": "Searchable tags" },
                            "exclude": { "type": "array", "items": { "type": "string" }, "description": "Glob patterns to exclude" },
                            "entry_points": { "type": "array", "items": { "type": "string" }, "description": "Main entry files" }
                        },
                        "required": ["name", "description"]
                    }
                },
                {
                    "name": "list_modules",
                    "description": "List all defined modules with file counts and descriptions.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "dimension": { "type": "string", "description": "Filter by dimension (e.g. 'by-service')" }
                        }
                    }
                },
                {
                    "name": "get_module",
                    "description": "Get complete module context: files, dependencies, interfaces, knowledge, and constraints. Call this before working on a module. For code examples, also call get_patterns.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "name": { "type": "string", "description": "Module name" }
                        },
                        "required": ["name"]
                    }
                },
                {
                    "name": "remove_module",
                    "description": "Remove a module definition.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "name": { "type": "string", "description": "Module name to remove" }
                        },
                        "required": ["name"]
                    }
                },
                {
                    "name": "remember",
                    "description": "Store a piece of knowledge about the project. Records design decisions, known bugs, constraints, and lessons learned. Knowledge persists across sessions and is automatically shown when working on related code.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "title": { "type": "string", "description": "One-line summary" },
                            "content": { "type": "string", "description": "Full description — why it matters, what to watch out for" },
                            "type": { "type": "string", "enum": ["decision", "bug", "constraint", "experience", "note"], "description": "Knowledge type" },
                            "module": { "type": "string", "description": "Anchor to a module" },
                            "file": { "type": "string", "description": "Anchor to a file path" },
                            "function_name": { "type": "string", "description": "Anchor to a function" },
                            "tags": { "type": "array", "items": { "type": "string" }, "description": "Searchable tags" }
                        },
                        "required": ["title", "content", "type"]
                    }
                },
                {
                    "name": "recall",
                    "description": "Retrieve stored project knowledge. Search by keyword, filter by module or type.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": { "type": "string", "description": "Keyword search" },
                            "module": { "type": "string", "description": "Filter by module" },
                            "type": { "type": "string", "description": "Filter by type" },
                            "include_stale": { "type": "boolean", "description": "Include stale entries" }
                        }
                    }
                },
                {
                    "name": "forget",
                    "description": "Expire a knowledge entry by ID. Full history is preserved.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "id": { "type": "string", "description": "Knowledge entry ID" }
                        },
                        "required": ["id"]
                    }
                },
                {
                    "name": "record_experience",
                    "description": "Record a structured symptom→cause→fix experience. Use after debugging to capture lessons learned.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "symptom": { "type": "string", "description": "What was observed (the problem)" },
                            "cause": { "type": "string", "description": "Root cause" },
                            "fix": { "type": "string", "description": "How it was fixed" },
                            "module": { "type": "string", "description": "Related module" },
                            "constraint_note": { "type": "string", "description": "Constraint: what must be done if this changes" },
                            "tags": { "type": "array", "items": { "type": "string" } }
                        },
                        "required": ["symptom", "cause", "fix"]
                    }
                },
                {
                    "name": "search_symptom",
                    "description": "Search known symptom→cause→fix records. Use when debugging to check if this problem has been seen before.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "symptom": { "type": "string", "description": "Symptom description to search for" }
                        },
                        "required": ["symptom"]
                    }
                },
                {
                    "name": "find_causal_chain",
                    "description": "Trace causal relationships from an entity. Shows what triggers what, what is affected by changes.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "entity": { "type": "string", "description": "Starting entity (knowledge ID, module name, or code entity)" },
                            "max_depth": { "type": "integer", "description": "Max traversal depth (default 3)" }
                        },
                        "required": ["entity"]
                    }
                },
                {
                    "name": "add_causal_relation",
                    "description": "Record a causal relationship: A triggers/causes/affects/constrains B.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "from_entity": { "type": "string", "description": "Source entity" },
                            "to_entity": { "type": "string", "description": "Target entity" },
                            "relation_type": { "type": "string", "enum": ["triggers", "causes", "affects", "constrains", "depends_on"], "description": "Relation type" },
                            "description": { "type": "string", "description": "Why this relation exists" }
                        },
                        "required": ["from_entity", "to_entity", "relation_type"]
                    }
                },
                {
                    "name": "get_constraints",
                    "description": "Get all constraint-type knowledge for a module. Check before implementing changes.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "module": { "type": "string", "description": "Module name" }
                        },
                        "required": ["module"]
                    }
                },
                {
                    "name": "search_knowledge",
                    "description": "Semantic search across knowledge entries. Uses embedding similarity when configured, falls back to keyword search. Finds conceptually related knowledge even without exact keyword matches.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": { "type": "string", "description": "Natural language search query" },
                            "limit": { "type": "integer", "description": "Max results (default 10)" }
                        },
                        "required": ["query"]
                    }
                },
                {
                    "name": "scan_module_interfaces",
                    "description": "Scan a module's Java files to extract public API surface (REST endpoints, public methods). Updates interfaces/<module>.json.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "module": { "type": "string", "description": "Module name to scan" }
                        },
                        "required": ["module"]
                    }
                },
                {
                    "name": "refresh_modules",
                    "description": "Scan project for new package structures and suggest new modules. Does not modify existing modules. Only suggests — user must confirm.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {}
                    }
                },
                {
                    "name": "get_patterns",
                    "description": "Get code patterns and conventions for a module. Returns example code snippets from key files showing how things are implemented. ALWAYS call this before writing new code in a module — it shows the patterns you should follow.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "module": { "type": "string", "description": "Module name" },
                            "pattern_type": { "type": "string", "description": "What kind of pattern: 'handler', 'service', 'controller', 'test', or omit for all" }
                        },
                        "required": ["module"]
                    }
                },
                {
                    "name": "auto_extract",
                    "description": "Auto-extract knowledge from conversation context using LLM. Extracts constraints, decisions, causal relations, and experiences. Stores them automatically. Use after completing a task to capture what was learned.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "context": { "type": "string", "description": "Conversation or code change context to extract knowledge from" }
                        },
                        "required": ["context"]
                    }
                },
                {
                    "name": "validate_modules",
                    "description": "Check all module definitions for issues: glob patterns that match no files, stale interfaces, missing paths.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {}
                    }
                }
            ]
        }))
    }

    fn handle_tools_call(&mut self, params: &Value) -> Result<Value> {
        let tool_name = params["name"].as_str().unwrap_or("");
        let args = params.get("arguments").cloned().unwrap_or(json!({}));

        match tool_name {
            "search_code" => self.tool_search_code(&args),
            "get_file_context" => self.tool_get_file_context(&args),
            "rescan_code" => self.tool_rescan_code(&args),
            "define_module" => self.tool_define_module(&args),
            "list_modules" => self.tool_list_modules(&args),
            "get_module" => self.tool_get_module(&args),
            "remove_module" => self.tool_remove_module(&args),
            "remember" => self.tool_remember(&args),
            "recall" => self.tool_recall(&args),
            "forget" => self.tool_forget(&args),
            "record_experience" => self.tool_record_experience(&args),
            "search_symptom" => self.tool_search_symptom(&args),
            "find_causal_chain" => self.tool_find_causal_chain(&args),
            "add_causal_relation" => self.tool_add_causal_relation(&args),
            "get_constraints" => self.tool_get_constraints(&args),
            "search_knowledge" => self.tool_search_knowledge(&args),
            "scan_module_interfaces" => self.tool_scan_module_interfaces(&args),
            "get_patterns" => self.tool_get_patterns(&args),
            "auto_extract" => self.tool_auto_extract(&args),
            "refresh_modules" => self.tool_refresh_modules(&args),
            "validate_modules" => self.tool_validate_modules(&args),
            _ => Err(anyhow::anyhow!("Unknown tool: {}", tool_name)),
        }
    }

    fn tool_search_code(&mut self, args: &Value) -> Result<Value> {
        let query = args["query"]
            .as_str()
            .context("Missing 'query' argument")?;

        let graph = self.ensure_graph()?;
        let result = graph.search(query);

        let mut lines = Vec::new();

        if !result.direct_matches.is_empty() {
            lines.push(format!(
                "## Direct matches ({})",
                result.direct_matches.len()
            ));
            for node in result.direct_matches.iter().take(15) {
                let exp = if node.exported { " (exported)" } else { "" };
                let line = node.line.map(|l| format!(":{}", l)).unwrap_or_default();
                lines.push(format!(
                    "- [{}] **{}**{} — {}{}",
                    node.node_type, node.name, exp, node.file_path, line
                ));
            }
        }

        if !result.impact_chain.is_empty() {
            lines.push(format!(
                "\n## Impact chain ({} connected nodes)",
                result.impact_chain.len()
            ));
            for impact in result.impact_chain.iter().take(20) {
                let line = impact
                    .node
                    .line
                    .map(|l| format!(":{}", l))
                    .unwrap_or_default();
                lines.push(format!(
                    "- depth={} [{}] {} — {}{}",
                    impact.depth,
                    impact.node.node_type,
                    impact.node.name,
                    impact.node.file_path,
                    line
                ));
            }
        }

        if result.direct_matches.is_empty() {
            lines.push(
                "No matches found. Try different keywords (use English code identifiers).".into(),
            );
        }

        Ok(json!({
            "content": [{ "type": "text", "text": lines.join("\n") }]
        }))
    }

    fn tool_get_file_context(&mut self, args: &Value) -> Result<Value> {
        let file_path = args["file_path"]
            .as_str()
            .context("Missing 'file_path' argument")?;

        let graph = self.ensure_graph()?;

        // Find file node
        let file_node = graph
            .nodes
            .iter()
            .find(|n| {
                n.node_type == crate::graph::NodeType::File
                    && (n.file_path == file_path || n.file_path.ends_with(file_path))
            });

        let file_node = match file_node {
            Some(n) => n,
            None => {
                return Ok(json!({
                    "content": [{ "type": "text", "text": format!("File \"{}\" not found in code graph. Run rescan if file was recently added.", file_path) }]
                }));
            }
        };

        let mut lines = vec![format!(
            "## {} (module: {})",
            file_node.file_path, file_node.module
        )];

        // Imports
        let imports: Vec<_> = graph
            .edges
            .iter()
            .filter(|e| e.from == file_node.id && e.edge_type == crate::graph::EdgeType::Imports)
            .collect();
        if !imports.is_empty() {
            lines.push(format!("\n### Imports ({})", imports.len()));
            for e in &imports {
                let detail = e
                    .detail
                    .as_deref()
                    .map(|d| format!(" — {}", d))
                    .unwrap_or_default();
                lines.push(format!("- {}{}", e.to, detail));
            }
        }

        // Imported by
        let imported_by: Vec<_> = graph
            .edges
            .iter()
            .filter(|e| e.to == file_node.id && e.edge_type == crate::graph::EdgeType::Imports)
            .collect();
        if !imported_by.is_empty() {
            lines.push(format!("\n### Imported by ({})", imported_by.len()));
            for e in &imported_by {
                let detail = e
                    .detail
                    .as_deref()
                    .map(|d| format!(" — {}", d))
                    .unwrap_or_default();
                lines.push(format!("- {}{}", e.from, detail));
            }
        }

        // Exports
        let exports: Vec<_> = graph
            .edges
            .iter()
            .filter(|e| e.from == file_node.id && e.edge_type == crate::graph::EdgeType::Exports)
            .collect();
        if !exports.is_empty() {
            lines.push(format!("\n### Exports ({})", exports.len()));
            for e in &exports {
                lines.push(format!(
                    "- {}",
                    e.detail.as_deref().unwrap_or(&e.to)
                ));
            }
        }

        // PROACTIVE CONSTRAINT INJECTION
        let constraints = self.get_constraints_for_file(file_path);
        if !constraints.is_empty() {
            lines.push(format!(
                "\n### ⚠️ CONSTRAINTS — do NOT violate ({})",
                constraints.len()
            ));
            for c in &constraints {
                let stale = if c.status == "stale" { " [STALE]" } else { "" };
                lines.push(format!(
                    "- **{}**{}: {}",
                    c.title, stale, c.content
                ));
            }
        }

        // Inject other knowledge (decisions, experiences) for this file
        let knowledge = self.get_knowledge_for_file(file_path);
        let non_constraints: Vec<_> = knowledge
            .iter()
            .filter(|k| k.entry_type != "constraint")
            .collect();
        if !non_constraints.is_empty() {
            lines.push(format!(
                "\n### Knowledge — this file ({})",
                non_constraints.len()
            ));
            for k in &non_constraints {
                let icon = match k.entry_type.as_str() {
                    "decision" => "🎯",
                    "bug" => "🐛",
                    "experience" => "💡",
                    _ => "📝",
                };
                let stale = if k.status == "stale" { " ⚠️STALE" } else { "" };
                lines.push(format!("{} **{}**{}", icon, k.title, stale));
                let preview = if k.content.len() > 200 { &k.content[..200] } else { &k.content };
                lines.push(format!("  {}", preview));
            }
        }

        Ok(json!({
            "content": [{ "type": "text", "text": lines.join("\n") }]
        }))
    }

    fn tool_rescan_code(&mut self, _args: &Value) -> Result<Value> {
        let t0 = std::time::Instant::now();
        self.graph = None;

        let scanner = Scanner::new(&self.project_path);
        let graph = scanner.full_scan()?;
        let stats = graph.stats();

        graph.save(&self.temper_dir().join("graph.json"))?;

        let mut meta = crate::graph::Meta::new(&self.project_path);
        meta.update_after_scan(&scanner, &graph);
        meta.save(&self.temper_dir().join("meta.json"))?;

        self.graph = Some(graph);

        let elapsed = t0.elapsed().as_millis();
        Ok(json!({
            "content": [{ "type": "text", "text": format!(
                "Rescanned in {}ms: {} nodes, {} edges, {} files",
                elapsed, stats.nodes, stats.edges, stats.files
            ) }]
        }))
    }

    fn get_registry(&mut self) -> Result<crate::modules::ModuleRegistry> {
        let graph = self.ensure_graph()?;
        let files = graph.files.clone();
        let temper = self.temper_dir();
        Ok(crate::modules::ModuleRegistry::new(&temper, files))
    }

    fn tool_define_module(&mut self, args: &Value) -> Result<Value> {
        let name = args["name"].as_str().context("Missing 'name'")?;
        let description = args["description"].as_str().context("Missing 'description'")?;

        let paths: Vec<String> = args
            .get("paths")
            .and_then(|v| serde_json::from_value(v.clone()).ok())
            .unwrap_or_default();

        let tags: Vec<String> = args
            .get("tags")
            .and_then(|v| serde_json::from_value(v.clone()).ok())
            .unwrap_or_default();

        let exclude: Vec<String> = args
            .get("exclude")
            .and_then(|v| serde_json::from_value(v.clone()).ok())
            .unwrap_or_default();

        let entry_points: Vec<String> = args
            .get("entry_points")
            .and_then(|v| serde_json::from_value(v.clone()).ok())
            .unwrap_or_default();

        let module = crate::modules::ModuleDef {
            name: name.to_string(),
            description: description.to_string(),
            paths,
            exclude,
            entry_points,
            tags,
            updated_at: chrono::Utc::now().format("%Y-%m-%d").to_string(),
        };

        let registry = self.get_registry()?;
        let file_count = registry.file_count(&module);
        registry.define_module(&module)?;

        Ok(json!({
            "content": [{ "type": "text", "text": format!(
                "Module '{}' defined ({} files matched).", name, file_count
            ) }]
        }))
    }

    fn tool_list_modules(&mut self, _args: &Value) -> Result<Value> {
        let registry = self.get_registry()?;
        let modules = registry.list_modules()?;

        if modules.is_empty() {
            return Ok(json!({
                "content": [{ "type": "text", "text": "No modules defined." }]
            }));
        }

        let mut lines = vec![format!(
            "{:<30} {:>5}  {}",
            "Module", "Files", "Description"
        )];
        lines.push("-".repeat(80));

        for module in &modules {
            let file_count = registry.file_count(module);
            lines.push(format!(
                "{:<30} {:>5}  {}",
                module.name, file_count, module.description
            ));
        }

        Ok(json!({
            "content": [{ "type": "text", "text": lines.join("\n") }]
        }))
    }

    fn tool_get_module(&mut self, args: &Value) -> Result<Value> {
        let name = args["name"].as_str().context("Missing 'name'")?;
        let registry = self.get_registry()?;

        let module = match registry.get_module(name)? {
            Some(m) => m,
            None => {
                return Ok(json!({
                    "content": [{ "type": "text", "text": format!("Module '{}' not found.", name) }]
                }));
            }
        };

        let files = registry.resolve_files(&module)?;
        let mut lines = Vec::new();

        lines.push(format!("## Module: {}", module.name));
        lines.push(format!("**Description:** {}", module.description));

        if !module.tags.is_empty() {
            lines.push(format!("**Tags:** {}", module.tags.join(", ")));
        }

        // Files
        lines.push(format!("\n### Files ({})", files.len()));
        for f in files.iter().take(50) {
            lines.push(format!("- {}", f));
        }
        if files.len() > 50 {
            lines.push(format!("... and {} more", files.len() - 50));
        }

        // Entry points
        if !module.entry_points.is_empty() {
            lines.push("\n### Entry Points".into());
            for ep in &module.entry_points {
                lines.push(format!("- {}", ep));
            }
        }

        // Interfaces (if scanned)
        if let Ok(Some(iface)) = crate::modules::load_interface(&self.temper_dir(), name) {
            if !iface.exposes.rest.is_empty() {
                lines.push(format!("\n### REST Endpoints ({})", iface.exposes.rest.len()));
                for ep in &iface.exposes.rest {
                    lines.push(format!("- {} {} → {} (line {})", ep.method, ep.path, ep.handler, ep.line));
                }
            }

            if !iface.exposes.java.is_empty() {
                lines.push(format!("\n### Public Methods ({})", iface.exposes.java.len()));
                for m in &iface.exposes.java {
                    lines.push(format!("- {}.{} (line {})", m.class, m.method, m.line));
                }
            }

            if !iface.depends_on.is_empty() {
                lines.push("\n### Depends On".into());
                for d in &iface.depends_on {
                    lines.push(format!("- {} ({}.{}) — {}", d.module, d.class, d.method, d.usage));
                }
            }

            if !iface.depended_by.is_empty() {
                lines.push("\n### Depended By".into());
                for d in &iface.depended_by {
                    lines.push(format!("- {} ({}.{}) — {}", d.module, d.class, d.method, d.usage));
                }
            }
        }

        // Knowledge for this module
        let query = storage::RecallQuery {
            module: Some(name.to_string()),
            ..Default::default()
        };
        if let Ok(entries) = self.store.recall(query) {
            if !entries.is_empty() {
                lines.push(format!("\n### Knowledge ({})", entries.len()));
                for e in entries.iter().take(10) {
                    let stale = if e.status == "stale" { " ⚠️STALE" } else { "" };
                    lines.push(format!("- [{}] **{}**{}", e.entry_type, e.title, stale));
                    if e.content.len() > 150 {
                        lines.push(format!("  {}", &e.content[..150]));
                    } else {
                        lines.push(format!("  {}", e.content));
                    }
                }
            }
        }

        // Constraints
        if let Ok(constraints) = self.store.get_constraints(name) {
            if !constraints.is_empty() {
                lines.push(format!("\n### ⚠️ Constraints ({})", constraints.len()));
                for c in &constraints {
                    lines.push(format!("- **{}**: {}", c.title, c.content));
                }
            }
        }

        Ok(json!({
            "content": [{ "type": "text", "text": lines.join("\n") }]
        }))
    }

    fn tool_remove_module(&mut self, args: &Value) -> Result<Value> {
        let name = args["name"].as_str().context("Missing 'name'")?;
        let registry = self.get_registry()?;
        let removed = registry.remove_module(name)?;

        let msg = if removed {
            format!("Module '{}' removed.", name)
        } else {
            format!("Module '{}' not found.", name)
        };

        Ok(json!({
            "content": [{ "type": "text", "text": msg }]
        }))
    }

    // --- Knowledge tools ---

    fn tool_remember(&self, args: &Value) -> Result<Value> {
        let title = args["title"].as_str().context("Missing 'title'")?;
        let content = args["content"].as_str().context("Missing 'content'")?;
        let entry_type = args["type"].as_str().context("Missing 'type'")?;

        let tags: Vec<String> = args.get("tags")
            .and_then(|v| serde_json::from_value(v.clone()).ok())
            .unwrap_or_default();

        let entry = storage::KnowledgeEntry {
            id: String::new(),
            entry_type: entry_type.to_string(),
            title: title.to_string(),
            content: content.to_string(),
            module: args.get("module").and_then(|v| v.as_str()).map(String::from),
            file: args.get("file").and_then(|v| v.as_str()).map(String::from),
            function: args.get("function_name").and_then(|v| v.as_str()).map(String::from),
            tags,
            status: "active".to_string(),
            current_version: 1,
            git_commit: None,
            created_at: 0,
            updated_at: 0,
        };

        // Smart remember with dedup
        let emb_client = storage::EmbeddingClient::from_config().ok().flatten();
        let (action, id) = crate::memory::smart_remember(&self.store, entry, emb_client.as_ref())?;

        let file_info = args.get("file").and_then(|v| v.as_str())
            .map(|f| format!(" → {}", f))
            .unwrap_or_default();

        Ok(json!({
            "content": [{ "type": "text", "text": format!(
                "Remembered: \"{}\" [{}]{} — {} (id: {})", title, entry_type, file_info, action, id
            ) }]
        }))
    }

    fn tool_recall(&self, args: &Value) -> Result<Value> {
        let query = storage::RecallQuery {
            query: args.get("query").and_then(|v| v.as_str()).map(String::from),
            module: args.get("module").and_then(|v| v.as_str()).map(String::from),
            entry_type: args.get("type").and_then(|v| v.as_str()).map(String::from),
            include_stale: args.get("include_stale").and_then(|v| v.as_bool()).unwrap_or(false),
        };

        let entries = self.store.recall(query)?;

        if entries.is_empty() {
            return Ok(json!({
                "content": [{ "type": "text", "text": "No knowledge found." }]
            }));
        }

        let icons = |t: &str| match t {
            "decision" => "🎯", "bug" => "🐛", "constraint" => "⚠️",
            "experience" => "💡", _ => "📝",
        };

        let mut lines = vec![format!("Found {} entries:\n", entries.len())];
        for e in &entries {
            let stale = if e.status == "stale" { " ⚠️STALE" } else { "" };
            lines.push(format!(
                "{} **{}**{}\n  {}\n  {}{}| id: {}",
                icons(&e.entry_type),
                e.title,
                stale,
                if e.content.len() > 300 { &e.content[..300] } else { &e.content },
                e.file.as_deref().map(|f| format!("file: {} ", f)).unwrap_or_default(),
                if !e.tags.is_empty() { format!("tags: {} ", e.tags.join(", ")) } else { String::new() },
                e.id,
            ));
        }

        Ok(json!({
            "content": [{ "type": "text", "text": lines.join("\n\n") }]
        }))
    }

    fn tool_forget(&self, args: &Value) -> Result<Value> {
        let id = args["id"].as_str().context("Missing 'id'")?;
        self.store.forget(id)?;
        Ok(json!({
            "content": [{ "type": "text", "text": format!("Expired: {} (history preserved)", id) }]
        }))
    }

    fn tool_record_experience(&self, args: &Value) -> Result<Value> {
        let tags: Vec<String> = args.get("tags")
            .and_then(|v| serde_json::from_value(v.clone()).ok())
            .unwrap_or_default();

        let exp = storage::Experience {
            id: String::new(),
            module: args.get("module").and_then(|v| v.as_str()).map(String::from),
            symptom: args["symptom"].as_str().context("Missing 'symptom'")?.to_string(),
            cause: args["cause"].as_str().context("Missing 'cause'")?.to_string(),
            fix: args["fix"].as_str().context("Missing 'fix'")?.to_string(),
            constraint_note: args.get("constraint_note").and_then(|v| v.as_str()).map(String::from),
            tags,
            status: "active".to_string(),
            git_commit: None,
            created_at: 0,
            updated_at: 0,
        };

        let id = self.store.record_experience(exp)?;
        Ok(json!({
            "content": [{ "type": "text", "text": format!("Experience recorded (id: {})", id) }]
        }))
    }

    fn tool_search_symptom(&self, args: &Value) -> Result<Value> {
        let symptom = args["symptom"].as_str().context("Missing 'symptom'")?;
        let results = self.store.search_symptom(symptom)?;

        if results.is_empty() {
            return Ok(json!({
                "content": [{ "type": "text", "text": "No matching experiences found." }]
            }));
        }

        let mut lines = vec![format!("Found {} experiences:\n", results.len())];
        for e in &results {
            lines.push(format!(
                "**Symptom:** {}\n**Cause:** {}\n**Fix:** {}{}\nid: {}",
                e.symptom, e.cause, e.fix,
                e.constraint_note.as_deref().map(|c| format!("\n**Constraint:** {}", c)).unwrap_or_default(),
                e.id,
            ));
        }

        Ok(json!({
            "content": [{ "type": "text", "text": lines.join("\n\n---\n\n") }]
        }))
    }

    fn tool_find_causal_chain(&self, args: &Value) -> Result<Value> {
        let entity = args["entity"].as_str().context("Missing 'entity'")?;
        let max_depth = args.get("max_depth").and_then(|v| v.as_u64()).unwrap_or(3) as u32;

        let chain = self.store.find_causal_chain(entity, max_depth)?;

        if chain.is_empty() {
            return Ok(json!({
                "content": [{ "type": "text", "text": format!("No causal relations found for '{}'.", entity) }]
            }));
        }

        let mut lines = vec![format!("Causal chain from '{}':\n", entity)];
        for node in &chain {
            let indent = "  ".repeat(node.depth as usize);
            let desc = node.description.as_deref().map(|d| format!(" — {}", d)).unwrap_or_default();
            lines.push(format!("{}[{}] {}{}", indent, node.relation, node.entity, desc));
        }

        Ok(json!({
            "content": [{ "type": "text", "text": lines.join("\n") }]
        }))
    }

    fn tool_add_causal_relation(&self, args: &Value) -> Result<Value> {
        let relation = storage::CausalRelation {
            id: String::new(),
            from_entity: args["from_entity"].as_str().context("Missing 'from_entity'")?.to_string(),
            to_entity: args["to_entity"].as_str().context("Missing 'to_entity'")?.to_string(),
            relation_type: args["relation_type"].as_str().context("Missing 'relation_type'")?.to_string(),
            description: args.get("description").and_then(|v| v.as_str()).map(String::from),
            confidence: "suspected".to_string(),
            git_commit: None,
            created_at: 0,
        };

        let id = self.store.add_causal_relation(relation)?;
        Ok(json!({
            "content": [{ "type": "text", "text": format!(
                "Causal relation added: {} → [{}] → {} (id: {})",
                args["from_entity"].as_str().unwrap_or("?"),
                args["relation_type"].as_str().unwrap_or("?"),
                args["to_entity"].as_str().unwrap_or("?"),
                id
            ) }]
        }))
    }

    fn tool_search_knowledge(&self, args: &Value) -> Result<Value> {
        let query_text = args["query"].as_str().context("Missing 'query'")?;
        let limit = args.get("limit").and_then(|v| v.as_u64()).unwrap_or(10) as usize;

        // Try semantic search first if embedding client is available
        if let Ok(Some(client)) = storage::EmbeddingClient::from_config() {
            let emb_store = storage::EmbeddingStore::new(self.store.connection());
            let _ = emb_store.init_schema();

            if let Ok(query_emb) = client.embed(query_text) {
                let results = emb_store.search(&query_emb, limit)?;

                if !results.is_empty() {
                    let mut lines = vec![format!("Semantic search results ({}):\n", results.len())];

                    for (id, entity_type, score) in &results {
                        // Look up the actual entry
                        let detail = if entity_type == "knowledge" {
                            let q = storage::RecallQuery {
                                query: Some(id.clone()),
                                include_stale: true,
                                ..Default::default()
                            };
                            self.store.recall(q).ok()
                                .and_then(|e| e.into_iter().find(|e| e.id == *id))
                                .map(|e| format!("[{}] **{}**\n  {}", e.entry_type, e.title, &e.content[..e.content.len().min(200)]))
                                .unwrap_or_else(|| id.clone())
                        } else {
                            id.clone()
                        };
                        lines.push(format!("- (score: {:.3}) {}", score, detail));
                    }

                    return Ok(json!({
                        "content": [{ "type": "text", "text": lines.join("\n") }]
                    }));
                }
            }
        }

        // Fallback: keyword search via recall
        let query = storage::RecallQuery {
            query: Some(query_text.to_string()),
            include_stale: true,
            ..Default::default()
        };

        let entries = self.store.recall(query)?;

        if entries.is_empty() {
            return Ok(json!({
                "content": [{ "type": "text", "text": "No knowledge found. (Semantic search unavailable — set embedding API key for better results)" }]
            }));
        }

        let mut lines = vec![format!("Keyword search results ({}):\n", entries.len())];
        for e in entries.iter().take(limit) {
            let stale = if e.status == "stale" { " ⚠️STALE" } else { "" };
            lines.push(format!(
                "- [{}] **{}**{}\n  {}",
                e.entry_type, e.title, stale,
                if e.content.len() > 200 { &e.content[..200] } else { &e.content },
            ));
        }

        Ok(json!({
            "content": [{ "type": "text", "text": lines.join("\n") }]
        }))
    }

    fn tool_scan_module_interfaces(&mut self, args: &Value) -> Result<Value> {
        let module_name = args["module"].as_str().context("Missing 'module'")?;
        let registry = self.get_registry()?;

        let iface = crate::modules::scan_module_interfaces(
            &self.project_path, &registry, module_name,
        )?;

        let rest_count = iface.exposes.rest.len();
        let java_count = iface.exposes.java.len();

        crate::modules::save_interface(&self.temper_dir(), &iface)?;

        Ok(json!({
            "content": [{ "type": "text", "text": format!(
                "Scanned '{}': {} REST endpoints, {} public methods. Saved to interfaces/.",
                module_name, rest_count, java_count
            ) }]
        }))
    }

    fn tool_get_constraints(&self, args: &Value) -> Result<Value> {
        let module = args["module"].as_str().context("Missing 'module'")?;
        let constraints = self.store.get_constraints(module)?;

        if constraints.is_empty() {
            return Ok(json!({
                "content": [{ "type": "text", "text": format!("No constraints found for module '{}'.", module) }]
            }));
        }

        let mut lines = vec![format!("Constraints for '{}':\n", module)];
        for c in &constraints {
            let stale = if c.status == "stale" { " ⚠️STALE" } else { "" };
            lines.push(format!("⚠️ **{}**{}\n  {}", c.title, stale, c.content));
        }

        Ok(json!({
            "content": [{ "type": "text", "text": lines.join("\n\n") }]
        }))
    }

    fn tool_get_patterns(&mut self, args: &Value) -> Result<Value> {
        let module_name = args["module"].as_str().context("Missing 'module'")?;
        let pattern_type = args.get("pattern_type").and_then(|v| v.as_str());

        let registry = self.get_registry()?;
        let module = match registry.get_module(module_name)? {
            Some(m) => m,
            None => {
                return Ok(json!({
                    "content": [{ "type": "text", "text": format!("Module '{}' not found.", module_name) }]
                }));
            }
        };

        let files = registry.resolve_files(&module)?;
        let mut lines = vec![format!("## Code Patterns for module: {}\n", module_name)];

        // Filter files by pattern type
        let relevant_files: Vec<&String> = files.iter().filter(|f| {
            match pattern_type {
                Some("handler") => f.contains("handler") || f.contains("Handler"),
                Some("service") => f.contains("service") || f.contains("Service"),
                Some("controller") => f.contains("controller") || f.contains("Controller"),
                Some("test") => f.contains("test") || f.contains("Test"),
                _ => true,
            }
        }).collect();

        // Read up to 5 key files, extract class structure
        let sample_files: Vec<&&String> = relevant_files.iter().take(5).collect();

        if sample_files.is_empty() {
            lines.push("No matching files found for this pattern type.".into());
        }

        for file_path in &sample_files {
            let full_path = self.project_path.join(file_path);
            if !full_path.exists() {
                continue;
            }

            let content = match std::fs::read_to_string(&full_path) {
                Ok(c) => c,
                Err(_) => continue,
            };

            lines.push(format!("### {}\n", file_path));

            // Extract class/interface declarations and public method signatures
            let mut in_class = false;
            let mut brace_depth = 0i32;
            let mut snippet_lines = Vec::new();

            for (i, line) in content.lines().enumerate() {
                let trimmed = line.trim();

                // Class/interface declaration
                if (trimmed.contains("class ") || trimmed.contains("interface "))
                    && (trimmed.starts_with("public") || trimmed.starts_with("abstract") || trimmed.starts_with("@"))
                {
                    snippet_lines.push(format!("L{}: {}", i + 1, line));
                    in_class = true;
                }

                // Public method signatures (first 2 lines of each)
                if in_class
                    && trimmed.starts_with("public")
                    && trimmed.contains("(")
                    && !trimmed.contains("class ")
                {
                    snippet_lines.push(format!("L{}: {}", i + 1, line));
                    // Include next line if method sig continues
                    if !trimmed.contains(")") || !trimmed.contains("{") {
                        if let Some(next) = content.lines().nth(i + 1) {
                            snippet_lines.push(format!("L{}: {}", i + 2, next));
                        }
                    }
                }

                // Track imports for dependencies
                if trimmed.starts_with("import ") && !trimmed.contains("java.util") && !trimmed.contains("java.io") {
                    snippet_lines.push(format!("L{}: {}", i + 1, line));
                }

                // Annotations on classes/methods
                if trimmed.starts_with("@") && !trimmed.starts_with("@Override") && !trimmed.starts_with("@Slf4j") {
                    snippet_lines.push(format!("L{}: {}", i + 1, line));
                }
            }

            if snippet_lines.is_empty() {
                lines.push("  (no public API extracted)\n".into());
            } else {
                lines.push("```java".into());
                // Dedup and limit
                let mut seen = std::collections::HashSet::new();
                for sl in snippet_lines.iter().take(30) {
                    if seen.insert(sl.clone()) {
                        lines.push(sl.clone());
                    }
                }
                lines.push("```\n".into());
            }
        }

        // Add constraints for this module
        if let Ok(constraints) = self.store.get_constraints(module_name) {
            if !constraints.is_empty() {
                lines.push(format!("### ⚠️ Constraints ({}):\n", constraints.len()));
                for c in &constraints {
                    lines.push(format!("- **{}**: {}", c.title, c.content));
                }
            }
        }

        // Add relevant experiences
        let query = storage::RecallQuery {
            module: Some(module_name.to_string()),
            entry_type: Some("decision".to_string()),
            ..Default::default()
        };
        if let Ok(decisions) = self.store.recall(query) {
            if !decisions.is_empty() {
                lines.push(format!("\n### Design Decisions ({}):\n", decisions.len()));
                for d in decisions.iter().take(5) {
                    lines.push(format!("- 🎯 **{}**: {}", d.title, d.content));
                }
            }
        }

        Ok(json!({
            "content": [{ "type": "text", "text": lines.join("\n") }]
        }))
    }

    fn tool_auto_extract(&self, args: &Value) -> Result<Value> {
        let context = args["context"].as_str().context("Missing 'context'")?;

        // Get LLM config — use the same endpoint as embedding but with a chat model
        let config = crate::config::GlobalConfig::load_or_default()?;
        let api_key = std::env::var(&config.embedding.api_key_env)
            .unwrap_or_default();

        if api_key.is_empty() {
            return Ok(json!({
                "content": [{ "type": "text", "text": "auto_extract requires an LLM API key. Set OPENAI_API_KEY or configure in ~/.temper/config.yaml" }]
            }));
        }

        // Use chat completions endpoint (not embeddings)
        let chat_endpoint = config.embedding.endpoint
            .replace("/embeddings", "/chat/completions");
        let model = std::env::var("TEMPER_EXTRACT_MODEL")
            .unwrap_or_else(|_| "gpt-4o-mini".to_string());

        let result = crate::memory::auto_extract(
            context, &chat_endpoint, &api_key, &model,
        )?;

        let mut lines = Vec::new();
        let mut stored_count = 0;

        // Store constraints
        for fact in &result.constraints {
            let entry = storage::KnowledgeEntry {
                id: String::new(),
                entry_type: "constraint".to_string(),
                title: fact.title.clone(),
                content: fact.content.clone(),
                module: fact.module.clone(),
                file: fact.file.clone(),
                function: None,
                tags: Vec::new(),
                status: "active".to_string(),
                current_version: 1,
                git_commit: None,
                created_at: 0,
                updated_at: 0,
            };

            // Use smart dedup
            let emb_client = storage::EmbeddingClient::from_config().ok().flatten();
            let (action, id) = crate::memory::smart_remember(
                &self.store, entry, emb_client.as_ref(),
            )?;
            lines.push(format!("⚠️ [constraint] {} — {} ({})", fact.title, action, id));
            stored_count += 1;
        }

        // Store decisions
        for fact in &result.decisions {
            let entry = storage::KnowledgeEntry {
                id: String::new(),
                entry_type: "decision".to_string(),
                title: fact.title.clone(),
                content: fact.content.clone(),
                module: fact.module.clone(),
                file: fact.file.clone(),
                function: None,
                tags: Vec::new(),
                status: "active".to_string(),
                current_version: 1,
                git_commit: None,
                created_at: 0,
                updated_at: 0,
            };
            let emb_client = storage::EmbeddingClient::from_config().ok().flatten();
            let (action, id) = crate::memory::smart_remember(
                &self.store, entry, emb_client.as_ref(),
            )?;
            lines.push(format!("🎯 [decision] {} — {} ({})", fact.title, action, id));
            stored_count += 1;
        }

        // Store causal relations
        for rel in &result.causal_relations {
            let relation = storage::CausalRelation {
                id: String::new(),
                from_entity: rel.from.clone(),
                to_entity: rel.to.clone(),
                relation_type: rel.relation_type.clone(),
                description: rel.description.clone(),
                confidence: "suspected".to_string(),
                git_commit: None,
                created_at: 0,
            };
            let id = self.store.add_causal_relation(relation)?;
            lines.push(format!("🔗 [causal] {} → [{}] → {} ({})", rel.from, rel.relation_type, rel.to, id));
            stored_count += 1;
        }

        // Store experiences
        for exp in &result.experiences {
            let experience = storage::Experience {
                id: String::new(),
                module: None,
                symptom: exp.symptom.clone(),
                cause: exp.cause.clone(),
                fix: exp.fix.clone(),
                constraint_note: exp.constraint_note.clone(),
                tags: Vec::new(),
                status: "active".to_string(),
                git_commit: None,
                created_at: 0,
                updated_at: 0,
            };
            let id = self.store.record_experience(experience)?;
            lines.push(format!("💡 [experience] {} ({})", exp.symptom, id));
            stored_count += 1;
        }

        if stored_count == 0 {
            return Ok(json!({
                "content": [{ "type": "text", "text": "No knowledge extracted from this context." }]
            }));
        }

        let mut output = vec![format!("Auto-extracted {} items:\n", stored_count)];
        output.extend(lines);

        Ok(json!({
            "content": [{ "type": "text", "text": output.join("\n") }]
        }))
    }

    fn tool_refresh_modules(&mut self, _args: &Value) -> Result<Value> {
        let graph = self.ensure_graph()?;
        let files = graph.files.clone();
        let existing = {
            let temper = self.temper_dir();
            let registry = crate::modules::ModuleRegistry::new(&temper, files.clone());
            registry.list_modules()?.iter().map(|m| m.name.clone()).collect::<Vec<_>>()
        };

        let suggestions = crate::modules::suggest_modules(&files);

        // Filter out already-defined modules
        let new_suggestions: Vec<_> = suggestions
            .iter()
            .filter(|s| !existing.iter().any(|e| e == &s.name))
            .collect();

        if new_suggestions.is_empty() {
            return Ok(json!({
                "content": [{ "type": "text", "text": "No new modules to suggest. All detected packages are already defined." }]
            }));
        }

        let mut lines = vec![format!("Found {} new module candidates:\n", new_suggestions.len())];
        for (i, s) in new_suggestions.iter().enumerate() {
            lines.push(format!(
                "{}. **{}** — {} ({} files)\n   paths: {:?}",
                i + 1, s.name, s.description, s.file_count, s.paths
            ));
        }
        lines.push("\nUse `define_module` to add any of these.".into());

        Ok(json!({
            "content": [{ "type": "text", "text": lines.join("\n") }]
        }))
    }

    fn tool_validate_modules(&mut self, _args: &Value) -> Result<Value> {
        let graph = self.ensure_graph()?;
        let files = graph.files.clone();
        let temper = self.temper_dir();
        let registry = crate::modules::ModuleRegistry::new(&temper, files);
        let modules = registry.list_modules()?;

        let mut issues = Vec::new();

        for module in &modules {
            // Check if glob matches any files
            let matched = registry.file_count(module);
            if matched == 0 {
                issues.push(format!(
                    "⚠️ **{}**: glob patterns match 0 files. Paths: {:?}",
                    module.name, module.paths
                ));
            }

            // Check if interface file exists and is fresh
            if let Ok(Some(iface)) = crate::modules::load_interface(&temper, &module.name) {
                // Simple staleness check: interface older than 7 days
                if let Ok(gen_date) = chrono::NaiveDate::parse_from_str(&iface.generated_at, "%Y-%m-%d") {
                    let today = chrono::Utc::now().date_naive();
                    let age = today.signed_duration_since(gen_date).num_days();
                    if age > 7 {
                        issues.push(format!(
                            "📅 **{}**: interface scanned {} days ago ({}). Consider running scan_module_interfaces.",
                            module.name, age, iface.generated_at
                        ));
                    }
                }
            }

            // Check for empty description
            if module.description.is_empty() {
                issues.push(format!("📝 **{}**: no description.", module.name));
            }

            // Check for no tags
            if module.tags.is_empty() {
                issues.push(format!("🏷️ **{}**: no tags (affects dimension auto-inference).", module.name));
            }
        }

        if issues.is_empty() {
            Ok(json!({
                "content": [{ "type": "text", "text": format!("All {} modules are valid. No issues found.", modules.len()) }]
            }))
        } else {
            let mut lines = vec![format!("Found {} issues across {} modules:\n", issues.len(), modules.len())];
            lines.extend(issues);
            Ok(json!({
                "content": [{ "type": "text", "text": lines.join("\n") }]
            }))
        }
    }

    /// Proactive constraint injection: find constraints anchored to a file path.
    fn get_constraints_for_file(&self, file_path: &str) -> Vec<storage::KnowledgeEntry> {
        // Search by exact file match and partial path match
        let query = storage::RecallQuery {
            query: None,
            module: None,
            entry_type: Some("constraint".to_string()),
            include_stale: false,
        };

        self.store.recall(query).unwrap_or_default()
            .into_iter()
            .filter(|k| {
                if let Some(ref f) = k.file {
                    file_path.contains(f.as_str()) || f.contains(file_path)
                } else {
                    false
                }
            })
            .collect()
    }

    /// Get all knowledge (constraints + decisions + experiences) related to a file.
    fn get_knowledge_for_file(&self, file_path: &str) -> Vec<storage::KnowledgeEntry> {
        let query = storage::RecallQuery {
            query: None,
            module: None,
            entry_type: None,
            include_stale: false,
        };

        self.store.recall(query).unwrap_or_default()
            .into_iter()
            .filter(|k| {
                if let Some(ref f) = k.file {
                    file_path.contains(f.as_str()) || f.contains(file_path)
                } else {
                    false
                }
            })
            .collect()
    }
}

/// Get changed files via git status (works with uncommitted changes).
fn get_changed_files_git_status(project_path: &std::path::Path) -> Vec<String> {
    let output = std::process::Command::new("git")
        .args(["status", "--porcelain"])
        .current_dir(project_path)
        .output();

    match output {
        Ok(out) => {
            String::from_utf8_lossy(&out.stdout)
                .lines()
                .filter_map(|line| {
                    // Format: "XY filename" — XY is exactly 2 chars, then a space, then path
                    // Examples: " M src/Foo.java", "?? src/Bar.java", "R  old -> new"
                    if line.len() < 4 {
                        return None;
                    }
                    // Skip first 3 chars: XY + space
                    let path = line[3..].trim();
                    // Handle rename: "old -> new"
                    let path = if let Some(pos) = path.find(" -> ") {
                        &path[pos + 4..]
                    } else {
                        path
                    };
                    // Only source files
                    let source_exts = [".java", ".py", ".ts", ".tsx", ".js", ".mjs"];
                    if source_exts.iter().any(|ext| path.ends_with(ext)) {
                        Some(path.to_string())
                    } else {
                        None
                    }
                })
                .collect()
        }
        Err(_) => Vec::new(),
    }
}
