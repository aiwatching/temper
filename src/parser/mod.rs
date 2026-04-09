use anyhow::{Context, Result};
use std::collections::HashSet;
use std::path::{Path, PathBuf};

use crate::graph::{CodeEdge, CodeGraph, CodeNode};

mod java;
mod python;
mod rust;
mod typescript;

/// Source file scanner — finds files and orchestrates parsing.
pub struct Scanner {
    project_path: PathBuf,
    exclude_dirs: HashSet<String>,
}

impl Scanner {
    pub fn new(project_path: &Path) -> Self {
        let config = crate::config::GlobalConfig::load_or_default()
            .unwrap_or_default();

        let exclude_dirs: HashSet<String> = config
            .defaults
            .scan_exclude
            .into_iter()
            .collect();

        Self {
            project_path: project_path.to_path_buf(),
            exclude_dirs,
        }
    }

    /// Full project scan — parse all source files.
    pub fn full_scan(&self) -> Result<CodeGraph> {
        let files = self.find_source_files()?;
        let mut graph = CodeGraph::new();
        graph.files = files.clone();

        for rel_path in &files {
            let full_path = self.project_path.join(rel_path);
            let module = infer_module(rel_path);

            match parse_file(&full_path, rel_path, &module) {
                Ok((nodes, edges)) => {
                    graph.nodes.extend(nodes);
                    graph.edges.extend(edges);
                }
                Err(e) => {
                    eprintln!("Warning: failed to parse {}: {}", rel_path, e);
                }
            }
        }

        graph.resolve_edges();
        Ok(graph)
    }

    /// Incremental update — re-parse only changed files.
    pub fn incremental_update(
        &self,
        mut existing: CodeGraph,
        changed_files: &[String],
    ) -> Result<CodeGraph> {
        let source_exts = [".java", ".py", ".ts", ".tsx", ".js", ".mjs", ".rs"];
        let source_files: Vec<&String> = changed_files
            .iter()
            .filter(|f| source_exts.iter().any(|ext| f.ends_with(ext)))
            .filter(|f| !f.contains(".test.") && !f.contains(".spec.") && !f.ends_with("Test.java"))
            .collect();

        if source_files.is_empty() {
            return Ok(existing);
        }

        let changed_set: HashSet<&str> = source_files.iter().map(|s| s.as_str()).collect();

        // Remove old nodes/edges for changed files
        existing.nodes.retain(|n| !changed_set.contains(n.file_path.as_str()));
        existing.edges.retain(|e| {
            let from_file = e.from.split("::").next().unwrap_or(&e.from);
            !changed_set.contains(from_file)
        });

        // Re-parse changed files
        for rel_path in &source_files {
            let full_path = self.project_path.join(rel_path);
            if !full_path.exists() {
                continue; // file deleted
            }
            let module = infer_module(rel_path);
            match parse_file(&full_path, rel_path, &module) {
                Ok((nodes, edges)) => {
                    existing.nodes.extend(nodes);
                    existing.edges.extend(edges);
                }
                Err(e) => {
                    eprintln!("Warning: failed to parse {}: {}", rel_path, e);
                }
            }
        }

        // Update file list
        existing.files.retain(|f| !changed_set.contains(f.as_str()));
        for f in &source_files {
            let full = self.project_path.join(f);
            if full.exists() {
                existing.files.push(f.to_string());
            }
        }

        existing.resolve_edges();
        existing.scanned_at = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs();

        Ok(existing)
    }

    /// Find all source files in the project.
    pub fn find_source_files(&self) -> Result<Vec<String>> {
        let mut results = Vec::new();
        self.walk_dir(&self.project_path, "", &mut results)?;
        Ok(results)
    }

    fn walk_dir(&self, dir: &Path, rel_dir: &str, results: &mut Vec<String>) -> Result<()> {
        let entries = std::fs::read_dir(dir)
            .with_context(|| format!("Failed to read directory: {}", dir.display()))?;

        for entry in entries {
            let entry = entry?;
            let name = entry.file_name().to_string_lossy().to_string();

            // Skip hidden and excluded directories
            if name.starts_with('.') || self.exclude_dirs.contains(&name) {
                continue;
            }

            let full = entry.path();
            let rel = if rel_dir.is_empty() {
                name.clone()
            } else {
                format!("{}/{}", rel_dir, name)
            };

            if full.is_dir() {
                self.walk_dir(&full, &rel, results)?;
            } else if is_source_file(&name) {
                results.push(rel);
            }
        }

        Ok(())
    }

    /// Get HEAD commit hash.
    pub fn head_commit(&self) -> Option<String> {
        let repo = git2::Repository::open(&self.project_path).ok()?;
        let head = repo.head().ok()?;
        let commit = head.peel_to_commit().ok()?;
        Some(commit.id().to_string()[..12].to_string())
    }

    /// Get files changed since a given commit.
    pub fn get_changed_files(&self, since_commit: Option<&str>) -> Result<Vec<String>> {
        let since = match since_commit {
            Some(c) => c,
            None => return Ok(Vec::new()),
        };

        let repo = git2::Repository::open(&self.project_path)
            .context("Not a git repository")?;

        let old_oid = repo
            .revparse_single(since)
            .context("Failed to resolve commit")?
            .id();
        let old_commit = repo.find_commit(old_oid)?;
        let old_tree = old_commit.tree()?;

        let head = repo.head()?.peel_to_commit()?.tree()?;
        let diff = repo.diff_tree_to_tree(Some(&old_tree), Some(&head), None)?;

        let mut files = Vec::new();
        diff.foreach(
            &mut |delta, _| {
                if let Some(path) = delta.new_file().path() {
                    files.push(path.to_string_lossy().to_string());
                }
                true
            },
            None,
            None,
            None,
        )?;

        Ok(files)
    }
}

/// Parse a single source file into nodes and edges.
fn parse_file(
    full_path: &Path,
    rel_path: &str,
    module: &str,
) -> Result<(Vec<CodeNode>, Vec<CodeEdge>)> {
    let ext = full_path
        .extension()
        .map(|e| e.to_string_lossy().to_string())
        .unwrap_or_default();

    match ext.as_str() {
        "java" => java::parse_java(full_path, rel_path, module),
        "py" => python::parse_python(full_path, rel_path, module),
        "ts" | "tsx" => typescript::parse_typescript(full_path, rel_path, module),
        "js" | "mjs" => typescript::parse_typescript(full_path, rel_path, module),
        "rs" => rust::parse_rust(full_path, rel_path, module),
        _ => Ok((Vec::new(), Vec::new())),
    }
}

/// Infer module name from file path (first directory segment).
fn infer_module(rel_path: &str) -> String {
    let parts: Vec<&str> = rel_path.split('/').collect();
    if parts.len() > 1 {
        parts[0].to_string()
    } else {
        "_root".to_string()
    }
}

/// Check if a filename is a parseable source file.
fn is_source_file(name: &str) -> bool {
    let source_exts = [".java", ".py", ".ts", ".tsx", ".js", ".mjs", ".rs"];
    let test_patterns = [".test.", ".spec.", "Test.java"];

    source_exts.iter().any(|ext| name.ends_with(ext))
        && !test_patterns.iter().any(|pat| name.contains(pat))
        && !name.ends_with(".d.ts")
}

