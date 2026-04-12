use anyhow::{Context, Result};
use serde_json::json;
use std::collections::HashMap;
use std::path::Path;

use crate::graph::{CodeGraph, EdgeType, NodeType};
use crate::modules::ModuleRegistry;
use crate::storage::{KnowledgeStore, LocalStorage, RecallQuery};

/// Generate a self-contained interactive HTML dashboard.
pub fn export_html(project_path: &Path, output_dir: &Path) -> Result<()> {
    let temper_dir = project_path.join(".temper");

    let graph = CodeGraph::load(&temper_dir.join("graph.json"))
        .context("No code graph found. Run `temper scan` first.")?;

    let registry = ModuleRegistry::new(&temper_dir, graph.files.clone());
    let modules = registry.list_modules()?;

    let db_path = temper_dir.join("knowledge.db");
    let knowledge = if db_path.exists() {
        let store = LocalStorage::open(&db_path)?;
        store.recall(RecallQuery {
            include_stale: true,
            ..Default::default()
        })?
    } else {
        Vec::new()
    };

    let stats = graph.stats();

    // --- Build file-to-module lookup ---
    let mut file_to_module: HashMap<String, String> = HashMap::new();
    for m in &modules {
        if let Ok(files) = registry.resolve_files(m) {
            for f in files {
                file_to_module.insert(f, m.name.clone());
            }
        }
    }

    // --- Build file node index (must come before search_index) ---
    let file_nodes: Vec<&crate::graph::CodeNode> = graph
        .nodes
        .iter()
        .filter(|n| n.node_type == NodeType::File)
        .collect();

    let mut file_id_to_idx: HashMap<String, u32> = HashMap::new();
    for (idx, node) in file_nodes.iter().enumerate() {
        file_id_to_idx.insert(node.id.clone(), idx as u32);
    }

    let file_paths: Vec<&str> = file_nodes.iter().map(|n| n.file_path.as_str()).collect();

    let file_modules: Vec<String> = file_nodes
        .iter()
        .map(|n| {
            file_to_module
                .get(&n.file_path)
                .cloned()
                .unwrap_or_else(|| "_unassigned".to_string())
        })
        .collect();

    // Reverse adjacency: reverse_adj[i] = [parent indices]
    let mut reverse_adj: Vec<Vec<u32>> = vec![Vec::new(); file_nodes.len()];
    for edge in graph.edges.iter().filter(|e| e.edge_type == EdgeType::Imports) {
        if let (Some(&from_idx), Some(&to_idx)) = (
            file_id_to_idx.get(&edge.from),
            file_id_to_idx.get(&edge.to),
        ) {
            reverse_adj[to_idx as usize].push(from_idx);
        }
    }

    // --- Build searchable node index (compact array format) ---
    // Schema: [name, type_code, file_idx, line_or_null, is_exported]
    // type_code: 0=file, 1=class, 2=function, 3=variable
    let search_index: Vec<serde_json::Value> = graph
        .nodes
        .iter()
        .filter(|n| {
            if n.node_type == NodeType::File {
                return true;
            }
            n.exported
        })
        .filter_map(|n| {
            let file_idx = file_id_to_idx.get(n.id.split("::").next()?)?;
            let type_code: u8 = match n.node_type {
                NodeType::File => 0,
                NodeType::Class => 1,
                NodeType::Function => 2,
                NodeType::Variable => 3,
            };
            Some(json!([n.name, type_code, *file_idx, n.line, n.exported]))
        })
        .collect();

    // --- Dependency hotspots (top 30 most-imported files) ---
    let mut incoming_count: HashMap<String, u32> = HashMap::new();
    for edge in graph.edges.iter().filter(|e| e.edge_type == EdgeType::Imports) {
        *incoming_count.entry(edge.to.clone()).or_insert(0) += 1;
    }

    let mut hotspots: Vec<_> = incoming_count
        .iter()
        .filter_map(|(id, count)| {
            graph
                .nodes
                .iter()
                .find(|n| &n.id == id && n.node_type == NodeType::File)
                .map(|n| {
                    let module = file_to_module
                        .get(&n.file_path)
                        .cloned()
                        .unwrap_or_else(|| "_unassigned".to_string());
                    (n.file_path.clone(), *count, module)
                })
        })
        .collect();
    hotspots.sort_by(|a, b| b.1.cmp(&a.1));
    let hotspot_rows: Vec<serde_json::Value> = hotspots
        .iter()
        .take(30)
        .map(|(file, count, module)| {
            json!({
                "file": file,
                "count": count,
                "module": module,
            })
        })
        .collect();

    // --- Module dependency graph (not file-level) ---
    // Build module→module edges from file imports
    let mut module_edges: HashMap<(String, String), u32> = HashMap::new();
    for edge in graph.edges.iter().filter(|e| e.edge_type == EdgeType::Imports) {
        let from_mod = graph.nodes.iter().find(|n| n.id == edge.from)
            .and_then(|n| file_to_module.get(&n.file_path));
        let to_mod = graph.nodes.iter().find(|n| n.id == edge.to)
            .and_then(|n| file_to_module.get(&n.file_path));
        if let (Some(f), Some(t)) = (from_mod, to_mod) {
            if f != t {
                *module_edges.entry((f.clone(), t.clone())).or_insert(0) += 1;
            }
        }
    }

    // Vis.js module nodes
    let vis_nodes: Vec<serde_json::Value> = modules
        .iter()
        .map(|m| {
            let fc = registry.file_count(m);
            json!({
                "id": m.name,
                "label": format!("{}\n({} files)", m.name, fc),
                "shape": "box",
                "color": { "background": "#1f6feb", "border": "#58a6ff" },
                "font": { "color": "#fff", "size": 14 },
                "value": fc,
            })
        })
        .collect();

    let vis_edges: Vec<serde_json::Value> = module_edges
        .iter()
        .filter(|(_, count)| **count >= 2) // prune weak edges
        .map(|((from, to), count)| {
            json!({
                "from": from,
                "to": to,
                "arrows": "to",
                "value": count,
                "label": format!("{}", count),
                "color": { "color": "#58a6ff66" },
                "font": { "color": "#8b949e", "size": 10 },
            })
        })
        .collect();

    // --- Module table rows ---
    let module_rows: Vec<serde_json::Value> = modules
        .iter()
        .map(|m| {
            let file_count = registry.file_count(m);
            let knowledge_count = knowledge
                .iter()
                .filter(|k| k.module.as_deref() == Some(&m.name))
                .count();
            json!({
                "name": m.name,
                "description": m.description,
                "files": file_count,
                "knowledge": knowledge_count,
                "tags": m.tags.join(", "),
            })
        })
        .collect();

    // --- Knowledge rows ---
    let knowledge_rows: Vec<serde_json::Value> = knowledge
        .iter()
        .map(|k| {
            json!({
                "id": k.id,
                "type": k.entry_type,
                "title": k.title,
                "content": k.content,
                "module": k.module,
                "file": k.file,
                "status": k.status,
                "tags": k.tags.join(", "),
            })
        })
        .collect();

    // --- Analysis data for new tabs ---
    let migration = crate::analysis::migration_progress(project_path, "*", "restructured");
    let dead = crate::analysis::dead_code(&graph, Some(&registry));
    let violations = crate::analysis::boundary_violations(&graph, &registry);
    let cohesion = crate::analysis::module_cohesion(&graph, &registry);

    let migration_json = serde_json::to_string(&migration)?;
    let dead_code_json = serde_json::to_string(&dead)?;
    let violations_json = serde_json::to_string(&violations)?;
    let cohesion_json = serde_json::to_string(&cohesion)?;

    let project_name = project_path
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_else(|| "project".into());

    let html = generate_html(
        &project_name,
        &stats,
        &serde_json::to_string(&vis_nodes)?,
        &serde_json::to_string(&vis_edges)?,
        &serde_json::to_string(&module_rows)?,
        &serde_json::to_string(&knowledge_rows)?,
        &serde_json::to_string(&hotspot_rows)?,
        &serde_json::to_string(&search_index)?,
        &serde_json::to_string(&file_paths)?,
        &serde_json::to_string(&file_modules)?,
        &serde_json::to_string(&reverse_adj)?,
        &migration_json,
        &dead_code_json,
        &violations_json,
        &cohesion_json,
    );

    std::fs::create_dir_all(output_dir)?;
    let output_path = output_dir.join("index.html");
    std::fs::write(&output_path, html)?;

    eprintln!("Exported to: {}", output_path.display());
    Ok(())
}

fn generate_html(
    project_name: &str,
    stats: &crate::graph::GraphStats,
    vis_nodes: &str,
    vis_edges: &str,
    module_rows: &str,
    knowledge_rows: &str,
    hotspot_rows: &str,
    search_index: &str,
    file_paths: &str,
    file_modules: &str,
    reverse_adj: &str,
    migration_json: &str,
    dead_code_json: &str,
    violations_json: &str,
    cohesion_json: &str,
) -> String {
    format!(
        r#"<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Temper — {project_name}</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,monospace;background:#0d1117;color:#c9d1d9}}
.header{{background:#161b22;padding:16px 24px;border-bottom:1px solid #30363d;display:flex;justify-content:space-between;align-items:center}}
.header h1{{font-size:18px;color:#58a6ff}}
.stats{{display:flex;gap:16px;font-size:12px;color:#8b949e}}
.stats b{{color:#c9d1d9}}
.tabs{{background:#161b22;display:flex;border-bottom:1px solid #30363d;padding:0 24px}}
.tab{{padding:10px 16px;cursor:pointer;color:#8b949e;border-bottom:2px solid transparent;font-size:13px}}
.tab:hover{{color:#c9d1d9}}
.tab.active{{color:#58a6ff;border-bottom-color:#58a6ff}}
.content{{padding:20px 24px;max-width:1600px}}
.panel{{display:none}}.panel.active{{display:block}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{text-align:left;padding:6px 10px;background:#161b22;color:#8b949e;border-bottom:1px solid #30363d;position:sticky;top:0;font-size:11px;text-transform:uppercase;letter-spacing:.5px}}
td{{padding:6px 10px;border-bottom:1px solid #21262d;vertical-align:top}}
tr:hover{{background:#161b22}}
tr.clickable{{cursor:pointer}}
.badge{{display:inline-block;padding:1px 8px;border-radius:10px;font-size:10px;font-weight:600}}
.b-constraint{{background:#da363322;color:#f85149;border:1px solid #da363344}}
.b-decision{{background:#1f6feb22;color:#58a6ff;border:1px solid #1f6feb44}}
.b-note{{background:#8b949e22;color:#8b949e;border:1px solid #8b949e44}}
.b-experience{{background:#23863622;color:#3fb950;border:1px solid #23863644}}
.b-bug{{background:#d2992222;color:#d29922;border:1px solid #d2992244}}
.b-active{{background:#23863622;color:#3fb950}}
.b-stale{{background:#d2992222;color:#d29922}}
.b-file{{background:#1f6feb22;color:#58a6ff}}
.b-class{{background:#23863622;color:#3fb950}}
.b-function{{background:#8b949e22;color:#8b949e}}
.search-box{{margin-bottom:15px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}}
.search-box input{{background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:8px 12px;border-radius:6px;width:360px;font-size:13px;font-family:inherit}}
.search-box input:focus{{outline:none;border-color:#58a6ff}}
.search-box select{{background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:8px 12px;border-radius:6px;font-size:13px}}
.search-box button{{background:#238636;border:none;color:#fff;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600}}
.search-box button:hover{{background:#2ea043}}
.impact-results{{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:16px;margin-top:12px}}
.impact-results h3{{font-size:13px;color:#c9d1d9;margin-bottom:8px;border-bottom:1px solid #21262d;padding-bottom:6px}}
.impact-results .stats-row{{display:flex;gap:20px;font-size:12px;color:#8b949e;margin-bottom:12px}}
.impact-results .stats-row b{{color:#58a6ff}}
.module-group{{margin-top:12px;padding:10px;background:#0d1117;border-radius:6px}}
.module-group .mod-name{{font-weight:600;color:#58a6ff;font-size:13px;margin-bottom:6px}}
.module-group ul{{list-style:none;margin-left:8px}}
.module-group li{{font-size:11px;color:#8b949e;padding:2px 0;font-family:Consolas,monospace}}
.module-group li .depth{{color:#484f58;margin-right:6px}}
.risk-bar{{display:inline-block;width:200px;height:8px;background:#21262d;border-radius:4px;overflow:hidden;vertical-align:middle}}
.risk-bar .fill{{height:100%;background:linear-gradient(90deg,#3fb950,#d29922,#f85149)}}
.depth-hist{{background:#0d1117;padding:10px 14px;border-radius:6px;margin-top:12px}}
.hist-row{{display:flex;align-items:center;gap:10px;padding:3px 0;font-size:11px}}
.hist-label{{color:#8b949e;font-family:Consolas,monospace;width:60px}}
.hist-bar{{flex:1;height:10px;background:#21262d;border-radius:3px;overflow:hidden;max-width:400px}}
.hist-fill{{height:100%;background:linear-gradient(90deg,#1f6feb,#58a6ff)}}
.hist-count{{color:#58a6ff;font-family:Consolas,monospace;font-weight:600;width:40px;text-align:right}}
.chain-link{{cursor:pointer;transition:background 0.1s}}
.chain-link:hover{{background:#1f6feb22;border-radius:3px}}
.chain-viz{{background:#0d1117;padding:12px;border-radius:6px;margin-top:8px}}
.chain-step{{display:flex;align-items:center;gap:12px;padding:8px 12px;background:#161b22;border-radius:6px;border:1px solid #21262d;margin:2px 0}}
.chain-source{{border-color:#da3633;background:#da363311}}
.chain-target{{border-color:#3fb950;background:#23863611}}
.chain-role{{font-size:11px;color:#8b949e;width:90px;font-weight:600}}
.chain-path{{flex:1;font-size:11px}}
.chain-mod{{font-size:10px}}
.chain-arrow{{text-align:center;color:#58a6ff;font-size:10px;padding:2px 0}}
.search-box button{{background:#238636;border:none;color:#fff;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600}}
.search-box button:hover{{background:#2ea043}}
#graph-container{{width:100%;height:600px;border:1px solid #30363d;border-radius:6px;background:#0d1117}}
.hint{{font-size:12px;color:#8b949e;margin-top:4px}}
.mono{{font-family:Consolas,monospace;font-size:11px}}
.dim{{color:#484f58}}
</style>
</head>
<body>

<div class="header">
  <h1>Temper — {project_name}</h1>
  <div class="stats">
    Files: <b>{files}</b> &nbsp;
    Functions: <b>{functions}</b> &nbsp;
    Classes: <b>{classes}</b> &nbsp;
    Edges: <b>{edges}</b>
  </div>
</div>

<div class="tabs">
  <div class="tab active" data-tab="impact">Impact Explorer</div>
  <div class="tab" data-tab="hotspots">Hotspots</div>
  <div class="tab" data-tab="graph">Module Graph</div>
  <div class="tab" data-tab="migration">Migration</div>
  <div class="tab" data-tab="boundary">Boundaries</div>
  <div class="tab" data-tab="cohesion">Cohesion</div>
  <div class="tab" data-tab="deadcode">Dead Code</div>
  <div class="tab" data-tab="modules">Modules</div>
  <div class="tab" data-tab="knowledge">Knowledge</div>
</div>

<div class="content">

  <!-- Impact Explorer -->
  <div id="panel-impact" class="panel active">
    <div class="search-box">
      <input type="text" id="impact-query" placeholder="Enter a class, function, or file name (e.g., UserDAO, HostRecord)..." onkeydown="if(event.key==='Enter')runImpact()">
      <select id="impact-depth">
        <option value="1">Depth 1</option>
        <option value="2">Depth 2</option>
        <option value="3" selected>Depth 3</option>
        <option value="5">Depth 5</option>
      </select>
      <button onclick="runImpact()">Analyze</button>
    </div>
    <div class="hint">Find a symbol and see which files/modules depend on it. This is what breaks if you change it.</div>
    <div id="impact-results"></div>
  </div>

  <!-- Hotspots -->
  <div id="panel-hotspots" class="panel">
    <div class="hint">Most-imported files. Changing these has the largest blast radius.</div>
    <table style="margin-top:12px">
      <thead><tr><th>#</th><th>File</th><th>Module</th><th>Incoming Imports</th><th>Risk</th></tr></thead>
      <tbody id="hotspots-body"></tbody>
    </table>
  </div>

  <!-- Module Graph -->
  <div id="panel-graph" class="panel">
    <div class="hint">Module-level dependency graph. Edge thickness = number of cross-module imports. Weak edges (≤1) are pruned.</div>
    <div id="graph-container" style="margin-top:12px"></div>
  </div>

  <!-- Migration Progress -->
  <div id="panel-migration" class="panel">
    <div id="migration-content"></div>
  </div>

  <!-- Boundary Violations -->
  <div id="panel-boundary" class="panel">
    <div class="hint">Cross-module imports. Modules that import from many others are highly coupled.</div>
    <div id="boundary-content" style="margin-top:12px"></div>
  </div>

  <!-- Module Cohesion -->
  <div id="panel-cohesion" class="panel">
    <div class="hint">Cohesion ratio = internal imports / total imports. Higher = more self-contained.</div>
    <div id="cohesion-content" style="margin-top:12px"></div>
  </div>

  <!-- Dead Code -->
  <div id="panel-deadcode" class="panel">
    <div class="hint">Files with no incoming imports. May be entry points, tests, or unused code.</div>
    <div id="deadcode-content" style="margin-top:12px"></div>
  </div>

  <!-- Modules -->
  <div id="panel-modules" class="panel">
    <div class="search-box"><input type="text" placeholder="Filter modules..." oninput="filterTable('mod-body',this.value)"></div>
    <table>
      <thead><tr><th>Module</th><th>Files</th><th>Knowledge</th><th>Tags</th><th>Description</th></tr></thead>
      <tbody id="mod-body"></tbody>
    </table>
  </div>

  <!-- Knowledge -->
  <div id="panel-knowledge" class="panel">
    <div class="search-box"><input type="text" placeholder="Filter knowledge..." oninput="filterTable('know-body',this.value)"></div>
    <table>
      <thead><tr><th>Type</th><th>Status</th><th>Title</th><th>Module</th><th>File</th><th>Content</th></tr></thead>
      <tbody id="know-body"></tbody>
    </table>
  </div>

</div>

<script>
const visNodes = {vis_nodes};
const visEdges = {vis_edges};
const moduleRows = {module_rows};
const knowledgeRows = {knowledge_rows};
const hotspotRows = {hotspot_rows};
// searchIndex entries: [name, type_code, file_idx, line, is_exported]
// type_code: 0=file, 1=class, 2=function, 3=variable
const searchIndex = {search_index};
// filePaths[i] = path string
const filePaths = {file_paths};
// fileModules[i] = module name
const fileModules = {file_modules};
// reverseAdj[i] = [parent file indices]
const reverseAdj = {reverse_adj};

// Analysis data
const migrationData = {migration_json};
const deadCodeData = {dead_code_json};
const violationsData = {violations_json};
const cohesionData = {cohesion_json};

// ─── Tabs ───
function showTab(name, el) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  if (el) el.classList.add('active');
  else document.querySelector('[data-tab="' + name + '"]').classList.add('active');
  document.getElementById('panel-' + name).classList.add('active');
  if (name === 'graph' && !graphInit) initGraph();
}}

function filterTable(bodyId, q) {{
  const ql = q.toLowerCase();
  document.querySelectorAll('#' + bodyId + ' tr').forEach(r => {{
    r.style.display = r.textContent.toLowerCase().includes(ql) ? '' : 'none';
  }});
}}

function badge(type, text) {{
  return '<span class="badge b-' + type + '">' + (text || type) + '</span>';
}}

// ─── Impact Explorer ───
// searchIndex entries: [name, type_code, file_idx, line, is_exported]
const TYPE_LABELS = ['file', 'class', 'function', 'variable'];

// State shared across impact runs
let currentImpact = null;
let impactNetwork = null;

function runImpact() {{
  const query = document.getElementById('impact-query').value.trim().toLowerCase();
  const maxDepth = parseInt(document.getElementById('impact-depth').value);
  const out = document.getElementById('impact-results');

  if (!query) {{
    out.innerHTML = '<div class="hint">Enter a name to analyze.</div>';
    return;
  }}

  // Find matches
  const matches = searchIndex.filter(entry => {{
    const name = entry[0].toLowerCase();
    const fileIdx = entry[2];
    const path = (filePaths[fileIdx] || '').toLowerCase();
    return name.includes(query) || path.includes(query);
  }});

  if (matches.length === 0) {{
    out.innerHTML = '<div class="impact-results"><h3>No matches for "' + query + '"</h3></div>';
    return;
  }}

  // Sort: exported first, then by name length (shorter = more relevant usually)
  const direct = matches
    .sort((a, b) => {{
      if (a[4] !== b[4]) return b[4] ? 1 : -1;
      return a[0].length - b[0].length;
    }})
    .slice(0, 30);

  // Unique start file indices
  const startFiles = new Set();
  matches.forEach(entry => {{
    startFiles.add(entry[2]);
  }});

  // BFS with PARENT tracking so we can reconstruct chains
  // impacted: fidx → {{ depth, parent: fidx_or_null }}
  const impacted = new Map();
  const queue = [];
  for (const fidx of startFiles) {{
    queue.push(fidx);
    impacted.set(fidx, {{ depth: 0, parent: null }});
  }}

  while (queue.length > 0) {{
    const idx = queue.shift();
    const info = impacted.get(idx);
    if (info.depth >= maxDepth) continue;
    const parents = reverseAdj[idx] || [];
    for (const p of parents) {{
      if (!impacted.has(p)) {{
        impacted.set(p, {{ depth: info.depth + 1, parent: idx }});
        queue.push(p);
      }}
    }}
  }}

  // Save state for chain drill-down
  currentImpact = {{ startFiles, impacted, direct }};

  // Separate source vs impact
  const impactedFiles = new Map();
  for (const [fidx, info] of impacted) {{
    if (!startFiles.has(fidx)) impactedFiles.set(fidx, info);
  }}

  // Depth histogram
  const depthCounts = new Map();
  for (const [, info] of impactedFiles) {{
    depthCounts.set(info.depth, (depthCounts.get(info.depth) || 0) + 1);
  }}

  // Group by module
  const byModule = new Map();
  for (const [fidx, info] of impactedFiles) {{
    const mod = fileModules[fidx] || '_unassigned';
    if (!byModule.has(mod)) byModule.set(mod, []);
    byModule.get(mod).push({{ fidx, depth: info.depth }});
  }}

  const sortedModules = [...byModule.entries()].sort((a, b) => b[1].length - a[1].length);

  // ─── Build HTML ───
  let html = '<div class="impact-results">';
  html += '<h3>Results for "' + query + '"</h3>';
  html += '<div class="stats-row">';
  html += 'Matches: <b>' + matches.length + '</b>';
  html += 'Source files: <b>' + startFiles.size + '</b>';
  html += 'Impacted files: <b>' + impactedFiles.size + '</b>';
  html += 'Modules affected: <b>' + byModule.size + '</b>';
  html += 'Max depth: <b>' + maxDepth + '</b>';
  html += '</div>';

  // Depth histogram (bar chart)
  if (depthCounts.size > 0) {{
    html += '<div class="depth-hist">';
    html += '<div class="mod-name">Depth distribution</div>';
    const maxCount = Math.max(...depthCounts.values());
    const depths = [...depthCounts.keys()].sort((a, b) => a - b);
    for (const d of depths) {{
      const count = depthCounts.get(d);
      const pct = (count / maxCount * 100).toFixed(0);
      html += '<div class="hist-row"><span class="hist-label">depth=' + d + '</span>';
      html += '<div class="hist-bar"><div class="hist-fill" style="width:' + pct + '%"></div></div>';
      html += '<span class="hist-count">' + count + '</span></div>';
    }}
    html += '</div>';
  }}

  // Direct matches
  if (direct.length > 0) {{
    html += '<div class="module-group">';
    html += '<div class="mod-name">Direct matches (' + matches.length + ' total, showing ' + Math.min(direct.length, 15) + ')</div><ul>';
    direct.slice(0, 15).forEach(entry => {{
      const name = entry[0];
      const typeCode = entry[1];
      const fileIdx = entry[2];
      const line = entry[3];
      const exported = entry[4];
      const label = TYPE_LABELS[typeCode] || '?';
      const exp = exported ? ' [exported]' : '';
      const lineStr = line ? ':' + line : '';
      const path = filePaths[fileIdx] || '';
      html += '<li>' + badge(label) + ' <b>' + name + '</b>' + exp + ' — <span class="mono dim">' + path + lineStr + '</span></li>';
    }});
    html += '</ul></div>';
  }}

  // Impact by module
  if (sortedModules.length > 0) {{
    html += '<h3 style="margin-top:16px">Impact chain by module</h3>';
    html += '<div class="hint">Click any file to see the full path from source.</div>';
    for (const [mod, items] of sortedModules) {{
      items.sort((a, b) => a.depth - b.depth);
      const shown = Math.min(items.length, 20);
      html += '<div class="module-group">';
      html += '<div class="mod-name">' + mod + ' <span class="dim">(' + items.length + ' files)</span></div>';
      html += '<ul>';
      items.slice(0, shown).forEach(it => {{
        const path = filePaths[it.fidx] || '';
        html += '<li class="chain-link" data-fidx="' + it.fidx + '">'
          + '<span class="depth">d' + it.depth + '</span>'
          + '<span class="mono">' + path + '</span></li>';
      }});
      if (items.length > shown) {{
        html += '<li class="dim">... and ' + (items.length - shown) + ' more</li>';
      }}
      html += '</ul></div>';
    }}

    // Add visualize button
    html += '<div style="margin-top:16px"><button class="visualize-btn">📊 Visualize impact subgraph</button></div>';
    html += '<div id="impact-graph-container" style="display:none;margin-top:12px"></div>';
    html += '<div id="chain-detail" style="margin-top:12px"></div>';
  }} else if (direct.length > 0) {{
    html += '<div class="hint" style="margin-top:12px">No downstream impact — nothing imports these files.</div>';
  }}

  html += '</div>';
  out.innerHTML = html;
}}

// ─── Populate tables ───
function populate() {{
  document.getElementById('hotspots-body').innerHTML = hotspotRows.map((h, i) => {{
    const maxCount = hotspotRows[0] ? hotspotRows[0].count : 1;
    const pct = (h.count / maxCount) * 100;
    const risk = h.count > 50 ? '🔴 CRITICAL' : h.count > 20 ? '🟠 HIGH' : h.count > 5 ? '🟡 MEDIUM' : '🟢 LOW';
    return '<tr><td>' + (i + 1) + '</td>'
      + '<td class="mono">' + h.file + '</td>'
      + '<td class="mono dim">' + h.module + '</td>'
      + '<td><b>' + h.count + '</b> <div class="risk-bar"><div class="fill" style="width:' + pct + '%"></div></div></td>'
      + '<td>' + risk + '</td></tr>';
  }}).join('');

  document.getElementById('mod-body').innerHTML = moduleRows.map(m =>
    '<tr><td><b>' + m.name + '</b></td>'
    + '<td>' + m.files + '</td>'
    + '<td>' + m.knowledge + '</td>'
    + '<td class="mono dim">' + m.tags + '</td>'
    + '<td>' + m.description + '</td></tr>'
  ).join('');

  document.getElementById('know-body').innerHTML = knowledgeRows.map(k =>
    '<tr><td>' + badge(k.type) + '</td>'
    + '<td>' + badge(k.status) + '</td>'
    + '<td><b>' + k.title + '</b></td>'
    + '<td class="mono">' + (k.module || '-') + '</td>'
    + '<td class="mono dim">' + (k.file || '-') + '</td>'
    + '<td>' + (k.content || '').substring(0, 150) + '</td></tr>'
  ).join('');
}}

// ─── Chain drill-down: show full path from source to target ───
function showChain(targetIdx) {{
  if (!currentImpact) return;
  const info = currentImpact.impacted.get(targetIdx);
  if (!info) return;

  // Walk parent pointers back to source
  const chain = [];
  let cur = targetIdx;
  while (cur !== null && cur !== undefined) {{
    chain.push(cur);
    const nodeInfo = currentImpact.impacted.get(cur);
    if (!nodeInfo) break;
    cur = nodeInfo.parent;
  }}

  // Reverse so source is first
  chain.reverse();

  const out = document.getElementById('chain-detail');
  let html = '<div class="impact-results"><h3>Dependency chain to ' + filePaths[targetIdx] + '</h3>';
  html += '<div class="chain-viz">';
  chain.forEach((fidx, i) => {{
    const path = filePaths[fidx] || '';
    const mod = fileModules[fidx] || '_unassigned';
    const isSource = i === 0;
    const isTarget = i === chain.length - 1;
    const role = isSource ? '📍 SOURCE' : isTarget ? '🎯 TARGET' : '↑';
    html += '<div class="chain-step ' + (isSource ? 'chain-source' : isTarget ? 'chain-target' : '') + '">';
    html += '<div class="chain-role">' + role + '</div>';
    html += '<div class="chain-path"><span class="mono">' + path + '</span></div>';
    html += '<div class="chain-mod dim">' + mod + '</div>';
    html += '</div>';
    if (i < chain.length - 1) {{
      html += '<div class="chain-arrow">↑ imported by</div>';
    }}
  }});
  html += '</div>';
  html += '<div class="hint" style="margin-top:8px">Chain length: ' + chain.length + ' files</div>';
  html += '</div>';
  out.innerHTML = html;
  out.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
}}

// ─── Visualize impact subgraph as a network ───
function visualizeImpact() {{
  if (!currentImpact) return;
  const container = document.getElementById('impact-graph-container');
  container.style.display = 'block';
  container.innerHTML = '<div id="impact-net" style="width:100%;height:500px;border:1px solid #30363d;border-radius:6px"></div>';

  // Build subgraph: sources + impacted, with BFS tree edges
  const nodes = [];
  const edges = [];
  const seen = new Set();

  // Limit to top 200 nodes (sort by depth ascending)
  const allImpacted = [...currentImpact.impacted.entries()]
    .sort((a, b) => a[1].depth - b[1].depth)
    .slice(0, 200);

  for (const [fidx, info] of allImpacted) {{
    if (seen.has(fidx)) continue;
    seen.add(fidx);
    const path = filePaths[fidx] || ('f' + fidx);
    const shortName = path.split('/').pop();
    const isSource = currentImpact.startFiles.has(fidx);
    nodes.push({{
      id: fidx,
      label: shortName,
      title: path + '\n(' + (fileModules[fidx] || '?') + ')',
      color: isSource
        ? {{ background: '#da3633', border: '#f85149' }}
        : info.depth === 1
          ? {{ background: '#d29922', border: '#d29922' }}
          : {{ background: '#1f6feb', border: '#58a6ff' }},
      font: {{ color: '#fff', size: 10 }},
      shape: isSource ? 'box' : 'dot',
      size: isSource ? 16 : Math.max(6, 16 - info.depth * 2),
    }});
    if (info.parent !== null && info.parent !== undefined && seen.has(info.parent)) {{
      edges.push({{
        from: fidx,
        to: info.parent,
        arrows: 'to',
        color: {{ color: '#58a6ff66' }},
      }});
    }}
  }}

  // Second pass to add edges for nodes whose parent was added later
  for (const [fidx, info] of allImpacted) {{
    if (info.parent !== null && info.parent !== undefined && seen.has(info.parent) && seen.has(fidx)) {{
      if (!edges.some(e => e.from === fidx && e.to === info.parent)) {{
        edges.push({{
          from: fidx,
          to: info.parent,
          arrows: 'to',
          color: {{ color: '#58a6ff66' }},
        }});
      }}
    }}
  }}

  impactNetwork = new vis.Network(
    document.getElementById('impact-net'),
    {{ nodes: new vis.DataSet(nodes), edges: new vis.DataSet(edges) }},
    {{
      physics: {{ solver: 'forceAtlas2Based', forceAtlas2Based: {{ gravitationalConstant: -60 }} }},
      interaction: {{ hover: true, navigationButtons: true }},
      layout: {{ improvedLayout: true }},
    }}
  );

  impactNetwork.on('click', params => {{
    if (params.nodes.length > 0) {{
      showChain(params.nodes[0]);
    }}
  }});
}}

// ─── Module graph (lazy init on tab switch) ───
let graphInit = false;
let network = null;
function initGraph() {{
  if (visNodes.length === 0) {{
    document.getElementById('graph-container').innerHTML = '<div style="padding:20px;color:#8b949e">No modules defined.</div>';
    graphInit = true;
    return;
  }}
  const container = document.getElementById('graph-container');
  network = new vis.Network(container, {{
    nodes: new vis.DataSet(visNodes),
    edges: new vis.DataSet(visEdges),
  }}, {{
    physics: {{ solver: 'forceAtlas2Based', forceAtlas2Based: {{ gravitationalConstant: -80 }} }},
    interaction: {{ hover: true, tooltipDelay: 100, navigationButtons: true, keyboard: true }},
    layout: {{ improvedLayout: true }},
    edges: {{ smooth: {{ type: 'curvedCW', roundness: 0.15 }} }},
  }});
  graphInit = true;
}}

populate();
populateAnalysis();

function populateAnalysis() {{
  // ─── Migration ───
  const mig = document.getElementById('migration-content');
  if (migrationData) {{
    const m = migrationData;
    const filled = Math.round(m.progress_pct * 0.4);
    const bar = '█'.repeat(filled) + '░'.repeat(40 - filled);
    let h = '<div class="impact-results">';
    h += '<h3>Migration: ' + m.source_dir + ' → ' + m.target_dir + '</h3>';
    h += '<div class="stats-row">';
    h += 'Source: <b>' + m.source_files + '</b>';
    h += 'Target: <b>' + m.target_files + '</b>';
    h += 'Migrated: <b>' + m.migrated + '</b>';
    h += 'Remaining: <b>' + m.remaining + '</b>';
    h += '</div>';
    h += '<div style="margin:16px 0;font-family:Consolas,monospace;font-size:14px"><span style="color:#3fb950">' + bar + '</span> <b>' + m.progress_pct + '%</b></div>';
    if (m.top_directories && m.top_directories.length > 0) {{
      h += '<h3 style="margin-top:16px">Remaining by directory</h3>';
      h += '<table><thead><tr><th>Count</th><th>Directory</th></tr></thead><tbody>';
      m.top_directories.forEach(d => {{
        h += '<tr><td><b>' + d[1] + '</b></td><td class="mono">' + d[0] + '</td></tr>';
      }});
      h += '</tbody></table>';
    }}
    h += '</div>';
    mig.innerHTML = h;
  }} else {{
    mig.innerHTML = '<div class="hint">No migration data. Run from a project with common/ and restructured/ directories.</div>';
  }}

  // ─── Boundary ───
  const bnd = document.getElementById('boundary-content');
  if (violationsData && violationsData.length > 0) {{
    let h = '<div class="stats-row" style="margin-bottom:12px">Total cross-module edges: <b>' + violationsData.length + '</b></div>';
    h += '<table><thead><tr><th>From</th><th>To</th><th>Count</th><th>Sample</th></tr></thead><tbody>';
    violationsData.slice(0, 50).forEach(v => {{
      const sample = (v.sample_files[0] || '').split(' → ');
      const sampleStr = sample.length > 1 ? sample[0].split('/').pop() + ' → ' + sample[1].split('/').pop() : '';
      h += '<tr>';
      h += '<td class="mono"><b>' + v.from_module + '</b></td>';
      h += '<td class="mono">' + v.to_module + '</td>';
      h += '<td><b>' + v.count + '</b></td>';
      h += '<td class="mono dim">' + sampleStr + '</td>';
      h += '</tr>';
    }});
    h += '</tbody></table>';
    if (violationsData.length > 50) h += '<div class="hint" style="margin-top:8px">... and ' + (violationsData.length - 50) + ' more</div>';
    bnd.innerHTML = h;
  }} else {{
    bnd.innerHTML = '<div class="hint">No boundary violations detected.</div>';
  }}

  // ─── Cohesion ───
  const coh = document.getElementById('cohesion-content');
  if (cohesionData && cohesionData.length > 0) {{
    let h = '<table><thead><tr><th>Module</th><th>Files</th><th>Internal</th><th>External</th><th>Cohesion</th><th>Top external deps</th></tr></thead><tbody>';
    cohesionData.forEach(c => {{
      const pct = (c.cohesion_ratio * 100).toFixed(0);
      const barColor = c.cohesion_ratio > 0.7 ? '#3fb950' : c.cohesion_ratio > 0.4 ? '#d29922' : '#f85149';
      const ext = (c.top_external || []).slice(0, 3).map(e => e[0] + ' (' + e[1] + ')').join(', ');
      h += '<tr>';
      h += '<td class="mono"><b>' + c.module + '</b></td>';
      h += '<td>' + c.file_count + '</td>';
      h += '<td>' + c.internal_imports + '</td>';
      h += '<td>' + c.external_imports + '</td>';
      h += '<td><div class="risk-bar" style="width:120px"><div class="fill" style="width:' + pct + '%;background:' + barColor + '"></div></div> ' + pct + '%</td>';
      h += '<td class="mono dim">' + ext + '</td>';
      h += '</tr>';
    }});
    h += '</tbody></table>';
    coh.innerHTML = h;
  }} else {{
    coh.innerHTML = '<div class="hint">No cohesion data.</div>';
  }}

  // ─── Dead Code ───
  const dc = document.getElementById('deadcode-content');
  if (deadCodeData) {{
    let h = '<div class="stats-row" style="margin-bottom:12px">';
    h += 'Total files: <b>' + deadCodeData.total_files + '</b> ';
    h += 'Imported: <b>' + deadCodeData.imported_files + '</b> ';
    h += 'Unreferenced: <b>' + deadCodeData.dead_files.length + '</b>';
    h += '</div>';
    if (deadCodeData.by_directory && deadCodeData.by_directory.length > 0) {{
      h += '<table><thead><tr><th>Count</th><th>Directory</th></tr></thead><tbody>';
      deadCodeData.by_directory.forEach(d => {{
        h += '<tr><td><b>' + d[1] + '</b></td><td class="mono">' + d[0] + '</td></tr>';
      }});
      h += '</tbody></table>';
    }}
    dc.innerHTML = h;
  }} else {{
    dc.innerHTML = '<div class="hint">No dead code data.</div>';
  }}
}}

// ─── Wire up event handlers programmatically (bulletproof) ───
document.addEventListener('DOMContentLoaded', wireUp);
if (document.readyState !== 'loading') wireUp();

function wireUp() {{
  // Tabs
  document.querySelectorAll('.tab').forEach(tab => {{
    tab.addEventListener('click', () => {{
      const name = tab.getAttribute('data-tab');
      showTab(name, tab);
    }});
  }});

  // Analyze button
  const analyzeBtn = document.querySelector('#panel-impact button');
  if (analyzeBtn) analyzeBtn.addEventListener('click', runImpact);

  const queryInput = document.getElementById('impact-query');
  if (queryInput) {{
    queryInput.addEventListener('keydown', e => {{
      if (e.key === 'Enter') runImpact();
    }});
  }}

  // Delegate clicks on chain-link items
  const resultsEl = document.getElementById('impact-results');
  if (resultsEl) {{
    resultsEl.addEventListener('click', e => {{
      const link = e.target.closest('.chain-link');
      if (link && link.dataset.fidx) {{
        showChain(parseInt(link.dataset.fidx));
      }}
      if (e.target.matches('.visualize-btn')) {{
        visualizeImpact();
      }}
    }});
  }}

  console.log('[Temper] Wired. searchIndex:', searchIndex.length, 'files:', filePaths.length);
}}
</script>
</body>
</html>"#,
        project_name = project_name,
        files = stats.files,
        functions = stats.functions,
        classes = stats.classes,
        edges = stats.edges,
        vis_nodes = vis_nodes,
        vis_edges = vis_edges,
        module_rows = module_rows,
        knowledge_rows = knowledge_rows,
        hotspot_rows = hotspot_rows,
        search_index = search_index,
        file_paths = file_paths,
        file_modules = file_modules,
        reverse_adj = reverse_adj,
        migration_json = migration_json,
        dead_code_json = dead_code_json,
        violations_json = violations_json,
        cohesion_json = cohesion_json,
    )
}
