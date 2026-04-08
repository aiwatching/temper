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

    eprintln!("\nTemper initialized at {}", temper.display());
    eprintln!("Use `temper modules` to list modules, `temper search` to search code.");
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
