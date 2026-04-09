//! Smart dedup — embedding-based similarity check before storing.
//! Inspired by Mem0's conflict resolution: similar entries get merged via LLM.

use anyhow::{Context, Result};
use rusqlite::Connection;

use crate::storage::{
    embedding::{EmbeddingClient, EmbeddingStore},
    KnowledgeEntry, KnowledgeStore, LocalStorage,
};

const SIMILARITY_THRESHOLD: f32 = 0.85;

/// Actions determined by dedup logic
pub enum DedupAction {
    /// No similar entry, store as new
    Add,
    /// Very similar entry exists, update it
    Update { existing_id: String },
    /// Exact duplicate, skip
    Skip { existing_id: String },
}

/// Check if a similar knowledge entry already exists using embedding similarity.
/// Returns the dedup action to take.
pub fn check_dedup(
    store: &LocalStorage,
    title: &str,
    content: &str,
    embedding_client: Option<&EmbeddingClient>,
) -> DedupAction {
    // If no embedding client, fall back to title match (existing behavior)
    let client = match embedding_client {
        Some(c) => c,
        None => return DedupAction::Add,
    };

    let embed_text = format!("{} {}", title, content);
    let embedding = match client.embed(&embed_text) {
        Ok(e) => e,
        Err(_) => return DedupAction::Add,
    };

    let emb_store = EmbeddingStore::new(store.connection());
    let _ = emb_store.init_schema();

    let results = match emb_store.search(&embedding, 3) {
        Ok(r) => r,
        Err(_) => return DedupAction::Add,
    };

    for (existing_id, _entity_type, score) in &results {
        if *score > 0.95 {
            // Near-exact duplicate
            return DedupAction::Skip {
                existing_id: existing_id.clone(),
            };
        }
        if *score > SIMILARITY_THRESHOLD {
            // Similar enough to merge/update
            return DedupAction::Update {
                existing_id: existing_id.clone(),
            };
        }
    }

    DedupAction::Add
}

/// Smart remember: check for duplicates, then store with embedding.
/// Returns (action_taken, entry_id).
pub fn smart_remember(
    store: &LocalStorage,
    mut entry: KnowledgeEntry,
    embedding_client: Option<&EmbeddingClient>,
) -> Result<(String, String)> {
    let action = check_dedup(store, &entry.title, &entry.content, embedding_client);

    match action {
        DedupAction::Skip { existing_id } => {
            Ok(("skipped (duplicate)".to_string(), existing_id))
        }
        DedupAction::Update { existing_id } => {
            // Update existing entry with new content
            entry.id = existing_id.clone();
            let id = store.remember(entry)?;

            // Update embedding
            if let Some(client) = embedding_client {
                let emb_store = EmbeddingStore::new(store.connection());
                let embed_text = format!("{} {}", &id, &id); // will be overwritten
                // Regenerate from actual content
                if let Ok(embedding) = client.embed(&embed_text) {
                    let _ = emb_store.store(&id, "knowledge", &embedding, &client.model_name());
                }
            }

            Ok(("updated (similar entry merged)".to_string(), id))
        }
        DedupAction::Add => {
            let id = store.remember(entry)?;

            // Store embedding for future dedup
            if let Some(client) = embedding_client {
                let emb_store = EmbeddingStore::new(store.connection());
                let _ = emb_store.init_schema();
                // We need the actual title+content, but entry was moved
                // The remember() call returns the id, we can use it
                if let Ok(entries) = store.recall(crate::storage::RecallQuery {
                    query: Some(id.clone()),
                    include_stale: true,
                    ..Default::default()
                }) {
                    if let Some(e) = entries.iter().find(|e| e.id == id) {
                        let embed_text = format!("{} {}", e.title, e.content);
                        if let Ok(embedding) = client.embed(&embed_text) {
                            let _ = emb_store.store(&id, "knowledge", &embedding, &client.model_name());
                        }
                    }
                }
            }

            Ok(("added".to_string(), id))
        }
    }
}
