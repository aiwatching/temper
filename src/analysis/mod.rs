//! Code analysis primitives shared between CLI and HTML export.

use crate::graph::{CodeGraph, EdgeType, NodeType};
use crate::modules::ModuleRegistry;
use serde::Serialize;
use std::collections::HashMap;

#[derive(Debug, Serialize)]
pub struct MigrationProgress {
    pub source_dir: String,
    pub target_dir: String,
    pub source_files: usize,
    pub target_files: usize,
    pub migrated: usize,
    pub remaining: usize,
    pub progress_pct: u32,
    pub top_directories: Vec<(String, usize)>,
}

#[derive(Debug, Serialize)]
pub struct DeadCodeReport {
    pub total_files: usize,
    pub imported_files: usize,
    pub dead_files: Vec<String>,
    pub by_directory: Vec<(String, usize)>,
}

#[derive(Debug, Serialize)]
pub struct BoundaryViolation {
    pub from_module: String,
    pub to_module: String,
    pub count: u32,
    pub sample_files: Vec<String>, // up to 5
}

#[derive(Debug, Serialize)]
pub struct ModuleCohesion {
    pub module: String,
    pub file_count: usize,
    pub internal_imports: u32,
    pub external_imports: u32,
    /// internal / (internal + external)
    pub cohesion_ratio: f32,
    pub top_external: Vec<(String, u32)>, // which external modules it depends on
}

/// Compute migration progress by comparing two directories.
/// If source == "*", compare all dirs except target/excludes → target.
pub fn migration_progress(
    project_path: &std::path::Path,
    source: &str,
    target: &str,
) -> Option<MigrationProgress> {
    let target_dir = project_path.join(target);
    if !target_dir.exists() {
        return None;
    }

    let target_files = collect_source_files(&target_dir);

    // Gather source files
    let source_files: Vec<String> = if source == "*" {
        // Scan all top-level dirs except target and standard excludes
        let mut all = Vec::new();
        let excludes = [target, ".git", "node_modules", "target", "build", "dist", ".temper", ".claude", ".forge", ".accord"];
        if let Ok(entries) = std::fs::read_dir(project_path) {
            for entry in entries.flatten() {
                let name = entry.file_name().to_string_lossy().to_string();
                if name.starts_with('.') || excludes.contains(&name.as_str()) {
                    continue;
                }
                let path = entry.path();
                if path.is_dir() {
                    let sub_files = collect_source_files(&path);
                    for f in sub_files {
                        all.push(format!("{}/{}", name, f));
                    }
                }
            }
        }
        all
    } else {
        let source_dir = project_path.join(source);
        if !source_dir.exists() {
            return None;
        }
        collect_source_files(&source_dir)
    };

    let mut target_basenames: std::collections::HashSet<String> = std::collections::HashSet::new();
    for f in &target_files {
        if let Some(name) = std::path::Path::new(f).file_name() {
            target_basenames.insert(name.to_string_lossy().to_string());
        }
    }

    let mut migrated = 0;
    let mut missing = Vec::new();

    for src_file in &source_files {
        if let Some(name) = std::path::Path::new(src_file).file_name() {
            let basename = name.to_string_lossy().to_string();
            if target_basenames.contains(&basename) {
                migrated += 1;
            } else {
                missing.push(src_file.clone());
            }
        }
    }

    // Group missing by directory (first 4 path segments)
    let mut by_dir: HashMap<String, usize> = HashMap::new();
    for f in &missing {
        let parts: Vec<&str> = f.split('/').collect();
        let key = if parts.len() >= 4 {
            parts[..4].join("/")
        } else {
            parts.join("/")
        };
        *by_dir.entry(key).or_insert(0) += 1;
    }

    let mut top_directories: Vec<(String, usize)> = by_dir.into_iter().collect();
    top_directories.sort_by(|a, b| b.1.cmp(&a.1));
    top_directories.truncate(30);

    let progress_pct = if source_files.is_empty() {
        0
    } else {
        (migrated as f64 / source_files.len() as f64 * 100.0) as u32
    };

    Some(MigrationProgress {
        source_dir: source.to_string(),
        target_dir: target.to_string(),
        source_files: source_files.len(),
        target_files: target_files.len(),
        migrated,
        remaining: missing.len(),
        progress_pct,
        top_directories,
    })
}

pub fn collect_source_files(dir: &std::path::Path) -> Vec<String> {
    let mut result = Vec::new();
    let exts = [".java", ".py", ".ts", ".tsx", ".js", ".rs"];
    fn walk(dir: &std::path::Path, base: &std::path::Path, exts: &[&str], out: &mut Vec<String>) {
        if let Ok(entries) = std::fs::read_dir(dir) {
            for entry in entries.flatten() {
                let name = entry.file_name().to_string_lossy().to_string();
                if name.starts_with('.') || name == "target" || name == "node_modules" {
                    continue;
                }
                let path = entry.path();
                if path.is_dir() {
                    walk(&path, base, exts, out);
                } else if let Ok(rel) = path.strip_prefix(base) {
                    let rel_str = rel.to_string_lossy().to_string();
                    if exts.iter().any(|ext| rel_str.ends_with(ext)) {
                        out.push(rel_str);
                    }
                }
            }
        }
    }
    walk(dir, dir, &exts, &mut result);
    result
}

/// Find dead code: files with no incoming imports.
/// If registry is provided, only considers files belonging to defined modules.
pub fn dead_code(graph: &CodeGraph, registry: Option<&ModuleRegistry>) -> DeadCodeReport {
    let mut imported: std::collections::HashSet<&String> = std::collections::HashSet::new();
    for e in &graph.edges {
        if e.edge_type == EdgeType::Imports {
            imported.insert(&e.to);
        }
    }

    // Build allowed-files set if registry is provided
    let allowed_files: Option<std::collections::HashSet<String>> = registry.map(|r| {
        let mut set = std::collections::HashSet::new();
        if let Ok(modules) = r.list_modules() {
            for m in &modules {
                if let Ok(files) = r.resolve_files(m) {
                    for f in files { set.insert(f); }
                }
            }
        }
        set
    });

    let total_files = graph.nodes.iter()
        .filter(|n| n.node_type == NodeType::File)
        .filter(|n| allowed_files.as_ref().map(|s| s.contains(&n.file_path)).unwrap_or(true))
        .count();

    let dead: Vec<&crate::graph::CodeNode> = graph.nodes.iter()
        .filter(|n| n.node_type == NodeType::File)
        .filter(|n| allowed_files.as_ref().map(|s| s.contains(&n.file_path)).unwrap_or(true))
        .filter(|n| !imported.contains(&n.id))
        .filter(|n| {
            !n.name.contains("Test") && !n.name.contains("Main")
                && !n.file_path.contains("/test/")
        })
        .collect();

    let dead_files: Vec<String> = dead.iter().map(|n| n.file_path.clone()).collect();

    let mut by_dir: HashMap<String, usize> = HashMap::new();
    for f in &dead {
        let parts: Vec<&str> = f.file_path.split('/').collect();
        let key = if parts.len() >= 3 {
            parts[..3].join("/")
        } else {
            parts.join("/")
        };
        *by_dir.entry(key).or_insert(0) += 1;
    }

    let mut by_directory: Vec<(String, usize)> = by_dir.into_iter().collect();
    by_directory.sort_by(|a, b| b.1.cmp(&a.1));
    by_directory.truncate(30);

    DeadCodeReport {
        total_files,
        imported_files: imported.len(),
        dead_files,
        by_directory,
    }
}

/// Detect cross-module imports (boundary violations).
pub fn boundary_violations(
    graph: &CodeGraph,
    registry: &ModuleRegistry,
) -> Vec<BoundaryViolation> {
    // Build file → module map
    let mut file_to_module: HashMap<String, String> = HashMap::new();
    if let Ok(modules) = registry.list_modules() {
        for m in &modules {
            if let Ok(files) = registry.resolve_files(m) {
                for f in files {
                    file_to_module.insert(f, m.name.clone());
                }
            }
        }
    }

    // Build: module_pair → (count, sample files)
    let mut violations: HashMap<(String, String), (u32, Vec<String>)> = HashMap::new();

    for edge in graph.edges.iter().filter(|e| e.edge_type == EdgeType::Imports) {
        let from_path = graph.nodes.iter()
            .find(|n| n.id == edge.from)
            .map(|n| &n.file_path);
        let to_path = graph.nodes.iter()
            .find(|n| n.id == edge.to)
            .map(|n| &n.file_path);

        if let (Some(fp), Some(tp)) = (from_path, to_path) {
            let from_mod = file_to_module.get(fp);
            let to_mod = file_to_module.get(tp);

            if let (Some(fm), Some(tm)) = (from_mod, to_mod) {
                if fm != tm {
                    let entry = violations.entry((fm.clone(), tm.clone()))
                        .or_insert_with(|| (0, Vec::new()));
                    entry.0 += 1;
                    if entry.1.len() < 5 {
                        entry.1.push(format!("{} → {}", fp, tp));
                    }
                }
            }
        }
    }

    let mut result: Vec<BoundaryViolation> = violations
        .into_iter()
        .map(|((from, to), (count, samples))| BoundaryViolation {
            from_module: from,
            to_module: to,
            count,
            sample_files: samples,
        })
        .collect();

    result.sort_by(|a, b| b.count.cmp(&a.count));
    result
}

/// Compute cohesion for each module.
pub fn module_cohesion(
    graph: &CodeGraph,
    registry: &ModuleRegistry,
) -> Vec<ModuleCohesion> {
    let mut file_to_module: HashMap<String, String> = HashMap::new();
    let modules = match registry.list_modules() {
        Ok(m) => m,
        Err(_) => return Vec::new(),
    };

    for m in &modules {
        if let Ok(files) = registry.resolve_files(m) {
            for f in files {
                file_to_module.insert(f, m.name.clone());
            }
        }
    }

    // For each module, count internal and external imports
    let mut stats: HashMap<String, (u32, u32, HashMap<String, u32>)> = HashMap::new();
    // (internal, external, external_by_target_module)

    for edge in graph.edges.iter().filter(|e| e.edge_type == EdgeType::Imports) {
        let from_path = graph.nodes.iter().find(|n| n.id == edge.from).map(|n| &n.file_path);
        let to_path = graph.nodes.iter().find(|n| n.id == edge.to).map(|n| &n.file_path);

        if let (Some(fp), Some(tp)) = (from_path, to_path) {
            let from_mod = file_to_module.get(fp);
            let to_mod = file_to_module.get(tp);

            if let Some(fm) = from_mod {
                let entry = stats.entry(fm.clone()).or_insert_with(|| (0, 0, HashMap::new()));
                match to_mod {
                    Some(tm) if tm == fm => entry.0 += 1, // internal
                    Some(tm) => {
                        entry.1 += 1; // external
                        *entry.2.entry(tm.clone()).or_insert(0) += 1;
                    }
                    None => {
                        entry.1 += 1; // external (unknown module)
                    }
                }
            }
        }
    }

    let mut result: Vec<ModuleCohesion> = modules
        .iter()
        .map(|m| {
            let file_count = registry.file_count(m);
            let (internal, external, by_target) = stats
                .get(&m.name)
                .cloned()
                .unwrap_or_else(|| (0, 0, HashMap::new()));

            let total = internal + external;
            let cohesion_ratio = if total > 0 {
                internal as f32 / total as f32
            } else {
                0.0
            };

            let mut top_external: Vec<(String, u32)> = by_target.into_iter().collect();
            top_external.sort_by(|a, b| b.1.cmp(&a.1));
            top_external.truncate(10);

            ModuleCohesion {
                module: m.name.clone(),
                file_count,
                internal_imports: internal,
                external_imports: external,
                cohesion_ratio,
                top_external,
            }
        })
        .collect();

    // Sort by file count (largest modules first)
    result.sort_by(|a, b| b.file_count.cmp(&a.file_count));
    result
}
