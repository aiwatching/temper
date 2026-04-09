pub mod interfaces;
pub mod registry;
pub mod suggest;

pub use interfaces::{scan_module_interfaces, load_interface, save_interface};
pub use registry::{ModuleDef, ModuleRegistry};
pub use suggest::suggest_modules;
