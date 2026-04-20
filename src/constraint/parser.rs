//! Parse a `RawConstraint` body into the 4-part structure and extract
//! symbols / forbidden patterns the check phase will verify.

use crate::constraint::scanner::RawConstraint;
use once_cell::sync::Lazy;
use regex::Regex;

/// A structured constraint ready for checking.
#[derive(Debug, Clone, serde::Serialize)]
pub struct ParsedConstraint {
    pub raw: RawConstraint,
    pub what: String,
    pub why: String,
    pub rule: String,
    pub escape: String,
    /// Class / interface / trait names referenced in the body.
    pub symbols: Vec<String>,
    /// Forbidden code patterns (roughly `Foo<..., Bar>` or fully-qualified names).
    pub forbidden_patterns: Vec<String>,
}

static SYMBOL_RE: Lazy<Regex> = Lazy::new(|| {
    // PascalCase identifier ≥ 3 chars; skips obvious natural-language words
    // by requiring at least one lowercase letter followed by an uppercase.
    Regex::new(r"\b([A-Z][a-z]+(?:[A-Z][a-zA-Z]+)+)\b").unwrap()
});

static PATTERN_RE: Lazy<Regex> = Lazy::new(|| {
    // Java generics shape: Identifier< ... >
    Regex::new(r"([A-Z][A-Za-z0-9]+\s*<[^>\n]{0,80}>)").unwrap()
});

/// Noise symbols we don't want in the symbol-existence check.
const SYMBOL_DENYLIST: &[&str] = &[
    "TEMPER", "CONSTRAINT", "INC", "MIG", "TODO", "FIXME", "NOTE", "README",
    "API", "URL", "HTTP", "JSON", "YAML", "UUID", "ID", "TTL", "OOM", "CI",
    "PR", "QA", "ACL", "JPA", "ORM", "SQL", "DTO", "POJO",
];

pub fn parse(raw: RawConstraint) -> ParsedConstraint {
    let (what, why, rule, escape) = split_sections(&raw.body);

    let mut symbols: Vec<String> = SYMBOL_RE
        .captures_iter(&raw.body)
        .map(|c| c[1].to_string())
        .filter(|s| !SYMBOL_DENYLIST.contains(&s.as_str()))
        .collect();
    symbols.sort();
    symbols.dedup();

    let mut forbidden_patterns: Vec<String> = PATTERN_RE
        .captures_iter(&raw.body)
        .map(|c| c[1].trim().to_string())
        .collect();
    forbidden_patterns.sort();
    forbidden_patterns.dedup();

    ParsedConstraint {
        raw,
        what,
        why,
        rule,
        escape,
        symbols,
        forbidden_patterns,
    }
}

/// Split the body into What / Why / Rule / Escape sections.
/// Supports `<h3>Foo</h3>` (Javadoc style) and `Foo:` (plain) headings.
fn split_sections(body: &str) -> (String, String, String, String) {
    let mut sections: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    let mut current = String::from("preamble");
    let mut buf = String::new();

    for raw_line in body.lines() {
        let line = raw_line.trim();
        let lower = line.to_lowercase();
        let heading = detect_heading(&lower);
        if let Some(h) = heading {
            if !buf.is_empty() {
                sections
                    .entry(current.clone())
                    .or_default()
                    .push_str(buf.trim());
                buf.clear();
            }
            current = h;
            continue;
        }
        buf.push_str(raw_line);
        buf.push('\n');
    }
    if !buf.is_empty() {
        sections
            .entry(current.clone())
            .or_default()
            .push_str(buf.trim());
    }

    let what = sections.remove("what").unwrap_or_default();
    let why = sections.remove("why").unwrap_or_default();
    let rule = sections.remove("rule").unwrap_or_default();
    let escape = sections.remove("escape").unwrap_or_default();
    (what, why, rule, escape)
}

fn detect_heading(lower_line: &str) -> Option<String> {
    for key in ["what", "why", "rule", "escape"] {
        if lower_line == format!("{}:", key)
            || lower_line == format!("# {}", key)
            || lower_line == format!("## {}", key)
            || lower_line == format!("### {}", key)
            || lower_line == format!("<h3>{}</h3>", key)
            || lower_line.starts_with(&format!("**{}**", key))
        {
            return Some(key.to_string());
        }
    }
    None
}
