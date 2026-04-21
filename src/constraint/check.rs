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

/// A constraint without `Last-Verified:` whose file is older than this many
/// days gets an `UnverifiedAge` warning. The choice is conservative — most
/// production constraint comments should be re-verified at least twice a year.
const UNVERIFIED_AGE_WARN_DAYS: i64 = 180;

pub fn run(project_path: &Path) -> Result<CheckReport> {
    run_filtered(project_path, None)
}

/// Like `run` but only processes constraints whose file is in `only_files`
/// (when provided). Used by `temper check --staged` to narrow the scan to
/// files a pre-commit hook is about to commit.
pub fn run_filtered(project_path: &Path, only_files: Option<&[String]>) -> Result<CheckReport> {
    let raws = scan_project(project_path)?;
    let parsed: Vec<ParsedConstraint> = raws
        .into_iter()
        .filter(|r| {
            only_files
                .map(|list| list.iter().any(|f| f == &r.file_path))
                .unwrap_or(true)
        })
        .map(parse)
        .collect();

    // Build a single in-memory index of the whole project's text so we can
    // answer "does symbol X appear?" and "does pattern P appear?" cheaply.
    let project_text = load_project_text(project_path)?;

    let in_git_repo = is_git_repo(project_path);

    let mut checks = Vec::new();
    for pc in parsed {
        let mut statuses: Vec<Status> = Vec::new();
        let mut dangling_symbols = Vec::new();
        let mut contradicting_patterns = Vec::new();
        let mut banned_in_code = Vec::new();

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

        // --- Ban lexicon: "Do NOT use X" / "X is banned" phrases whose X
        //     shows up in the constraint's own file (outside the comment
        //     block itself). A constraint that sits next to code which
        //     still uses the banned token is either stale or being violated.
        //
        //     Scope is the file the constraint lives in, not the whole
        //     project — most "Do not import/inject/use X" phrasing really
        //     means "here in this file", and a project-wide ban needs a
        //     different mechanism (lint / CI).
        let own_file_body = project_text
            .get(&pc.raw.file_path)
            .cloned()
            .unwrap_or_default();
        let code_only = strip_comment_block(&own_file_body, pc.raw.line);
        for token in &pc.banned_tokens {
            if token_in_text(&code_only, token) {
                banned_in_code.push(token.clone());
            }
        }
        if !banned_in_code.is_empty() {
            statuses.push(Status::Banned {
                tokens: banned_in_code.clone(),
            });
        }

        // --- Git-timestamp staleness ---
        //   Last-Verified: YYYY-MM-DD in the body vs the file's most recent
        //   commit. If the file moved since the verification date, the
        //   constraint may no longer match the code it guards.
        if in_git_repo {
            let file_last_commit = file_last_commit_date(project_path, &pc.raw.file_path);
            match (pc.last_verified.as_deref(), file_last_commit.as_deref()) {
                (Some(verified), Some(committed)) => {
                    if committed_after(committed, verified) {
                        statuses.push(Status::Stale {
                            last_verified: Some(verified.to_string()),
                            file_last_commit: Some(committed.to_string()),
                            reason: "file modified after Last-Verified".to_string(),
                        });
                    }
                }
                (None, Some(committed)) => {
                    // No Last-Verified header. Warn if the file itself is old enough
                    // that the author almost certainly never came back to re-check.
                    if let Some(age) = days_since(committed) {
                        if age >= UNVERIFIED_AGE_WARN_DAYS {
                            statuses.push(Status::UnverifiedAge { age_days: age });
                        }
                    }
                }
                _ => {}
            }
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
            banned_tokens: pc.banned_tokens.clone(),
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

/// Does the banned token appear in the given text (already scoped / stripped)?
fn token_in_text(text: &str, token: &str) -> bool {
    if token.contains('.') {
        text.contains(token)
    } else {
        contains_as_token(text, token)
    }
}

/// Remove a /** ... */ block whose opening `/**` or `/*` sits at or before
/// `anchor_line` (1-based). Keeps everything else intact so the remaining
/// text is "code only" for the containing file.
fn strip_comment_block(text: &str, anchor_line: usize) -> String {
    let lines: Vec<&str> = text.lines().collect();
    if anchor_line == 0 || anchor_line > lines.len() {
        return text.to_string();
    }

    // Walk backwards from anchor to find the comment opener (first line
    // starting with /** or /* or """). Java-like comments are the main
    // target; """ is kept for Python.
    let anchor_idx = anchor_line - 1;
    let mut start = anchor_idx;
    loop {
        let t = lines[start].trim_start();
        if t.starts_with("/**") || t.starts_with("/*") || t.starts_with("\"\"\"") {
            break;
        }
        if start == 0 {
            return text.to_string();
        }
        start -= 1;
    }

    // Walk forward from anchor to find the closer.
    let mut end = anchor_idx;
    while end < lines.len() {
        let t = lines[end].trim();
        if t.ends_with("*/") || t == "\"\"\"" {
            break;
        }
        end += 1;
    }

    let mut out = String::new();
    for (i, line) in lines.iter().enumerate() {
        if i >= start && i <= end {
            continue;
        }
        out.push_str(line);
        out.push('\n');
    }
    out
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

// --- git helpers ---

fn is_git_repo(project_path: &Path) -> bool {
    std::process::Command::new("git")
        .args(["rev-parse", "--is-inside-work-tree"])
        .current_dir(project_path)
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

/// ISO-8601 timestamp of the file's most recent commit, or None if the file
/// has no history (untracked / just added).
fn file_last_commit_date(project_path: &Path, file_rel: &str) -> Option<String> {
    let out = std::process::Command::new("git")
        .args(["log", "-1", "--format=%cI", "--", file_rel])
        .current_dir(project_path)
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if s.is_empty() { None } else { Some(s) }
}

/// Compare a committed timestamp (ISO-8601, possibly with offset) against a
/// verification date (YYYY-MM-DD). Returns true if the commit's local date
/// is strictly after the verification date. We compare date-to-date so the
/// author's "I verified today" assertion works regardless of UTC offset.
fn committed_after(committed_iso: &str, verified_date: &str) -> bool {
    let committed_dt = match chrono::DateTime::parse_from_rfc3339(committed_iso) {
        Ok(dt) => dt,
        Err(_) => return false,
    };
    let verified = match chrono::NaiveDate::parse_from_str(verified_date, "%Y-%m-%d") {
        Ok(d) => d,
        Err(_) => return false,
    };
    // Use the commit's local date (respecting its tz offset).
    committed_dt.date_naive() > verified
}

fn days_since(iso_timestamp: &str) -> Option<i64> {
    let ts = chrono::DateTime::parse_from_rfc3339(iso_timestamp).ok()?;
    let now = chrono::Utc::now();
    let duration = now.signed_duration_since(ts.with_timezone(&chrono::Utc));
    Some(duration.num_days())
}
