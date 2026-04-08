use anyhow::{Context, Result};
use globset::{Glob, GlobSet, GlobSetBuilder};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::{Path, PathBuf};

// --- Module Definition ---

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModuleDef {
    pub name: String,
    pub description: String,
    #[serde(default)]
    pub paths: Vec<String>,
    #[serde(default)]
    pub exclude: Vec<String>,
    #[serde(default)]
    pub entry_points: Vec<String>,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default)]
    pub updated_at: String,
}

// --- Module Index (_index.yaml) ---

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModuleIndex {
    #[serde(default = "default_version")]
    pub version: u32,
    #[serde(default)]
    pub project: String,
    #[serde(default)]
    pub updated_at: String,
    #[serde(default)]
    pub dimensions: HashMap<String, Vec<DimensionGroup>>,
}

fn default_version() -> u32 {
    1
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DimensionGroup {
    pub name: String,
    /// Sub-groups (recursive nesting)
    #[serde(default)]
    pub children: Vec<DimensionGroup>,
    /// Modules directly belonging to this group
    #[serde(default)]
    pub modules: Vec<String>,
    #[serde(default)]
    pub verified: bool,
}

// --- Module Registry (manages all module operations) ---

pub struct ModuleRegistry {
    temper_dir: PathBuf,
    project_files: Vec<String>, // all source files in project
}

impl ModuleRegistry {
    pub fn new(temper_dir: &Path, project_files: Vec<String>) -> Self {
        Self {
            temper_dir: temper_dir.to_path_buf(),
            project_files,
        }
    }

    fn modules_dir(&self) -> PathBuf {
        self.temper_dir.join("modules")
    }

    fn index_path(&self) -> PathBuf {
        self.modules_dir().join("_index.yaml")
    }

    fn module_path(&self, name: &str) -> PathBuf {
        // "web-server/user" → "web-server--user.yaml"
        let filename = name.replace('/', "--");
        self.modules_dir().join(format!("{}.yaml", filename))
    }

    // --- CRUD ---

    pub fn define_module(&self, module: &ModuleDef) -> Result<()> {
        std::fs::create_dir_all(self.modules_dir())?;

        let path = self.module_path(&module.name);
        let content = serde_yaml::to_string(module)?;
        std::fs::write(&path, content)
            .with_context(|| format!("Failed to write module: {}", path.display()))?;

        // Update index
        self.update_index_for_module(module)?;

        Ok(())
    }

    pub fn remove_module(&self, name: &str) -> Result<bool> {
        let path = self.module_path(name);
        if !path.exists() {
            return Ok(false);
        }

        std::fs::remove_file(&path)?;

        // Update index: remove from all dimensions
        let mut index = self.load_index()?;
        for groups in index.dimensions.values_mut() {
            for group in groups.iter_mut() {
                remove_module_from_children(&mut group.children, name);
                group.modules.retain(|m| m != name);
            }
            // Remove empty groups
            groups.retain(|g| !g.children.is_empty() || !g.modules.is_empty());
        }
        self.save_index(&index)?;

        Ok(true)
    }

    pub fn get_module(&self, name: &str) -> Result<Option<ModuleDef>> {
        let path = self.module_path(name);
        if !path.exists() {
            return Ok(None);
        }

        let content = std::fs::read_to_string(&path)?;
        let module: ModuleDef = serde_yaml::from_str(&content)?;
        Ok(Some(module))
    }

    pub fn list_modules(&self) -> Result<Vec<ModuleDef>> {
        let dir = self.modules_dir();
        if !dir.exists() {
            return Ok(Vec::new());
        }

        let mut modules = Vec::new();
        for entry in std::fs::read_dir(&dir)? {
            let entry = entry?;
            let name = entry.file_name().to_string_lossy().to_string();

            // Skip _index.yaml and non-yaml files
            if name.starts_with('_') || !name.ends_with(".yaml") {
                continue;
            }

            let content = std::fs::read_to_string(entry.path())?;
            if let Ok(module) = serde_yaml::from_str::<ModuleDef>(&content) {
                modules.push(module);
            }
        }

        modules.sort_by(|a, b| a.name.cmp(&b.name));
        Ok(modules)
    }

    // --- Glob matching ---

    /// Resolve which project files match a module's paths/exclude globs.
    pub fn resolve_files(&self, module: &ModuleDef) -> Result<Vec<String>> {
        if module.paths.is_empty() {
            return Ok(Vec::new());
        }

        let include_set = build_globset(&module.paths)?;
        let exclude_set = if module.exclude.is_empty() {
            None
        } else {
            Some(build_globset(&module.exclude)?)
        };

        let matched: Vec<String> = self
            .project_files
            .iter()
            .filter(|f| {
                let included = include_set.is_match(f.as_str());
                let excluded = exclude_set
                    .as_ref()
                    .map(|ex| ex.is_match(f.as_str()))
                    .unwrap_or(false);
                included && !excluded
            })
            .cloned()
            .collect();

        Ok(matched)
    }

    /// Get file count for a module (for list display).
    pub fn file_count(&self, module: &ModuleDef) -> usize {
        self.resolve_files(module).map(|f| f.len()).unwrap_or(0)
    }

    // --- Index management ---

    pub fn load_index(&self) -> Result<ModuleIndex> {
        let path = self.index_path();
        if !path.exists() {
            return Ok(ModuleIndex {
                version: 1,
                project: String::new(),
                updated_at: now_string(),
                dimensions: HashMap::new(),
            });
        }

        let content = std::fs::read_to_string(&path)?;
        Ok(serde_yaml::from_str(&content)?)
    }

    pub fn save_index(&self, index: &ModuleIndex) -> Result<()> {
        std::fs::create_dir_all(self.modules_dir())?;
        let content = serde_yaml::to_string(index)?;
        std::fs::write(self.index_path(), content)?;
        Ok(())
    }

    /// Update index after defining a module.
    /// Only modifies non-verified dimension entries.
    fn update_index_for_module(&self, module: &ModuleDef) -> Result<()> {
        let mut index = self.load_index()?;
        index.updated_at = now_string();

        // Auto-infer by-service dimension from module name
        // "web-server/user" → nested: service="web-server" → child="user"
        // "backend/masterloader/plugin/radius" → 4-level nesting
        let parts: Vec<&str> = module.name.split('/').collect();
        if parts.len() >= 2 {
            let by_service = index
                .dimensions
                .entry("by-service".to_string())
                .or_insert_with(Vec::new);

            insert_into_dimension_tree(by_service, &parts, &module.name);
        }

        // Auto-infer by-function from tags
        if !module.tags.is_empty() {
            let by_function = index
                .dimensions
                .entry("by-function".to_string())
                .or_insert_with(Vec::new);

            for tag in &module.tags {
                let group = by_function
                    .iter_mut()
                    .find(|g| g.name == *tag);

                if let Some(group) = group {
                    if !group.verified && !group.modules.contains(&module.name) {
                        group.modules.push(module.name.clone());
                    }
                } else {
                    by_function.push(DimensionGroup {
                        name: tag.clone(),
                        children: Vec::new(),
                        modules: vec![module.name.clone()],
                        verified: false,
                    });
                }
            }
        }

        self.save_index(&index)?;
        Ok(())
    }

    /// List modules filtered by dimension and optional group path.
    /// group_path: "backend/web-server" → only modules under that subtree.
    pub fn list_modules_by_dimension(
        &self,
        dimension: &str,
        group_path: Option<&str>,
    ) -> Result<Vec<String>> {
        let index = self.load_index()?;
        let groups = match index.dimensions.get(dimension) {
            Some(g) => g,
            None => return Ok(Vec::new()),
        };

        if let Some(path) = group_path {
            let segments: Vec<&str> = path.split('/').collect();
            let mut current = groups.as_slice();
            for seg in &segments {
                match current.iter().find(|g| g.name == *seg) {
                    Some(group) => current = &group.children,
                    None => return Ok(Vec::new()),
                }
            }
            // Collect all modules from this subtree
            let mut result = Vec::new();
            // Find the target group
            let target = {
                let mut cur = groups.as_slice();
                let mut found = None;
                for seg in &segments {
                    if let Some(g) = cur.iter().find(|g| g.name == *seg) {
                        found = Some(g);
                        cur = &g.children;
                    }
                }
                found
            };
            if let Some(group) = target {
                collect_modules_recursive(group, &mut result);
            }
            Ok(result)
        } else {
            let mut result = Vec::new();
            for group in groups {
                collect_modules_recursive(group, &mut result);
            }
            Ok(result)
        }
    }
}

// --- Helpers ---

fn build_globset(patterns: &[String]) -> Result<GlobSet> {
    let mut builder = GlobSetBuilder::new();
    for pattern in patterns {
        builder.add(
            Glob::new(pattern)
                .with_context(|| format!("Invalid glob pattern: {}", pattern))?,
        );
    }
    builder.build().context("Failed to build glob set")
}

fn now_string() -> String {
    chrono::Utc::now().format("%Y-%m-%d").to_string()
}

/// Insert a module into a dimension tree at the right nesting level.
/// parts: ["web-server", "user"] → find/create "web-server" group, add "user" as module leaf.
fn insert_into_dimension_tree(
    groups: &mut Vec<DimensionGroup>,
    parts: &[&str],
    module_name: &str,
) {
    if parts.is_empty() {
        return;
    }

    let group_name = parts[0];
    let group = groups.iter_mut().find(|g| g.name == group_name);

    if parts.len() == 1 {
        // Leaf: add module to an existing or new group at this level
        // (shouldn't normally happen — leaf is the module itself)
        return;
    }

    let group = if let Some(g) = group {
        g
    } else {
        groups.push(DimensionGroup {
            name: group_name.to_string(),
            children: Vec::new(),
            modules: Vec::new(),
            verified: false,
        });
        groups.last_mut().unwrap()
    };

    if group.verified {
        return; // Don't modify user-verified groups
    }

    if parts.len() == 2 {
        // Last segment before leaf — add module_name to this group's modules
        if !group.modules.contains(&module_name.to_string()) {
            group.modules.push(module_name.to_string());
        }
    } else {
        // More levels to go — recurse into children
        insert_into_dimension_tree(&mut group.children, &parts[1..], module_name);
    }
}

/// Recursively remove a module from nested children groups.
fn remove_module_from_children(children: &mut Vec<DimensionGroup>, module_name: &str) {
    for child in children.iter_mut() {
        child.modules.retain(|m| m != module_name);
        remove_module_from_children(&mut child.children, module_name);
    }
    // Remove empty groups
    children.retain(|c| !c.children.is_empty() || !c.modules.is_empty());
}

/// Recursively collect all module names from a dimension group and its children.
fn collect_modules_recursive(group: &DimensionGroup, result: &mut Vec<String>) {
    result.extend(group.modules.iter().cloned());
    for child in &group.children {
        collect_modules_recursive(child, result);
    }
}
