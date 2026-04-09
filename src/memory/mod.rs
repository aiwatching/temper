//! Smart memory layer — Mem0-inspired auto-extraction, dedup, and selective recall.
//!
//! Combines Mem0's LLM-based intelligence with Temper's code-aware storage.

pub mod extract;
pub mod dedup;

pub use extract::auto_extract;
pub use dedup::smart_remember;
