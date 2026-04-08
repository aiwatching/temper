//! Storage abstraction layer.
//!
//! LocalStorage (SQLite) — current implementation
//! RemoteStorage (HTTP API → Central Server) — future
//! CachedRemoteStorage (HTTP + local cache) — future

pub mod embedding;
pub mod local;

use anyhow::Result;

pub use embedding::{EmbeddingClient, EmbeddingStore};
pub use local::LocalStorage;

/// Core trait for knowledge storage operations.
pub trait KnowledgeStore {
    fn remember(&self, entry: KnowledgeEntry) -> Result<String>;
    fn recall(&self, query: RecallQuery) -> Result<Vec<KnowledgeEntry>>;
    fn forget(&self, id: &str) -> Result<()>;

    fn add_causal_relation(&self, relation: CausalRelation) -> Result<String>;
    fn find_causal_chain(&self, entity: &str, max_depth: u32) -> Result<Vec<CausalChainNode>>;
    fn get_constraints(&self, module: &str) -> Result<Vec<KnowledgeEntry>>;

    fn record_experience(&self, exp: Experience) -> Result<String>;
    fn search_symptom(&self, symptom: &str) -> Result<Vec<Experience>>;

    fn get_history(&self, entity_id: &str) -> Result<Vec<HistoryRecord>>;
    fn mark_stale(&self, file_path: &str, git_commit: &str) -> Result<u32>;
}

// --- Data Types ---

#[derive(Debug, Clone)]
pub struct KnowledgeEntry {
    pub id: String,
    pub entry_type: String,
    pub title: String,
    pub content: String,
    pub module: Option<String>,
    pub file: Option<String>,
    pub function: Option<String>,
    pub tags: Vec<String>,
    pub status: String,
    pub current_version: u32,
    pub git_commit: Option<String>,
    pub created_at: u64,
    pub updated_at: u64,
}

#[derive(Debug, Clone, Default)]
pub struct RecallQuery {
    pub query: Option<String>,
    pub module: Option<String>,
    pub entry_type: Option<String>,
    pub include_stale: bool,
}

#[derive(Debug, Clone)]
pub struct CausalRelation {
    pub id: String,
    pub from_entity: String,
    pub to_entity: String,
    pub relation_type: String,
    pub description: Option<String>,
    pub confidence: String,
    pub git_commit: Option<String>,
    pub created_at: u64,
}

#[derive(Debug, Clone)]
pub struct CausalChainNode {
    pub entity: String,
    pub relation: String,
    pub description: Option<String>,
    pub depth: u32,
}

#[derive(Debug, Clone)]
pub struct Experience {
    pub id: String,
    pub module: Option<String>,
    pub symptom: String,
    pub cause: String,
    pub fix: String,
    pub constraint_note: Option<String>,
    pub tags: Vec<String>,
    pub status: String,
    pub git_commit: Option<String>,
    pub created_at: u64,
    pub updated_at: u64,
}

#[derive(Debug, Clone)]
pub struct HistoryRecord {
    pub entity_id: String,
    pub entity_type: String,
    pub version: u32,
    pub status: String,
    pub content: String,
    pub git_commit: Option<String>,
    pub changed_by: String,
    pub reason: Option<String>,
    pub timestamp: u64,
}
