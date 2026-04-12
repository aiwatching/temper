use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use std::path::PathBuf;

use crate::config::GlobalConfig;
use crate::graph::CodeGraph;
use crate::modules::{ModuleDef, ModuleRegistry};
use crate::parser::Scanner;

#[derive(Parser)]
#[command(name = "temper", version, about = "Forged memory for your code")]
pub struct Cli {
    #[command(subcommand)]
    pub command: Command,
}

#[derive(Subcommand)]
pub enum Command {
    /// Initialize Temper for the current project (scan + suggest modules)
    Init {
        /// Project path (defaults to current directory)
        #[arg(default_value = ".")]
        path: PathBuf,
    },

    /// Start the MCP server (Claude Code calls this automatically)
    Serve {
        /// Project path
        #[arg(default_value = ".")]
        path: PathBuf,
    },

    /// Scan project code structure
    Scan {
        /// Project path
        #[arg(default_value = ".")]
        path: PathBuf,
        /// Force full rescan (ignore cache)
        #[arg(long)]
        force: bool,
    },

    /// List or inspect modules
    Modules {
        /// Module name to inspect (omit for list)
        name: Option<String>,
        /// Filter by dimension
        #[arg(long)]
        dimension: Option<String>,
    },

    /// Search code and knowledge
    Search {
        /// Search query
        query: String,
    },

    /// List or filter knowledge entries
    Knowledge {
        /// Filter by module
        #[arg(long)]
        module: Option<String>,
        /// Filter by type (decision/bug/constraint/experience/causal)
        #[arg(long, name = "type")]
        entry_type: Option<String>,
    },

    /// View temporal history of a knowledge entry
    History {
        /// Knowledge entry ID
        id: String,
    },

    /// Code graph operations
    Graph {
        /// Show statistics
        #[arg(long)]
        stats: bool,
        /// Show module dependency graph (ASCII)
        #[arg(long)]
        deps: Option<String>,
        /// Show causal chain (ASCII)
        #[arg(long)]
        causal: Option<String>,
    },

    /// Project overview
    Status,

    /// Show Temper usage statistics
    Stats,

    /// Show impact chain for a symbol (class, function, file)
    Impact {
        /// Symbol to analyze (e.g. "HostRecord", "UserService.findAll")
        symbol: String,
        /// Max depth of impact chain (default 3)
        #[arg(long, default_value = "3")]
        depth: u32,
    },

    /// Assess risk of current uncommitted changes
    Risk {
        /// Compare against this git ref (default: HEAD)
        #[arg(long, default_value = "HEAD")]
        base: String,
    },

    /// Project architecture overview (for onboarding)
    Overview,

    /// Compare directories to show migration progress
    /// Use --source '*' to compare all top-level dirs against target
    Diff {
        /// Source directory, or '*' for all top-level dirs except target
        #[arg(long, default_value = "*")]
        source: String,
        /// Target directory (e.g. restructured/)
        #[arg(long, default_value = "restructured")]
        target: String,
    },

    /// Check which files have upstream changes not synced to target
    SyncCheck {
        /// Source directory
        #[arg(long, default_value = "common")]
        source: String,
        /// Target directory
        #[arg(long, default_value = "restructured")]
        target: String,
    },

    /// Show up/down call tree for a function or class
    CallTree {
        /// Symbol name
        symbol: String,
        /// Direction: up (callers), down (callees), both
        #[arg(long, default_value = "both")]
        direction: String,
        /// Max depth
        #[arg(long, default_value = "3")]
        depth: u32,
    },

    /// Find dead code (files/functions with no incoming references)
    DeadCode,

    /// Show cross-module import violations (boundary checks)
    Boundary,

    /// Show module cohesion (internal vs external imports)
    Cohesion,

    /// Export visualization
    Export {
        /// Output format
        #[arg(long, default_value = "html")]
        format: String,
        /// Output directory
        #[arg(long)]
        output: Option<PathBuf>,
        /// Open in browser after export
        #[arg(long)]
        open: bool,
    },

    /// Interactive TUI
    Ui,

    /// Configuration management
    Config {
        #[command(subcommand)]
        action: ConfigAction,
    },

    /// Sync with central server (future)
    Sync {
        #[command(subcommand)]
        action: SyncAction,
    },

    /// Upgrade: update npm package + rescan project with new parsers
    Upgrade,
}

#[derive(Subcommand)]
pub enum ConfigAction {
    /// Set a config value
    Set {
        key: String,
        value: String,
    },
    /// Get a config value
    Get {
        key: String,
    },
    /// Show all config
    Show,
}

#[derive(Subcommand)]
pub enum SyncAction {
    /// Push local knowledge to central server
    Push,
    /// Pull knowledge from central server
    Pull,
}

pub fn run(cli: Cli) -> Result<()> {
    // Check for updates on every command (non-blocking, silent on error)
    check_for_update();

    match cli.command {
        Command::Init { path } => cmd_init(path),
        Command::Serve { path } => cmd_serve(path),
        Command::Scan { path, force } => cmd_scan(path, force),
        Command::Status => cmd_status(),
        Command::Stats => cmd_stats(),
        Command::Impact { symbol, depth } => cmd_impact(&symbol, depth),
        Command::Risk { base } => cmd_risk(&base),
        Command::Overview => cmd_overview(),
        Command::Diff { source, target } => cmd_diff(&source, &target),
        Command::SyncCheck { source, target } => cmd_sync_check(&source, &target),
        Command::CallTree { symbol, direction, depth } => cmd_call_tree(&symbol, &direction, depth),
        Command::DeadCode => cmd_dead_code(),
        Command::Boundary => cmd_boundary(),
        Command::Cohesion => cmd_cohesion(),
        Command::Search { query } => cmd_search(&query),
        Command::Modules { name, dimension } => cmd_modules(name, dimension),
        Command::Knowledge { module, entry_type } => cmd_knowledge(module, entry_type),
        Command::History { id } => cmd_history(&id),
        Command::Graph { stats, deps, causal } => cmd_graph(stats, deps, causal),
        Command::Export { format, output, open } => cmd_export(&format, output, open),
        Command::Ui => cmd_ui(),
        Command::Config { action } => cmd_config(action),
        Command::Sync { action } => cmd_sync(action),
        Command::Upgrade => cmd_upgrade(),
    }
}

fn resolve_project_path(path: PathBuf) -> Result<PathBuf> {
    let path = if path.is_relative() {
        std::env::current_dir()?.join(path)
    } else {
        path
    };
    path.canonicalize()
        .with_context(|| format!("Project path not found: {}", path.display()))
}

fn temper_dir(project_path: &std::path::Path) -> PathBuf {
    project_path.join(".temper")
}

fn cmd_init(path: PathBuf) -> Result<()> {
    let project_path = resolve_project_path(path)?;
    let temper = temper_dir(&project_path);

    // Create .temper directory
    std::fs::create_dir_all(temper.join("modules"))?;
    std::fs::create_dir_all(temper.join("interfaces"))?;

    eprintln!("Initializing Temper for: {}", project_path.display());

    // Ensure global config
    GlobalConfig::ensure_default()?;

    // Register project
    crate::config::register_project(&project_path)?;

    // Full scan
    eprintln!("Scanning project...");
    let scanner = Scanner::new(&project_path);
    let graph = scanner.full_scan()?;

    let stats = graph.stats();
    eprintln!(
        "Scan complete: {} files, {} functions, {} classes, {} edges",
        stats.files, stats.functions, stats.classes, stats.edges
    );

    // Save graph
    graph.save(&temper.join("graph.json"))?;

    // Save meta
    let mut meta = crate::graph::Meta::new(&project_path);
    meta.update_after_scan(&scanner, &graph);
    meta.save(&temper.join("meta.json"))?;

    // Auto-suggest modules
    let suggestions = crate::modules::suggest_modules(&graph.files);
    if !suggestions.is_empty() {
        eprintln!("\nDetected package structure, suggested modules:");
        let registry = ModuleRegistry::new(&temper, graph.files.clone());

        for (i, suggestion) in suggestions.iter().enumerate() {
            eprintln!(
                "  {}. {} — {} ({} files)",
                i + 1,
                suggestion.name,
                suggestion.description,
                suggestion.file_count
            );
        }

        eprintln!("\nAccept all suggestions? [Y/n/select] ");
        let mut input = String::new();
        std::io::stdin().read_line(&mut input)?;
        let input = input.trim().to_lowercase();

        let to_define: Vec<&crate::modules::suggest::ModuleSuggestion> = if input.is_empty() || input == "y" || input == "yes" {
            suggestions.iter().collect()
        } else if input == "n" || input == "no" {
            Vec::new()
        } else {
            // Parse comma-separated numbers: "1,3,5"
            let indices: Vec<usize> = input
                .split(',')
                .filter_map(|s| s.trim().parse::<usize>().ok())
                .filter(|&i| i >= 1 && i <= suggestions.len())
                .collect();
            indices.iter().map(|&i| &suggestions[i - 1]).collect()
        };

        for suggestion in &to_define {
            let module = ModuleDef {
                name: suggestion.name.clone(),
                description: suggestion.description.clone(),
                paths: suggestion.paths.clone(),
                exclude: Vec::new(),
                entry_points: Vec::new(),
                tags: suggestion.tags.clone(),
                updated_at: chrono::Utc::now().format("%Y-%m-%d").to_string(),
            };
            registry.define_module(&module)?;
            eprintln!("  Defined: {}", suggestion.name);
        }

        if !to_define.is_empty() {
            eprintln!("\n{} modules defined.", to_define.len());
        }
    }

    // Auto-register MCP server for Claude Code
    let project_str = project_path.to_string_lossy().to_string();
    let mcp_config_path = project_path.join(".mcp.json");

    // Read or create .mcp.json
    let mut mcp_config: serde_json::Value = if mcp_config_path.exists() {
        let content = std::fs::read_to_string(&mcp_config_path).unwrap_or_default();
        serde_json::from_str(&content).unwrap_or(serde_json::json!({}))
    } else {
        serde_json::json!({})
    };

    // Add temper MCP server
    if mcp_config.get("mcpServers").is_none() {
        mcp_config["mcpServers"] = serde_json::json!({});
    }
    mcp_config["mcpServers"]["temper"] = serde_json::json!({
        "type": "stdio",
        "command": "temper",
        "args": ["serve", project_str]
    });

    std::fs::write(&mcp_config_path, serde_json::to_string_pretty(&mcp_config)?)?;
    eprintln!("  .mcp.json — MCP server registered");

    // Setup Claude Code hooks
    let claude_dir = project_path.join(".claude");
    let hooks_dir = claude_dir.join("hooks");
    std::fs::create_dir_all(&hooks_dir)?;

    // PreToolUse hook: inject constraints before Edit/Write
    let hook_script = hooks_dir.join("temper-pre-edit.sh");
    if !hook_script.exists() {
        std::fs::write(&hook_script, r#"#!/bin/bash
# Temper: inject constraints before Edit/Write
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
[ -z "$FILE_PATH" ] && exit 0
DB=".temper/knowledge.db"
[ ! -f "$DB" ] && exit 0
CONSTRAINTS=$(sqlite3 "$DB" "SELECT type, title, content FROM knowledge WHERE status='active' AND type='constraint' AND (file LIKE '%${FILE_PATH}%' OR '${FILE_PATH}' LIKE '%' || file || '%') LIMIT 5;" 2>/dev/null)
[ -z "$CONSTRAINTS" ] && exit 0
jq -n --arg ctx "⚠️ TEMPER CONSTRAINTS for ${FILE_PATH}:
${CONSTRAINTS}" '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":$ctx}}'
exit 0
"#)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&hook_script, std::fs::Permissions::from_mode(0o755))?;
        }
    }

    // Statusline script
    let statusline_script = hooks_dir.join("temper-statusline.sh");
    if !statusline_script.exists() {
        std::fs::write(&statusline_script, r#"#!/bin/bash
cat > /dev/null
CALLS=0; KNOWLEDGE=0
[ -f .temper/stats.json ] && CALLS=$(python3 -c "import json; print(json.load(open('.temper/stats.json')).get('session_total',0))" 2>/dev/null || echo 0)
[ -f .temper/knowledge.db ] && KNOWLEDGE=$(sqlite3 .temper/knowledge.db "SELECT COUNT(*) FROM knowledge WHERE status='active'" 2>/dev/null || echo 0)
echo "Temper: ${CALLS} calls | ${KNOWLEDGE} knowledge"
"#)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&statusline_script, std::fs::Permissions::from_mode(0o755))?;
        }
    }

    // UserPromptSubmit hook: auto-inject relevant knowledge before Claude thinks
    let inject_script = hooks_dir.join("temper-context-inject.sh");
    if !inject_script.exists() {
        std::fs::write(&inject_script, r#"#!/bin/bash
INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty' 2>/dev/null)
[ -z "$PROMPT" ] && exit 0
DB=".temper/knowledge.db"
[ ! -f "$DB" ] && exit 0
KEYWORDS=$(echo "$PROMPT" | tr '[:upper:]' '[:lower:]' | tr -cs '[:alnum:]' '\n' | grep -v -E '^(the|a|an|to|of|in|is|it|and|or|for|on|at|by|do|be|if|so|no|up|my|me|we|he|she|can|you|this|that|with|from|have|will|what|how|are|was|not|but|all|just|show|make|add|get|set|put|use|run|let|try|new|old|any|may|its|too)$' | head -5)
[ -z "$KEYWORDS" ] && exit 0
CONDITIONS=""
for kw in $KEYWORDS; do
  [ ${#kw} -lt 3 ] && continue
  [ -n "$CONDITIONS" ] && CONDITIONS="$CONDITIONS OR"
  CONDITIONS="$CONDITIONS title LIKE '%${kw}%' OR content LIKE '%${kw}%' OR tags LIKE '%${kw}%'"
done
[ -z "$CONDITIONS" ] && exit 0
RESULTS=$(sqlite3 -separator '|' "$DB" "SELECT type, title, content FROM knowledge WHERE status='active' AND ($CONDITIONS) LIMIT 5;" 2>/dev/null)
[ -z "$RESULTS" ] && exit 0
CONTEXT="[Temper] Relevant knowledge:
$(echo "$RESULTS" | while IFS='|' read -r type title content; do echo "- [$type] $title: $content"; done)"
CONTEXT=$(echo "$CONTEXT" | head -c 5000)
jq -n --arg ctx "$CONTEXT" '{"additionalContext":$ctx}'
exit 0
"#)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&inject_script, std::fs::Permissions::from_mode(0o755))?;
        }
    }

    // Write .claude/settings.json with hooks + statusline
    let settings_path = claude_dir.join("settings.json");
    let mut settings: serde_json::Value = if settings_path.exists() {
        let content = std::fs::read_to_string(&settings_path).unwrap_or_default();
        serde_json::from_str(&content).unwrap_or(serde_json::json!({}))
    } else {
        serde_json::json!({})
    };

    // Add hooks if not already present
    if settings.get("hooks").is_none() {
        let hook_cmd = hook_script.to_string_lossy().to_string();
        let inject_cmd = inject_script.to_string_lossy().to_string();
        settings["hooks"] = serde_json::json!({
            "UserPromptSubmit": [
                {
                    "hooks": [{"type": "command", "command": inject_cmd, "timeout": 3}]
                }
            ],
            "PreToolUse": [
                {
                    "matcher": "Edit",
                    "hooks": [{"type": "command", "command": hook_cmd, "timeout": 5}]
                },
                {
                    "matcher": "Write",
                    "hooks": [{"type": "command", "command": hook_cmd, "timeout": 5}]
                }
            ]
        });
    }

    // Add statusline if not already present
    if settings.get("statusLine").is_none() {
        let sl_cmd = statusline_script.to_string_lossy().to_string();
        settings["statusLine"] = serde_json::json!({
            "type": "command",
            "command": sl_cmd,
            "refreshInterval": 10
        });
    }

    std::fs::write(&settings_path, serde_json::to_string_pretty(&settings)?)?;
    eprintln!("  .claude/settings.json — hooks + statusline configured");
    eprintln!("  .claude/hooks/temper-pre-edit.sh — constraint injection");
    eprintln!("  .claude/hooks/temper-statusline.sh — status bar");

    // Append Temper section to CLAUDE.md if not already present
    let claudemd_path = project_path.join("CLAUDE.md");
    let temper_marker = "## Temper — Project Memory";
    let needs_append = if claudemd_path.exists() {
        let content = std::fs::read_to_string(&claudemd_path).unwrap_or_default();
        !content.contains(temper_marker)
    } else {
        true
    };

    if needs_append {
        let section = format!("\n\n{}\n\n\
This project uses Temper for persistent memory. Available tools:\n\
- `get_module` — module context (files, interfaces, constraints)\n\
- `search_code` — AST-based code search with impact chain\n\
- `recall` — retrieve stored knowledge\n\
- `get_patterns` — code patterns for writing new code\n\
- `remember` — store constraints, decisions, experiences\n\
- `search_symptom` — find past incidents by symptom\n\n\
Use Temper tools before grep/glob for better results.\n", temper_marker);

        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&claudemd_path)?;
        std::io::Write::write_all(&mut file, section.as_bytes())?;
        eprintln!("  CLAUDE.md — Temper section appended");
    }

    eprintln!("\nTemper initialized at {}", temper.display());
    eprintln!("Restart Claude Code to connect.");
    Ok(())
}

fn cmd_serve(path: PathBuf) -> Result<()> {
    let project_path = resolve_project_path(path)?;
    let rt = tokio::runtime::Runtime::new()?;
    rt.block_on(crate::mcp::serve(project_path))
}

fn cmd_scan(path: PathBuf, force: bool) -> Result<()> {
    let project_path = resolve_project_path(path)?;
    let temper = temper_dir(&project_path);

    let scanner = Scanner::new(&project_path);

    let graph = if force {
        eprintln!("Full rescan...");
        scanner.full_scan()?
    } else {
        let meta_path = temper.join("meta.json");
        let graph_path = temper.join("graph.json");

        if meta_path.exists() && graph_path.exists() {
            let meta = crate::graph::Meta::load(&meta_path)?;
            let existing = CodeGraph::load(&graph_path)?;
            let changed = scanner.get_changed_files(meta.last_scan_commit.as_deref())?;

            if changed.is_empty() {
                eprintln!("No changes detected.");
                return Ok(());
            }

            if changed.len() < 50 {
                eprintln!("Incremental update: {} files changed", changed.len());
                scanner.incremental_update(existing, &changed)?
            } else {
                eprintln!("Too many changes ({}), full rescan...", changed.len());
                scanner.full_scan()?
            }
        } else {
            eprintln!("No existing graph, full scan...");
            scanner.full_scan()?
        }
    };

    let stats = graph.stats();
    eprintln!(
        "Done: {} files, {} functions, {} classes, {} edges",
        stats.files, stats.functions, stats.classes, stats.edges
    );

    graph.save(&temper.join("graph.json"))?;

    let mut meta = crate::graph::Meta::new(&project_path);
    meta.update_after_scan(&scanner, &graph);
    meta.save(&temper.join("meta.json"))?;

    Ok(())
}

fn cmd_status() -> Result<()> {
    let project_path = resolve_project_path(PathBuf::from("."))?;
    let temper = temper_dir(&project_path);

    if !temper.exists() {
        eprintln!("Temper not initialized. Run `temper init` first.");
        return Ok(());
    }

    let graph_path = temper.join("graph.json");
    if graph_path.exists() {
        let graph = CodeGraph::load(&graph_path)?;
        let stats = graph.stats();
        println!("Project:    {}", project_path.display());
        println!("Files:      {}", stats.files);
        println!("Functions:  {}", stats.functions);
        println!("Classes:    {}", stats.classes);
        println!("Edges:      {}", stats.edges);
        println!("Scanned:    {}", graph.scanned_at_display());
    }

    let meta_path = temper.join("meta.json");
    if meta_path.exists() {
        let meta = crate::graph::Meta::load(&meta_path)?;
        if let Some(commit) = &meta.last_scan_commit {
            println!("Git commit: {}", commit);
        }
    }

    Ok(())
}

fn cmd_search(query: &str) -> Result<()> {
    let project_path = resolve_project_path(PathBuf::from("."))?;
    let temper = temper_dir(&project_path);
    let graph_path = temper.join("graph.json");

    if !graph_path.exists() {
        eprintln!("No code graph. Run `temper scan` first.");
        return Ok(());
    }

    let graph = CodeGraph::load(&graph_path)?;
    let result = graph.search(query);

    if result.direct_matches.is_empty() {
        println!("No matches found. Try different keywords.");
        return Ok(());
    }

    println!("## Direct matches ({})", result.direct_matches.len());
    for node in result.direct_matches.iter().take(15) {
        let exp = if node.exported { " (exported)" } else { "" };
        let line = node.line.map(|l| format!(":{}", l)).unwrap_or_default();
        println!(
            "  [{}] {}{} — {}{}",
            node.node_type, node.name, exp, node.file_path, line
        );
    }

    if !result.impact_chain.is_empty() {
        println!(
            "\n## Impact chain ({} connected nodes)",
            result.impact_chain.len()
        );
        for impact in result.impact_chain.iter().take(20) {
            let line = impact
                .node
                .line
                .map(|l| format!(":{}", l))
                .unwrap_or_default();
            println!(
                "  depth={} [{}] {} — {}{}",
                impact.depth, impact.node.node_type, impact.node.name, impact.node.file_path, line
            );
        }
    }

    Ok(())
}

fn cmd_modules(name: Option<String>, _dimension: Option<String>) -> Result<()> {
    let project_path = resolve_project_path(PathBuf::from("."))?;
    let temper = temper_dir(&project_path);

    if !temper.join("modules").exists() {
        eprintln!("No modules defined. Run `temper init` first.");
        return Ok(());
    }

    // Load project files for glob resolution
    let graph_path = temper.join("graph.json");
    let project_files = if graph_path.exists() {
        CodeGraph::load(&graph_path)?.files
    } else {
        Vec::new()
    };

    let registry = ModuleRegistry::new(&temper, project_files);

    if let Some(name) = name {
        // Show module detail
        match registry.get_module(&name)? {
            Some(module) => {
                println!("Module:       {}", module.name);
                println!("Description:  {}", module.description);
                println!("Tags:         {}", module.tags.join(", "));
                println!("Updated:      {}", module.updated_at);

                if !module.paths.is_empty() {
                    println!("\nPaths:");
                    for p in &module.paths {
                        println!("  {}", p);
                    }
                }

                if !module.exclude.is_empty() {
                    println!("\nExclude:");
                    for p in &module.exclude {
                        println!("  {}", p);
                    }
                }

                if !module.entry_points.is_empty() {
                    println!("\nEntry points:");
                    for p in &module.entry_points {
                        println!("  {}", p);
                    }
                }

                let files = registry.resolve_files(&module)?;
                println!("\nMatched files ({}):", files.len());
                for f in files.iter().take(30) {
                    println!("  {}", f);
                }
                if files.len() > 30 {
                    println!("  ... and {} more", files.len() - 30);
                }
            }
            None => {
                eprintln!("Module '{}' not found.", name);
            }
        }
    } else {
        // List all modules
        let modules = registry.list_modules()?;

        if modules.is_empty() {
            eprintln!("No modules defined. Use `temper init` or define with MCP tool.");
            return Ok(());
        }

        println!(
            "{:<30} {:>5}  {}",
            "Module", "Files", "Description"
        );
        println!("{}", "-".repeat(80));

        for module in &modules {
            let file_count = registry.file_count(module);
            let desc = if module.description.chars().count() > 40 {
                let truncated: String = module.description.chars().take(37).collect();
                format!("{}...", truncated)
            } else {
                module.description.clone()
            };
            println!("{:<30} {:>5}  {}", module.name, file_count, desc);
        }

        // Show index dimensions if available
        let index = registry.load_index()?;
        if !index.dimensions.is_empty() {
            println!("\nDimensions:");
            for (dim_name, groups) in &index.dimensions {
                println!("  {}:", dim_name);
                fn print_group(group: &crate::modules::registry::DimensionGroup, indent: usize) {
                    let pad = "  ".repeat(indent);
                    let verified = if group.verified { " [verified]" } else { "" };
                    if !group.modules.is_empty() {
                        println!(
                            "{}{}  modules: [{}]{}",
                            pad, group.name,
                            group.modules.join(", "),
                            verified
                        );
                    } else if !group.children.is_empty() {
                        println!("{}{}{}", pad, group.name, verified);
                    }
                    for child in &group.children {
                        print_group(child, indent + 1);
                    }
                }
                for group in groups {
                    print_group(group, 2);
                }
            }
        }
    }

    Ok(())
}

fn cmd_knowledge(module: Option<String>, entry_type: Option<String>) -> Result<()> {
    let project_path = resolve_project_path(PathBuf::from("."))?;
    let temper = temper_dir(&project_path);
    let db_path = temper.join("knowledge.db");

    if !db_path.exists() {
        eprintln!("No knowledge database. Run `temper init` first.");
        return Ok(());
    }

    let store = crate::storage::LocalStorage::open(&db_path)?;
    let query = crate::storage::RecallQuery {
        query: None,
        module,
        entry_type,
        include_stale: true,
    };

    let entries = crate::storage::KnowledgeStore::recall(&store, query)?;

    if entries.is_empty() {
        println!("No knowledge entries found.");
        return Ok(());
    }

    println!(
        "{:<20} {:<12} {:<8} {:>3}  {}",
        "ID", "Type", "Status", "Ver", "Title"
    );
    println!("{}", "-".repeat(80));

    for e in &entries {
        let title = if e.title.len() > 35 {
            format!("{}...", &e.title[..32])
        } else {
            e.title.clone()
        };
        println!(
            "{:<20} {:<12} {:<8} {:>3}  {}",
            e.id, e.entry_type, e.status, e.current_version, title
        );
    }

    Ok(())
}

fn cmd_history(id: &str) -> Result<()> {
    let project_path = resolve_project_path(PathBuf::from("."))?;
    let temper = temper_dir(&project_path);
    let db_path = temper.join("knowledge.db");

    if !db_path.exists() {
        eprintln!("No knowledge database.");
        return Ok(());
    }

    let store = crate::storage::LocalStorage::open(&db_path)?;
    let records = crate::storage::KnowledgeStore::get_history(&store, id)?;

    if records.is_empty() {
        println!("No history found for '{}'.", id);
        return Ok(());
    }

    println!(
        "{:>3}  {:<10} {:<12} {:<12} {}",
        "Ver", "Status", "Changed By", "Git Commit", "Reason"
    );
    println!("{}", "-".repeat(80));

    for r in &records {
        let commit = r.git_commit.as_deref().unwrap_or("-");
        let reason = r.reason.as_deref().unwrap_or("-");
        let time = chrono::DateTime::from_timestamp(r.timestamp as i64, 0)
            .map(|dt| dt.format("%Y-%m-%d %H:%M").to_string())
            .unwrap_or_else(|| "?".into());
        println!(
            "{:>3}  {:<10} {:<12} {:<12} {}  ({})",
            r.version, r.status, r.changed_by, commit, reason, time
        );
    }

    Ok(())
}

fn cmd_graph(stats: bool, deps: Option<String>, causal: Option<String>) -> Result<()> {
    let project_path = resolve_project_path(PathBuf::from("."))?;
    let temper = temper_dir(&project_path);
    let graph_path = temper.join("graph.json");

    if !graph_path.exists() {
        eprintln!("No code graph. Run `temper scan` first.");
        return Ok(());
    }

    if stats {
        let graph = CodeGraph::load(&graph_path)?;
        let s = graph.stats();
        println!("Files:      {}", s.files);
        println!("Functions:  {}", s.functions);
        println!("Classes:    {}", s.classes);
        println!("Variables:  {}", s.variables);
        println!("Imports:    {}", s.import_edges);
        println!("Calls:      {}", s.call_edges);
        println!("Exports:    {}", s.export_edges);
        println!("Total:      {} nodes, {} edges", s.nodes, s.edges);
    }

    if let Some(ref _module) = deps {
        eprintln!("Module dependency graph not yet implemented (Phase 2).");
    }

    if let Some(ref _entity) = causal {
        eprintln!("Causal chain graph not yet implemented (Phase 3).");
    }

    if !stats && deps.is_none() && causal.is_none() {
        eprintln!("Usage: temper graph --stats | --deps <module> | --causal <entity>");
    }

    Ok(())
}

fn cmd_export(_format: &str, output: Option<PathBuf>, open: bool) -> Result<()> {
    let project_path = resolve_project_path(PathBuf::from("."))?;
    let temper = temper_dir(&project_path);

    let output_dir = output.unwrap_or_else(|| temper.join("export"));
    crate::export::export_html(&project_path, &output_dir)?;

    if open {
        let index = output_dir.join("index.html");
        #[cfg(target_os = "macos")]
        { let _ = std::process::Command::new("open").arg(&index).spawn(); }
        #[cfg(target_os = "linux")]
        { let _ = std::process::Command::new("xdg-open").arg(&index).spawn(); }
    }

    Ok(())
}

fn cmd_ui() -> Result<()> {
    eprintln!("TUI not yet implemented (Phase 3).");
    Ok(())
}

fn cmd_config(action: ConfigAction) -> Result<()> {
    match action {
        ConfigAction::Show => {
            let config = GlobalConfig::load_or_default()?;
            println!("{}", serde_yaml::to_string(&config)?);
        }
        ConfigAction::Get { key } => {
            let config = GlobalConfig::load_or_default()?;
            match key.as_str() {
                "embedding.provider" => println!("{}", config.embedding.provider),
                "embedding.endpoint" => println!("{}", config.embedding.endpoint),
                "embedding.model" => println!("{}", config.embedding.model),
                _ => eprintln!("Unknown config key: {}", key),
            }
        }
        ConfigAction::Set { key, value } => {
            let mut config = GlobalConfig::load_or_default()?;
            match key.as_str() {
                "embedding.provider" => config.embedding.provider = value,
                "embedding.endpoint" => config.embedding.endpoint = value,
                "embedding.model" => config.embedding.model = value,
                "embedding.api_key_env" => config.embedding.api_key_env = value,
                _ => {
                    eprintln!("Unknown config key: {}", key);
                    return Ok(());
                }
            }
            config.save()?;
            eprintln!("Config updated.");
        }
    }
    Ok(())
}

/// Check npm for a newer version. Caches result for 24h to avoid spamming.
fn check_for_update() {
    // Cache file: ~/.temper/update-check
    let cache_path = dirs();
    let cache_file = cache_path.join("update-check");

    // Only check once per 24 hours
    if let Ok(meta) = std::fs::metadata(&cache_file) {
        if let Ok(modified) = meta.modified() {
            if modified.elapsed().unwrap_or_default().as_secs() < 86400 {
                // Read cached result
                if let Ok(content) = std::fs::read_to_string(&cache_file) {
                    let parts: Vec<&str> = content.trim().split('\n').collect();
                    if parts.len() >= 2 && parts[0] == "update-available" {
                        let latest = parts[1];
                        let current = env!("CARGO_PKG_VERSION");
                        if latest != current {
                            eprintln!(
                                "\n  ⬆️  Temper v{} available (current: v{}). Run: temper upgrade\n",
                                latest, current
                            );
                        }
                    }
                }
                return;
            }
        }
    }

    // Background check: spawn a thread so it doesn't block CLI
    let cache_file_clone = cache_file.clone();
    std::thread::spawn(move || {
        let output = std::process::Command::new("npm")
            .args(["view", "@aion0/temper", "version"])
            .output();

        if let Ok(output) = output {
            if output.status.success() {
                let latest = String::from_utf8_lossy(&output.stdout).trim().to_string();
                let current = env!("CARGO_PKG_VERSION");

                // Write cache
                let _ = std::fs::create_dir_all(cache_file_clone.parent().unwrap_or(std::path::Path::new(".")));
                if latest != current {
                    let _ = std::fs::write(&cache_file_clone, format!("update-available\n{}", latest));
                    eprintln!(
                        "\n  ⬆️  Temper v{} available (current: v{}). Run: temper upgrade\n",
                        latest, current
                    );
                } else {
                    let _ = std::fs::write(&cache_file_clone, "up-to-date");
                }
            }
        }
    });
}

fn dirs() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".to_string());
    PathBuf::from(home).join(".temper")
}

fn cmd_sync(action: SyncAction) -> Result<()> {
    match action {
        SyncAction::Push => eprintln!("Central sync not yet implemented (future)."),
        SyncAction::Pull => eprintln!("Central sync not yet implemented (future)."),
    }
    Ok(())
}

fn cmd_impact(symbol: &str, max_depth: u32) -> Result<()> {
    let project_path = resolve_project_path(PathBuf::from("."))?;
    let temper = temper_dir(&project_path);
    let graph_path = temper.join("graph.json");

    if !graph_path.exists() {
        eprintln!("No graph found. Run `temper init` or `temper scan` first.");
        return Ok(());
    }

    let graph = CodeGraph::load(&graph_path)?;
    let result = graph.search(symbol);

    if result.direct_matches.is_empty() {
        println!("No symbol found matching '{}'", symbol);
        return Ok(());
    }

    println!("## Impact analysis for '{}'\n", symbol);

    // Direct matches
    println!("### Direct matches ({})", result.direct_matches.len());
    for n in result.direct_matches.iter().take(10) {
        let loc = n.line.map(|l| format!(":{}", l)).unwrap_or_default();
        let exp = if n.exported { " [exported]" } else { "" };
        println!("  [{}] {}{} — {}{}", n.node_type, n.name, exp, n.file_path, loc);
    }
    if result.direct_matches.len() > 10 {
        println!("  ... and {} more", result.direct_matches.len() - 10);
    }

    // Group impact chain by module (depth <= max_depth)
    let impacted: Vec<_> = result.impact_chain.iter()
        .filter(|i| i.depth <= max_depth)
        .collect();

    if impacted.is_empty() {
        println!("\nNo downstream impact.");
        return Ok(());
    }

    // Group by module
    let mut by_module: std::collections::BTreeMap<String, Vec<&crate::graph::ImpactNode>> = std::collections::BTreeMap::new();
    for i in &impacted {
        by_module.entry(i.node.module.clone()).or_default().push(*i);
    }

    println!("\n### Impact chain ({} nodes across {} modules, depth<={})",
        impacted.len(), by_module.len(), max_depth);

    // Sort modules by number of impacted nodes (descending)
    let mut modules: Vec<_> = by_module.iter().collect();
    modules.sort_by(|a, b| b.1.len().cmp(&a.1.len()));

    for (module, nodes) in modules {
        println!("\n  {} ({} nodes):", module, nodes.len());
        // Show top 5 most direct impacts in this module
        let mut sorted_nodes: Vec<_> = nodes.iter().collect();
        sorted_nodes.sort_by_key(|n| n.depth);
        for impact in sorted_nodes.iter().take(5) {
            let loc = impact.node.line.map(|l| format!(":{}", l)).unwrap_or_default();
            println!("    depth={} [{}] {} — {}{}",
                impact.depth, impact.node.node_type, impact.node.name,
                impact.node.file_path, loc);
        }
        if nodes.len() > 5 {
            println!("    ... and {} more", nodes.len() - 5);
        }
    }

    // Summary line at the end
    let total_files: std::collections::HashSet<_> = impacted.iter()
        .map(|i| i.node.file_path.as_str())
        .collect();
    println!("\n### Summary");
    println!("  Symbol: {}", symbol);
    println!("  Direct matches: {}", result.direct_matches.len());
    println!("  Impacted nodes: {}", impacted.len());
    println!("  Impacted files: {}", total_files.len());
    println!("  Modules affected: {}", by_module.len());

    Ok(())
}

fn cmd_risk(base: &str) -> Result<()> {
    let project_path = resolve_project_path(PathBuf::from("."))?;
    let temper = temper_dir(&project_path);
    let graph_path = temper.join("graph.json");

    if !graph_path.exists() {
        eprintln!("No graph found. Run `temper init` first.");
        return Ok(());
    }

    // Get changed files from git
    let output = std::process::Command::new("git")
        .args(["diff", "--name-only", base])
        .current_dir(&project_path)
        .output()?;

    let changed_files: Vec<String> = String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter(|l| !l.is_empty())
        .filter(|l| {
            let exts = [".java", ".py", ".ts", ".tsx", ".js", ".mjs", ".rs"];
            exts.iter().any(|ext| l.ends_with(ext))
        })
        .map(String::from)
        .collect();

    if changed_files.is_empty() {
        println!("No source file changes vs {}", base);
        return Ok(());
    }

    let graph = CodeGraph::load(&graph_path)?;
    println!("## Change risk analysis (vs {})\n", base);
    println!("### Changed files ({})", changed_files.len());
    for f in &changed_files {
        println!("  - {}", f);
    }

    // For each changed file, find its impact chain
    let mut all_impacted: std::collections::HashMap<String, u32> = std::collections::HashMap::new();
    let mut modules_affected: std::collections::HashSet<String> = std::collections::HashSet::new();

    for file in &changed_files {
        // Find the file node in graph
        let file_node = graph.nodes.iter().find(|n| {
            n.node_type == crate::graph::NodeType::File
                && (n.file_path == *file || n.file_path.ends_with(file))
        });

        if let Some(node) = file_node {
            modules_affected.insert(node.module.clone());
            // Find all reverse edges (who imports this file)
            for edge in &graph.edges {
                if edge.to == node.id && edge.edge_type == crate::graph::EdgeType::Imports {
                    if let Some(importer) = graph.nodes.iter().find(|n| n.id == edge.from) {
                        *all_impacted.entry(importer.file_path.clone()).or_insert(0) += 1;
                        modules_affected.insert(importer.module.clone());
                    }
                }
            }
        }
    }

    // Risk score: based on impacted files and modules
    let impacted_count = all_impacted.len();
    let modules_count = modules_affected.len();

    let risk_level = if impacted_count < 5 {
        "🟢 LOW"
    } else if impacted_count < 20 {
        "🟡 MEDIUM"
    } else if impacted_count < 50 {
        "🟠 HIGH"
    } else {
        "🔴 CRITICAL"
    };

    println!("\n### Impact");
    println!("  Files that import changed files: {}", impacted_count);
    println!("  Modules affected: {}", modules_count);
    println!("  Risk level: {}", risk_level);

    if impacted_count > 0 {
        println!("\n### Top impacted files");
        let mut sorted: Vec<_> = all_impacted.iter().collect();
        sorted.sort_by(|a, b| b.1.cmp(a.1));
        for (file, count) in sorted.iter().take(15) {
            println!("  [{} refs] {}", count, file);
        }
    }

    if !modules_affected.is_empty() {
        println!("\n### Modules affected");
        let mut mods: Vec<_> = modules_affected.iter().collect();
        mods.sort();
        for m in mods {
            println!("  - {}", m);
        }
    }

    Ok(())
}

fn cmd_overview() -> Result<()> {
    let project_path = resolve_project_path(PathBuf::from("."))?;
    let temper = temper_dir(&project_path);
    let graph_path = temper.join("graph.json");

    if !graph_path.exists() {
        eprintln!("No graph found. Run `temper init` first.");
        return Ok(());
    }

    let graph = CodeGraph::load(&graph_path)?;
    let stats = graph.stats();

    println!("# Project Overview\n");
    println!("Location: {}", project_path.display());
    println!("\n## Stats");
    println!("  Files:      {}", stats.files);
    println!("  Functions:  {}", stats.functions);
    println!("  Classes:    {}", stats.classes);
    println!("  Edges:      {}", stats.edges);

    // Modules
    let registry = ModuleRegistry::new(&temper, graph.files.clone());
    if let Ok(modules) = registry.list_modules() {
        if !modules.is_empty() {
            println!("\n## Modules ({})", modules.len());
            let mut sorted: Vec<_> = modules.iter().collect();
            sorted.sort_by(|a, b| {
                let ac = registry.file_count(a);
                let bc = registry.file_count(b);
                bc.cmp(&ac)
            });
            for m in sorted.iter().take(15) {
                let fc = registry.file_count(m);
                println!("  [{:>4} files] {} — {}", fc, m.name, m.description);
            }
            if sorted.len() > 15 {
                println!("  ... and {} more modules", sorted.len() - 15);
            }
        }
    }

    // Find hotspots: most-imported files (popular dependencies)
    let mut incoming_count: std::collections::HashMap<String, u32> = std::collections::HashMap::new();
    for edge in &graph.edges {
        if edge.edge_type == crate::graph::EdgeType::Imports {
            *incoming_count.entry(edge.to.clone()).or_insert(0) += 1;
        }
    }

    let mut hotspots: Vec<_> = incoming_count.iter()
        .filter_map(|(id, count)| {
            graph.nodes.iter()
                .find(|n| &n.id == id && n.node_type == crate::graph::NodeType::File)
                .map(|n| (n, *count))
        })
        .collect();
    hotspots.sort_by(|a, b| b.1.cmp(&a.1));

    if !hotspots.is_empty() {
        println!("\n## Dependency Hotspots (most imported files)");
        for (node, count) in hotspots.iter().take(10) {
            println!("  [{:>4} imports] {}", count, node.file_path);
        }
    }

    // Knowledge summary if available
    let db_path = temper.join("knowledge.db");
    if db_path.exists() {
        if let Ok(store) = crate::storage::LocalStorage::open(&db_path) {
            if let Ok(all) = crate::storage::KnowledgeStore::recall(
                &store,
                crate::storage::RecallQuery { include_stale: true, ..Default::default() },
            ) {
                let constraints = all.iter().filter(|k| k.entry_type == "constraint").count();
                let decisions = all.iter().filter(|k| k.entry_type == "decision").count();
                if constraints + decisions > 0 {
                    println!("\n## Knowledge");
                    println!("  Constraints: {}", constraints);
                    println!("  Decisions:   {}", decisions);
                }
            }
        }
    }

    println!("\n## Next Steps");
    println!("  temper impact <symbol>    — analyze impact chain for a class/function");
    println!("  temper risk               — assess current uncommitted changes");
    println!("  temper export --open      — interactive HTML visualization");

    Ok(())
}

fn cmd_diff(source: &str, target: &str) -> Result<()> {
    let project_path = resolve_project_path(PathBuf::from("."))?;

    let report = match crate::analysis::migration_progress(&project_path, source, target) {
        Some(r) => r,
        None => {
            eprintln!("Could not analyze migration (source or target not found)");
            return Ok(());
        }
    };

    let source_label = if source == "*" { "all sources" } else { source };
    println!("# Migration Progress: {} → {}\n", source_label, target);

    println!("## Summary");
    println!("  Source files:    {}", report.source_files);
    println!("  Target files:    {}", report.target_files);
    println!("  Migrated:        {} ({}%)", report.migrated, report.progress_pct);
    println!("  Remaining:       {}", report.remaining);

    let bar_width = 40;
    let filled = (report.progress_pct as usize * bar_width) / 100;
    let bar: String = std::iter::repeat('█').take(filled)
        .chain(std::iter::repeat('░').take(bar_width - filled))
        .collect();
    println!("\n  [{}] {}%", bar, report.progress_pct);

    if !report.top_directories.is_empty() {
        println!("\n## Top directories to migrate");
        for (dir, count) in report.top_directories.iter().take(20) {
            println!("  {:>5}  {}", count, dir);
        }
    }

    Ok(())
}

fn cmd_sync_check(source: &str, target: &str) -> Result<()> {
    let project_path = resolve_project_path(PathBuf::from("."))?;

    println!("# Sync Check: {} → {}\n", source, target);

    // Get recent commits on source dir
    let output = std::process::Command::new("git")
        .args([
            "log",
            "--name-only",
            "--pretty=format:COMMIT %H %s",
            "--since=1 month ago",
            "--",
            source,
        ])
        .current_dir(&project_path)
        .output()?;

    if !output.status.success() {
        eprintln!("git log failed. Are you in a git repo?");
        return Ok(());
    }

    let log_output = String::from_utf8_lossy(&output.stdout);

    // Parse: collect list of (commit_hash, file) pairs for files in source/
    let mut recent_changes: std::collections::HashMap<String, String> =
        std::collections::HashMap::new();
    let mut current_commit = String::new();

    for line in log_output.lines() {
        if let Some(rest) = line.strip_prefix("COMMIT ") {
            let parts: Vec<&str> = rest.splitn(2, ' ').collect();
            if !parts.is_empty() {
                current_commit = parts[0].to_string();
            }
        } else if line.starts_with(&format!("{}/", source)) {
            // Keep only the most recent commit per file
            recent_changes
                .entry(line.to_string())
                .or_insert_with(|| current_commit.clone());
        }
    }

    println!("## Files changed in {} recently: {}\n", source, recent_changes.len());

    // For each changed source file, check if target has the same file and when it was last touched
    let mut out_of_sync = Vec::new();
    let mut no_target = Vec::new();

    for (source_file, commit) in &recent_changes {
        let basename = std::path::Path::new(source_file)
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();

        // Find target file with same basename
        let find_output = std::process::Command::new("find")
            .args([target, "-name", &basename, "-type", "f"])
            .current_dir(&project_path)
            .output();

        let target_path: Option<String> = match find_output {
            Ok(out) if out.status.success() => {
                let s = String::from_utf8_lossy(&out.stdout);
                s.lines().next().map(String::from)
            }
            _ => None,
        };

        match target_path {
            None => no_target.push((source_file.clone(), commit.clone())),
            Some(tgt) => {
                // Check if target file was modified AFTER the source commit
                let tgt_log = std::process::Command::new("git")
                    .args(["log", "-1", "--pretty=format:%H", "--", &tgt])
                    .current_dir(&project_path)
                    .output();

                if let Ok(lo) = tgt_log {
                    let tgt_commit = String::from_utf8_lossy(&lo.stdout).trim().to_string();
                    // Simple heuristic: if target commit is same or ancestor, it's out of sync
                    let ancestor_check = std::process::Command::new("git")
                        .args(["merge-base", "--is-ancestor", commit, &tgt_commit])
                        .current_dir(&project_path)
                        .output();

                    let is_synced = ancestor_check
                        .map(|o| o.status.success())
                        .unwrap_or(false);

                    if !is_synced {
                        out_of_sync.push((source_file.clone(), tgt, commit.clone()));
                    }
                }
            }
        }
    }

    if !out_of_sync.is_empty() {
        println!("## ⚠️  Out of sync ({}) — source was modified, target may be stale", out_of_sync.len());
        for (src, tgt, commit) in out_of_sync.iter().take(20) {
            println!("  {} (commit {})", src, &commit[..7.min(commit.len())]);
            println!("    → {}", tgt);
        }
        if out_of_sync.len() > 20 {
            println!("  ... and {} more", out_of_sync.len() - 20);
        }
    }

    if !no_target.is_empty() {
        println!("\n## 🆕 New in source, not migrated yet ({})", no_target.len());
        for (src, commit) in no_target.iter().take(20) {
            println!("  {} (commit {})", src, &commit[..7.min(commit.len())]);
        }
        if no_target.len() > 20 {
            println!("  ... and {} more", no_target.len() - 20);
        }
    }

    if out_of_sync.is_empty() && no_target.is_empty() {
        println!("✅ All recent source changes are synced or migrated.");
    }

    Ok(())
}

fn cmd_call_tree(symbol: &str, direction: &str, max_depth: u32) -> Result<()> {
    let project_path = resolve_project_path(PathBuf::from("."))?;
    let temper = temper_dir(&project_path);
    let graph_path = temper.join("graph.json");

    if !graph_path.exists() {
        eprintln!("No graph found. Run `temper init` first.");
        return Ok(());
    }

    let graph = CodeGraph::load(&graph_path)?;

    // Find the symbol node
    let matches: Vec<&crate::graph::CodeNode> = graph.nodes.iter()
        .filter(|n| n.name == symbol || n.id.ends_with(&format!("::{}", symbol)))
        .collect();

    if matches.is_empty() {
        println!("No symbol found: {}", symbol);
        return Ok(());
    }

    println!("# Call tree for '{}'\n", symbol);

    for m in matches.iter().take(5) {
        println!("## {} ({}:{}, {})",
            m.name,
            m.file_path,
            m.line.unwrap_or(0),
            m.node_type
        );

        // Build adjacency: "calls" edges
        let mut calls_out: std::collections::HashMap<String, Vec<String>> = std::collections::HashMap::new();
        let mut calls_in: std::collections::HashMap<String, Vec<String>> = std::collections::HashMap::new();
        for e in &graph.edges {
            if e.edge_type == crate::graph::EdgeType::Calls {
                calls_out.entry(e.from.clone()).or_default().push(e.to.clone());
                calls_in.entry(e.to.clone()).or_default().push(e.from.clone());
            }
        }

        if direction == "down" || direction == "both" {
            println!("\n  Calls (downstream):");
            print_tree(&graph, &m.id, &calls_out, max_depth, 0, "  ");
        }
        if direction == "up" || direction == "both" {
            println!("\n  Called by (upstream):");
            print_tree(&graph, &m.id, &calls_in, max_depth, 0, "  ");
        }
        println!();
    }

    if matches.len() > 5 {
        println!("... and {} more matches", matches.len() - 5);
    }

    Ok(())
}

fn print_tree(
    graph: &CodeGraph,
    node_id: &str,
    adj: &std::collections::HashMap<String, Vec<String>>,
    max_depth: u32,
    depth: u32,
    prefix: &str,
) {
    if depth >= max_depth {
        return;
    }
    if let Some(children) = adj.get(node_id) {
        for (i, child_id) in children.iter().take(10).enumerate() {
            let is_last = i == children.len() - 1 || i == 9;
            let connector = if is_last { "└─" } else { "├─" };

            if let Some(node) = graph.nodes.iter().find(|n| &n.id == child_id) {
                let file_short = node.file_path.rsplit('/').next().unwrap_or(&node.file_path);
                println!(
                    "{}{} {} — {}{}",
                    prefix,
                    connector,
                    node.name,
                    file_short,
                    node.line.map(|l| format!(":{}", l)).unwrap_or_default()
                );
            } else {
                println!("{}{} {}", prefix, connector, child_id);
            }

            let new_prefix = format!("{}{}", prefix, if is_last { "   " } else { "│  " });
            print_tree(graph, child_id, adj, max_depth, depth + 1, &new_prefix);
        }
        if children.len() > 10 {
            println!("{}   ... and {} more", prefix, children.len() - 10);
        }
    }
}

fn cmd_dead_code() -> Result<()> {
    let project_path = resolve_project_path(PathBuf::from("."))?;
    let temper = temper_dir(&project_path);
    let graph_path = temper.join("graph.json");

    if !graph_path.exists() {
        eprintln!("No graph found. Run `temper init` first.");
        return Ok(());
    }

    let graph = CodeGraph::load(&graph_path)?;
    let registry = ModuleRegistry::new(&temper, graph.files.clone());

    // Only analyze files inside defined modules
    let report = crate::analysis::dead_code(&graph, Some(&registry));

    println!("# Dead code analysis (scoped to defined modules)\n");
    println!("Files in modules:     {}", report.total_files);
    println!("Files with imports:   {}", report.imported_files);
    println!("Unreferenced:         {}\n", report.dead_files.len());

    if report.dead_files.is_empty() {
        println!("✅ No dead files found in defined modules.");
        return Ok(());
    }

    println!("## Unreferenced files by directory");
    for (dir, count) in report.by_directory.iter().take(20) {
        println!("  {:>5}  {}", count, dir);
    }

    Ok(())
}

fn cmd_boundary() -> Result<()> {
    let project_path = resolve_project_path(PathBuf::from("."))?;
    let temper = temper_dir(&project_path);
    let graph = CodeGraph::load(&temper.join("graph.json"))?;
    let registry = ModuleRegistry::new(&temper, graph.files.clone());

    let violations = crate::analysis::boundary_violations(&graph, &registry);

    println!("# Module Boundary Violations\n");
    println!("Cross-module imports: {}\n", violations.len());

    if violations.is_empty() {
        println!("✅ No cross-module imports detected.");
        return Ok(());
    }

    println!("## Top violations (by import count)\n");
    println!("{:<40} {:<40} {:>6}", "From", "To", "Count");
    println!("{}", "-".repeat(90));

    for v in violations.iter().take(30) {
        println!("{:<40} {:<40} {:>6}", v.from_module, v.to_module, v.count);
    }

    if violations.len() > 30 {
        println!("\n... and {} more violation pairs", violations.len() - 30);
    }

    Ok(())
}

fn cmd_cohesion() -> Result<()> {
    let project_path = resolve_project_path(PathBuf::from("."))?;
    let temper = temper_dir(&project_path);
    let graph = CodeGraph::load(&temper.join("graph.json"))?;
    let registry = ModuleRegistry::new(&temper, graph.files.clone());

    let cohesion = crate::analysis::module_cohesion(&graph, &registry);

    println!("# Module Cohesion\n");
    println!("Ratio = internal imports / (internal + external)");
    println!("Higher = more self-contained, lower = more coupled\n");

    println!("{:<35} {:>5} {:>10} {:>10} {:>8}",
        "Module", "Files", "Internal", "External", "Ratio");
    println!("{}", "-".repeat(75));

    for c in &cohesion {
        let bar_width = 20;
        let filled = (c.cohesion_ratio * bar_width as f32) as usize;
        let bar: String = std::iter::repeat('█').take(filled)
            .chain(std::iter::repeat('░').take(bar_width - filled))
            .collect();

        println!("{:<35} {:>5} {:>10} {:>10} {:>7.1}% {}",
            c.module, c.file_count, c.internal_imports, c.external_imports,
            c.cohesion_ratio * 100.0, bar);
    }

    Ok(())
}

fn cmd_stats() -> Result<()> {
    let project_path = resolve_project_path(PathBuf::from("."))?;
    let temper = temper_dir(&project_path);
    let stats_path = temper.join("stats.json");

    if !stats_path.exists() {
        eprintln!("No usage stats yet. Stats are recorded when Claude Code calls Temper tools.");
        return Ok(());
    }

    let content = std::fs::read_to_string(&stats_path)?;
    let data: serde_json::Value = serde_json::from_str(&content)?;

    // Header
    println!("Temper Usage Stats");
    println!("{}", "=".repeat(50));

    // Last active
    if let Some(last) = data.get("last_active").and_then(|v| v.as_str()) {
        println!("Last active: {}", last);
    }

    // Session stats
    let session_total = data.get("session_total").and_then(|v| v.as_u64()).unwrap_or(0);
    let uptime = data.get("session_uptime_secs").and_then(|v| v.as_u64()).unwrap_or(0);
    println!("\nLast session: {} calls, {}s uptime", session_total, uptime);

    // Per-tool breakdown
    if let Some(calls) = data.get("session_tool_calls").and_then(|v| v.as_object()) {
        if !calls.is_empty() {
            println!("\n{:<25} {:>6}", "Tool", "Calls");
            println!("{}", "-".repeat(32));
            let mut sorted: Vec<_> = calls.iter().collect();
            sorted.sort_by(|a, b| b.1.as_u64().cmp(&a.1.as_u64()));
            for (tool, count) in &sorted {
                println!("{:<25} {:>6}", tool, count);
            }
        }
    }

    // Knowledge stats
    let db_path = temper.join("knowledge.db");
    if db_path.exists() {
        let store = crate::storage::LocalStorage::open(&db_path)?;
        let all = crate::storage::KnowledgeStore::recall(
            &store,
            crate::storage::RecallQuery { include_stale: true, ..Default::default() },
        )?;
        let active = all.iter().filter(|k| k.status == "active").count();
        let stale = all.iter().filter(|k| k.status == "stale").count();

        println!("\nKnowledge: {} active, {} stale", active, stale);
    }

    Ok(())
}

fn cmd_upgrade() -> Result<()> {
    eprintln!("Temper upgrade — update binary + rescan project\n");

    // Step 1: Update npm package
    eprintln!("Step 1: Updating @aion0/temper...");
    let npm_result = std::process::Command::new("npm")
        .args(["update", "-g", "@aion0/temper"])
        .status();

    match npm_result {
        Ok(status) if status.success() => eprintln!("  npm package updated."),
        Ok(status) => eprintln!("  npm update exited with: {} (may need sudo)", status),
        Err(_) => eprintln!("  npm not found, skipping package update."),
    }

    // Step 2: Check if we're in a temper-initialized project
    let project_path = resolve_project_path(PathBuf::from("."))?;
    let temper = temper_dir(&project_path);

    if !temper.exists() {
        eprintln!("\nNo .temper/ found. Run `temper init` first.");
        return Ok(());
    }

    // Step 3: Load old graph stats for comparison
    let old_stats = {
        let graph_path = temper.join("graph.json");
        if graph_path.exists() {
            CodeGraph::load(&graph_path).ok().map(|g| g.stats())
        } else {
            None
        }
    };

    // Step 4: Rescan with new parsers (keeps modules + knowledge intact)
    eprintln!("\nStep 2: Rescanning project with updated parsers...");
    let scanner = Scanner::new(&project_path);
    let graph = scanner.full_scan()?;
    let stats = graph.stats();

    graph.save(&temper.join("graph.json"))?;

    let mut meta = crate::graph::Meta::new(&project_path);
    meta.update_after_scan(&scanner, &graph);
    meta.save(&temper.join("meta.json"))?;

    eprintln!(
        "  Scan complete: {} files, {} functions, {} classes, {} edges",
        stats.files, stats.functions, stats.classes, stats.edges
    );

    // Step 5: Compare with old stats and show changes
    if let Some(old) = old_stats {
        let df = stats.files as i64 - old.files as i64;
        let dfn = stats.functions as i64 - old.functions as i64;
        let dc = stats.classes as i64 - old.classes as i64;
        let de = stats.edges as i64 - old.edges as i64;

        if df != 0 || dfn != 0 || dc != 0 {
            eprintln!("\n  Changes detected:");
            if df > 0 {
                eprintln!("    +{} new files (new language support may have picked up .py/.ts/.js files)", df);
            } else if df < 0 {
                eprintln!("    {} files removed", df);
            }
            if dfn != 0 {
                eprintln!("    {:+} functions", dfn);
            }
            if dc != 0 {
                eprintln!("    {:+} classes", dc);
            }
            if de != 0 {
                eprintln!("    {:+} edges", de);
            }
        } else {
            eprintln!("\n  No changes — graph is up to date.");
        }
    }

    // Step 6: Check for stale knowledge
    let db_path = temper.join("knowledge.db");
    if db_path.exists() {
        let store = crate::storage::LocalStorage::open(&db_path)?;

        // Mark stale knowledge for changed files
        let changed = std::process::Command::new("git")
            .args(["diff", "--name-only", "HEAD"])
            .current_dir(&project_path)
            .output();

        if let Ok(output) = changed {
            let changed_files: Vec<String> = String::from_utf8_lossy(&output.stdout)
                .lines()
                .filter(|l| !l.is_empty())
                .map(String::from)
                .collect();

            if !changed_files.is_empty() {
                let mut stale_count = 0;
                for f in &changed_files {
                    stale_count += crate::storage::KnowledgeStore::mark_stale(&store, f, "upgrade")?;
                }
                if stale_count > 0 {
                    eprintln!("\n  ⚠️  {} knowledge entries marked stale (anchored files changed)", stale_count);
                    eprintln!("     Run `temper knowledge` to review, use `recall` to validate.");
                }
            }
        }
    }

    // Step 7: Show what's preserved
    let db_path = temper.join("knowledge.db");
    if db_path.exists() {
        let store = crate::storage::LocalStorage::open(&db_path)?;
        let knowledge = crate::storage::KnowledgeStore::recall(
            &store,
            crate::storage::RecallQuery { include_stale: true, ..Default::default() },
        )?;
        eprintln!("  Knowledge preserved: {} entries", knowledge.len());
    }

    let modules_dir = temper.join("modules");
    if modules_dir.exists() {
        let count = std::fs::read_dir(&modules_dir)?
            .filter(|e| {
                e.as_ref().ok().map(|e| {
                    let name = e.file_name().to_string_lossy().to_string();
                    !name.starts_with('_') && name.ends_with(".yaml")
                }).unwrap_or(false)
            })
            .count();
        eprintln!("  Modules preserved: {}", count);
    }

    eprintln!("\nUpgrade complete. New language support is now active.");
    eprintln!("Supported: Java, Python, TypeScript, JavaScript");
    Ok(())
}
