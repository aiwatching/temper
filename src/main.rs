#![allow(dead_code)]

mod analysis;
mod cli;
mod config;
mod constraint;
mod export;
mod graph;
mod modules;
mod parser;

use anyhow::Result;
use clap::Parser;
use cli::Cli;

fn main() -> Result<()> {
    let cli = Cli::parse();
    cli::run(cli)
}
