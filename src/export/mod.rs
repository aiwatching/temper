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
  <div class="tab active" onclick="showTab('impact')">Impact Explorer</div>
  <div class="tab" onclick="showTab('hotspots')">Hotspots</div>
  <div class="tab" onclick="showTab('graph')">Module Graph</div>
  <div class="tab" onclick="showTab('modules')">Modules</div>
  <div class="tab" onclick="showTab('knowledge')">Knowledge</div>
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

// ─── Tabs ───
function showTab(name) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  event.target.classList.add('active');
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

function runImpact() {{
  const query = document.getElementById('impact-query').value.trim().toLowerCase();
  const maxDepth = parseInt(document.getElementById('impact-depth').value);
  const out = document.getElementById('impact-results');

  if (!query) {{
    out.innerHTML = '<div class="hint">Enter a name to analyze.</div>';
    return;
  }}

  // Find matches: search name or file path
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

  // Top 20 (exported first)
  const direct = matches
    .sort((a, b) => (b[4] ? 1 : 0) - (a[4] ? 1 : 0))
    .slice(0, 20);

  // Unique start file indices
  const startFiles = new Set();
  matches.forEach(entry => {{
    startFiles.add(entry[2]); // file_idx
  }});

  // BFS with integer indices
  const impacted = new Map(); // file_idx → depth
  const queue = [];
  for (const fidx of startFiles) {{
    queue.push([fidx, 0]);
    impacted.set(fidx, 0);
  }}

  while (queue.length > 0) {{
    const [idx, depth] = queue.shift();
    if (depth >= maxDepth) continue;
    const parents = reverseAdj[idx] || [];
    for (const p of parents) {{
      if (!impacted.has(p)) {{
        impacted.set(p, depth + 1);
        queue.push([p, depth + 1]);
      }}
    }}
  }}

  // Remove source files from impact
  for (const fidx of startFiles) impacted.delete(fidx);

  // Group by module
  const byModule = new Map();
  for (const [fidx, depth] of impacted) {{
    const mod = fileModules[fidx] || '_unassigned';
    if (!byModule.has(mod)) byModule.set(mod, []);
    byModule.get(mod).push({{ fidx, depth }});
  }}

  // Sort modules by impact count
  const sortedModules = [...byModule.entries()].sort((a, b) => b[1].length - a[1].length);

  // Build HTML
  let html = '<div class="impact-results">';
  html += '<h3>Results for "' + query + '"</h3>';
  html += '<div class="stats-row">';
  html += 'Matches: <b>' + matches.length + '</b>';
  html += 'Impacted files: <b>' + impacted.size + '</b>';
  html += 'Modules affected: <b>' + byModule.size + '</b>';
  html += 'Max depth: <b>' + maxDepth + '</b>';
  html += '</div>';

  if (direct.length > 0) {{
    html += '<div class="module-group">';
    html += '<div class="mod-name">Direct matches (' + matches.length + ')</div><ul>';
    direct.slice(0, 10).forEach(entry => {{
      const name = entry[0];
      const typeCode = entry[1];
      const fileIdx = entry[2];
      const line = entry[3];
      const exported = entry[4];
      const label = TYPE_LABELS[typeCode] || '?';
      const exp = exported ? ' [exported]' : '';
      const lineStr = line ? ':' + line : '';
      const path = filePaths[fileIdx] || '';
      html += '<li>' + badge(label) + ' ' + name + exp + ' — <span class="mono dim">' + path + lineStr + '</span></li>';
    }});
    if (matches.length > 10) {{
      html += '<li class="dim">... and ' + (matches.length - 10) + ' more</li>';
    }}
    html += '</ul></div>';
  }}

  if (sortedModules.length > 0) {{
    html += '<h3 style="margin-top:16px">Impact by module</h3>';
    for (const [mod, items] of sortedModules) {{
      items.sort((a, b) => a.depth - b.depth);
      html += '<div class="module-group">';
      html += '<div class="mod-name">' + mod + ' <span class="dim">(' + items.length + ' files)</span></div>';
      html += '<ul>';
      items.slice(0, 8).forEach(it => {{
        const path = filePaths[it.fidx] || '';
        html += '<li><span class="depth">depth=' + it.depth + '</span><span class="mono">' + path + '</span></li>';
      }});
      if (items.length > 8) {{
        html += '<li class="dim">... and ' + (items.length - 8) + ' more</li>';
      }}
      html += '</ul></div>';
    }}
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
    )
}
