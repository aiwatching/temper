//! Auto-extraction — LLM-based fact extraction from conversation context.
//! Inspired by Mem0's extraction prompts, adapted for code knowledge.

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};

/// Extraction prompt for code-related knowledge.
/// Focused on constraints, decisions, causal relations, and experiences.
const EXTRACT_PROMPT: &str = r#"You are a code knowledge extractor. Analyze the following conversation/code change context and extract ONLY explicitly stated or clearly implied knowledge.

Extract into these categories:

1. **constraints**: Rules that MUST be followed. "Don't do X because Y", "Must use Z", "Never change W without updating V"
2. **decisions**: Architecture/design choices and WHY they were made. "We chose X over Y because Z"
3. **causal_relations**: Cause-and-effect chains. "Changing X triggers Y", "X depends on Y"
4. **experiences**: Problems encountered and how they were solved. Symptom → Cause → Fix

Output as JSON:
{
  "constraints": [
    {"title": "short summary", "content": "detailed explanation", "file": "path/if/applicable", "module": "module/if/applicable"}
  ],
  "decisions": [
    {"title": "short summary", "content": "detailed explanation with reasoning"}
  ],
  "causal_relations": [
    {"from": "entity A", "to": "entity B", "type": "triggers|causes|affects|depends_on", "description": "why"}
  ],
  "experiences": [
    {"symptom": "what was observed", "cause": "root cause", "fix": "how it was fixed", "constraint_note": "what to watch for next time"}
  ]
}

Rules:
- Extract ONLY from the provided context, do NOT invent facts
- If nothing worth extracting, return empty arrays
- For code-related facts, include file paths when mentioned
- Be precise: "UserDAO must not cache" not "caching should be avoided"
- A fact must be actionable or important for future work
"#;

/// Prompt for extracting entity relations from code changes.
const EXTRACT_RELATIONS_PROMPT: &str = r#"Extract entity relationships from the following code context.

Output as JSON array:
[
  {"source": "entity1", "relationship": "type", "destination": "entity2", "description": "why"}
]

Relationship types: triggers, causes, affects, depends_on, implements, extends, constrains

Rules:
- Entities should be specific: class names, module names, config keys — not generic terms
- Only extract explicitly stated relationships
- Normalize entity names: lowercase, consistent naming
"#;

#[derive(Debug, Serialize, Deserialize)]
pub struct ExtractionResult {
    #[serde(default)]
    pub constraints: Vec<ExtractedFact>,
    #[serde(default)]
    pub decisions: Vec<ExtractedFact>,
    #[serde(default)]
    pub causal_relations: Vec<ExtractedRelation>,
    #[serde(default)]
    pub experiences: Vec<ExtractedExperience>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ExtractedFact {
    pub title: String,
    pub content: String,
    #[serde(default)]
    pub file: Option<String>,
    #[serde(default)]
    pub module: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ExtractedRelation {
    pub from: String,
    pub to: String,
    #[serde(rename = "type")]
    pub relation_type: String,
    #[serde(default)]
    pub description: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ExtractedExperience {
    pub symptom: String,
    pub cause: String,
    pub fix: String,
    #[serde(default)]
    pub constraint_note: Option<String>,
}

/// Call LLM API to extract knowledge from conversation context.
pub fn auto_extract(
    context: &str,
    api_endpoint: &str,
    api_key: &str,
    model: &str,
) -> Result<ExtractionResult> {
    let client = reqwest::blocking::Client::new();

    let body = serde_json::json!({
        "model": model,
        "messages": [
            {"role": "system", "content": EXTRACT_PROMPT},
            {"role": "user", "content": context}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1
    });

    let response = client
        .post(api_endpoint)
        .header("Authorization", format!("Bearer {}", api_key))
        .header("Content-Type", "application/json")
        .json(&body)
        .send()
        .context("Failed to call LLM API for extraction")?;

    if !response.status().is_success() {
        let status = response.status();
        let text = response.text().unwrap_or_default();
        anyhow::bail!("LLM API error {}: {}", status, text);
    }

    let resp: serde_json::Value = response.json()?;
    let content = resp["choices"][0]["message"]["content"]
        .as_str()
        .context("No content in LLM response")?;

    // Parse JSON, handle markdown code blocks
    let clean = content
        .trim()
        .trim_start_matches("```json")
        .trim_start_matches("```")
        .trim_end_matches("```")
        .trim();

    let result: ExtractionResult = serde_json::from_str(clean)
        .with_context(|| format!("Failed to parse extraction result: {}", clean))?;

    Ok(result)
}

/// Extract entity relations from code context.
pub fn extract_relations(
    context: &str,
    api_endpoint: &str,
    api_key: &str,
    model: &str,
) -> Result<Vec<ExtractedRelation>> {
    let client = reqwest::blocking::Client::new();

    let body = serde_json::json!({
        "model": model,
        "messages": [
            {"role": "system", "content": EXTRACT_RELATIONS_PROMPT},
            {"role": "user", "content": context}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1
    });

    let response = client
        .post(api_endpoint)
        .header("Authorization", format!("Bearer {}", api_key))
        .header("Content-Type", "application/json")
        .json(&body)
        .send()?;

    if !response.status().is_success() {
        return Ok(Vec::new());
    }

    let resp: serde_json::Value = response.json()?;
    let content = resp["choices"][0]["message"]["content"]
        .as_str()
        .unwrap_or("[]");

    let clean = content
        .trim()
        .trim_start_matches("```json")
        .trim_start_matches("```")
        .trim_end_matches("```")
        .trim();

    // Try parsing as array or as object with "relations" key
    if let Ok(relations) = serde_json::from_str::<Vec<ExtractedRelation>>(clean) {
        return Ok(relations);
    }

    if let Ok(obj) = serde_json::from_str::<serde_json::Value>(clean) {
        if let Some(arr) = obj.get("relations").or(obj.get("relationships")) {
            if let Ok(relations) = serde_json::from_value::<Vec<ExtractedRelation>>(arr.clone()) {
                return Ok(relations);
            }
        }
    }

    Ok(Vec::new())
}
