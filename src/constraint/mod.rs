//! TEMPER-CONSTRAINT lifecycle: scanner → parser → checker → report.
//!
//! The v2 product thesis is that constraint comments embedded in source code
//! are the only reliable way to shape Claude Code's behavior at edit time.
//! Rounds 1, 2, 4 of the benchmark (see benchmark/) show +75pp WIN rate over
//! baseline. Round 3 showed that stale constraints are actively harmful, so
//! this module exists to detect staleness before it causes damage.

pub mod check;
pub mod parser;
pub mod report;
pub mod scanner;
