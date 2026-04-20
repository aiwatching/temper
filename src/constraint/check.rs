//! Verify that each TEMPER-CONSTRAINT in a project is consistent with the
//! current code. Two checks per constraint:
//!   - symbol existence: every PascalCase identifier the constraint mentions
//!     must appear somewhere in the source tree, else `DANGLING`.
//!   - pattern drift: if the constraint lists a "forbidden pattern" (e.g.
//!     `Map<..., Entity>`), that pattern should NOT appear in the codebase
//!     outside the constraint comment itself — else `CONTRADICTED`.

use anyhow::Result;
use std::collections::HashMap;
use std::path::Path;

use crate::constraint::parser::{parse, ParsedConstraint};
use crate::constraint::report::{CheckReport, ConstraintCheck, Status};
use crate::constraint::scanner::scan_project;

pub fn run(project_path: &Path) -> Result<CheckReport> {
    let raws = scan_project(project_path)?;
    let parsed: Vec<ParsedConstraint> = raws.into_iter().map(parse).collect();

    // Build a single in-memory index of the whole project's text so we can
    // answer "does symbol X appear?" and "does pattern P appear?" cheaply.
    let project_text = load_project_text(project_path)?;

    let mut checks = Vec::new();
    for pc in parsed {
        let mut statuses: Vec<Status> = Vec::new();
        let mut dangling_symbols = Vec::new();
        let mut contradicting_patterns = Vec::new();

        // --- Symbol existence ---
        for sym in &pc.symbols {
            if !symbol_exists(&project_text, sym, &pc.raw.file_path) {
                dangling_symbols.push(sym.clone());
            }
        }
        if !dangling_symbols.is_empty() {
            statuses.push(Status::Dangling {
                symbols: dangling_symbols.clone(),
            });
        }

        // --- Pattern drift ---
        for pat in &pc.forbidden_patterns {
            if pattern_appears(&project_text, pat, &pc.raw.file_path) {
                contradicting_patterns.push(pat.clone());
            }
        }
        if !contradicting_patterns.is_empty() {
            statuses.push(Status::Contradicted {
                patterns: contradicting_patterns.clone(),
            });
        }

        if statuses.is_empty() {
            statuses.push(Status::Ok);
        }

        checks.push(ConstraintCheck {
            file_path: pc.raw.file_path.clone(),
            line: pc.raw.line,
            title: pc.raw.title.clone(),
            symbols: pc.symbols.clone(),
            forbidden_patterns: pc.forbidden_patterns.clone(),
            statuses,
        });
    }

    Ok(CheckReport { checks })
}

/// Map file → full text. Used for symbol/pattern lookup outside the
/// constraint's own file.
fn load_project_text(project_path: &Path) -> Result<HashMap<String, String>> {
    use std::path::PathBuf;
    let mut map = HashMap::new();
    let mut stack: Vec<PathBuf> = vec![project_path.to_path_buf()];
    const EXCLUDE: &[&str] = &[
        ".git", "node_modules", "target", "build", "dist", ".next", "out",
        ".gradle", ".mvn", "__pycache__", ".idea", ".vscode", ".temper",
    ];
    const EXTS: &[&str] = &["java", "py", "ts", "tsx", "js", "mjs", "rs", "go", "kt", "scala", "xml", "yml", "yaml", "toml"];

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
                if EXCLUDE.contains(&name.as_str()) {
                    continue;
                }
                stack.push(path);
            } else if path.is_file() {
                let ok = path.extension().and_then(|e| e.to_str())
                    .map(|e| EXTS.contains(&e)).unwrap_or(false);
                if !ok { continue; }
                let rel = path.strip_prefix(project_path).unwrap_or(&path)
                    .to_string_lossy().to_string();
                if let Ok(content) = std::fs::read_to_string(&path) {
                    map.insert(rel, content);
                }
            }
        }
    }
    Ok(map)
}

/// Does `symbol` appear in any file other than `skip_file` as a class/interface-
/// like token? We treat it as present if it appears standalone (not as part of
/// a longer identifier).
fn symbol_exists(project_text: &HashMap<String, String>, symbol: &str, skip_file: &str) -> bool {
    let needle_word_boundary = format!("{}", symbol);
    for (path, text) in project_text {
        if path == skip_file {
            continue;
        }
        if contains_as_token(text, &needle_word_boundary) {
            return true;
        }
    }
    false
}

/// Does the forbidden pattern appear in the codebase outside the constraint
/// file? Naive substring test, good enough for a first pass.
fn pattern_appears(project_text: &HashMap<String, String>, pattern: &str, skip_file: &str) -> bool {
    let normalized = normalize_pattern(pattern);
    for (path, text) in project_text {
        if path == skip_file {
            continue;
        }
        let t = text.replace(char::is_whitespace, "");
        if t.contains(&normalized) {
            return true;
        }
    }
    false
}

fn normalize_pattern(pat: &str) -> String {
    pat.chars().filter(|c| !c.is_whitespace()).collect()
}

/// Word-boundary containment: `symbol` must be bounded by non-identifier
/// characters on either side to avoid matching inside `EntityType` when
/// looking for `Entity`.
fn contains_as_token(haystack: &str, symbol: &str) -> bool {
    let mut start = 0;
    let bytes = haystack.as_bytes();
    while let Some(idx) = haystack[start..].find(symbol) {
        let abs = start + idx;
        let left_ok = abs == 0 || !is_ident_char(bytes[abs - 1]);
        let right = abs + symbol.len();
        let right_ok = right >= bytes.len() || !is_ident_char(bytes[right]);
        if left_ok && right_ok {
            return true;
        }
        start = abs + 1;
    }
    false
}

fn is_ident_char(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b == b'_'
}
