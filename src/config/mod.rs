use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

fn global_dir() -> PathBuf {
    dirs_home().join(".temper")
}

fn dirs_home() -> PathBuf {
    std::env::var("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
}

#[derive(Debug, Serialize, Deserialize)]
pub struct GlobalConfig {
    pub embedding: EmbeddingConfig,
    pub defaults: DefaultsConfig,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct EmbeddingConfig {
    pub provider: String,
    pub endpoint: String,
    pub model: String,
    pub api_key_env: String,
    #[serde(default = "default_dimensions")]
    pub dimensions: u32,
}

fn default_dimensions() -> u32 {
    1536
}

#[derive(Debug, Serialize, Deserialize)]
pub struct DefaultsConfig {
    pub languages: Vec<String>,
    pub scan_exclude: Vec<String>,
}

impl Default for GlobalConfig {
    fn default() -> Self {
        Self {
            embedding: EmbeddingConfig {
                provider: "openai".into(),
                endpoint: "https://api.openai.com/v1/embeddings".into(),
                model: "text-embedding-3-small".into(),
                api_key_env: "OPENAI_API_KEY".into(),
                dimensions: 1536,
            },
            defaults: DefaultsConfig {
                languages: vec!["java".into()],
                scan_exclude: vec![
                    "node_modules".into(),
                    "target".into(),
                    "build".into(),
                    ".git".into(),
                    ".idea".into(),
                    ".vscode".into(),
                    "__pycache__".into(),
                    ".gradle".into(),
                    ".mvn".into(),
                    "dist".into(),
                    ".next".into(),
                    "out".into(),
                ],
            },
        }
    }
}

impl GlobalConfig {
    fn config_path() -> PathBuf {
        global_dir().join("config.yaml")
    }

    pub fn load_or_default() -> Result<Self> {
        let path = Self::config_path();
        if path.exists() {
            let content = std::fs::read_to_string(&path)
                .with_context(|| format!("Failed to read {}", path.display()))?;
            serde_yaml::from_str(&content)
                .with_context(|| format!("Failed to parse {}", path.display()))
        } else {
            Ok(Self::default())
        }
    }

    pub fn save(&self) -> Result<()> {
        let path = Self::config_path();
        std::fs::create_dir_all(path.parent().unwrap())?;
        let content = serde_yaml::to_string(self)?;
        std::fs::write(&path, content)?;
        Ok(())
    }

    pub fn ensure_default() -> Result<()> {
        let path = Self::config_path();
        if !path.exists() {
            Self::default().save()?;
        }
        Ok(())
    }
}

// --- Project Registry ---

#[derive(Debug, Serialize, Deserialize)]
pub struct ProjectRegistry {
    pub projects: Vec<ProjectEntry>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ProjectEntry {
    pub id: String,
    pub path: String,
    pub initialized_at: u64,
    pub last_scan_at: Option<u64>,
}

impl ProjectRegistry {
    fn registry_path() -> PathBuf {
        global_dir().join("projects.json")
    }

    pub fn load() -> Result<Self> {
        let path = Self::registry_path();
        if path.exists() {
            let content = std::fs::read_to_string(&path)?;
            Ok(serde_json::from_str(&content)?)
        } else {
            Ok(Self {
                projects: Vec::new(),
            })
        }
    }

    pub fn save(&self) -> Result<()> {
        let path = Self::registry_path();
        std::fs::create_dir_all(path.parent().unwrap())?;
        let content = serde_json::to_string_pretty(self)?;
        std::fs::write(&path, content)?;
        Ok(())
    }
}

pub fn register_project(project_path: &Path) -> Result<()> {
    let mut registry = ProjectRegistry::load()?;

    let path_str = project_path.to_string_lossy().to_string();

    // Check if already registered
    if registry.projects.iter().any(|p| p.path == path_str) {
        return Ok(());
    }

    // Generate ID from directory name
    let id = project_path
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_else(|| "project".into())
        .to_lowercase();

    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();

    registry.projects.push(ProjectEntry {
        id,
        path: path_str,
        initialized_at: now,
        last_scan_at: None,
    });

    registry.save()?;
    Ok(())
}
