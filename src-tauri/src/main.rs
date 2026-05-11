#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use tauri::RunEvent;

fn main() {
    let backend_child = Arc::new(Mutex::new(None::<Child>));
    let managed_backend = backend_child.clone();

    tauri::Builder::default()
        .setup(move |_app| {
            if let Some(child) = spawn_backend_if_needed()? {
                *managed_backend.lock().expect("backend mutex poisoned") = Some(child);
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build Jarvis shell")
        .run(move |_app_handle, event| {
            if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
                if let Some(mut child) = backend_child.lock().expect("backend mutex poisoned").take() {
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        });
}

fn spawn_backend_if_needed() -> Result<Option<Child>, Box<dyn std::error::Error>> {
    if TcpStream::connect("127.0.0.1:8731").is_ok() {
        return Ok(None);
    }

    let root = find_project_root().ok_or("could not locate project root")?;
    let python = find_python(&root).ok_or("could not locate python runtime for backend")?;
    let backend_main = root.join("backend").join("main.py");

    let child = Command::new(python)
        .arg(backend_main)
        .current_dir(root)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()?;

    Ok(Some(child))
}

fn find_project_root() -> Option<PathBuf> {
    let mut candidates = Vec::new();
    if let Ok(current_dir) = std::env::current_dir() {
        candidates.push(current_dir);
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            candidates.push(parent.to_path_buf());
        }
    }
    candidates.push(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."));

    for candidate in candidates {
        for ancestor in candidate.ancestors() {
            let root = ancestor.to_path_buf();
            if root.join("backend").join("main.py").exists() && root.join("frontend").exists() {
                return Some(root);
            }
        }
    }
    None
}

fn find_python(root: &Path) -> Option<PathBuf> {
    let venv_python = root.join(".venv").join("bin").join("python");
    if venv_python.exists() {
        return Some(venv_python);
    }
    Some(PathBuf::from("python3"))
}
