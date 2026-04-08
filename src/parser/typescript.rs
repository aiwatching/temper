use anyhow::{Context, Result};
use std::path::Path;
use tree_sitter::{Node, Parser};

use crate::graph::{CodeEdge, CodeNode, EdgeType, NodeType};

pub fn parse_typescript(
    full_path: &Path,
    rel_path: &str,
    module: &str,
) -> Result<(Vec<CodeNode>, Vec<CodeEdge>)> {
    let content = std::fs::read_to_string(full_path)
        .with_context(|| format!("Failed to read {}", full_path.display()))?;

    let mut parser = Parser::new();
    let is_tsx = rel_path.ends_with(".tsx");
    let language = if is_tsx {
        tree_sitter_typescript::LANGUAGE_TSX
    } else {
        tree_sitter_typescript::LANGUAGE_TYPESCRIPT
    };
    parser.set_language(&language.into()).context("Failed to set TypeScript language")?;

    let tree = parser.parse(&content, None).context("Failed to parse TypeScript file")?;
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

    extract_nodes(root, &content, &file_id, rel_path, module, &mut nodes, &mut edges);

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
            "import_statement" => {
                extract_import(child, source, file_id, edges);
            }
            "export_statement" => {
                // May contain function/class declarations
                let mut inner_cursor = child.walk();
                for inner in child.children(&mut inner_cursor) {
                    match inner.kind() {
                        "function_declaration" | "generator_function_declaration" => {
                            extract_function(inner, source, file_id, rel_path, module, true, nodes);
                        }
                        "class_declaration" => {
                            extract_class(inner, source, file_id, rel_path, module, true, nodes);
                        }
                        "lexical_declaration" => {
                            extract_variable_functions(inner, source, file_id, rel_path, module, true, nodes);
                        }
                        _ => {}
                    }
                }
            }
            "function_declaration" | "generator_function_declaration" => {
                extract_function(child, source, file_id, rel_path, module, false, nodes);
            }
            "class_declaration" => {
                extract_class(child, source, file_id, rel_path, module, false, nodes);
            }
            "lexical_declaration" => {
                extract_variable_functions(child, source, file_id, rel_path, module, false, nodes);
            }
            _ => {}
        }
    }
}

fn extract_import(node: Node, source: &str, file_id: &str, edges: &mut Vec<CodeEdge>) {
    // import { Foo, Bar } from './module'
    if let Some(source_node) = find_child(node, "string") {
        let import_path = source[source_node.byte_range()].trim_matches(|c| c == '\'' || c == '"').to_string();
        if import_path.starts_with('.') || import_path.starts_with('/') {
            let mut details = Vec::new();
            if let Some(clause) = find_child(node, "import_clause") {
                let mut clause_cursor = clause.walk();
                for c in clause.children(&mut clause_cursor) {
                    if c.kind() == "identifier" {
                        details.push(source[c.byte_range()].to_string());
                    }
                    if c.kind() == "named_imports" {
                        let mut imp_cursor = c.walk();
                        for spec in c.children(&mut imp_cursor) {
                            if spec.kind() == "import_specifier" {
                                if let Some(name) = find_child(spec, "identifier") {
                                    details.push(source[name.byte_range()].to_string());
                                }
                            }
                        }
                    }
                }
            }
            edges.push(CodeEdge {
                from: file_id.to_string(),
                to: resolve_ts_import(file_id, &import_path),
                edge_type: EdgeType::Imports,
                detail: if details.is_empty() { None } else { Some(details.join(", ")) },
            });
        }
    }
}

fn extract_function(node: Node, source: &str, file_id: &str, rel_path: &str, module: &str, exported: bool, nodes: &mut Vec<CodeNode>) {
    if let Some(name_node) = find_child(node, "identifier") {
        let name = source[name_node.byte_range()].to_string();
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

fn extract_class(node: Node, source: &str, file_id: &str, rel_path: &str, module: &str, exported: bool, nodes: &mut Vec<CodeNode>) {
    if let Some(name_node) = find_child(node, "type_identifier").or_else(|| find_child(node, "identifier")) {
        let name = source[name_node.byte_range()].to_string();
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

fn extract_variable_functions(node: Node, source: &str, file_id: &str, rel_path: &str, module: &str, exported: bool, nodes: &mut Vec<CodeNode>) {
    // const foo = () => {} or const foo = function() {}
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.kind() == "variable_declarator" {
            if let Some(name_node) = find_child(child, "identifier") {
                if let Some(value) = find_child(child, "arrow_function").or_else(|| find_child(child, "function")) {
                    let name = source[name_node.byte_range()].to_string();
                    let func_id = format!("{}::{}", file_id, name);
                    if !nodes.iter().any(|n| n.id == func_id) {
                        nodes.push(CodeNode {
                            id: func_id,
                            node_type: NodeType::Function,
                            name,
                            file_path: rel_path.to_string(),
                            line: Some(value.start_position().row as u32 + 1),
                            exported,
                            module: module.to_string(),
                        });
                    }
                }
            }
        }
    }
}

fn resolve_ts_import(from_file: &str, import_path: &str) -> String {
    let dir = if from_file.contains('/') {
        from_file.rsplit_once('/').map(|(d, _)| d).unwrap_or(".")
    } else {
        "."
    };
    let mut resolved = format!("{}/{}", dir, import_path);
    if resolved.starts_with("./") {
        resolved = resolved[2..].to_string();
    }
    resolved
}

fn find_child<'a>(node: Node<'a>, kind: &str) -> Option<Node<'a>> {
    let mut cursor = node.walk();
    let result = node.children(&mut cursor).find(|c| c.kind() == kind);
    result
}
