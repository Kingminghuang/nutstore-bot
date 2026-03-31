use serde::Serialize;
use std::fs;
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Manager, Url, WindowEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const DEFAULT_NEXT_PORT: u16 = 3000;
const DEFAULT_SIDECAR_PORT: u16 = 8765;
const DEFAULT_SIDECAR_HEALTH_TIMEOUT_SECS: u64 = 45;
const NEXT_SIDECAR_ID: &str = "binaries/next-sidecar";
const PYTHON_SIDECAR_ID: &str = "binaries/nsbot-sidecar";

#[derive(Debug)]
struct RunningProcesses {
    next_child: Option<CommandChild>,
    sidecar_child: Option<CommandChild>,
}

impl RunningProcesses {
    fn stop(&mut self) {
        if let Some(child) = self.next_child.take() {
            let _ = child.kill();
        }
        if let Some(child) = self.sidecar_child.take() {
            let _ = child.kill();
        }
    }
}

impl Drop for RunningProcesses {
    fn drop(&mut self) {
        self.stop();
    }
}

#[derive(Clone, Debug)]
struct RuntimeConfig {
    runtime_root: PathBuf,
    next_port: u16,
    sidecar_port: u16,
}

#[derive(Debug)]
struct RuntimeState {
    config: RuntimeConfig,
    processes: Option<RunningProcesses>,
    last_error: Option<String>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct RuntimeStatus {
    running: bool,
    next_port: u16,
    sidecar_port: u16,
    last_error: Option<String>,
}

#[derive(Clone, Debug)]
struct InitOutputs {
    ns_bot_home: PathBuf,
    fd_executable: PathBuf,
    rg_executable: PathBuf,
}

fn log_runtime_error(context: &str, err: &str) {
    eprintln!("[desktop-runtime] {context} failed: {err}");
}

fn format_path_error(action: &str, path: &Path, err: &dyn std::error::Error) -> String {
    format!("{action} {} failed: {err}", path.display())
}

fn show_main_window(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let main = app
        .get_webview_window("main")
        .ok_or("main window not found")?;
    main.show()?;
    let _ = main.set_focus();
    Ok(())
}

#[tauri::command]
fn runtime_status(state: tauri::State<'_, Arc<Mutex<RuntimeState>>>) -> RuntimeStatus {
    let state_guard = state.lock().expect("runtime state poisoned");
    RuntimeStatus {
        running: state_guard.processes.is_some(),
        next_port: state_guard.config.next_port,
        sidecar_port: state_guard.config.sidecar_port,
        last_error: state_guard.last_error.clone(),
    }
}

#[tauri::command]
fn runtime_retry(
    app: AppHandle,
    state: tauri::State<'_, Arc<Mutex<RuntimeState>>>,
) -> Result<RuntimeStatus, String> {
    let mut guard = state
        .lock()
        .map_err(|_| "Failed to acquire runtime state".to_string())?;

    if guard.processes.is_some() {
        return Ok(RuntimeStatus {
            running: true,
            next_port: guard.config.next_port,
            sidecar_port: guard.config.sidecar_port,
            last_error: None,
        });
    }

    let config = guard.config.clone();
    match start_runtime(&config, &app) {
        Ok(mut processes) => {
            if let Err(err) = navigate_to_next(&app, guard.config.next_port) {
                let err_message = err.to_string();
                log_runtime_error("runtime retry navigate", &err_message);
                processes.stop();
                guard.last_error = Some(err_message.clone());
                let _ = show_main_window(&app);
                return Err(err_message);
            }
            guard.last_error = None;
            guard.processes = Some(processes);
            if let Err(err) = show_main_window(&app) {
                let err_message = err.to_string();
                log_runtime_error("runtime retry show window", &err_message);
                guard.last_error = Some(err_message.clone());
                if let Some(mut processes) = guard.processes.take() {
                    processes.stop();
                }
                return Err(err_message);
            }
        }
        Err(err) => {
            let err_message = err.to_string();
            log_runtime_error("runtime retry start", &err_message);
            guard.last_error = Some(err_message.clone());
            let _ = show_main_window(&app);
            return Err(err_message);
        }
    }

    Ok(RuntimeStatus {
        running: guard.processes.is_some(),
        next_port: guard.config.next_port,
        sidecar_port: guard.config.sidecar_port,
        last_error: guard.last_error.clone(),
    })
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let runtime_root = app
                .path()
                .resource_dir()
                .map_err(|err| err.to_string())?
                .join("runtime");

            let config = RuntimeConfig {
                runtime_root,
                next_port: DEFAULT_NEXT_PORT,
                sidecar_port: DEFAULT_SIDECAR_PORT,
            };

            let state = Arc::new(Mutex::new(RuntimeState {
                config: config.clone(),
                processes: None,
                last_error: None,
            }));

            match start_runtime(&config, app.handle()) {
                Ok(mut processes) => {
                    if let Err(err) = navigate_to_next(app.handle(), config.next_port) {
                        let err_message = err.to_string();
                        log_runtime_error("initial navigate", &err_message);
                        processes.stop();
                        let mut guard = state.lock().map_err(|_| "Failed to lock state")?;
                        guard.last_error = Some(err_message.clone());
                        let _ = show_main_window(app.handle());
                    } else {
                        let mut guard = state.lock().map_err(|_| "Failed to lock state")?;
                        guard.processes = Some(processes);
                        guard.last_error = None;
                        if let Err(err) = show_main_window(app.handle()) {
                            let err_message = err.to_string();
                            log_runtime_error("initial show window", &err_message);
                            if let Some(mut processes) = guard.processes.take() {
                                processes.stop();
                            }
                            guard.last_error = Some(err_message);
                        }
                    }
                }
                Err(err) => {
                    let err_message = err.to_string();
                    log_runtime_error("initial runtime start", &err_message);
                    let mut guard = state.lock().map_err(|_| "Failed to lock state")?;
                    guard.last_error = Some(err_message);
                    let _ = show_main_window(app.handle());
                }
            }

            if let Some(main_window) = app.get_webview_window("main") {
                let state_for_close = state.clone();
                main_window.on_window_event(move |event| {
                    if matches!(
                        event,
                        WindowEvent::CloseRequested { .. } | WindowEvent::Destroyed
                    ) {
                        if let Ok(mut guard) = state_for_close.lock() {
                            if let Some(mut processes) = guard.processes.take() {
                                processes.stop();
                            }
                        }
                    }
                });
            }

            app.manage(state);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![runtime_status, runtime_retry])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

fn navigate_to_next(app: &AppHandle, next_port: u16) -> Result<(), Box<dyn std::error::Error>> {
    let url = format!("http://127.0.0.1:{next_port}");
    let parsed = Url::parse(&url)?;
    let main = app
        .get_webview_window("main")
        .ok_or("main window not found")?;
    main.navigate(parsed)?;
    Ok(())
}

fn start_runtime(
    config: &RuntimeConfig,
    app: &AppHandle,
) -> Result<RunningProcesses, Box<dyn std::error::Error>> {
    let init_outputs = ensure_runtime_initialized(config, app)?;

    let next_env = vec![
        ("HOSTNAME", "127.0.0.1".to_string()),
        ("PORT", config.next_port.to_string()),
        ("NEXT_TELEMETRY_DISABLED", "1".to_string()),
        (
            "NS_BOT_HOME",
            init_outputs.ns_bot_home.to_string_lossy().to_string(),
        ),
    ];
    let (next_child, next_logs) =
        spawn_sidecar_process(app, NEXT_SIDECAR_ID, &next_env).map_err(|err| {
            format_with_logs(
                &format!("failed to spawn sidecar {NEXT_SIDECAR_ID}: {err}"),
                &[],
            )
        })?;

    if let Err(err) = wait_for_tcp("127.0.0.1", config.next_port, Duration::from_secs(30)) {
        let _ = next_child.kill();
        return Err(format_with_logs(
            &err.to_string(),
            &[("next-sidecar", &next_logs)],
        )
        .into());
    }

    let sidecar_env = vec![
        ("NS_BOT_HOST", "127.0.0.1".to_string()),
        ("NS_BOT_PORT", config.sidecar_port.to_string()),
        (
            "NS_BOT_HOME",
            init_outputs.ns_bot_home.to_string_lossy().to_string(),
        ),
        (
            "NSBOT_FD_EXECUTABLE",
            init_outputs.fd_executable.to_string_lossy().to_string(),
        ),
        (
            "NSBOT_RG_EXECUTABLE",
            init_outputs.rg_executable.to_string_lossy().to_string(),
        ),
    ];

    let (sidecar_child, sidecar_logs) =
        match spawn_sidecar_process(app, PYTHON_SIDECAR_ID, &sidecar_env) {
            Ok(result) => result,
            Err(err) => {
                let _ = next_child.kill();
                return Err(format_with_logs(
                    &format!("failed to spawn sidecar {PYTHON_SIDECAR_ID}: {err}"),
                    &[("next-sidecar", &next_logs)],
                )
                .into());
            }
        };

    let sidecar_health_timeout = std::env::var("NSBOT_SIDECAR_HEALTH_TIMEOUT_SECONDS")
        .ok()
        .and_then(|raw| raw.parse::<u64>().ok())
        .filter(|seconds| *seconds > 0)
        .unwrap_or(DEFAULT_SIDECAR_HEALTH_TIMEOUT_SECS);

    if let Err(err) = wait_for_http_ok(
        "127.0.0.1",
        config.sidecar_port,
        "/health",
        Duration::from_secs(sidecar_health_timeout),
    ) {
        let _ = sidecar_child.kill();
        let _ = next_child.kill();
        return Err(format_with_logs(
            &err.to_string(),
            &[
                ("next-sidecar", &next_logs),
                ("python-sidecar", &sidecar_logs),
            ],
        )
        .into());
    }

    Ok(RunningProcesses {
        next_child: Some(next_child),
        sidecar_child: Some(sidecar_child),
    })
}

fn spawn_sidecar_process(
    app: &AppHandle,
    sidecar_id: &str,
    envs: &[(&str, String)],
) -> Result<(CommandChild, Arc<Mutex<Vec<String>>>), Box<dyn std::error::Error>> {
    let mut command = app.shell().sidecar(sidecar_id)?;
    for (key, value) in envs {
        command = command.env(key, value);
    }

    let (mut rx, child) = command.spawn()?;

    let log_buffer = Arc::new(Mutex::new(Vec::new()));
    let log_buffer_for_task = log_buffer.clone();
    let sidecar_label = sidecar_id.to_string();

    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stderr(payload) => {
                    push_sidecar_log_line(
                        &log_buffer_for_task,
                        &sidecar_label,
                        "stderr",
                        &payload,
                    );
                }
                CommandEvent::Stdout(payload) => {
                    push_sidecar_log_line(
                        &log_buffer_for_task,
                        &sidecar_label,
                        "stdout",
                        &payload,
                    );
                }
                CommandEvent::Error(message) => {
                    if !message.trim().is_empty() {
                        let formatted = format!("spawn error: {message}");
                        eprintln!("[{sidecar_label}] {formatted}");
                        if let Ok(mut logs) = log_buffer_for_task.lock() {
                            logs.push(formatted);
                        }
                    }
                }
                CommandEvent::Terminated(payload) => {
                    let summary = format!(
                        "terminated (code={:?}, signal={:?})",
                        payload.code, payload.signal
                    );
                    eprintln!("[{sidecar_label}] {summary}");
                    if let Ok(mut logs) = log_buffer_for_task.lock() {
                        logs.push(summary);
                    }
                }
                _ => {}
            }
        }
    });

    Ok((child, log_buffer))
}

fn push_sidecar_log_line(
    log_buffer: &Arc<Mutex<Vec<String>>>,
    sidecar_label: &str,
    stream: &str,
    payload: &[u8],
) {
    let text = String::from_utf8_lossy(payload).trim().to_string();
    if text.is_empty() {
        return;
    }

    let formatted = format!("{stream}: {text}");
    eprintln!("[{sidecar_label}] {formatted}");
    if let Ok(mut logs) = log_buffer.lock() {
        logs.push(formatted);
        if logs.len() > 120 {
            let drain_count = logs.len() - 120;
            logs.drain(0..drain_count);
        }
    }
}

fn format_with_logs(base_error: &str, sources: &[(&str, &Arc<Mutex<Vec<String>>>)]) -> String {
    let mut parts = vec![base_error.to_string()];

    for (name, source) in sources {
        let lines = match source.lock() {
            Ok(buffer) => buffer.clone(),
            Err(_) => Vec::new(),
        };
        if lines.is_empty() {
            continue;
        }
        let tail = lines.iter().rev().take(6).cloned().collect::<Vec<_>>();
        let tail = tail.into_iter().rev().collect::<Vec<_>>().join(" | ");
        parts.push(format!("{name} output tail: {tail}"));
    }

    parts.join("\n")
}

fn ensure_runtime_initialized(
    config: &RuntimeConfig,
    app: &AppHandle,
) -> Result<InitOutputs, Box<dyn std::error::Error>> {
    let ns_bot_home = app
        .path()
        .app_data_dir()
        .map_err(|err| err.to_string())?
        .join("NutstoreBot");
    fs::create_dir_all(&ns_bot_home)
        .map_err(|err| format_path_error("create app data directory", &ns_bot_home, &err))?;

    let bin_dir = ns_bot_home.join("bin");
    fs::create_dir_all(&bin_dir)
        .map_err(|err| format_path_error("create bin directory", &bin_dir, &err))?;

    let templates_source = config.runtime_root.join("templates");
    let templates_target = ns_bot_home.join("templates");
    if templates_source.exists() && !templates_target.exists() {
        copy_dir_recursive(&templates_source, &templates_target).map_err(|err| {
            format!(
                "copy templates from {} to {} failed: {err}",
                templates_source.display(),
                templates_target.display()
            )
        })?;
    }

    let fd_name = if cfg!(windows) { "fd.exe" } else { "fd" };
    let rg_name = if cfg!(windows) { "rg.exe" } else { "rg" };
    let fd_source = config.runtime_root.join("search-tools").join(fd_name);
    let rg_source = config.runtime_root.join("search-tools").join(rg_name);
    let fd_target = bin_dir.join(fd_name);
    let rg_target = bin_dir.join(rg_name);

    if !fd_target.exists() {
        fs::copy(&fd_source, &fd_target)
            .map_err(|err| format!("copy fd from {} to {} failed: {err}", fd_source.display(), fd_target.display()))?;
    }
    if !rg_target.exists() {
        fs::copy(&rg_source, &rg_target)
            .map_err(|err| format!("copy rg from {} to {} failed: {err}", rg_source.display(), rg_target.display()))?;
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&fd_target, fs::Permissions::from_mode(0o755))
            .map_err(|err| format_path_error("set permissions on", &fd_target, &err))?;
        fs::set_permissions(&rg_target, fs::Permissions::from_mode(0o755))
            .map_err(|err| format_path_error("set permissions on", &rg_target, &err))?;
    }

    Ok(InitOutputs {
        ns_bot_home,
        fd_executable: fd_target,
        rg_executable: rg_target,
    })
}

fn wait_for_tcp(
    host: &str,
    port: u16,
    timeout: Duration,
) -> Result<(), Box<dyn std::error::Error>> {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if TcpStream::connect((host, port)).is_ok() {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(250));
    }
    Err(format!("Timed out waiting for {}:{}", host, port).into())
}

fn wait_for_http_ok(
    host: &str,
    port: u16,
    path: &str,
    timeout: Duration,
) -> Result<(), Box<dyn std::error::Error>> {
    let url = format!("http://{host}:{port}{path}");
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()?;
    let start = Instant::now();
    while start.elapsed() < timeout {
        if let Ok(response) = client.get(&url).send() {
            if response.status().is_success() {
                return Ok(());
            }
        }
        thread::sleep(Duration::from_millis(300));
    }
    Err(format!("Timed out waiting for {url}").into())
}

fn copy_dir_recursive(source: &Path, destination: &Path) -> Result<(), Box<dyn std::error::Error>> {
    fs::create_dir_all(destination)?;
    for entry in fs::read_dir(source)? {
        let entry = entry?;
        let source_path = entry.path();
        let target_path = destination.join(entry.file_name());
        if source_path.is_dir() {
            copy_dir_recursive(&source_path, &target_path)?;
        } else {
            fs::copy(&source_path, &target_path)?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_copy_dir_recursive_rejects_missing_source() {
        let root = std::env::temp_dir().join("nsbot-desktop-copy-test");
        let _ = fs::remove_dir_all(&root);
        let source = root.join("missing");
        let destination = root.join("dest");
        let result = copy_dir_recursive(&source, &destination);
        assert!(result.is_err());
    }
}
