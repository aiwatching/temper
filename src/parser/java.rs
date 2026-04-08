use anyhow::{Context, Result};
use std::path::Path;
use tree_sitter::{Parser, Node};

use crate::graph::{CodeEdge, CodeNode, EdgeType, NodeType};

/// Parse a Java file using tree-sitter, extracting classes, methods, imports.
pub fn parse_java(
    full_path: &Path,
    rel_path: &str,
    module: &str,
) -> Result<(Vec<CodeNode>, Vec<CodeEdge>)> {
    let content = std::fs::read_to_string(full_path)
        .with_context(|| format!("Failed to read {}", full_path.display()))?;

    let mut parser = Parser::new();
    let language = tree_sitter_java::LANGUAGE;
    parser
        .set_language(&language.into())
        .context("Failed to set Java language for tree-sitter")?;

    let tree = parser
        .parse(&content, None)
        .context("Failed to parse Java file")?;

    let root = tree.root_node();
    let file_id = rel_path.to_string();

    let mut nodes = vec![CodeNode {
        id: file_id.clone(),
        node_type: NodeType::File,
        name: Path::new(rel_path)
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_else(|| rel_path.to_string()),
        file_path: rel_path.to_string(),
        line: None,
        exported: false,
        module: module.to_string(),
    }];

    let mut edges = Vec::new();

    extract_nodes(
        root,
        &content,
        &file_id,
        rel_path,
        module,
        &mut nodes,
        &mut edges,
    );

    Ok((nodes, edges))
}

fn extract_nodes(
    node: Node,
    source: &str,
    file_id: &str,
    rel_path: &str,
    module: &str,
    nodes: &mut Vec<CodeNode>,
    edges: &mut Vec<CodeEdge>,
) {
    let mut cursor = node.walk();

    for child in node.children(&mut cursor) {
        match child.kind() {
            "import_declaration" => {
                extract_import(child, source, file_id, edges);
            }
            "class_declaration" | "interface_declaration" | "enum_declaration" => {
                extract_class(child, source, file_id, rel_path, module, nodes, edges);
            }
            "package_declaration" => {
                // Could use for module inference in the future
            }
            _ => {
                // Recurse into other top-level nodes
                extract_nodes(child, source, file_id, rel_path, module, nodes, edges);
            }
        }
    }
}

fn extract_import(node: Node, source: &str, file_id: &str, edges: &mut Vec<CodeEdge>) {
    // import com.foo.bar.ClassName;
    // The scoped_identifier or identifier contains the full path
    if let Some(path_node) = find_child_by_kind(node, "scoped_identifier") {
        let import_path = node_text(path_node, source);
        let parts: Vec<&str> = import_path.split('.').collect();
        let short_name = parts.last().copied().unwrap_or(&import_path);

        // Convert package to path format: com.foo.Bar → com/foo/Bar
        let target = import_path.replace('.', "/");

        edges.push(CodeEdge {
            from: file_id.to_string(),
            to: target,
            edge_type: EdgeType::Imports,
            detail: Some(short_name.to_string()),
        });
    }
}

fn extract_class(
    node: Node,
    source: &str,
    file_id: &str,
    rel_path: &str,
    module: &str,
    nodes: &mut Vec<CodeNode>,
    edges: &mut Vec<CodeEdge>,
) {
    let name = match find_child_by_kind(node, "identifier") {
        Some(n) => node_text(n, source),
        None => return,
    };

    let class_id = format!("{}::{}", file_id, name);
    let line = node.start_position().row as u32 + 1;
    let is_public = has_modifier(node, source, "public");

    nodes.push(CodeNode {
        id: class_id.clone(),
        node_type: NodeType::Class,
        name: name.clone(),
        file_path: rel_path.to_string(),
        line: Some(line),
        exported: is_public,
        module: module.to_string(),
    });

    if is_public {
        edges.push(CodeEdge {
            from: file_id.to_string(),
            to: class_id.clone(),
            edge_type: EdgeType::Exports,
            detail: Some(name.clone()),
        });
    }

    // Extract methods within the class body
    if let Some(body) = find_child_by_kind(node, "class_body")
        .or_else(|| find_child_by_kind(node, "interface_body"))
        .or_else(|| find_child_by_kind(node, "enum_body"))
    {
        extract_methods(body, source, file_id, rel_path, module, &name, nodes, edges);
        extract_annotations_rest(body, source, file_id, rel_path, &name, edges);
    }
}

fn extract_methods(
    body: Node,
    source: &str,
    file_id: &str,
    rel_path: &str,
    module: &str,
    class_name: &str,
    nodes: &mut Vec<CodeNode>,
    edges: &mut Vec<CodeEdge>,
) {
    let mut cursor = body.walk();
    for child in body.children(&mut cursor) {
        if child.kind() == "method_declaration" || child.kind() == "constructor_declaration" {
            let method_name = if child.kind() == "constructor_declaration" {
                class_name.to_string()
            } else {
                match find_child_by_kind(child, "identifier") {
                    Some(n) => node_text(n, source),
                    None => continue,
                }
            };

            let func_id = format!("{}::{}", file_id, method_name);
            let line = child.start_position().row as u32 + 1;
            let is_public = has_modifier(child, source, "public");

            // Avoid duplicate IDs (overloaded methods)
            if !nodes.iter().any(|n| n.id == func_id) {
                nodes.push(CodeNode {
                    id: func_id.clone(),
                    node_type: NodeType::Function,
                    name: method_name.clone(),
                    file_path: rel_path.to_string(),
                    line: Some(line),
                    exported: is_public,
                    module: module.to_string(),
                });
            }

            // Extract method calls within the method body
            if let Some(method_body) = find_child_by_kind(child, "block") {
                extract_calls(method_body, source, file_id, edges);
            }
        }
    }
}

fn extract_calls(node: Node, source: &str, file_id: &str, edges: &mut Vec<CodeEdge>) {
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.kind() == "method_invocation" {
            // obj.method() or method()
            if let Some(name_node) = find_child_by_kind(child, "identifier") {
                let call_name = node_text(name_node, source);

                // Check if it's obj.method()
                if let Some(obj_node) = child.child(0) {
                    if obj_node.kind() == "identifier" {
                        let obj_name = node_text(obj_node, source);
                        let full_call = format!("{}.{}", obj_name, call_name);
                        edges.push(CodeEdge {
                            from: file_id.to_string(),
                            to: format!("{}::{}", obj_name, call_name),
                            edge_type: EdgeType::Calls,
                            detail: Some(full_call),
                        });
                    }
                }
            }
        }

        // Recurse
        extract_calls(child, source, file_id, edges);
    }
}

/// Extract REST annotations (@GetMapping, @PostMapping, etc.) for Interface Map.
fn extract_annotations_rest(
    body: Node,
    source: &str,
    file_id: &str,
    _rel_path: &str,
    class_name: &str,
    edges: &mut Vec<CodeEdge>,
) {
    let mut cursor = body.walk();
    for child in body.children(&mut cursor) {
        if child.kind() != "method_declaration" {
            continue;
        }

        let method_name = match find_child_by_kind(child, "identifier") {
            Some(n) => node_text(n, source),
            None => continue,
        };

        // Check for REST annotations on this method
        let mut ann_cursor = child.walk();
        for ann_child in child.children(&mut ann_cursor) {
            if ann_child.kind() == "marker_annotation" || ann_child.kind() == "annotation" {
                let ann_text = node_text(ann_child, source);
                let rest_methods = [
                    "GetMapping",
                    "PostMapping",
                    "PutMapping",
                    "DeleteMapping",
                    "PatchMapping",
                    "RequestMapping",
                ];

                for rm in &rest_methods {
                    if ann_text.contains(rm) {
                        edges.push(CodeEdge {
                            from: file_id.to_string(),
                            to: format!("{}::{}::{}", file_id, class_name, method_name),
                            edge_type: EdgeType::Exports,
                            detail: Some(format!("@{} {}.{}", rm, class_name, method_name)),
                        });
                        break;
                    }
                }
            }
        }
    }
}

// --- Helpers ---

fn node_text(node: Node, source: &str) -> String {
    source[node.byte_range()].to_string()
}

fn find_child_by_kind<'a>(node: Node<'a>, kind: &str) -> Option<Node<'a>> {
    let mut cursor = node.walk();
    let result = node.children(&mut cursor)
        .find(|c| c.kind() == kind);
    result
}

fn has_modifier(node: Node, source: &str, modifier: &str) -> bool {
    let mut cursor = node.walk();
    let result = node.children(&mut cursor).any(|c| {
        c.kind() == "modifiers" && node_text(c, source).contains(modifier)
    });
    result
}
