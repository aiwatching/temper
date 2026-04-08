use std::collections::HashMap;

/// A suggested module from auto-analysis.
#[derive(Debug, Clone)]
pub struct ModuleSuggestion {
    pub name: String,
    pub description: String,
    pub paths: Vec<String>,
    pub tags: Vec<String>,
    pub file_count: usize,
}

/// Analyze project file paths and suggest module boundaries.
/// For Java: finds the optimal package split level where hierarchy diverges most.
pub fn suggest_modules(files: &[String]) -> Vec<ModuleSuggestion> {
    // Extract Java package paths
    let java_files: Vec<&String> = files.iter().filter(|f| f.ends_with(".java")).collect();

    if java_files.is_empty() {
        return Vec::new();
    }

    // Parse package structure from file paths
    // "src/main/java/com/example/user/UserController.java" → "com/example/user"
    let packages: Vec<PackageInfo> = java_files
        .iter()
        .filter_map(|f| extract_java_package(f))
        .collect();

    if packages.is_empty() {
        return Vec::new();
    }

    // Find optimal split level
    let suggestions = find_optimal_split(&packages, files);
    suggestions
}

#[derive(Debug, Clone)]
struct PackageInfo {
    /// Package segments: ["com", "example", "user"]
    segments: Vec<String>,
    /// Original file path
    file_path: String,
    /// Directory containing Java source (e.g., "src/main/java/")
    source_root: String,
}

/// Extract package info from a Java file path.
/// "src/main/java/com/example/user/UserController.java" →
///   segments=["com","example","user"], source_root="src/main/java/"
fn extract_java_package(file_path: &str) -> Option<PackageInfo> {
    // Find the Java source root (contains "java/" or "src/")
    let source_markers = ["src/main/java/", "src/test/java/", "src/java/", "java/"];

    for marker in &source_markers {
        if let Some(pos) = file_path.find(marker) {
            let source_root = &file_path[..pos + marker.len()];
            let remainder = &file_path[pos + marker.len()..];

            // remainder: "com/example/user/UserController.java"
            // Remove the filename to get package path
            if let Some(last_slash) = remainder.rfind('/') {
                let pkg_path = &remainder[..last_slash];
                let segments: Vec<String> =
                    pkg_path.split('/').map(|s| s.to_string()).collect();

                return Some(PackageInfo {
                    segments,
                    file_path: file_path.to_string(),
                    source_root: source_root.to_string(),
                });
            }
        }
    }

    // Fallback: use directory structure directly
    if let Some(last_slash) = file_path.rfind('/') {
        let dir = &file_path[..last_slash];
        let segments: Vec<String> = dir.split('/').map(|s| s.to_string()).collect();
        return Some(PackageInfo {
            segments,
            file_path: file_path.to_string(),
            source_root: String::new(),
        });
    }

    None
}

/// Find the optimal package level to split modules.
/// Strategy: find the depth where the number of distinct children is highest
/// relative to the number of files — this is where the hierarchy diverges most.
fn find_optimal_split(packages: &[PackageInfo], all_files: &[String]) -> Vec<ModuleSuggestion> {
    if packages.is_empty() {
        return Vec::new();
    }

    // Find common prefix length (skip it, it's the base package)
    let min_depth = packages.iter().map(|p| p.segments.len()).min().unwrap_or(0);
    let max_depth = packages.iter().map(|p| p.segments.len()).max().unwrap_or(0);

    if min_depth == 0 || max_depth == 0 {
        return Vec::new();
    }

    // Find common prefix
    let common_prefix_len = find_common_prefix_len(packages);

    // Try each depth level after common prefix, score by divergence
    let mut best_depth = common_prefix_len + 1;
    let mut best_score = 0usize;

    for depth in (common_prefix_len + 1)..=max_depth.min(common_prefix_len + 4) {
        let groups = group_at_depth(packages, depth);
        let num_groups = groups.len();

        // Score: number of groups with >= 2 files (meaningful modules)
        let meaningful = groups.values().filter(|files| files.len() >= 2).count();

        // Prefer levels with more meaningful groups
        let score = meaningful * 10 + num_groups;
        if score > best_score {
            best_score = score;
            best_depth = depth;
        }
    }

    // Generate suggestions at the best depth
    let groups = group_at_depth(packages, best_depth);
    let source_root = packages
        .first()
        .map(|p| p.source_root.as_str())
        .unwrap_or("");

    let mut suggestions: Vec<ModuleSuggestion> = groups
        .into_iter()
        .filter(|(_, files)| !files.is_empty())
        .map(|(group_key, group_files)| {
            let segments: Vec<&str> = group_key.split('/').collect();

            // Generate module name: skip common package prefix, use last 1-2 meaningful segments
            let name = generate_module_name(&segments, common_prefix_len);

            // Generate glob path
            let glob_path = if source_root.is_empty() {
                format!("{}/**/*.java", group_key)
            } else {
                format!("{}{}/**/*.java", source_root, group_key)
            };

            // Generate tags from the last segment
            let tags: Vec<String> = segments
                .iter()
                .skip(common_prefix_len)
                .map(|s| s.to_string())
                .collect();

            let description = format!(
                "Package {} ({} files)",
                group_key,
                group_files.len()
            );

            ModuleSuggestion {
                name,
                description,
                paths: vec![glob_path],
                tags,
                file_count: group_files.len(),
            }
        })
        .collect();

    suggestions.sort_by(|a, b| b.file_count.cmp(&a.file_count));
    suggestions
}

/// Group packages by their path at a given depth.
fn group_at_depth<'a>(
    packages: &'a [PackageInfo],
    depth: usize,
) -> HashMap<String, Vec<&'a PackageInfo>> {
    let mut groups: HashMap<String, Vec<&PackageInfo>> = HashMap::new();
    for pkg in packages {
        if pkg.segments.len() >= depth {
            let key = pkg.segments[..depth].join("/");
            groups.entry(key).or_default().push(pkg);
        }
    }
    groups
}

/// Find how many leading segments are common to all packages.
fn find_common_prefix_len(packages: &[PackageInfo]) -> usize {
    if packages.is_empty() {
        return 0;
    }

    let first = &packages[0].segments;
    let mut prefix_len = 0;

    for i in 0..first.len() {
        if packages
            .iter()
            .all(|p| p.segments.len() > i && p.segments[i] == first[i])
        {
            prefix_len = i + 1;
        } else {
            break;
        }
    }

    prefix_len
}

/// Generate a human-friendly module name from package segments.
/// "com/example/server/user" with common_prefix=2 → "server/user"
fn generate_module_name(segments: &[&str], common_prefix_len: usize) -> String {
    let meaningful: Vec<&str> = segments
        .iter()
        .skip(common_prefix_len)
        .copied()
        .collect();

    if meaningful.is_empty() {
        segments.last().copied().unwrap_or("unknown").to_string()
    } else {
        meaningful.join("/")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_java_package() {
        let info = extract_java_package(
            "src/main/java/com/example/user/UserController.java",
        )
        .unwrap();
        assert_eq!(info.segments, vec!["com", "example", "user"]);
        assert_eq!(info.source_root, "src/main/java/");
    }

    #[test]
    fn test_suggest_modules() {
        let files = vec![
            "src/main/java/com/example/user/UserController.java".to_string(),
            "src/main/java/com/example/user/UserService.java".to_string(),
            "src/main/java/com/example/auth/AuthService.java".to_string(),
            "src/main/java/com/example/auth/TokenManager.java".to_string(),
            "src/main/java/com/example/dao/UserDAO.java".to_string(),
        ];

        let suggestions = suggest_modules(&files);
        assert!(!suggestions.is_empty());

        // Should suggest user, auth, dao modules
        let names: Vec<&str> = suggestions.iter().map(|s| s.name.as_str()).collect();
        assert!(names.contains(&"user"));
        assert!(names.contains(&"auth"));
        assert!(names.contains(&"dao"));
    }
}
