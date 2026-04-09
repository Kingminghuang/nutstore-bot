use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Manager, RunEvent, WindowEvent};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

const DEFAULT_SIDECAR_PORT: u16 = 18765;
const DEFAULT_SIDECAR_HEALTH_TIMEOUT_SECS: u64 = 45;
const PYTHON_SIDECAR_ID: &str = "binaries/nsbot-sidecar";

#[derive(Debug)]
enum ManagedChild {
    Shell(CommandChild),
    Std(Child),
}

impl ManagedChild {
    fn kill(self) -> Result<(), Box<dyn std::error::Error>> {
        match self {
            ManagedChild::Shell(child) => child.kill().map_err(Into::into),
            ManagedChild::Std(mut child) => child.kill().map_err(Into::into),
        }
    }
}

#[derive(Debug)]
struct RunningProcesses {
    sidecar_child: Option<ManagedChild>,
}

impl RunningProcesses {
    fn stop(&mut self) {
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
    sidecar_port: u16,
    last_error: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SidecarClientConfig {
    base_url: String,
    auth_header_value: String,
}

#[derive(Clone, Debug)]
struct InitOutputs {
    ns_bot_home: PathBuf,
    fd_executable: PathBuf,
    rg_executable: PathBuf,
}

#[tauri::command]
fn runtime_status(state: tauri::State<'_, Arc<Mutex<RuntimeState>>>) -> RuntimeStatus {
    let guard = state.lock().expect("runtime state poisoned");
    RuntimeStatus {
        running: guard.processes.is_some(),
        sidecar_port: guard.config.sidecar_port,
        last_error: guard.last_error.clone(),
    }
}

#[tauri::command]
fn get_sidecar_client_config(
    app: AppHandle,
    state: tauri::State<'_, Arc<Mutex<RuntimeState>>>,
) -> Result<SidecarClientConfig, String> {
    let guard = state
        .lock()
        .map_err(|_| "Failed to acquire runtime state".to_string())?;
    read_sidecar_client_config(&app, &guard.config)
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
            sidecar_port: guard.config.sidecar_port,
            last_error: None,
        });
    }

    let config = guard.config.clone();
    match start_runtime(&config, &app) {
        Ok(processes) => {
            guard.last_error = None;
            guard.processes = Some(processes);
            let _ = show_main_window(&app);
        }
        Err(err) => {
            let message = err.to_string();
            eprintln!("[desktop-runtime] runtime retry failed: {message}");
            guard.last_error = Some(message.clone());
            let _ = show_main_window(&app);
            return Err(message);
        }
    }

    Ok(RuntimeStatus {
        running: true,
        sidecar_port: guard.config.sidecar_port,
        last_error: None,
    })
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let runtime_root = app
                .path()
                .resource_dir()
                .map_err(|err| err.to_string())?
                .join("runtime");

            let config = RuntimeConfig {
                runtime_root,
                sidecar_port: DEFAULT_SIDECAR_PORT,
            };

            let state = Arc::new(Mutex::new(RuntimeState {
                config: config.clone(),
                processes: None,
                last_error: None,
            }));

            match start_runtime(&config, app.handle()) {
                Ok(processes) => {
                    let mut guard = state.lock().map_err(|_| "Failed to lock state")?;
                    guard.processes = Some(processes);
                    guard.last_error = None;
                    let _ = show_main_window(app.handle());
                }
                Err(err) => {
                    let message = err.to_string();
                    eprintln!("[desktop-runtime] initial runtime start failed: {message}");
                    let mut guard = state.lock().map_err(|_| "Failed to lock state")?;
                    guard.last_error = Some(message);
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
        .invoke_handler(tauri::generate_handler![
            runtime_status,
            runtime_retry,
            get_sidecar_client_config
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            if let Some(state) = app_handle.try_state::<Arc<Mutex<RuntimeState>>>() {
                if let Ok(mut guard) = state.inner().lock() {
                    if let Some(mut processes) = guard.processes.take() {
                        processes.stop();
                    }
                }
            }
        }
    });
}

fn show_main_window(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let main = app
        .get_webview_window("main")
        .ok_or("main window not found")?;
    main.show()?;
    let _ = main.set_focus();
    Ok(())
}

fn start_runtime(
    config: &RuntimeConfig,
    app: &AppHandle,
) -> Result<RunningProcesses, Box<dyn std::error::Error>> {
    cleanup_stale_sidecar(config.sidecar_port);
    let init_outputs = ensure_runtime_initialized(config, app)?;

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

    let sidecar_child = spawn_python_process(app, PYTHON_SIDECAR_ID, &sidecar_env)?;

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
        return Err(err.into());
    }

    Ok(RunningProcesses {
        sidecar_child: Some(sidecar_child),
    })
}

#[cfg(unix)]
fn cleanup_stale_sidecar(port: u16) {
    let output = Command::new("lsof")
        .args(["-nP", &format!("-iTCP:{port}"), "-sTCP:LISTEN", "-t"])
        .output();

    let Ok(output) = output else {
        return;
    };
    if !output.status.success() {
        return;
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    for line in stdout.lines() {
        if let Ok(pid) = line.trim().parse::<u32>() {
            let _ = Command::new("kill").args(["-TERM", &pid.to_string()]).status();
        }
    }
}

#[cfg(not(unix))]
fn cleanup_stale_sidecar(_port: u16) {}

fn wait_for_http_ok(
    host: &str,
    port: u16,
    path: &str,
    timeout: Duration,
) -> Result<(), String> {
    let start = Instant::now();
    let url = format!("http://{host}:{port}{path}");

    while start.elapsed() < timeout {
        if let Ok(response) = reqwest::blocking::get(&url) {
            if response.status().is_success() {
                return Ok(());
            }
        }

        thread::sleep(Duration::from_millis(250));
    }

    Err(format!(
        "timed out waiting for {url} to return success in {}s",
        timeout.as_secs()
    ))
}

fn copy_dir_recursive(source: &Path, destination: &Path) -> Result<(), Box<dyn std::error::Error>> {
    if !source.exists() {
        return Ok(());
    }

    fs::create_dir_all(destination)?;

    for entry in fs::read_dir(source)? {
        let entry = entry?;
        let source_path = entry.path();
        let destination_path = destination.join(entry.file_name());
        if source_path.is_dir() {
            copy_dir_recursive(&source_path, &destination_path)?;
        } else {
            fs::copy(&source_path, &destination_path)?;
        }
    }

    Ok(())
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
    fs::create_dir_all(&ns_bot_home)?;

    let bin_dir = ns_bot_home.join("bin");
    fs::create_dir_all(&bin_dir)?;

    let templates_source = config.runtime_root.join("templates");
    let templates_target = ns_bot_home.join("templates");
    if templates_source.exists() && !templates_target.exists() {
        copy_dir_recursive(&templates_source, &templates_target)?;
    }

    let fd_name = if cfg!(windows) { "fd.exe" } else { "fd" };
    let rg_name = if cfg!(windows) { "rg.exe" } else { "rg" };
    let fd_source = config.runtime_root.join("search-tools").join(fd_name);
    let rg_source = config.runtime_root.join("search-tools").join(rg_name);
    let fd_target = bin_dir.join(fd_name);
    let rg_target = bin_dir.join(rg_name);

    if !fd_target.exists() {
        fs::copy(&fd_source, &fd_target)?;
    }
    if !rg_target.exists() {
        fs::copy(&rg_source, &rg_target)?;
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&fd_target, fs::Permissions::from_mode(0o755))?;
        fs::set_permissions(&rg_target, fs::Permissions::from_mode(0o755))?;
    }

    Ok(InitOutputs {
        ns_bot_home,
        fd_executable: fd_target,
        rg_executable: rg_target,
    })
}

fn spawn_sidecar_process(
    app: &AppHandle,
    sidecar_id: &str,
    envs: &[(&str, String)],
) -> Result<ManagedChild, Box<dyn std::error::Error>> {
    let mut command = app.shell().sidecar(sidecar_id)?;
    for (key, value) in envs {
        command = command.env(key, value);
    }

    let (_rx, child) = command.spawn()?;
    Ok(ManagedChild::Shell(child))
}

#[cfg(target_os = "macos")]
fn bundled_macos_executable(name: &str) -> Option<PathBuf> {
    let current_executable = std::env::current_exe().ok()?;
    let executable = current_executable.parent()?.join(name);
    executable.exists().then_some(executable)
}

fn spawn_helper_process(
    executable: &Path,
    envs: &[(&str, String)],
) -> Result<ManagedChild, Box<dyn std::error::Error>> {
    let mut command = Command::new(executable);
    command.stdout(Stdio::null()).stderr(Stdio::null());
    for (key, value) in envs {
        command.env(key, value);
    }
    let child = command.spawn()?;
    Ok(ManagedChild::Std(child))
}

fn spawn_python_process(
    app: &AppHandle,
    sidecar_id: &str,
    envs: &[(&str, String)],
) -> Result<ManagedChild, Box<dyn std::error::Error>> {
    #[cfg(target_os = "macos")]
    {
        if let Some(executable) = bundled_macos_executable("nsbot-sidecar") {
            return spawn_helper_process(&executable, envs);
        }
    }

    spawn_sidecar_process(app, sidecar_id, envs)
}

fn read_sidecar_client_config(
    app: &AppHandle,
    config: &RuntimeConfig,
) -> Result<SidecarClientConfig, String> {
    let ns_bot_home = app
        .path()
        .app_data_dir()
        .map_err(|err| err.to_string())?
        .join("NutstoreBot");
    let candidate = ns_bot_home.join("sidecar-client.json");
    if let Ok(raw) = fs::read_to_string(&candidate) {
        if let Ok(payload) = serde_json::from_str::<serde_json::Value>(&raw) {
            if let (Some(base_url), Some(auth_header_value)) = (
                payload.get("baseUrl").and_then(|v| v.as_str()),
                payload.get("authHeaderValue").and_then(|v| v.as_str()),
            ) {
                return Ok(SidecarClientConfig {
                    base_url: base_url.trim_end_matches('/').to_string(),
                    auth_header_value: auth_header_value.to_string(),
                });
            }
        }
    }

    Ok(SidecarClientConfig {
        base_url: format!("http://127.0.0.1:{}", config.sidecar_port),
        auth_header_value: "Bearer dev-token".to_string(),
    })
}
