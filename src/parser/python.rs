use anyhow::{Context, Result};
use std::path::Path;
use tree_sitter::{Node, Parser};

use crate::graph::{CodeEdge, CodeNode, EdgeType, NodeType};

pub fn parse_python(
    full_path: &Path,
    rel_path: &str,
    module: &str,
) -> Result<(Vec<CodeNode>, Vec<CodeEdge>)> {
    let content = std::fs::read_to_string(full_path)
        .with_context(|| format!("Failed to read {}", full_path.display()))?;

    let mut parser = Parser::new();
    let language = tree_sitter_python::LANGUAGE;
    parser.set_language(&language.into()).context("Failed to set Python language")?;

    let tree = parser.parse(&content, None).context("Failed to parse Python file")?;
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
            "import_statement" | "import_from_statement" => {
                extract_import(child, &content, &file_id, &mut edges);
            }
            "function_definition" => {
                extract_function(child, &content, &file_id, rel_path, module, &mut nodes);
            }
            "class_definition" => {
                extract_class(child, &content, &file_id, rel_path, module, &mut nodes);
            }
            "decorated_definition" => {
                // Decorated functions/classes
                let mut inner_cursor = child.walk();
                for inner in child.children(&mut inner_cursor) {
                    match inner.kind() {
                        "function_definition" => extract_function(inner, &content, &file_id, rel_path, module, &mut nodes),
                        "class_definition" => extract_class(inner, &content, &file_id, rel_path, module, &mut nodes),
                        _ => {}
                    }
                }
            }
            _ => {}
        }
    }

    Ok((nodes, edges))
}

fn extract_import(node: Node, source: &str, file_id: &str, edges: &mut Vec<CodeEdge>) {
    let text = &source[node.byte_range()];
    // "from foo.bar import Baz" or "import foo.bar"
    if let Some(module_node) = find_child(node, "dotted_name") {
        let module_path = source[module_node.byte_range()].replace('.', "/");
        let detail = text.split_whitespace().last().unwrap_or("").to_string();
        edges.push(CodeEdge {
            from: file_id.to_string(),
            to: module_path,
            edge_type: EdgeType::Imports,
            detail: Some(detail),
        });
    }
}

fn extract_function(node: Node, source: &str, file_id: &str, rel_path: &str, module: &str, nodes: &mut Vec<CodeNode>) {
    if let Some(name_node) = find_child(node, "identifier") {
        let name = source[name_node.byte_range()].to_string();
        let line = node.start_position().row as u32 + 1;
        let exported = !name.starts_with('_');
        let func_id = format!("{}::{}", file_id, name);
        if !nodes.iter().any(|n| n.id == func_id) {
            nodes.push(CodeNode {
                id: func_id,
                node_type: NodeType::Function,
                name,
                file_path: rel_path.to_string(),
                line: Some(line),
                exported,
                module: module.to_string(),
            });
        }
    }
}

fn extract_class(node: Node, source: &str, file_id: &str, rel_path: &str, module: &str, nodes: &mut Vec<CodeNode>) {
    if let Some(name_node) = find_child(node, "identifier") {
        let name = source[name_node.byte_range()].to_string();
        let line = node.start_position().row as u32 + 1;
        let class_id = format!("{}::{}", file_id, name);
        nodes.push(CodeNode {
            id: class_id,
            node_type: NodeType::Class,
            name,
            file_path: rel_path.to_string(),
            line: Some(line),
            exported: true,
            module: module.to_string(),
        });
    }
}

fn find_child<'a>(node: Node<'a>, kind: &str) -> Option<Node<'a>> {
    let mut cursor = node.walk();
    let result = node.children(&mut cursor).find(|c| c.kind() == kind);
    result
}
