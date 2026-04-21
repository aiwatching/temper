//! Output shapes for `temper check`.

use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum Status {
    Ok,
    Dangling { symbols: Vec<String> },
    Contradicted { patterns: Vec<String> },
    Banned { tokens: Vec<String> },
    Stale { reason: String },
}

#[derive(Debug, Clone, Serialize)]
pub struct ConstraintCheck {
    pub file_path: String,
    pub line: usize,
    pub title: String,
    pub symbols: Vec<String>,
    pub forbidden_patterns: Vec<String>,
    pub banned_tokens: Vec<String>,
    pub statuses: Vec<Status>,
}

#[derive(Debug, Clone, Serialize)]
pub struct CheckReport {
    pub checks: Vec<ConstraintCheck>,
}

impl CheckReport {
    pub fn has_problems(&self) -> bool {
        self.checks.iter().any(|c| {
            c.statuses
                .iter()
                .any(|s| !matches!(s, Status::Ok))
        })
    }

    pub fn print_text(&self) {
        if self.checks.is_empty() {
            println!("No TEMPER-CONSTRAINT comments found.");
            return;
        }

        let mut ok = 0;
        let mut dangling = 0;
        let mut contradicted = 0;
        let mut banned_hits = 0;

        for c in &self.checks {
            let mut tags: Vec<String> = Vec::new();
            for s in &c.statuses {
                match s {
                    Status::Ok => { ok += 1; tags.push("OK".into()); }
                    Status::Dangling { symbols } => {
                        dangling += 1;
                        tags.push(format!("DANGLING({})", symbols.join(",")));
                    }
                    Status::Contradicted { patterns } => {
                        contradicted += 1;
                        tags.push(format!("CONTRADICTED({})", patterns.join(" | ")));
                    }
                    Status::Banned { tokens } => {
                        banned_hits += 1;
                        tags.push(format!("BANNED-TOKEN-IN-CODE({})", tokens.join(",")));
                    }
                    Status::Stale { reason } => {
                        tags.push(format!("STALE({})", reason));
                    }
                }
            }
            println!("{}:{}  [{}]  {}", c.file_path, c.line, tags.join(" "), c.title);
        }

        println!("\n---");
        println!(
            "Total: {}  OK: {}  Dangling: {}  Contradicted: {}  BannedInCode: {}",
            self.checks.len(), ok, dangling, contradicted, banned_hits
        );
    }
}
