use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet, VecDeque};
use std::path::Path;

// --- Core Types ---

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CodeNode {
    pub id: String,
    pub node_type: NodeType,
    pub name: String,
    pub file_path: String,
    pub line: Option<u32>,
    pub exported: bool,
    pub module: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum NodeType {
    File,
    Function,
    Class,
    Variable,
}

impl std::fmt::Display for NodeType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            NodeType::File => write!(f, "file"),
            NodeType::Function => write!(f, "function"),
            NodeType::Class => write!(f, "class"),
            NodeType::Variable => write!(f, "variable"),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CodeEdge {
    pub from: String,
    pub to: String,
    pub edge_type: EdgeType,
    pub detail: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum EdgeType {
    Imports,
    Calls,
    Exports,
    DependsOn,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct CodeGraph {
    pub nodes: Vec<CodeNode>,
    pub edges: Vec<CodeEdge>,
    pub files: Vec<String>,
    pub scanned_at: u64,
}

// --- Graph Stats ---

pub struct GraphStats {
    pub nodes: usize,
    pub edges: usize,
    pub files: usize,
    pub functions: usize,
    pub classes: usize,
    pub variables: usize,
    pub import_edges: usize,
    pub call_edges: usize,
    pub export_edges: usize,
}

// --- Search Results ---

pub struct SearchResult {
    pub direct_matches: Vec<CodeNode>,
    pub impact_chain: Vec<ImpactNode>,
}

pub struct ImpactNode {
    pub node: CodeNode,
    pub depth: u32,
}

// --- Meta ---

#[derive(Debug, Serialize, Deserialize)]
pub struct Meta {
    pub project_path: String,
    pub last_scan_commit: Option<String>,
    pub last_scan_at: Option<u64>,
    pub node_count: Option<usize>,
    pub edge_count: Option<usize>,
}

impl Meta {
    pub fn new(project_path: &Path) -> Self {
        Self {
            project_path: project_path.to_string_lossy().to_string(),
            last_scan_commit: None,
            last_scan_at: None,
            node_count: None,
            edge_count: None,
        }
    }

    pub fn load(path: &Path) -> Result<Self> {
        let content = std::fs::read_to_string(path)
            .with_context(|| format!("Failed to read meta: {}", path.display()))?;
        serde_json::from_str(&content).with_context(|| "Failed to parse meta.json")
    }

    pub fn save(&self, path: &Path) -> Result<()> {
        let content = serde_json::to_string_pretty(self)?;
        std::fs::write(path, content)?;
        Ok(())
    }

    pub fn update_after_scan(
        &mut self,
        scanner: &crate::parser::Scanner,
        graph: &CodeGraph,
    ) {
        self.last_scan_commit = scanner.head_commit();
        self.last_scan_at = Some(now_unix());
        self.node_count = Some(graph.nodes.len());
        self.edge_count = Some(graph.edges.len());
    }
}

// --- CodeGraph Implementation ---

impl CodeGraph {
    pub fn new() -> Self {
        Self {
            nodes: Vec::new(),
            edges: Vec::new(),
            files: Vec::new(),
            scanned_at: now_unix(),
        }
    }

    /// Load graph — tries binary first (fast), falls back to JSON (compatible).
    pub fn load(path: &Path) -> Result<Self> {
        let bin_path = path.with_extension("bin");
        if bin_path.exists() {
            let data = std::fs::read(&bin_path)
                .with_context(|| format!("Failed to read graph: {}", bin_path.display()))?;
            return bincode::deserialize(&data)
                .with_context(|| "Failed to parse graph.bin");
        }

        let content = std::fs::read_to_string(path)
            .with_context(|| format!("Failed to read graph: {}", path.display()))?;
        serde_json::from_str(&content).with_context(|| "Failed to parse graph.json")
    }

    /// Save graph — writes both binary (fast load) and JSON (human-readable).
    pub fn save(&self, path: &Path) -> Result<()> {
        // Binary format (fast)
        let bin_path = path.with_extension("bin");
        let bin_data = bincode::serialize(self)?;
        std::fs::write(&bin_path, bin_data)?;

        // JSON format (human-readable, backward compatible)
        let json_data = serde_json::to_string(self)?;
        std::fs::write(path, json_data)?;
        Ok(())
    }

    pub fn stats(&self) -> GraphStats {
        GraphStats {
            nodes: self.nodes.len(),
            edges: self.edges.len(),
            files: self.nodes.iter().filter(|n| n.node_type == NodeType::File).count(),
            functions: self.nodes.iter().filter(|n| n.node_type == NodeType::Function).count(),
            classes: self.nodes.iter().filter(|n| n.node_type == NodeType::Class).count(),
            variables: self.nodes.iter().filter(|n| n.node_type == NodeType::Variable).count(),
            import_edges: self.edges.iter().filter(|e| e.edge_type == EdgeType::Imports).count(),
            call_edges: self.edges.iter().filter(|e| e.edge_type == EdgeType::Calls).count(),
            export_edges: self.edges.iter().filter(|e| e.edge_type == EdgeType::Exports).count(),
        }
    }

    pub fn scanned_at_display(&self) -> String {
        chrono::DateTime::from_timestamp(self.scanned_at as i64, 0)
            .map(|dt| dt.format("%Y-%m-%d %H:%M:%S").to_string())
            .unwrap_or_else(|| "unknown".into())
    }

    /// Resolve import edge targets to actual file nodes.
    /// Handles extension resolution and Java package suffix matching.
    pub fn resolve_edges(&mut self) {
        let node_ids: HashSet<String> = self.nodes.iter().map(|n| n.id.clone()).collect();

        // Build suffix index for Java package imports
        let mut suffix_map: HashMap<String, String> = HashMap::new();
        for node in &self.nodes {
            if node.node_type != NodeType::File {
                continue;
            }
            let stripped = strip_extension(&node.id);
            suffix_map.insert(stripped.clone(), node.id.clone());
            let parts: Vec<&str> = stripped.split('/').collect();
            for i in 1..parts.len() {
                let suffix = parts[i..].join("/");
                suffix_map.entry(suffix).or_insert_with(|| node.id.clone());
            }
        }

        let extensions = [".ts", ".tsx", ".js", ".mjs", ".java", ".py", "/index.ts", "/index.js"];

        for edge in &mut self.edges {
            if node_ids.contains(&edge.to) {
                continue;
            }

            // Try direct extension resolution
            for ext in &extensions {
                let candidate = format!("{}{}", edge.to, ext);
                if node_ids.contains(&candidate) {
                    edge.to = candidate;
                    break;
                }
            }

            // Try suffix matching (Java package imports)
            if !node_ids.contains(&edge.to) {
                if let Some(resolved) = suffix_map.get(&edge.to) {
                    edge.to = resolved.clone();
                }
            }

            // Resolve call targets
            if !node_ids.contains(&edge.to) && edge.edge_type == EdgeType::Calls {
                if let Some((file_part, func_part)) = edge.to.split_once("::") {
                    for ext in &[".ts", ".tsx", ".js", ".mjs", ".java", ".py"] {
                        let candidate = format!("{}{}::{}", file_part, ext, func_part);
                        if node_ids.contains(&candidate) {
                            edge.to = candidate;
                            break;
                        }
                    }
                }
            }
        }

        // Deduplicate edges
        let mut seen = HashSet::new();
        self.edges.retain(|e| {
            let key = format!("{}→{}→{:?}", e.from, e.to, e.edge_type);
            seen.insert(key)
        });
    }

    /// Search the graph for nodes matching a query, then BFS for impact chain.
    pub fn search(&self, query: &str) -> SearchResult {
        let terms: Vec<String> = query.to_lowercase().split_whitespace()
            .map(|s| s.to_string())
            .collect();

        // Score nodes
        let mut scored: Vec<(usize, &CodeNode)> = self.nodes.iter()
            .filter_map(|node| {
                let score = match_node(node, &terms);
                if score > 0 { Some((score, node)) } else { None }
            })
            .collect();

        scored.sort_by(|a, b| b.0.cmp(&a.0));

        // Tier 1: nodes matching ALL terms
        let max_terms = terms.len();
        let mut direct_matches: Vec<CodeNode> = scored.iter()
            .filter(|(score, _)| *score >= max_terms)
            .map(|(_, node)| (*node).clone())
            .collect();

        // Tier 2: if none match all, take best scores
        if direct_matches.is_empty() {
            if let Some((best_score, _)) = scored.first() {
                let best = *best_score;
                direct_matches = scored.iter()
                    .filter(|(score, _)| *score == best)
                    .map(|(_, node)| (*node).clone())
                    .collect();
            }
        }

        // BFS impact chain (3 hops)
        let impact_chain = self.bfs_impact(&direct_matches, 3);

        SearchResult {
            direct_matches,
            impact_chain,
        }
    }

    fn bfs_impact(&self, start_nodes: &[CodeNode], max_depth: u32) -> Vec<ImpactNode> {
        let mut visited = HashSet::new();
        let mut result = Vec::new();
        let mut queue: VecDeque<(String, u32)> = VecDeque::new();

        // Build adjacency lists for fast lookup
        let mut outgoing: HashMap<&str, Vec<&str>> = HashMap::new();
        let mut incoming: HashMap<&str, Vec<&str>> = HashMap::new();
        for edge in &self.edges {
            outgoing.entry(edge.from.as_str()).or_default().push(edge.to.as_str());
            incoming.entry(edge.to.as_str()).or_default().push(edge.from.as_str());
        }

        let node_map: HashMap<&str, &CodeNode> = self.nodes.iter()
            .map(|n| (n.id.as_str(), n))
            .collect();

        for node in start_nodes {
            visited.insert(node.id.clone());
            queue.push_back((node.id.clone(), 0));
        }

        while let Some((id, depth)) = queue.pop_front() {
            if depth >= max_depth {
                continue;
            }

            let next_depth = depth + 1;

            // Outgoing edges
            if let Some(targets) = outgoing.get(id.as_str()) {
                for &target in targets {
                    if visited.insert(target.to_string()) {
                        if let Some(node) = node_map.get(target) {
                            result.push(ImpactNode {
                                node: (*node).clone(),
                                depth: next_depth,
                            });
                        }
                        queue.push_back((target.to_string(), next_depth));
                    }
                }
            }

            // Incoming edges
            if let Some(sources) = incoming.get(id.as_str()) {
                for &source in sources {
                    if visited.insert(source.to_string()) {
                        if let Some(node) = node_map.get(source) {
                            result.push(ImpactNode {
                                node: (*node).clone(),
                                depth: next_depth,
                            });
                        }
                        queue.push_back((source.to_string(), next_depth));
                    }
                }
            }
        }

        result
    }
}

// --- Helpers ---

fn now_unix() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs()
}

fn strip_extension(path: &str) -> String {
    if let Some(pos) = path.rfind('.') {
        let ext = &path[pos..];
        if [".java", ".py", ".ts", ".tsx", ".js", ".mjs"].contains(&ext) {
            return path[..pos].to_string();
        }
    }
    path.to_string()
}

/// Split a camelCase/snake_case identifier into searchable words.
fn split_identifier(name: &str) -> String {
    let mut words = String::new();

    // Split camelCase: "scheduleAutoSync" → "schedule auto sync"
    let mut last_was_upper = false;
    let mut word_start = 0;
    let chars: Vec<char> = name.chars().collect();

    for i in 0..chars.len() {
        if chars[i].is_uppercase() && !last_was_upper && i > word_start {
            words.push_str(&name[word_start..i].to_lowercase());
            words.push(' ');
            word_start = i;
        }
        last_was_upper = chars[i].is_uppercase();
    }
    words.push_str(&name[word_start..].to_lowercase());

    // Also split on _ - . /
    let words = words
        .replace(['_', '-', '.', '/'], " ");

    format!("{} {}", words, name.to_lowercase())
}

/// Score how well a node matches the search terms.
fn match_node(node: &CodeNode, terms: &[String]) -> usize {
    let haystack = format!(
        "{} {} {}",
        split_identifier(&node.name),
        split_identifier(&node.file_path),
        node.module
    )
    .to_lowercase();

    let hay_words: Vec<&str> = haystack.split_whitespace().collect();

    let mut matched = 0;
    for term in terms {
        if haystack.contains(term.as_str())
            || hay_words.iter().any(|w| w.starts_with(term.as_str()) || term.starts_with(w))
        {
            matched += 1;
        }
    }
    matched
}
