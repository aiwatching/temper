use anyhow::{Context, Result};
use std::path::Path;
use tree_sitter::{Node, Parser};

use crate::graph::{CodeEdge, CodeNode, EdgeType, NodeType};

pub fn parse_rust(
    full_path: &Path,
    rel_path: &str,
    module: &str,
) -> Result<(Vec<CodeNode>, Vec<CodeEdge>)> {
    let content = std::fs::read_to_string(full_path)
        .with_context(|| format!("Failed to read {}", full_path.display()))?;

    let mut parser = Parser::new();
    let language = tree_sitter_rust::LANGUAGE;
    parser.set_language(&language.into()).context("Failed to set Rust language")?;

    let tree = parser.parse(&content, None).context("Failed to parse Rust file")?;
    let root = tree.root_node();
    let file_id = rel_path.to_string();

    let mut nodes = vec![CodeNode {
        id: file_id.clone(),
        node_type: NodeType::File,
        name: Path::new(rel_path).file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default(),
        file_path: rel_path.to_string(),
        line: None,
        exported: false,
        module: module.to_string(),
    }];
    let mut edges = Vec::new();

    let mut cursor = root.walk();
    for child in root.children(&mut cursor) {
        match child.kind() {
            "use_declaration" => {
                extract_use(child, &content, &file_id, &mut edges);
            }
            "function_item" => {
                extract_function(child, &content, &file_id, rel_path, module, &mut nodes);
            }
            "struct_item" => {
                extract_struct(child, &content, &file_id, rel_path, module, &mut nodes);
            }
            "enum_item" => {
                extract_enum(child, &content, &file_id, rel_path, module, &mut nodes);
            }
            "impl_item" => {
                extract_impl_methods(child, &content, &file_id, rel_path, module, &mut nodes);
            }
            "trait_item" => {
                extract_trait(child, &content, &file_id, rel_path, module, &mut nodes);
            }
            "mod_item" => {
                // mod foo; or mod foo { ... }
                if let Some(name_node) = find_child(child, "identifier") {
                    let mod_name = text(name_node, &content);
                    edges.push(CodeEdge {
                        from: file_id.clone(),
                        to: format!("{}/{}", rel_path.trim_end_matches(".rs"), mod_name),
                        edge_type: EdgeType::Imports,
                        detail: Some(format!("mod {}", mod_name)),
                    });
                }
            }
            _ => {}
        }
    }

    Ok((nodes, edges))
}

fn extract_use(node: Node, source: &str, file_id: &str, edges: &mut Vec<CodeEdge>) {
    // use crate::foo::bar; or use super::baz;
    let use_text = text(node, source);
    if use_text.contains("crate::") || use_text.contains("super::") {
        let path = use_text
            .trim_start_matches("pub ")
            .trim_start_matches("use ")
            .trim_end_matches(';')
            .replace("::", "/");
        edges.push(CodeEdge {
            from: file_id.to_string(),
            to: path.clone(),
            edge_type: EdgeType::Imports,
            detail: Some(use_text.trim().to_string()),
        });
    }
}

fn extract_function(node: Node, source: &str, file_id: &str, rel_path: &str, module: &str, nodes: &mut Vec<CodeNode>) {
    if let Some(name_node) = find_child(node, "identifier") {
        let name = text(name_node, source);
        let exported = is_pub(node, source);
        let func_id = format!("{}::{}", file_id, name);
        if !nodes.iter().any(|n| n.id == func_id) {
            nodes.push(CodeNode {
                id: func_id,
                node_type: NodeType::Function,
                name,
                file_path: rel_path.to_string(),
                line: Some(node.start_position().row as u32 + 1),
                exported,
                module: module.to_string(),
            });
        }
    }
}

fn extract_struct(node: Node, source: &str, file_id: &str, rel_path: &str, module: &str, nodes: &mut Vec<CodeNode>) {
    if let Some(name_node) = find_child(node, "type_identifier") {
        let name = text(name_node, source);
        let exported = is_pub(node, source);
        nodes.push(CodeNode {
            id: format!("{}::{}", file_id, name),
            node_type: NodeType::Class,
            name,
            file_path: rel_path.to_string(),
            line: Some(node.start_position().row as u32 + 1),
            exported,
            module: module.to_string(),
        });
    }
}

fn extract_enum(node: Node, source: &str, file_id: &str, rel_path: &str, module: &str, nodes: &mut Vec<CodeNode>) {
    if let Some(name_node) = find_child(node, "type_identifier") {
        let name = text(name_node, source);
        let exported = is_pub(node, source);
        nodes.push(CodeNode {
            id: format!("{}::{}", file_id, name),
            node_type: NodeType::Class,
            name,
            file_path: rel_path.to_string(),
            line: Some(node.start_position().row as u32 + 1),
            exported,
            module: module.to_string(),
        });
    }
}

fn extract_trait(node: Node, source: &str, file_id: &str, rel_path: &str, module: &str, nodes: &mut Vec<CodeNode>) {
    if let Some(name_node) = find_child(node, "type_identifier") {
        let name = text(name_node, source);
        nodes.push(CodeNode {
            id: format!("{}::{}", file_id, name),
            node_type: NodeType::Class,
            name,
            file_path: rel_path.to_string(),
            line: Some(node.start_position().row as u32 + 1),
            exported: is_pub(node, source),
            module: module.to_string(),
        });
    }
}

fn extract_impl_methods(node: Node, source: &str, file_id: &str, rel_path: &str, module: &str, nodes: &mut Vec<CodeNode>) {
    // Get the type being impl'd
    let impl_type = find_child(node, "type_identifier")
        .map(|n| text(n, source))
        .unwrap_or_default();

    // Find the impl body
    if let Some(body) = find_child(node, "declaration_list") {
        let mut cursor = body.walk();
        for child in body.children(&mut cursor) {
            if child.kind() == "function_item" {
                if let Some(name_node) = find_child(child, "identifier") {
                    let name = text(name_node, source);
                    let exported = is_pub(child, source);
                    let full_name = if impl_type.is_empty() {
                        name.clone()
                    } else {
                        format!("{}::{}", impl_type, name)
                    };
                    let func_id = format!("{}::{}", file_id, full_name);
                    if !nodes.iter().any(|n| n.id == func_id) {
                        nodes.push(CodeNode {
                            id: func_id,
                            node_type: NodeType::Function,
                            name: full_name,
                            file_path: rel_path.to_string(),
                            line: Some(child.start_position().row as u32 + 1),
                            exported,
                            module: module.to_string(),
                        });
                    }
                }
            }
        }
    }
}

fn is_pub(node: Node, source: &str) -> bool {
    let count = node.child_count();
    for i in 0..count {
        if let Some(child) = node.child(i) {
            if child.kind() == "visibility_modifier" && text(child, source).starts_with("pub") {
                return true;
            }
        }
    }
    false
}

fn text(node: Node, source: &str) -> String {
    source[node.byte_range()].to_string()
}

fn find_child<'a>(node: Node<'a>, kind: &str) -> Option<Node<'a>> {
    let count = node.child_count();
    for i in 0..count {
        if let Some(child) = node.child(i) {
            if child.kind() == kind {
                return Some(child);
            }
        }
    }
    None
}
