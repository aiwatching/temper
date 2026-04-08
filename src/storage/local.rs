use anyhow::{Context, Result};
use rusqlite::{params, Connection};
use std::collections::{HashSet, VecDeque};
use std::path::Path;

use super::*;

pub struct LocalStorage {
    conn: Connection,
}

impl LocalStorage {
    pub fn connection(&self) -> &Connection {
        &self.conn
    }

    pub fn open(db_path: &Path) -> Result<Self> {
        let conn = Connection::open(db_path)
            .with_context(|| format!("Failed to open database: {}", db_path.display()))?;

        let storage = Self { conn };
        storage.init_schema()?;
        Ok(storage)
    }

    fn init_schema(&self) -> Result<()> {
        self.conn.execute_batch(
            "
            CREATE TABLE IF NOT EXISTS knowledge (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                module TEXT,
                file TEXT,
                function TEXT,
                tags TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                current_version INTEGER NOT NULL DEFAULT 1,
                git_commit TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS causal_relations (
                id TEXT PRIMARY KEY,
                from_entity TEXT NOT NULL,
                to_entity TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                description TEXT,
                confidence TEXT DEFAULT 'suspected',
                git_commit TEXT,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS experiences (
                id TEXT PRIMARY KEY,
                module TEXT,
                symptom TEXT NOT NULL,
                cause TEXT NOT NULL,
                fix TEXT NOT NULL,
                constraint_note TEXT,
                tags TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                git_commit TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS history (
                entity_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                version INTEGER NOT NULL,
                status TEXT NOT NULL,
                content TEXT NOT NULL,
                git_commit TEXT,
                changed_by TEXT NOT NULL,
                reason TEXT,
                timestamp INTEGER NOT NULL,
                PRIMARY KEY (entity_id, version)
            );

            CREATE INDEX IF NOT EXISTS idx_knowledge_module ON knowledge(module);
            CREATE INDEX IF NOT EXISTS idx_knowledge_file ON knowledge(file);
            CREATE INDEX IF NOT EXISTS idx_knowledge_status ON knowledge(status);
            CREATE INDEX IF NOT EXISTS idx_causal_from ON causal_relations(from_entity);
            CREATE INDEX IF NOT EXISTS idx_causal_to ON causal_relations(to_entity);
            CREATE INDEX IF NOT EXISTS idx_experiences_module ON experiences(module);
            CREATE INDEX IF NOT EXISTS idx_history_entity ON history(entity_id);
            ",
        )?;
        Ok(())
    }

    fn gen_id(prefix: &str) -> String {
        format!(
            "{}-{}-{}",
            prefix,
            now_unix(),
            &uuid::Uuid::new_v4().to_string()[..5]
        )
    }

    fn record_history(
        &self,
        entity_id: &str,
        entity_type: &str,
        version: u32,
        status: &str,
        content: &str,
        git_commit: Option<&str>,
        changed_by: &str,
        reason: Option<&str>,
    ) -> Result<()> {
        self.conn.execute(
            "INSERT INTO history (entity_id, entity_type, version, status, content, git_commit, changed_by, reason, timestamp)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                entity_id,
                entity_type,
                version,
                status,
                content,
                git_commit,
                changed_by,
                reason,
                now_unix() as i64,
            ],
        )?;
        Ok(())
    }
}

impl KnowledgeStore for LocalStorage {
    fn remember(&self, entry: KnowledgeEntry) -> Result<String> {
        let tags_json = serde_json::to_string(&entry.tags)?;

        // Dedup: same title + file → update
        let existing: Option<(String, u32)> = self
            .conn
            .query_row(
                "SELECT id, current_version FROM knowledge WHERE title = ?1 AND file IS ?2 AND status != 'expired'",
                params![entry.title, entry.file],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .ok();

        if let Some((existing_id, version)) = existing {
            let new_version = version + 1;
            self.conn.execute(
                "UPDATE knowledge SET content=?1, type=?2, tags=?3, module=?4, function=?5, git_commit=?6, current_version=?7, updated_at=?8 WHERE id=?9",
                params![
                    entry.content,
                    entry.entry_type,
                    tags_json,
                    entry.module,
                    entry.function,
                    entry.git_commit,
                    new_version,
                    now_unix() as i64,
                    existing_id,
                ],
            )?;

            let snapshot = serde_json::to_string(&serde_json::json!({
                "title": entry.title, "content": entry.content, "type": entry.entry_type,
                "module": entry.module, "file": entry.file, "tags": entry.tags,
            }))?;
            self.record_history(
                &existing_id, "knowledge", new_version, "active",
                &snapshot, entry.git_commit.as_deref(), "user", Some("updated"),
            )?;

            return Ok(existing_id);
        }

        // New entry
        let id = if entry.id.is_empty() {
            Self::gen_id("k")
        } else {
            entry.id.clone()
        };

        self.conn.execute(
            "INSERT INTO knowledge (id, type, title, content, module, file, function, tags, status, current_version, git_commit, created_at, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, 'active', 1, ?9, ?10, ?10)",
            params![
                id,
                entry.entry_type,
                entry.title,
                entry.content,
                entry.module,
                entry.file,
                entry.function,
                tags_json,
                entry.git_commit,
                now_unix() as i64,
            ],
        )?;

        let snapshot = serde_json::to_string(&serde_json::json!({
            "title": entry.title, "content": entry.content, "type": entry.entry_type,
            "module": entry.module, "file": entry.file, "tags": entry.tags,
        }))?;
        self.record_history(
            &id, "knowledge", 1, "active",
            &snapshot, entry.git_commit.as_deref(), "user", Some("initial creation"),
        )?;

        Ok(id)
    }

    fn recall(&self, query: RecallQuery) -> Result<Vec<KnowledgeEntry>> {
        let mut sql = String::from("SELECT id, type, title, content, module, file, function, tags, status, current_version, git_commit, created_at, updated_at FROM knowledge WHERE 1=1");
        let mut bind_values: Vec<Box<dyn rusqlite::types::ToSql>> = Vec::new();
        let mut param_idx = 1;

        if !query.include_stale {
            sql.push_str(&format!(" AND status != 'expired'"));
        }

        if let Some(ref module) = query.module {
            sql.push_str(&format!(" AND module = ?{}", param_idx));
            bind_values.push(Box::new(module.clone()));
            param_idx += 1;
        }

        if let Some(ref entry_type) = query.entry_type {
            sql.push_str(&format!(" AND type = ?{}", param_idx));
            bind_values.push(Box::new(entry_type.clone()));
            param_idx += 1;
        }

        if let Some(ref q) = query.query {
            sql.push_str(&format!(
                " AND (title LIKE ?{p} OR content LIKE ?{p} OR tags LIKE ?{p})",
                p = param_idx
            ));
            bind_values.push(Box::new(format!("%{}%", q)));
            param_idx += 1;
        }

        let _ = param_idx;
        sql.push_str(" ORDER BY updated_at DESC LIMIT 20");

        let bind_refs: Vec<&dyn rusqlite::types::ToSql> = bind_values.iter().map(|b| b.as_ref()).collect();
        let mut stmt = self.conn.prepare(&sql)?;
        let entries = stmt
            .query_map(bind_refs.as_slice(), |row| {
                let tags_str: String = row.get(7)?;
                let tags: Vec<String> =
                    serde_json::from_str(&tags_str).unwrap_or_default();
                Ok(KnowledgeEntry {
                    id: row.get(0)?,
                    entry_type: row.get(1)?,
                    title: row.get(2)?,
                    content: row.get(3)?,
                    module: row.get(4)?,
                    file: row.get(5)?,
                    function: row.get(6)?,
                    tags,
                    status: row.get(8)?,
                    current_version: row.get(9)?,
                    git_commit: row.get(10)?,
                    created_at: row.get(11)?,
                    updated_at: row.get(12)?,
                })
            })?
            .filter_map(|r| r.ok())
            .collect();

        Ok(entries)
    }

    fn forget(&self, id: &str) -> Result<()> {
        // Get current version
        let (version, title): (u32, String) = self
            .conn
            .query_row(
                "SELECT current_version, title FROM knowledge WHERE id = ?1",
                params![id],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .context("Knowledge entry not found")?;

        let new_version = version + 1;
        self.conn.execute(
            "UPDATE knowledge SET status = 'expired', current_version = ?1, updated_at = ?2 WHERE id = ?3",
            params![new_version, now_unix() as i64, id],
        )?;

        self.record_history(
            id, "knowledge", new_version, "expired",
            &format!("{{\"title\":\"{}\"}}", title),
            None, "user", Some("user deleted"),
        )?;

        Ok(())
    }

    fn add_causal_relation(&self, relation: CausalRelation) -> Result<String> {
        let id = if relation.id.is_empty() {
            Self::gen_id("cr")
        } else {
            relation.id.clone()
        };

        self.conn.execute(
            "INSERT OR REPLACE INTO causal_relations (id, from_entity, to_entity, relation_type, description, confidence, git_commit, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                id,
                relation.from_entity,
                relation.to_entity,
                relation.relation_type,
                relation.description,
                relation.confidence,
                relation.git_commit,
                now_unix() as i64,
            ],
        )?;

        Ok(id)
    }

    fn find_causal_chain(&self, entity: &str, max_depth: u32) -> Result<Vec<CausalChainNode>> {
        // BFS through causal_relations
        let mut visited = HashSet::new();
        let mut queue: VecDeque<(String, u32)> = VecDeque::new();
        let mut result = Vec::new();

        visited.insert(entity.to_string());
        queue.push_back((entity.to_string(), 0));

        while let Some((current, depth)) = queue.pop_front() {
            if depth >= max_depth {
                continue;
            }

            // Outgoing: current → ?
            let mut stmt = self.conn.prepare(
                "SELECT to_entity, relation_type, description FROM causal_relations WHERE from_entity = ?1",
            )?;
            let outgoing: Vec<(String, String, Option<String>)> = stmt
                .query_map(params![current], |row| {
                    Ok((row.get(0)?, row.get(1)?, row.get(2)?))
                })?
                .filter_map(|r| r.ok())
                .collect();

            for (target, rel_type, desc) in outgoing {
                if visited.insert(target.clone()) {
                    result.push(CausalChainNode {
                        entity: target.clone(),
                        relation: rel_type,
                        description: desc,
                        depth: depth + 1,
                    });
                    queue.push_back((target, depth + 1));
                }
            }

            // Incoming: ? → current
            let mut stmt = self.conn.prepare(
                "SELECT from_entity, relation_type, description FROM causal_relations WHERE to_entity = ?1",
            )?;
            let incoming: Vec<(String, String, Option<String>)> = stmt
                .query_map(params![current], |row| {
                    Ok((row.get(0)?, row.get(1)?, row.get(2)?))
                })?
                .filter_map(|r| r.ok())
                .collect();

            for (source, rel_type, desc) in incoming {
                if visited.insert(source.clone()) {
                    result.push(CausalChainNode {
                        entity: source.clone(),
                        relation: format!("←{}", rel_type),
                        description: desc,
                        depth: depth + 1,
                    });
                    queue.push_back((source, depth + 1));
                }
            }
        }

        Ok(result)
    }

    fn get_constraints(&self, module: &str) -> Result<Vec<KnowledgeEntry>> {
        let mut stmt = self.conn.prepare(
            "SELECT id, type, title, content, module, file, function, tags, status, current_version, git_commit, created_at, updated_at
             FROM knowledge WHERE type = 'constraint' AND module = ?1 AND status != 'expired'
             ORDER BY updated_at DESC",
        )?;

        let entries = stmt
            .query_map(params![module], |row| {
                let tags_str: String = row.get(7)?;
                let tags: Vec<String> = serde_json::from_str(&tags_str).unwrap_or_default();
                Ok(KnowledgeEntry {
                    id: row.get(0)?,
                    entry_type: row.get(1)?,
                    title: row.get(2)?,
                    content: row.get(3)?,
                    module: row.get(4)?,
                    file: row.get(5)?,
                    function: row.get(6)?,
                    tags,
                    status: row.get(8)?,
                    current_version: row.get(9)?,
                    git_commit: row.get(10)?,
                    created_at: row.get(11)?,
                    updated_at: row.get(12)?,
                })
            })?
            .filter_map(|r| r.ok())
            .collect();

        Ok(entries)
    }

    fn record_experience(&self, exp: Experience) -> Result<String> {
        let id = if exp.id.is_empty() {
            Self::gen_id("exp")
        } else {
            exp.id.clone()
        };

        let tags_json = serde_json::to_string(&exp.tags)?;
        self.conn.execute(
            "INSERT INTO experiences (id, module, symptom, cause, fix, constraint_note, tags, status, git_commit, created_at, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 'active', ?8, ?9, ?9)",
            params![
                id,
                exp.module,
                exp.symptom,
                exp.cause,
                exp.fix,
                exp.constraint_note,
                tags_json,
                exp.git_commit,
                now_unix() as i64,
            ],
        )?;

        let snapshot = serde_json::to_string(&serde_json::json!({
            "symptom": exp.symptom, "cause": exp.cause, "fix": exp.fix,
            "module": exp.module, "constraint": exp.constraint_note,
        }))?;
        self.record_history(
            &id, "experience", 1, "active",
            &snapshot, exp.git_commit.as_deref(), "user", Some("initial creation"),
        )?;

        Ok(id)
    }

    fn search_symptom(&self, symptom: &str) -> Result<Vec<Experience>> {
        let pattern = format!("%{}%", symptom);
        let mut stmt = self.conn.prepare(
            "SELECT id, module, symptom, cause, fix, constraint_note, tags, status, git_commit, created_at, updated_at
             FROM experiences WHERE (symptom LIKE ?1 OR cause LIKE ?1) AND status != 'expired'
             ORDER BY updated_at DESC LIMIT 10",
        )?;

        let entries = stmt
            .query_map(params![pattern], |row| {
                let tags_str: String = row.get(6)?;
                let tags: Vec<String> = serde_json::from_str(&tags_str).unwrap_or_default();
                Ok(Experience {
                    id: row.get(0)?,
                    module: row.get(1)?,
                    symptom: row.get(2)?,
                    cause: row.get(3)?,
                    fix: row.get(4)?,
                    constraint_note: row.get(5)?,
                    tags,
                    status: row.get(7)?,
                    git_commit: row.get(8)?,
                    created_at: row.get(9)?,
                    updated_at: row.get(10)?,
                })
            })?
            .filter_map(|r| r.ok())
            .collect();

        Ok(entries)
    }

    fn get_history(&self, entity_id: &str) -> Result<Vec<HistoryRecord>> {
        let mut stmt = self.conn.prepare(
            "SELECT entity_id, entity_type, version, status, content, git_commit, changed_by, reason, timestamp
             FROM history WHERE entity_id = ?1 ORDER BY version ASC",
        )?;

        let records = stmt
            .query_map(params![entity_id], |row| {
                Ok(HistoryRecord {
                    entity_id: row.get(0)?,
                    entity_type: row.get(1)?,
                    version: row.get(2)?,
                    status: row.get(3)?,
                    content: row.get(4)?,
                    git_commit: row.get(5)?,
                    changed_by: row.get(6)?,
                    reason: row.get(7)?,
                    timestamp: row.get(8)?,
                })
            })?
            .filter_map(|r| r.ok())
            .collect();

        Ok(records)
    }

    fn mark_stale(&self, file_path: &str, git_commit: &str) -> Result<u32> {
        let pattern = format!("%{}%", file_path);

        // Find active knowledge anchored to this file
        let mut stmt = self.conn.prepare(
            "SELECT id, current_version, title FROM knowledge WHERE file LIKE ?1 AND status = 'active'",
        )?;

        let entries: Vec<(String, u32, String)> = stmt
            .query_map(params![pattern], |row| {
                Ok((row.get(0)?, row.get(1)?, row.get(2)?))
            })?
            .filter_map(|r| r.ok())
            .collect();

        let count = entries.len() as u32;

        for (id, version, title) in entries {
            let new_version = version + 1;
            self.conn.execute(
                "UPDATE knowledge SET status = 'stale', current_version = ?1, updated_at = ?2 WHERE id = ?3",
                params![new_version, now_unix() as i64, id],
            )?;

            self.record_history(
                &id, "knowledge", new_version, "stale",
                &format!("{{\"title\":\"{}\"}}", title),
                Some(git_commit), "git-hook",
                Some(&format!("file changed: {}", file_path)),
            )?;
        }

        Ok(count)
    }
}

fn now_unix() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs()
}
