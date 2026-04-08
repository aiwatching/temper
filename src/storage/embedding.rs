use anyhow::{Context, Result};
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};

use crate::config::GlobalConfig;

/// Embedding client — calls external API to generate embeddings.
pub struct EmbeddingClient {
    endpoint: String,
    model: String,
    api_key: Option<String>,
    provider: String,
}

#[derive(Debug, Serialize)]
struct EmbeddingRequest {
    model: String,
    input: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct EmbeddingResponse {
    data: Vec<EmbeddingData>,
}

#[derive(Debug, Deserialize)]
struct EmbeddingData {
    embedding: Vec<f32>,
}

impl EmbeddingClient {
    pub fn from_config() -> Result<Option<Self>> {
        let config = GlobalConfig::load_or_default()?;

        // Check if API key is available
        let api_key = std::env::var(&config.embedding.api_key_env).ok();
        if api_key.is_none() {
            return Ok(None); // No API key — embedding disabled
        }

        Ok(Some(Self {
            endpoint: config.embedding.endpoint,
            model: config.embedding.model,
            api_key,
            provider: config.embedding.provider,
        }))
    }

    /// Generate embedding for a text string.
    pub fn embed(&self, text: &str) -> Result<Vec<f32>> {
        let texts = vec![text.to_string()];
        let mut results = self.embed_batch(&texts)?;
        results
            .pop()
            .context("Empty embedding response")
    }

    /// Generate embeddings for multiple texts.
    pub fn embed_batch(&self, texts: &[String]) -> Result<Vec<Vec<f32>>> {
        let api_key = self
            .api_key
            .as_ref()
            .context("No API key configured for embedding")?;

        let client = reqwest::blocking::Client::new();

        let request = EmbeddingRequest {
            model: self.model.clone(),
            input: texts.to_vec(),
        };

        let response = client
            .post(&self.endpoint)
            .header("Authorization", format!("Bearer {}", api_key))
            .header("Content-Type", "application/json")
            .json(&request)
            .send()
            .context("Failed to call embedding API")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().unwrap_or_default();
            anyhow::bail!("Embedding API error {}: {}", status, body);
        }

        let resp: EmbeddingResponse = response
            .json()
            .context("Failed to parse embedding API response")?;

        Ok(resp.data.into_iter().map(|d| d.embedding).collect())
    }
}

/// Store and search embeddings in SQLite.
pub struct EmbeddingStore<'a> {
    conn: &'a Connection,
}

impl<'a> EmbeddingStore<'a> {
    pub fn new(conn: &'a Connection) -> Self {
        Self { conn }
    }

    pub fn init_schema(&self) -> Result<()> {
        self.conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS embeddings (
                entity_id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                embedding TEXT NOT NULL,
                model TEXT,
                created_at INTEGER NOT NULL
            );"
        )?;
        Ok(())
    }

    /// Store an embedding for an entity.
    pub fn store(&self, entity_id: &str, entity_type: &str, embedding: &[f32], model: &str) -> Result<()> {
        let embedding_json = serde_json::to_string(embedding)?;
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)?
            .as_secs();

        self.conn.execute(
            "INSERT OR REPLACE INTO embeddings (entity_id, entity_type, embedding, model, created_at) VALUES (?1, ?2, ?3, ?4, ?5)",
            params![entity_id, entity_type, embedding_json, model, now as i64],
        )?;

        Ok(())
    }

    /// Semantic search: find top-K most similar entities.
    pub fn search(&self, query_embedding: &[f32], limit: usize) -> Result<Vec<(String, String, f32)>> {
        let mut stmt = self.conn.prepare(
            "SELECT entity_id, entity_type, embedding FROM embeddings"
        )?;

        let mut scored: Vec<(String, String, f32)> = stmt
            .query_map([], |row| {
                let id: String = row.get(0)?;
                let entity_type: String = row.get(1)?;
                let emb_json: String = row.get(2)?;
                Ok((id, entity_type, emb_json))
            })?
            .filter_map(|r| r.ok())
            .filter_map(|(id, entity_type, emb_json)| {
                let emb: Vec<f32> = serde_json::from_str(&emb_json).ok()?;
                let score = cosine_similarity(query_embedding, &emb);
                Some((id, entity_type, score))
            })
            .collect();

        scored.sort_by(|a, b| b.2.partial_cmp(&a.2).unwrap_or(std::cmp::Ordering::Equal));
        scored.truncate(limit);

        Ok(scored)
    }
}

/// Cosine similarity between two vectors.
fn cosine_similarity(a: &[f32], b: &[f32]) -> f32 {
    if a.len() != b.len() || a.is_empty() {
        return 0.0;
    }

    let mut dot = 0.0f32;
    let mut norm_a = 0.0f32;
    let mut norm_b = 0.0f32;

    for i in 0..a.len() {
        dot += a[i] * b[i];
        norm_a += a[i] * a[i];
        norm_b += b[i] * b[i];
    }

    let denom = norm_a.sqrt() * norm_b.sqrt();
    if denom == 0.0 {
        0.0
    } else {
        dot / denom
    }
}
