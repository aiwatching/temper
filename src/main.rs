mod cli;
mod config;
mod export;
mod graph;
mod mcp;
mod memory;
mod modules;
mod parser;
mod storage;

use anyhow::Result;
use clap::Parser;
use cli::Cli;

fn main() -> Result<()> {
    let cli = Cli::parse();
    cli::run(cli)
}
