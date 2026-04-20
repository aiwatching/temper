//! Walk a project tree, find Javadoc/block-comment fragments tagged
//! `TEMPER-CONSTRAINT`, and extract the raw text plus location.

use anyhow::Result;
use std::path::{Path, PathBuf};

/// A single TEMPER-CONSTRAINT block as it appears on disk.
///
/// Parsing of the four structured parts (What/Why/Rule/Escape) happens in
/// `parser`; this type is intentionally raw.
#[derive(Debug, Clone, serde::Serialize)]
pub struct RawConstraint {
    pub file_path: String,    // relative to project root
    pub line: usize,          // 1-based line where the constraint header appears
    pub title: String,        // first non-empty line after the marker
    pub body: String,         // full body text with comment markers stripped
}

const MARKER: &str = "TEMPER-CONSTRAINT";

const SOURCE_EXTENSIONS: &[&str] = &[
    "java", "py", "ts", "tsx", "js", "mjs", "rs", "go", "kt", "scala",
];

const EXCLUDE_DIRS: &[&str] = &[
    ".git", "node_modules", "target", "build", "dist", ".next", "out",
    ".gradle", ".mvn", "__pycache__", ".idea", ".vscode", ".temper",
];

/// Scan every source file under `project_path` and return all constraint blocks.
pub fn scan_project(project_path: &Path) -> Result<Vec<RawConstraint>> {
    let mut result = Vec::new();
    let mut stack: Vec<PathBuf> = vec![project_path.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let entries = match std::fs::read_dir(&dir) {
            Ok(e) => e,
            Err(_) => continue,
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().to_string();
            if path.is_dir() {
                if name.starts_with('.') && name != "." {
                    continue;
                }
                if EXCLUDE_DIRS.contains(&name.as_str()) {
                    continue;
                }
                stack.push(path);
            } else if path.is_file() {
                let ext_ok = path
                    .extension()
                    .and_then(|e| e.to_str())
                    .map(|e| SOURCE_EXTENSIONS.contains(&e))
                    .unwrap_or(false);
                if !ext_ok {
                    continue;
                }
                let rel = path.strip_prefix(project_path).unwrap_or(&path);
                let rel_str = rel.to_string_lossy().to_string();
                if let Ok(content) = std::fs::read_to_string(&path) {
                    for c in scan_file(&rel_str, &content) {
                        result.push(c);
                    }
                }
            }
        }
    }
    result.sort_by(|a, b| a.file_path.cmp(&b.file_path).then(a.line.cmp(&b.line)));
    Ok(result)
}

/// Find all TEMPER-CONSTRAINT blocks in a single file's text.
pub fn scan_file(file_path: &str, content: &str) -> Vec<RawConstraint> {
    let mut out = Vec::new();
    let lines: Vec<&str> = content.lines().collect();
    let mut i = 0;
    while i < lines.len() {
        if lines[i].contains(MARKER) {
            // Walk backwards to find the start of the enclosing comment block.
            let start = find_comment_start(&lines, i);
            // Walk forward to the end of the block.
            let end = find_comment_end(&lines, i);

            let header_line = start + 1; // 1-based
            let stripped_body = strip_comment_markers(&lines[start..=end]);
            let title = first_nonempty_line_after(&stripped_body, MARKER)
                .unwrap_or_else(|| stripped_body.lines().next().unwrap_or("").to_string());

            out.push(RawConstraint {
                file_path: file_path.to_string(),
                line: header_line,
                title: title.trim().to_string(),
                body: stripped_body,
            });

            i = end + 1;
            continue;
        }
        i += 1;
    }
    out
}

/// Walk backward from `idx` to find the comment block's first line.
/// Handles Java/TS/Rust `/**` blocks, C-style `/*`, Python `"""` and `'''`.
fn find_comment_start(lines: &[&str], idx: usize) -> usize {
    let mut i = idx;
    loop {
        let t = lines[i].trim_start();
        if t.starts_with("/**") || t.starts_with("/*")
            || t.starts_with("\"\"\"") || t.starts_with("'''")
        {
            return i;
        }
        if i == 0 {
            return idx;
        }
        i -= 1;
    }
}

fn find_comment_end(lines: &[&str], idx: usize) -> usize {
    for i in idx..lines.len() {
        let t = lines[i].trim();
        if t.ends_with("*/") || t == "\"\"\"" || t == "'''" {
            return i;
        }
    }
    lines.len().saturating_sub(1)
}

/// Strip leading `*`, `//`, `#`, `"""`, etc. from each line of a comment block.
fn strip_comment_markers(block: &[&str]) -> String {
    let mut out = String::new();
    for line in block {
        let mut t = line.trim_start();
        // Skip block openers/closers
        if t.starts_with("/**") || t.starts_with("/*") || t.starts_with("*/") {
            t = t.trim_start_matches("/*").trim_start_matches('*').trim_start_matches('/');
        }
        if t.starts_with("\"\"\"") || t.starts_with("'''") {
            t = &t[3..];
        }
        // Strip leading `*` for Javadoc lines
        if let Some(rest) = t.strip_prefix("* ") {
            t = rest;
        } else if t == "*" {
            t = "";
        }
        // Strip trailing `*/`
        let t = t.trim_end_matches("*/").trim_end();
        out.push_str(t);
        out.push('\n');
    }
    out
}

fn first_nonempty_line_after(body: &str, marker: &str) -> Option<String> {
    let mut found_marker = false;
    for line in body.lines() {
        let l = line.trim();
        if l.is_empty() {
            continue;
        }
        if l.contains(marker) {
            found_marker = true;
            // Some constraints put the title on the same line as the marker.
            if let Some(pos) = l.find(marker) {
                let after = l[pos + marker.len()..].trim_start_matches(&[':', '—', '-', ' '][..]);
                if !after.is_empty() {
                    return Some(after.to_string());
                }
            }
            continue;
        }
        if found_marker {
            return Some(l.to_string());
        }
    }
    None
}
