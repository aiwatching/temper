use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::path::Path;
use tree_sitter::{Node, Parser};

use crate::modules::ModuleRegistry;

// --- Interface Data Types ---

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModuleInterface {
    pub module: String,
    pub generated_at: String,
    pub verified: bool,
    pub exposes: ExposedApis,
    pub depends_on: Vec<Dependency>,
    pub depended_by: Vec<Dependency>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ExposedApis {
    #[serde(default)]
    pub rest: Vec<RestEndpoint>,
    #[serde(default)]
    pub java: Vec<JavaMethod>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RestEndpoint {
    pub method: String,
    pub path: String,
    pub handler: String,
    pub line: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JavaMethod {
    pub class: String,
    pub method: String,
    pub visibility: String,
    pub line: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Dependency {
    pub module: String,
    pub class: String,
    pub method: String,
    pub usage: String,
}

/// Scan a module's files and extract its public API surface.
pub fn scan_module_interfaces(
    project_path: &Path,
    registry: &ModuleRegistry,
    module_name: &str,
) -> Result<ModuleInterface> {
    let module = registry
        .get_module(module_name)?
        .context(format!("Module '{}' not found", module_name))?;

    let files = registry.resolve_files(&module)?;
    let java_files: Vec<&String> = files.iter().filter(|f| f.ends_with(".java")).collect();

    let mut exposes = ExposedApis::default();

    let mut parser = Parser::new();
    let language = tree_sitter_java::LANGUAGE;
    parser.set_language(&language.into())?;

    for rel_path in &java_files {
        let full_path = project_path.join(rel_path);
        if !full_path.exists() {
            continue;
        }

        let content = std::fs::read_to_string(&full_path)?;
        let tree = match parser.parse(&content, None) {
            Some(t) => t,
            None => continue,
        };

        extract_interfaces(tree.root_node(), &content, &mut exposes);
    }

    // Auto-fill depends_on by analyzing imports against other modules
    let all_modules = registry.list_modules()?;
    let mut depends_on = Vec::new();

    // Collect all import targets from this module's files
    let graph_path = project_path.join(".temper").join("graph.json");
    if let Ok(graph) = crate::graph::CodeGraph::load(&graph_path) {
        // Get imports from this module's files
        for file_path in &files {
            let imports: Vec<_> = graph.edges.iter()
                .filter(|e| e.from == *file_path && e.edge_type == crate::graph::EdgeType::Imports)
                .collect();

            for import_edge in imports {
                // Check which module the imported file belongs to
                for other_module in &all_modules {
                    if other_module.name == module_name {
                        continue;
                    }
                    if let Ok(other_files) = registry.resolve_files(other_module) {
                        if other_files.iter().any(|f| import_edge.to.contains(f.as_str()) || f.contains(&import_edge.to)) {
                            let detail = import_edge.detail.as_deref().unwrap_or("").to_string();
                            // Avoid duplicates
                            if !depends_on.iter().any(|d: &Dependency| d.module == other_module.name && d.class == detail) {
                                depends_on.push(Dependency {
                                    module: other_module.name.clone(),
                                    class: detail,
                                    method: String::new(),
                                    usage: format!("imported by {}", file_path),
                                });
                            }
                        }
                    }
                }
            }
        }
    }

    Ok(ModuleInterface {
        module: module_name.to_string(),
        generated_at: chrono::Utc::now().format("%Y-%m-%d").to_string(),
        verified: false,
        exposes,
        depends_on,
        depended_by: Vec::new(), // Filled when scanning OTHER modules that depend on this one
    })
}

fn extract_interfaces(root: Node, source: &str, exposes: &mut ExposedApis) {
    let mut cursor = root.walk();
    for child in root.children(&mut cursor) {
        if child.kind() == "class_declaration"
            || child.kind() == "interface_declaration"
        {
            extract_class_interface(child, source, exposes);
        } else {
            extract_interfaces(child, source, exposes);
        }
    }
}

fn extract_class_interface(node: Node, source: &str, exposes: &mut ExposedApis) {
    let class_name = match find_child(node, "identifier") {
        Some(n) => text(n, source),
        None => return,
    };

    let is_public = has_modifier(node, source, "public");
    if !is_public {
        return;
    }

    // Check for REST controller annotations
    let is_controller = has_annotation(node, source, &[
        "RestController", "Controller", "RequestMapping",
    ]);

    // Extract methods from class body
    let body = find_child(node, "class_body")
        .or_else(|| find_child(node, "interface_body"));

    if let Some(body) = body {
        let mut body_cursor = body.walk();
        for child in body.children(&mut body_cursor) {
            if child.kind() == "method_declaration" {
                extract_method_interface(
                    child, source, &class_name, is_controller, exposes,
                );
            }
        }
    }
}

fn extract_method_interface(
    node: Node,
    source: &str,
    class_name: &str,
    is_controller: bool,
    exposes: &mut ExposedApis,
) {
    let method_name = match find_child(node, "identifier") {
        Some(n) => text(n, source),
        None => return,
    };

    let is_public = has_modifier(node, source, "public");
    let line = node.start_position().row as u32 + 1;

    // Extract REST endpoint annotations
    if is_controller {
        let rest_annotations = [
            ("GetMapping", "GET"),
            ("PostMapping", "POST"),
            ("PutMapping", "PUT"),
            ("DeleteMapping", "DELETE"),
            ("PatchMapping", "PATCH"),
        ];

        for (ann_name, http_method) in &rest_annotations {
            if let Some(path) = get_annotation_value(node, source, ann_name) {
                exposes.rest.push(RestEndpoint {
                    method: http_method.to_string(),
                    path,
                    handler: format!("{}.{}", class_name, method_name),
                    line,
                });
            }
        }

        // Also check @RequestMapping
        if let Some(path) = get_annotation_value(node, source, "RequestMapping") {
            exposes.rest.push(RestEndpoint {
                method: "ANY".to_string(),
                path,
                handler: format!("{}.{}", class_name, method_name),
                line,
            });
        }
    }

    // Record public Java methods
    if is_public {
        // Build method signature
        let params = find_child(node, "formal_parameters")
            .map(|n| text(n, source))
            .unwrap_or_else(|| "()".to_string());

        exposes.java.push(JavaMethod {
            class: class_name.to_string(),
            method: format!("{}{}", method_name, params),
            visibility: "public".to_string(),
            line,
        });
    }
}

/// Save module interface to JSON file.
pub fn save_interface(temper_dir: &Path, iface: &ModuleInterface) -> Result<()> {
    let dir = temper_dir.join("interfaces");
    std::fs::create_dir_all(&dir)?;

    let filename = iface.module.replace('/', "--");
    let path = dir.join(format!("{}.json", filename));
    let content = serde_json::to_string_pretty(iface)?;
    std::fs::write(&path, content)?;

    Ok(())
}

/// Load module interface from JSON file.
pub fn load_interface(temper_dir: &Path, module_name: &str) -> Result<Option<ModuleInterface>> {
    let filename = module_name.replace('/', "--");
    let path = temper_dir.join("interfaces").join(format!("{}.json", filename));

    if !path.exists() {
        return Ok(None);
    }

    let content = std::fs::read_to_string(&path)?;
    let iface: ModuleInterface = serde_json::from_str(&content)?;
    Ok(Some(iface))
}

// --- Helpers ---

fn text(node: Node, source: &str) -> String {
    source[node.byte_range()].to_string()
}

fn find_child<'a>(node: Node<'a>, kind: &str) -> Option<Node<'a>> {
    let mut cursor = node.walk();
    let result = node.children(&mut cursor).find(|c| c.kind() == kind);
    result
}

fn has_modifier(node: Node, source: &str, modifier: &str) -> bool {
    let mut cursor = node.walk();
    let result = node.children(&mut cursor).any(|c| {
        c.kind() == "modifiers" && text(c, source).contains(modifier)
    });
    result
}

fn has_annotation(node: Node, source: &str, names: &[&str]) -> bool {
    let mut cursor = node.walk();
    let result = node.children(&mut cursor).any(|c| {
        if c.kind() == "modifiers" {
            let mod_text = text(c, source);
            names.iter().any(|n| mod_text.contains(n))
        } else {
            false
        }
    });
    result
}

fn get_annotation_value(node: Node, source: &str, annotation_name: &str) -> Option<String> {
    // Look through modifiers for the annotation
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.kind() != "modifiers" {
            continue;
        }
        let mod_text = text(child, source);
        if !mod_text.contains(annotation_name) {
            continue;
        }

        // Try to extract the path value from @GetMapping("/api/users")
        // or @GetMapping(value = "/api/users")
        let mut mod_cursor = child.walk();
        for ann in child.children(&mut mod_cursor) {
            if ann.kind() == "annotation" || ann.kind() == "marker_annotation" {
                let ann_text = text(ann, source);
                if ann_text.contains(annotation_name) {
                    // Extract string value from annotation
                    if let Some(start) = ann_text.find('"') {
                        if let Some(end) = ann_text[start + 1..].find('"') {
                            return Some(ann_text[start + 1..start + 1 + end].to_string());
                        }
                    }
                    // No value — return empty path
                    return Some(String::new());
                }
            }
        }
    }
    None
}
