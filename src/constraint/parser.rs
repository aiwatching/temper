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
    /// Tokens the constraint explicitly bans via "Do NOT use X" / "X is banned"
    /// style prose. If any of these appear in the codebase the constraint is
    /// likely stale or being violated.
    pub banned_tokens: Vec<String>,
    /// Optional `Last-Verified: YYYY-MM-DD` header — author's assertion that
    /// the constraint was reviewed against the code on that date.
    pub last_verified: Option<String>,
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

// Captures a `Last-Verified: YYYY-MM-DD` (or ISO datetime) header.
static LAST_VERIFIED_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)Last[-\s]?Verified\s*[:\-]\s*(\d{4}-\d{2}-\d{2}(?:[T\s][\d:+\-Z]+)?)")
        .unwrap()
});

// "Ban lexicon" — prose cues that explicitly forbid a named token.
// Each regex captures the token in group 1.
static BAN_PHRASES: Lazy<Vec<Regex>> = Lazy::new(|| {
    let patterns = [
        // English imperatives
        r"(?i)do\s*not\s+use\s+([A-Za-z0-9_.*]+)",
        r"(?i)don[\u2019']t\s+use\s+([A-Za-z0-9_.*]+)",
        r"(?i)do\s*not\s+(?:import|extend|inject|call|reference|invoke)\s+([A-Za-z0-9_.*]+)",
        r"(?i)do\s*not\s+use\s+the\s+([A-Za-z0-9_.*]+)",
        // Declarative
        r"([A-Za-z0-9_.]+)\s+is\s+(?:banned|prohibited|forbidden|deprecated)",
        r"(?i)banned\s*[:\-\u2014]\s*([A-Za-z0-9_.*]+)",
        // Chinese (light coverage — main constraints are still English)
        r"禁止(?:使用|引入|调用|扩展)?\s*([A-Za-z0-9_.*]+)",
        r"不要(?:使用|用|引入|调用|扩展)\s*([A-Za-z0-9_.*]+)",
    ];
    patterns.iter().map(|p| Regex::new(p).unwrap()).collect()
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

    let banned_tokens = extract_banned_tokens(&raw.body);

    let last_verified = LAST_VERIFIED_RE
        .captures(&raw.body)
        .and_then(|c| c.get(1).map(|m| m.as_str().trim().to_string()));

    ParsedConstraint {
        raw,
        what,
        why,
        rule,
        escape,
        symbols,
        forbidden_patterns,
        banned_tokens,
        last_verified,
    }
}

/// Scan the body for prose like "Do NOT use X" / "X is banned" and collect X.
/// Only tokens that look like code identifiers are kept (ASCII-start,
/// contains a dot or has a capital letter) — filters out matches on common
/// English words like "this", "that".
fn extract_banned_tokens(body: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for re in BAN_PHRASES.iter() {
        for caps in re.captures_iter(body) {
            if let Some(m) = caps.get(1) {
                let raw = m.as_str().trim_end_matches(&['.', ',', ';', ':', ')'][..]);
                let token = raw.trim_end_matches(".*").to_string();
                if looks_like_code_identifier(&token) && !is_ban_noise(&token) {
                    out.push(token);
                }
            }
        }
    }
    out.sort();
    out.dedup();
    out
}

fn looks_like_code_identifier(token: &str) -> bool {
    if token.len() < 3 {
        return false;
    }
    let has_upper = token.chars().any(|c| c.is_ascii_uppercase());
    let has_dot = token.contains('.');
    let has_underscore = token.contains('_');
    has_upper || has_dot || has_underscore
}

/// Words that the phrase regex picks up but are not real banned tokens.
fn is_ban_noise(token: &str) -> bool {
    const NOISE: &[&str] = &[
        "This", "That", "These", "Those", "Any", "All",
        "TEMPER-CONSTRAINT", "TEMPER", "CONSTRAINT",
        "INC", "MIG", "PR", "TODO", "FIXME",
    ];
    NOISE.contains(&token)
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
