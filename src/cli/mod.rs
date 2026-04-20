use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use std::path::{Path, PathBuf};

use crate::config::GlobalConfig;
use crate::graph::CodeGraph;
use crate::modules::{ModuleDef, ModuleRegistry};
use crate::parser::Scanner;

#[derive(Parser)]
#[command(name = "temper", version, about = "Constraint lifecycle platform for your code")]
pub struct Cli {
    #[command(subcommand)]
    pub command: Command,
}

#[derive(Subcommand)]
pub enum Command {
    /// Initialize Temper for the current project (scan + suggest modules)
    Init {
        #[arg(default_value = ".")]
        path: PathBuf,
    },

    /// Scan project code structure
    Scan {
        #[arg(default_value = ".")]
        path: PathBuf,
        #[arg(long)]
        force: bool,
    },

    /// List or inspect modules
    Modules {
        name: Option<String>,
    },

    /// Show graph statistics
    Graph,

    /// Export HTML visualization
    Export {
        #[arg(long)]
        output: Option<PathBuf>,
        #[arg(long)]
        open: bool,
    },

    /// Check TEMPER-CONSTRAINT comments for staleness and drift
    Check {
        #[arg(default_value = ".")]
        path: PathBuf,
        #[arg(long, value_enum, default_value = "text")]
        format: CheckFormat,
    },

    /// List all TEMPER-CONSTRAINT comments in the project
    Constraints {
        #[arg(default_value = ".")]
        path: PathBuf,
    },

    /// Show configuration
    Config,
}

#[derive(clap::ValueEnum, Clone, Debug)]
pub enum CheckFormat {
    Text,
    Json,
}

pub fn run(cli: Cli) -> Result<()> {
    match cli.command {
        Command::Init { path } => cmd_init(path),
        Command::Scan { path, force } => cmd_scan(path, force),
        Command::Modules { name } => cmd_modules(name),
        Command::Graph => cmd_graph(),
        Command::Export { output, open } => cmd_export(output, open),
        Command::Check { path, format } => cmd_check(path, format),
        Command::Constraints { path } => cmd_constraints(path),
        Command::Config => cmd_config(),
    }
}

fn resolve_project_path(path: PathBuf) -> Result<PathBuf> {
    let abs = if path.is_absolute() {
        path
    } else {
        std::env::current_dir()?.join(&path)
    };
    abs.canonicalize().context(format!("Failed to resolve path: {}", abs.display()))
}

fn temper_dir(project_path: &Path) -> PathBuf {
    project_path.join(".temper")
}

fn cmd_init(path: PathBuf) -> Result<()> {
    let project_path = resolve_project_path(path)?;
    let temper = temper_dir(&project_path);

    std::fs::create_dir_all(temper.join("modules"))?;
    std::fs::create_dir_all(temper.join("constraints"))?;

    eprintln!("Initializing Temper for: {}", project_path.display());

    GlobalConfig::ensure_default()?;
    crate::config::register_project(&project_path)?;

    eprintln!("Scanning project...");
    let scanner = Scanner::new(&project_path);
    let graph = scanner.full_scan()?;

    let stats = graph.stats();
    eprintln!(
        "Scan complete: {} files, {} functions, {} classes, {} edges",
        stats.files, stats.functions, stats.classes, stats.edges
    );

    graph.save(&temper.join("graph.json"))?;

    let mut meta = crate::graph::Meta::new(&project_path);
    meta.update_after_scan(&scanner, &graph);
    meta.save(&temper.join("meta.json"))?;

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

        eprintln!("\nAccept all suggestions? [Y/n] ");
        let mut input = String::new();
        std::io::stdin().read_line(&mut input)?;
        let input = input.trim().to_lowercase();

        let to_define: Vec<&crate::modules::suggest::ModuleSuggestion> =
            if input.is_empty() || input == "y" || input == "yes" {
                suggestions.iter().collect()
            } else {
                Vec::new()
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
    eprintln!("Next steps:");
    eprintln!("  temper check      — verify TEMPER-CONSTRAINT comments");
    eprintln!("  temper export --open  — HTML visualization");
    Ok(())
}

fn cmd_scan(path: PathBuf, force: bool) -> Result<()> {
    let project_path = resolve_project_path(path)?;
    let temper = temper_dir(&project_path);
    let graph_path = temper.join("graph.json");
    let meta_path = temper.join("meta.json");

    let scanner = Scanner::new(&project_path);

    let graph = if force || !graph_path.exists() {
        eprintln!("No existing graph, full scan...");
        scanner.full_scan()?
    } else {
        let existing = CodeGraph::load(&graph_path)?;
        let last_commit = meta_load_commit(&meta_path);
        let changed = scanner.changed_files_since(last_commit.as_deref());
        if changed.is_empty() {
            eprintln!("No changes detected.");
            return Ok(());
        }
        eprintln!("Incremental update: {} files changed", changed.len());
        scanner.incremental_update(existing, &changed)?
    };

    let stats = graph.stats();
    eprintln!(
        "Done: {} files, {} functions, {} classes, {} edges",
        stats.files, stats.functions, stats.classes, stats.edges
    );

    std::fs::create_dir_all(&temper)?;
    graph.save(&graph_path)?;

    let mut meta = crate::graph::Meta::load(&meta_path).unwrap_or_else(|_| crate::graph::Meta::new(&project_path));
    meta.update_after_scan(&scanner, &graph);
    meta.save(&meta_path)?;

    Ok(())
}

fn meta_load_commit(path: &Path) -> Option<String> {
    crate::graph::Meta::load(path).ok().and_then(|m| m.last_scan_commit)
}

fn cmd_modules(name: Option<String>) -> Result<()> {
    let project_path = resolve_project_path(PathBuf::from("."))?;
    let temper = temper_dir(&project_path);
    let graph_path = temper.join("graph.json");

    if !graph_path.exists() {
        eprintln!("No graph found. Run `temper init` first.");
        return Ok(());
    }

    let graph = CodeGraph::load(&graph_path)?;
    let registry = ModuleRegistry::new(&temper, graph.files.clone());

    let modules = registry.list_modules()?;
    if modules.is_empty() {
        eprintln!("No modules defined. Run `temper init` first.");
        return Ok(());
    }

    match name {
        Some(n) => {
            match modules.iter().find(|m| m.name == n) {
                Some(m) => {
                    println!("{}", m.name);
                    println!("  description: {}", m.description);
                    println!("  paths: {:?}", m.paths);
                    println!("  files: {}", registry.file_count(m));
                }
                None => eprintln!("Module '{}' not found", n),
            }
        }
        None => {
            for m in &modules {
                println!("  [{:>4} files] {} — {}", registry.file_count(m), m.name, m.description);
            }
        }
    }

    Ok(())
}

fn cmd_graph() -> Result<()> {
    let project_path = resolve_project_path(PathBuf::from("."))?;
    let temper = temper_dir(&project_path);
    let graph_path = temper.join("graph.json");

    if !graph_path.exists() {
        eprintln!("No graph found. Run `temper init` first.");
        return Ok(());
    }

    let graph = CodeGraph::load(&graph_path)?;
    let stats = graph.stats();
    println!("Files:      {}", stats.files);
    println!("Functions:  {}", stats.functions);
    println!("Classes:    {}", stats.classes);
    println!("Variables:  {}", stats.variables);
    println!("Edges:      {}", stats.edges);
    println!("  imports:  {}", stats.import_edges);
    println!("  calls:    {}", stats.call_edges);
    println!("  exports:  {}", stats.export_edges);
    Ok(())
}

fn cmd_export(output: Option<PathBuf>, open: bool) -> Result<()> {
    let project_path = resolve_project_path(PathBuf::from("."))?;
    let temper = temper_dir(&project_path);
    let out_dir = output.unwrap_or_else(|| temper.join("export"));
    crate::export::export_html(&project_path, &out_dir)?;

    if open {
        let _ = std::process::Command::new("open").arg(out_dir.join("index.html")).spawn();
    }
    Ok(())
}

fn cmd_check(path: PathBuf, format: CheckFormat) -> Result<()> {
    let project_path = resolve_project_path(path)?;
    let report = crate::constraint::check::run(&project_path)?;

    match format {
        CheckFormat::Text => report.print_text(),
        CheckFormat::Json => {
            println!("{}", serde_json::to_string_pretty(&report)?);
        }
    }

    if report.has_problems() {
        std::process::exit(1);
    }
    Ok(())
}

fn cmd_constraints(path: PathBuf) -> Result<()> {
    let project_path = resolve_project_path(path)?;
    let constraints = crate::constraint::scanner::scan_project(&project_path)?;
    if constraints.is_empty() {
        println!("No TEMPER-CONSTRAINT comments found.");
        return Ok(());
    }
    for c in &constraints {
        println!("{}:{} — {}", c.file_path, c.line, c.title);
    }
    println!("\n{} constraint(s) total.", constraints.len());
    Ok(())
}

fn cmd_config() -> Result<()> {
    let cfg = GlobalConfig::load_or_default()?;
    println!("{}", serde_yaml::to_string(&cfg)?);
    Ok(())
}
