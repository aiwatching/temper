use anyhow::{Context, Result};
use serde_json::json;
use std::path::Path;

use crate::graph::CodeGraph;
use crate::modules::ModuleRegistry;
use crate::storage::{KnowledgeStore, LocalStorage, RecallQuery};

/// Generate a self-contained HTML dashboard for the project.
pub fn export_html(project_path: &Path, output_dir: &Path) -> Result<()> {
    let temper_dir = project_path.join(".temper");

    // Load data
    let graph = CodeGraph::load(&temper_dir.join("graph.json"))
        .context("No code graph found. Run `temper scan` first.")?;

    let registry = ModuleRegistry::new(&temper_dir, graph.files.clone());
    let modules = registry.list_modules()?;

    // Load knowledge if available
    let db_path = temper_dir.join("knowledge.db");
    let knowledge = if db_path.exists() {
        let store = LocalStorage::open(&db_path)?;
        store.recall(RecallQuery { include_stale: true, ..Default::default() })?
    } else {
        Vec::new()
    };

    // Load interfaces
    let mut interfaces = Vec::new();
    for m in &modules {
        if let Ok(Some(iface)) = crate::modules::load_interface(&temper_dir, &m.name) {
            interfaces.push(iface);
        }
    }

    // Build graph data for vis.js
    let graph_stats = graph.stats();

    let mut vis_nodes = Vec::new();
    let mut vis_edges = Vec::new();

    // Module nodes
    for m in &modules {
        let file_count = registry.file_count(m);
        vis_nodes.push(json!({
            "id": format!("mod:{}", m.name),
            "label": format!("{}\n({} files)", m.name, file_count),
            "group": "module",
            "shape": "box",
            "color": "#4CAF50",
            "font": { "color": "#fff", "size": 14 }
        }));
    }

    // File nodes (only top-level, skip functions to keep graph readable)
    for node in graph.nodes.iter().filter(|n| n.node_type == crate::graph::NodeType::File) {
        let is_in_module = modules.iter().any(|m| {
            registry.resolve_files(m).ok()
                .map(|files| files.contains(&node.file_path))
                .unwrap_or(false)
        });

        vis_nodes.push(json!({
            "id": &node.id,
            "label": &node.name,
            "group": "file",
            "shape": "dot",
            "size": 8,
            "color": if is_in_module { "#2196F3" } else { "#9E9E9E" }
        }));

        // Edge from module to file
        for m in &modules {
            if let Ok(files) = registry.resolve_files(m) {
                if files.contains(&node.file_path) {
                    vis_edges.push(json!({
                        "from": format!("mod:{}", m.name),
                        "to": &node.id,
                        "color": { "color": "#4CAF5066" },
                        "dashes": true
                    }));
                }
            }
        }
    }

    // Import edges between files
    for edge in graph.edges.iter().filter(|e| e.edge_type == crate::graph::EdgeType::Imports) {
        if graph.nodes.iter().any(|n| n.id == edge.to && n.node_type == crate::graph::NodeType::File) {
            vis_edges.push(json!({
                "from": &edge.from,
                "to": &edge.to,
                "arrows": "to",
                "color": { "color": "#FF980066" }
            }));
        }
    }

    // Build module data for table
    let module_rows: Vec<serde_json::Value> = modules.iter().map(|m| {
        let file_count = registry.file_count(m);
        let iface = interfaces.iter().find(|i| i.module == m.name);
        let rest_count = iface.map(|i| i.exposes.rest.len()).unwrap_or(0);
        let method_count = iface.map(|i| i.exposes.java.len()).unwrap_or(0);
        let knowledge_count = knowledge.iter().filter(|k| k.module.as_deref() == Some(&m.name)).count();

        json!({
            "name": m.name,
            "description": m.description,
            "files": file_count,
            "rest": rest_count,
            "methods": method_count,
            "knowledge": knowledge_count,
            "tags": m.tags.join(", ")
        })
    }).collect();

    // Build knowledge data
    let knowledge_rows: Vec<serde_json::Value> = knowledge.iter().map(|k| {
        json!({
            "id": k.id,
            "type": k.entry_type,
            "title": k.title,
            "content": k.content,
            "module": k.module,
            "file": k.file,
            "status": k.status,
            "version": k.current_version,
            "tags": k.tags.join(", ")
        })
    }).collect();

    // Build interface data
    let interface_rows: Vec<serde_json::Value> = interfaces.iter().flat_map(|iface| {
        let mut rows = Vec::new();
        for ep in &iface.exposes.rest {
            rows.push(json!({
                "module": iface.module,
                "type": "REST",
                "detail": format!("{} {}", ep.method, ep.path),
                "handler": ep.handler,
                "line": ep.line
            }));
        }
        for m in &iface.exposes.java {
            rows.push(json!({
                "module": iface.module,
                "type": "Java",
                "detail": format!("{}.{}", m.class, m.method),
                "handler": "",
                "line": m.line
            }));
        }
        rows
    }).collect();

    let project_name = project_path.file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_else(|| "project".into());

    let html = generate_html(
        &project_name,
        &graph_stats,
        &serde_json::to_string(&vis_nodes)?,
        &serde_json::to_string(&vis_edges)?,
        &serde_json::to_string(&module_rows)?,
        &serde_json::to_string(&knowledge_rows)?,
        &serde_json::to_string(&interface_rows)?,
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
    interface_rows: &str,
) -> String {
    format!(r#"<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Temper — {project_name}</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace; background: #0d1117; color: #c9d1d9; }}
.header {{ background: #161b22; padding: 20px 30px; border-bottom: 1px solid #30363d; display: flex; align-items: center; gap: 20px; }}
.header h1 {{ font-size: 20px; color: #58a6ff; }}
.header .stats {{ display: flex; gap: 20px; font-size: 13px; color: #8b949e; }}
.header .stats span {{ color: #c9d1d9; font-weight: 600; }}
.tabs {{ background: #161b22; display: flex; border-bottom: 1px solid #30363d; padding: 0 30px; }}
.tab {{ padding: 10px 20px; cursor: pointer; color: #8b949e; border-bottom: 2px solid transparent; font-size: 14px; }}
.tab:hover {{ color: #c9d1d9; }}
.tab.active {{ color: #58a6ff; border-bottom-color: #58a6ff; }}
.content {{ padding: 20px 30px; }}
.panel {{ display: none; }}
.panel.active {{ display: block; }}
#graph-container {{ width: 100%; height: 500px; border: 1px solid #30363d; border-radius: 6px; background: #0d1117; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th {{ text-align: left; padding: 8px 12px; background: #161b22; color: #8b949e; border-bottom: 1px solid #30363d; position: sticky; top: 0; }}
td {{ padding: 8px 12px; border-bottom: 1px solid #21262d; }}
tr:hover {{ background: #161b22; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; }}
.badge-constraint {{ background: #da3633; color: #fff; }}
.badge-decision {{ background: #1f6feb; color: #fff; }}
.badge-bug {{ background: #d29922; color: #fff; }}
.badge-experience {{ background: #238636; color: #fff; }}
.badge-note {{ background: #8b949e; color: #fff; }}
.badge-active {{ background: #238636; color: #fff; }}
.badge-stale {{ background: #d29922; color: #fff; }}
.badge-expired {{ background: #da3633; color: #fff; }}
.search {{ margin-bottom: 15px; }}
.search input {{ background: #0d1117; border: 1px solid #30363d; color: #c9d1d9; padding: 8px 12px; border-radius: 6px; width: 300px; font-size: 13px; }}
.search input:focus {{ outline: none; border-color: #58a6ff; }}
</style>
</head>
<body>

<div class="header">
  <h1>Temper — {project_name}</h1>
  <div class="stats">
    Files: <span>{files}</span> &nbsp;
    Functions: <span>{functions}</span> &nbsp;
    Classes: <span>{classes}</span> &nbsp;
    Edges: <span>{edges}</span>
  </div>
</div>

<div class="tabs">
  <div class="tab active" onclick="showTab('graph')">Graph</div>
  <div class="tab" onclick="showTab('modules')">Modules</div>
  <div class="tab" onclick="showTab('interfaces')">Interfaces</div>
  <div class="tab" onclick="showTab('knowledge')">Knowledge</div>
</div>

<div class="content">

  <div id="panel-graph" class="panel active">
    <div id="graph-container"></div>
  </div>

  <div id="panel-modules" class="panel">
    <div class="search"><input type="text" placeholder="Filter modules..." oninput="filterTable('modules-table', this.value)"></div>
    <table id="modules-table">
      <thead><tr><th>Module</th><th>Files</th><th>REST</th><th>Methods</th><th>Knowledge</th><th>Tags</th><th>Description</th></tr></thead>
      <tbody id="modules-body"></tbody>
    </table>
  </div>

  <div id="panel-interfaces" class="panel">
    <div class="search"><input type="text" placeholder="Filter interfaces..." oninput="filterTable('interfaces-table', this.value)"></div>
    <table id="interfaces-table">
      <thead><tr><th>Module</th><th>Type</th><th>API</th><th>Handler</th><th>Line</th></tr></thead>
      <tbody id="interfaces-body"></tbody>
    </table>
  </div>

  <div id="panel-knowledge" class="panel">
    <div class="search"><input type="text" placeholder="Filter knowledge..." oninput="filterTable('knowledge-table', this.value)"></div>
    <table id="knowledge-table">
      <thead><tr><th>Type</th><th>Status</th><th>Title</th><th>Module</th><th>Content</th><th>Tags</th></tr></thead>
      <tbody id="knowledge-body"></tbody>
    </table>
  </div>

</div>

<script>
const visNodes = {vis_nodes};
const visEdges = {vis_edges};
const moduleRows = {module_rows};
const knowledgeRows = {knowledge_rows};
const interfaceRows = {interface_rows};

// Graph
const container = document.getElementById('graph-container');
const network = new vis.Network(container, {{
  nodes: new vis.DataSet(visNodes),
  edges: new vis.DataSet(visEdges)
}}, {{
  physics: {{ solver: 'forceAtlas2Based', forceAtlas2Based: {{ gravitationalConstant: -50 }} }},
  interaction: {{ hover: true, tooltipDelay: 100 }},
  layout: {{ improvedLayout: true }}
}});

// Tabs
function showTab(name) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelector(`.tab[onclick*="${{name}}"]`).classList.add('active');
  document.getElementById('panel-' + name).classList.add('active');
  if (name === 'graph') network.fit();
}}

// Tables
function badge(type, text) {{
  return `<span class="badge badge-${{type}}">${{text}}</span>`;
}}

function populateModules() {{
  const body = document.getElementById('modules-body');
  body.innerHTML = moduleRows.map(m => `<tr>
    <td><strong>${{m.name}}</strong></td><td>${{m.files}}</td><td>${{m.rest}}</td>
    <td>${{m.methods}}</td><td>${{m.knowledge}}</td><td>${{m.tags}}</td><td>${{m.description}}</td>
  </tr>`).join('');
}}

function populateKnowledge() {{
  const body = document.getElementById('knowledge-body');
  body.innerHTML = knowledgeRows.map(k => `<tr>
    <td>${{badge(k.type, k.type)}}</td>
    <td>${{badge(k.status, k.status)}}</td>
    <td><strong>${{k.title}}</strong></td>
    <td>${{k.module || '-'}}</td>
    <td>${{k.content.substring(0, 120)}}</td>
    <td>${{k.tags}}</td>
  </tr>`).join('');
}}

function populateInterfaces() {{
  const body = document.getElementById('interfaces-body');
  body.innerHTML = interfaceRows.map(i => `<tr>
    <td>${{i.module}}</td><td>${{badge(i.type === 'REST' ? 'decision' : 'note', i.type)}}</td>
    <td><strong>${{i.detail}}</strong></td><td>${{i.handler}}</td><td>${{i.line}}</td>
  </tr>`).join('');
}}

function filterTable(tableId, query) {{
  const rows = document.querySelectorAll(`#${{tableId}} tbody tr`);
  const q = query.toLowerCase();
  rows.forEach(row => {{
    row.style.display = row.textContent.toLowerCase().includes(q) ? '' : 'none';
  }});
}}

populateModules();
populateKnowledge();
populateInterfaces();
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
        interface_rows = interface_rows,
    )
}
