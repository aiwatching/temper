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
        /// Only check constraints in files currently staged in git
        #[arg(long)]
        staged: bool,
    },

    /// List or author TEMPER-CONSTRAINT comments
    #[command(subcommand)]
    Constraint(ConstraintAction),

    /// Manage git pre-commit hook
    Hook {
        #[command(subcommand)]
        action: HookAction,
    },

    /// Show configuration
    Config,
}

#[derive(Subcommand)]
pub enum ConstraintAction {
    /// List every TEMPER-CONSTRAINT found in the project
    List {
        #[arg(default_value = ".")]
        path: PathBuf,
    },
    /// Draft a new TEMPER-CONSTRAINT for a target file using Claude
    Add {
        /// Source file the constraint should guard
        #[arg(long)]
        target: PathBuf,
        /// One-line incident or rationale (e.g. "INC-1247: stale Entity cache")
        #[arg(long)]
        incident: String,
        /// Optional longer description; can also be passed via stdin
        #[arg(long)]
        detail: Option<String>,
        /// After drafting, insert above the first class declaration in-place
        #[arg(long)]
        apply: bool,
        /// Model to ask. Uses `claude -p` under the hood.
        #[arg(long, default_value = "sonnet")]
        model: String,
    },
}

#[derive(Subcommand)]
pub enum HookAction {
    /// Install `.git/hooks/pre-commit` that runs `temper check --staged`
    Install {
        #[arg(default_value = ".")]
        path: PathBuf,
    },
    /// Remove the temper pre-commit hook
    Uninstall {
        #[arg(default_value = ".")]
        path: PathBuf,
    },
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
        Command::Check { path, format, staged } => cmd_check(path, format, staged),
        Command::Constraint(action) => cmd_constraint(action),
        Command::Hook { action } => cmd_hook(action),
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

fn cmd_check(path: PathBuf, format: CheckFormat, staged: bool) -> Result<()> {
    let project_path = resolve_project_path(path)?;
    let filter = if staged {
        let files = staged_files(&project_path)?;
        if files.is_empty() {
            if matches!(format, CheckFormat::Text) {
                eprintln!("No staged files. Nothing to check.");
            }
            return Ok(());
        }
        Some(files)
    } else {
        None
    };

    let report = crate::constraint::check::run_filtered(&project_path, filter.as_deref())?;

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

fn staged_files(project_path: &Path) -> Result<Vec<String>> {
    let out = std::process::Command::new("git")
        .args(["diff", "--cached", "--name-only", "--diff-filter=ACMR"])
        .current_dir(project_path)
        .output()?;
    if !out.status.success() {
        return Ok(Vec::new());
    }
    Ok(String::from_utf8_lossy(&out.stdout)
        .lines()
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string())
        .collect())
}

fn cmd_constraint(action: ConstraintAction) -> Result<()> {
    match action {
        ConstraintAction::List { path } => cmd_constraint_list(path),
        ConstraintAction::Add { target, incident, detail, apply, model } => {
            cmd_constraint_add(target, incident, detail, apply, model)
        }
    }
}

fn cmd_constraint_list(path: PathBuf) -> Result<()> {
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

fn cmd_constraint_add(
    target: PathBuf,
    incident: String,
    detail: Option<String>,
    apply: bool,
    model: String,
) -> Result<()> {
    let target_abs = target
        .canonicalize()
        .with_context(|| format!("Target file not found: {}", target.display()))?;

    let file_content = std::fs::read_to_string(&target_abs)
        .with_context(|| format!("Failed to read {}", target_abs.display()))?;

    if file_content.contains("TEMPER-CONSTRAINT") {
        anyhow::bail!(
            "{} already contains a TEMPER-CONSTRAINT. Remove the existing one first or edit it in place.",
            target_abs.display()
        );
    }

    let today = chrono::Utc::now().format("%Y-%m-%d").to_string();
    let prompt = build_draft_prompt(&target_abs, &file_content, &incident, detail.as_deref(), &today);

    eprintln!("Asking claude -p ({}) to draft constraint for {}...", model, target_abs.display());
    let claude_out = std::process::Command::new("claude")
        .args(["-p", &prompt, "--model", &model])
        .output()
        .context("Failed to run `claude -p` — is Claude Code installed and on PATH?")?;

    if !claude_out.status.success() {
        let err = String::from_utf8_lossy(&claude_out.stderr);
        anyhow::bail!("claude -p failed: {}", err);
    }
    let raw = String::from_utf8_lossy(&claude_out.stdout).to_string();
    let draft = extract_comment_block(&raw).ok_or_else(|| {
        anyhow::anyhow!(
            "Claude did not return a /** ... */ block. Raw output:\n{}",
            raw
        )
    })?;

    println!("{}", draft);

    if apply {
        let updated = insert_constraint(&file_content, &draft)?;
        std::fs::write(&target_abs, updated)?;
        eprintln!("\nInserted into {}", target_abs.display());
    } else {
        eprintln!("\n(draft only — re-run with --apply to insert into the file)");
    }
    Ok(())
}

fn build_draft_prompt(
    target: &Path,
    body: &str,
    incident: &str,
    detail: Option<&str>,
    today: &str,
) -> String {
    let detail_block = detail.map(|d| format!("\n**更多背景**：{}\n", d)).unwrap_or_default();
    format!(r#"你是代码库维护者，要给一个关键文件加上 TEMPER-CONSTRAINT 注释块以防止某类事故重演。

**目标文件**：{target}

**当前文件内容**：
```
{body}
```

**事故 / 约束原因**：{incident}{detail_block}

**你的任务**：写一段 Javadoc / 块注释，结构包含：
1. **What** — 简洁声明禁止什么
2. **Why** — 事故原因、数据影响
3. **Rule** — 具体禁止的代码模式（越精确越好）和 anti-bypass 语句（比如 "any field holding X instances"），以及允许的模式
4. **Escape** — 如果真的需要破例应该怎么办
5. 结尾包含 `Last-Verified: {today}` 字段

注释里的类名/方法名必须和文件里的实际代码匹配。

**只输出最终的 /** ... */ 注释块本身，不要任何解释或多余文字。**
"#, target = target.display(), body = body, incident = incident, detail_block = detail_block, today = today)
}

fn extract_comment_block(text: &str) -> Option<String> {
    let start = text.find("/**")?;
    let after = &text[start..];
    let end_rel = after.find("*/")?;
    Some(after[..end_rel + 2].to_string())
}

/// Insert `constraint_block` immediately above the first class/interface/trait/
/// struct declaration in `file_content`. Returns the new file text.
fn insert_constraint(file_content: &str, constraint_block: &str) -> Result<String> {
    let lines: Vec<&str> = file_content.lines().collect();
    let mut insert_at: Option<usize> = None;
    for (i, l) in lines.iter().enumerate() {
        let t = l.trim_start();
        if t.starts_with("public class ")
            || t.starts_with("class ")
            || t.starts_with("public interface ")
            || t.starts_with("interface ")
            || t.starts_with("public enum ")
            || t.starts_with("public abstract ")
            || t.starts_with("pub struct ")
            || t.starts_with("struct ")
            || t.starts_with("pub trait ")
            || t.starts_with("trait ")
        {
            insert_at = Some(i);
            break;
        }
    }
    let insert_at = insert_at.ok_or_else(|| {
        anyhow::anyhow!(
            "Could not find a class/interface/struct declaration to anchor the constraint. \
             Paste it manually above the target declaration."
        )
    })?;

    let mut out = String::new();
    for (i, line) in lines.iter().enumerate() {
        if i == insert_at {
            out.push_str(constraint_block);
            if !constraint_block.ends_with('\n') {
                out.push('\n');
            }
        }
        out.push_str(line);
        out.push('\n');
    }
    if !file_content.ends_with('\n') {
        // preserve original non-terminating behavior
        out.pop();
    }
    Ok(out)
}

fn cmd_config() -> Result<()> {
    let cfg = GlobalConfig::load_or_default()?;
    println!("{}", serde_yaml::to_string(&cfg)?);
    Ok(())
}

// --- Hook management ---

const HOOK_MARKER: &str = "# temper-managed pre-commit hook";
const HOOK_BODY: &str = r#"#!/bin/sh
# temper-managed pre-commit hook
# Blocks commits that leave any TEMPER-CONSTRAINT in a stale / dangling /
# contradicted / banned-token state. Run `temper check --staged` manually
# to reproduce. To bypass once, use `git commit --no-verify`.
exec temper check --staged
"#;

fn cmd_hook(action: HookAction) -> Result<()> {
    match action {
        HookAction::Install { path } => {
            let project_path = resolve_project_path(path)?;
            install_pre_commit_hook(&project_path)
        }
        HookAction::Uninstall { path } => {
            let project_path = resolve_project_path(path)?;
            uninstall_pre_commit_hook(&project_path)
        }
    }
}

fn pre_commit_path(project_path: &Path) -> Result<PathBuf> {
    let out = std::process::Command::new("git")
        .args(["rev-parse", "--git-path", "hooks/pre-commit"])
        .current_dir(project_path)
        .output()
        .context("Failed to run `git rev-parse` — is this a git repo?")?;
    if !out.status.success() {
        anyhow::bail!("Not inside a git repository");
    }
    let rel = String::from_utf8_lossy(&out.stdout).trim().to_string();
    let path = if std::path::Path::new(&rel).is_absolute() {
        PathBuf::from(rel)
    } else {
        project_path.join(rel)
    };
    Ok(path)
}

fn install_pre_commit_hook(project_path: &Path) -> Result<()> {
    let hook = pre_commit_path(project_path)?;
    if let Some(parent) = hook.parent() {
        std::fs::create_dir_all(parent)?;
    }

    if hook.exists() {
        let existing = std::fs::read_to_string(&hook).unwrap_or_default();
        if existing.contains(HOOK_MARKER) {
            eprintln!("Temper hook already installed at {}", hook.display());
            return Ok(());
        }
        anyhow::bail!(
            "Refusing to overwrite existing pre-commit hook at {}. \
             Inspect it and remove it manually if you want temper's version.",
            hook.display()
        );
    }

    std::fs::write(&hook, HOOK_BODY)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = std::fs::metadata(&hook)?.permissions();
        perms.set_mode(0o755);
        std::fs::set_permissions(&hook, perms)?;
    }
    println!("Installed pre-commit hook at {}", hook.display());
    println!("Runs `temper check --staged` before every commit.");
    Ok(())
}

fn uninstall_pre_commit_hook(project_path: &Path) -> Result<()> {
    let hook = pre_commit_path(project_path)?;
    if !hook.exists() {
        println!("No pre-commit hook to remove.");
        return Ok(());
    }
    let existing = std::fs::read_to_string(&hook).unwrap_or_default();
    if !existing.contains(HOOK_MARKER) {
        anyhow::bail!(
            "Pre-commit hook at {} is not managed by temper. \
             Remove it manually if you really want to.",
            hook.display()
        );
    }
    std::fs::remove_file(&hook)?;
    println!("Removed temper pre-commit hook.");
    Ok(())
}
