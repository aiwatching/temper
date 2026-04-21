use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

fn global_dir() -> PathBuf {
    std::env::var("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
        .join(".temper")
}

#[derive(Debug, Serialize, Deserialize, Default)]
pub struct GlobalConfig {
    #[serde(default)]
    pub defaults: DefaultsConfig,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct DefaultsConfig {
    /// Days since a file's last commit at which we warn when a
    /// TEMPER-CONSTRAINT has no Last-Verified header.
    #[serde(default = "default_unverified_warn_days")]
    pub unverified_warn_days: i64,
}

impl Default for DefaultsConfig {
    fn default() -> Self {
        Self { unverified_warn_days: default_unverified_warn_days() }
    }
}

fn default_unverified_warn_days() -> i64 {
    180
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
}
