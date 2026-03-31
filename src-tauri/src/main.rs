use serde::Serialize;
use std::fs;
use std::fs::OpenOptions;
use std::io::{BufRead, BufReader};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tauri::{AppHandle, Manager, RunEvent, Url, WindowEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const DEFAULT_NEXT_PORT: u16 = 13000;
const DEFAULT_SIDECAR_PORT: u16 = 18765;
const DEFAULT_SIDECAR_HEALTH_TIMEOUT_SECS: u64 = 45;
const NEXT_SIDECAR_ID: &str = "binaries/next-sidecar";
const PYTHON_SIDECAR_ID: &str = "binaries/nsbot-sidecar";
const NEXT_HELPER_RELATIVE_EXECUTABLE: &str = "next-helper/next-runtime-helper";

#[derive(Debug)]
enum ManagedChild {
    Shell(CommandChild),
    Std(Child),
}

impl ManagedChild {
    fn kill(self) -> Result<(), Box<dyn std::error::Error>> {
        match self {
            ManagedChild::Shell(child) => child.kill().map_err(Into::into),
            ManagedChild::Std(child) => {
                #[cfg(unix)]
                {
                    let _ = terminate_pid_tree(child.id());
                    return Ok(());
                }

                #[cfg(not(unix))]
                {
                    child.kill().map_err(Into::into)
                }
            }
        }
    }
}

#[cfg(unix)]
fn terminate_pid_tree(root_pid: u32) -> Result<(), Box<dyn std::error::Error>> {
    let descendants = collect_descendant_pids(root_pid);
    let _ = send_signal_to_pids(&descendants, "-TERM");
    let _ = send_signal_to_pid(root_pid, "-TERM");
    thread::sleep(Duration::from_millis(220));
    let _ = send_signal_to_pids(&descendants, "-KILL");
    let _ = send_signal_to_pid(root_pid, "-KILL");
    Ok(())
}

#[cfg(unix)]
fn collect_descendant_pids(root_pid: u32) -> Vec<u32> {
    fn walk(parent_pid: u32, accumulator: &mut Vec<u32>) {
        let output = Command::new("pgrep")
            .args(["-P", &parent_pid.to_string()])
            .output();

        let Ok(output) = output else {
            return;
        };
        if !output.status.success() {
            return;
        }

        let stdout = String::from_utf8_lossy(&output.stdout);
        for line in stdout.lines() {
            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }
            if let Ok(pid) = trimmed.parse::<u32>() {
                accumulator.push(pid);
                walk(pid, accumulator);
            }
        }
    }

    let mut descendants = Vec::new();
    walk(root_pid, &mut descendants);
    descendants
}

#[cfg(unix)]
fn send_signal_to_pid(pid: u32, signal: &str) -> Result<(), Box<dyn std::error::Error>> {
    let status = Command::new("kill")
        .args([signal, &pid.to_string()])
        .status()?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("kill {signal} {pid} failed with status {status}").into())
    }
}

#[cfg(unix)]
fn send_signal_to_pids(pids: &[u32], signal: &str) -> Result<(), Box<dyn std::error::Error>> {
    for pid in pids.iter().rev() {
        let _ = send_signal_to_pid(*pid, signal);
    }
    Ok(())
}

#[cfg(unix)]
fn list_tcp_listener_pids(port: u16) -> Result<Vec<u32>, Box<dyn std::error::Error>> {
    let output = Command::new("lsof")
        .args([
            "-nP",
            &format!("-iTCP:{port}"),
            "-sTCP:LISTEN",
            "-t",
        ])
        .output()?;

    if !output.status.success() {
        return Ok(Vec::new());
    }

    let mut pids = Vec::new();
    let stdout = String::from_utf8_lossy(&output.stdout);
    for line in stdout.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        if let Ok(pid) = trimmed.parse::<u32>() {
            pids.push(pid);
        }
    }
    Ok(pids)
}

#[cfg(unix)]
fn command_line_for_pid(pid: u32) -> Result<String, Box<dyn std::error::Error>> {
    let output = Command::new("ps")
        .args(["-o", "command=", "-p", &pid.to_string()])
        .output()?;
    if !output.status.success() {
        return Ok(String::new());
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

#[cfg(unix)]
fn cleanup_stale_sidecars(config: &RuntimeConfig) {
    let targets: [(u16, &[&str]); 2] = [
        (
            config.next_port,
            &["next-sidecar", "node-runtime", "next-runtime-helper"],
        ),
        (config.sidecar_port, &["nsbot-sidecar"]),
    ];

    for (port, hints) in targets {
        let listener_pids = match list_tcp_listener_pids(port) {
            Ok(pids) => pids,
            Err(err) => {
                eprintln!("[desktop-runtime] failed to inspect listeners on {port}: {err}");
                continue;
            }
        };

        for pid in listener_pids {
            let command = match command_line_for_pid(pid) {
                Ok(text) => text,
                Err(err) => {
                    eprintln!("[desktop-runtime] failed to read pid {pid} command: {err}");
                    continue;
                }
            };

            if hints.iter().any(|hint| command.contains(hint)) {
                eprintln!(
                    "[desktop-runtime] terminating stale listener pid={pid} port={port} cmd={command}"
                );
                let _ = terminate_pid_tree(pid);
            }
        }
    }
}

#[cfg(not(unix))]
fn cleanup_stale_sidecars(_config: &RuntimeConfig) {}

#[derive(Debug)]
struct RunningProcesses {
    next_child: Option<ManagedChild>,
    sidecar_child: Option<ManagedChild>,
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
    sidecar_loggers: RuntimeSidecarLoggers,
}

#[derive(Clone, Debug, Default)]
struct RuntimeSidecarLoggers {
    next: Option<Arc<SidecarFileLogger>>,
    python: Option<Arc<SidecarFileLogger>>,
}

#[derive(Debug)]
struct SidecarFileLogger {
    sidecar_name: &'static str,
    file: Mutex<std::fs::File>,
}

impl SidecarFileLogger {
    fn new(sidecar_name: &'static str, path: &Path) -> Result<Self, Box<dyn std::error::Error>> {
        let file = OpenOptions::new().create(true).append(true).open(path)?;
        Ok(Self {
            sidecar_name,
            file: Mutex::new(file),
        })
    }

    fn write_line(&self, stream: &str, text: &str) {
        if text.is_empty() {
            return;
        }

        let timestamp_ms = unix_timestamp_ms();
        let line = format!(
            "{timestamp_ms} sidecar={} stream={} {}\n",
            self.sidecar_name, stream, text
        );
        if let Ok(mut file) = self.file.lock() {
            use std::io::Write;
            let _ = file.write_all(line.as_bytes());
        }
    }
}

fn unix_timestamp_ms() -> u128 {
    match SystemTime::now().duration_since(UNIX_EPOCH) {
        Ok(duration) => duration.as_millis(),
        Err(_) => 0,
    }
}

fn release_sidecar_logs_enabled() -> bool {
    !cfg!(debug_assertions)
}

fn create_runtime_sidecar_loggers(
    logs_dir: &Path,
) -> Result<RuntimeSidecarLoggers, Box<dyn std::error::Error>> {
    fs::create_dir_all(logs_dir)
        .map_err(|err| format_path_error("create logs directory", logs_dir, &err))?;

    let next = Arc::new(
        SidecarFileLogger::new("next-sidecar", &logs_dir.join("next-sidecar.log")).map_err(
            |err| {
                format!(
                    "open next-sidecar log file under {} failed: {err}",
                    logs_dir.display()
                )
            },
        )?,
    );
    let python = Arc::new(
        SidecarFileLogger::new("nsbot-sidecar", &logs_dir.join("nsbot-sidecar.log")).map_err(
            |err| {
                format!(
                    "open nsbot-sidecar log file under {} failed: {err}",
                    logs_dir.display()
                )
            },
        )?,
    );

    Ok(RuntimeSidecarLoggers {
        next: Some(next),
        python: Some(python),
    })
}

fn logger_for_sidecar(
    sidecar_id: &str,
    sidecar_loggers: &RuntimeSidecarLoggers,
) -> Option<Arc<SidecarFileLogger>> {
    match sidecar_id {
        NEXT_SIDECAR_ID => sidecar_loggers.next.clone(),
        PYTHON_SIDECAR_ID => sidecar_loggers.python.clone(),
        _ => None,
    }
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
    let app = tauri::Builder::default()
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
    cleanup_stale_sidecars(config);
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
    let next_logger = logger_for_sidecar(NEXT_SIDECAR_ID, &init_outputs.sidecar_loggers);
    let python_logger = logger_for_sidecar(PYTHON_SIDECAR_ID, &init_outputs.sidecar_loggers);

    let (next_child, next_logs) =
        spawn_next_process(config, app, &next_env, next_logger).map_err(|err| {
            format_with_logs(
                &format!("failed to spawn Next runtime: {err}"),
                &[("next-runtime", &err.logs)],
            )
        })?;

    if let Err(err) = wait_for_tcp("127.0.0.1", config.next_port, Duration::from_secs(30)) {
        let next_child = next_child;
        let _ = next_child.kill();
        return Err(format_with_logs(&err.to_string(), &[("next-runtime", &next_logs)]).into());
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
        match spawn_python_process(app, PYTHON_SIDECAR_ID, &sidecar_env, python_logger) {
            Ok(result) => result,
            Err(err) => {
                let next_child = next_child;
                let _ = next_child.kill();
                return Err(format_with_logs(
                    &format!("failed to spawn sidecar {PYTHON_SIDECAR_ID}: {err}"),
                    &[
                        ("next-runtime", &next_logs),
                        ("python-sidecar", &err.logs),
                    ],
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
        let next_child = next_child;
        let _ = next_child.kill();
        return Err(format_with_logs(
            &err.to_string(),
            &[
                ("next-runtime", &next_logs),
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
    file_logger: Option<Arc<SidecarFileLogger>>,
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
                        file_logger.as_deref(),
                    );
                }
                CommandEvent::Stdout(payload) => {
                    push_sidecar_log_line(
                        &log_buffer_for_task,
                        &sidecar_label,
                        "stdout",
                        &payload,
                        file_logger.as_deref(),
                    );
                }
                CommandEvent::Error(message) => {
                    if !message.trim().is_empty() {
                        let formatted = format!("spawn error: {message}");
                        eprintln!("[{sidecar_label}] {formatted}");
                        if let Some(logger) = file_logger.as_deref() {
                            logger.write_line("error", &formatted);
                        }
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
                    if let Some(logger) = file_logger.as_deref() {
                        logger.write_line("event", &summary);
                    }
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

struct ManagedSpawnError {
    source: Box<dyn std::error::Error>,
    logs: Arc<Mutex<Vec<String>>>,
}

impl std::fmt::Display for ManagedSpawnError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.source.fmt(f)
    }
}

impl std::fmt::Debug for ManagedSpawnError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ManagedSpawnError")
            .field("source", &self.source.to_string())
            .finish()
    }
}

fn spawn_next_process(
    config: &RuntimeConfig,
    app: &AppHandle,
    envs: &[(&str, String)],
    file_logger: Option<Arc<SidecarFileLogger>>,
) -> Result<(ManagedChild, Arc<Mutex<Vec<String>>>), ManagedSpawnError> {
    #[cfg(target_os = "macos")]
    {
        let helper_executable = config.runtime_root.join(NEXT_HELPER_RELATIVE_EXECUTABLE);
        if helper_executable.exists() {
            return spawn_helper_process(
                &helper_executable,
                "next-runtime-helper",
                envs,
                file_logger,
            )
            .map_err(|err| ManagedSpawnError {
                source: err,
                logs: Arc::new(Mutex::new(Vec::new())),
            });
        }
    }

    spawn_sidecar_process(app, NEXT_SIDECAR_ID, envs, file_logger)
        .map(|(child, logs)| (ManagedChild::Shell(child), logs))
        .map_err(|err| ManagedSpawnError {
            source: err,
            logs: Arc::new(Mutex::new(Vec::new())),
        })
}

fn spawn_python_process(
    app: &AppHandle,
    sidecar_id: &str,
    envs: &[(&str, String)],
    file_logger: Option<Arc<SidecarFileLogger>>,
) -> Result<(ManagedChild, Arc<Mutex<Vec<String>>>), ManagedSpawnError> {
    #[cfg(target_os = "macos")]
    {
        if let Some(executable) = bundled_macos_executable("nsbot-sidecar") {
            return spawn_helper_process(&executable, sidecar_id, envs, file_logger).map_err(
                |err| ManagedSpawnError {
                    source: err,
                    logs: Arc::new(Mutex::new(Vec::new())),
                },
            );
        }
    }

    spawn_sidecar_process(app, sidecar_id, envs, file_logger)
        .map(|(child, logs)| (ManagedChild::Shell(child), logs))
        .map_err(|err| ManagedSpawnError {
            source: err,
            logs: Arc::new(Mutex::new(Vec::new())),
        })
}

#[cfg(target_os = "macos")]
fn bundled_macos_executable(name: &str) -> Option<PathBuf> {
    let current_executable = std::env::current_exe().ok()?;
    let executable = current_executable.parent()?.join(name);
    executable.exists().then_some(executable)
}

fn spawn_helper_process(
    executable: &Path,
    label: &str,
    envs: &[(&str, String)],
    file_logger: Option<Arc<SidecarFileLogger>>,
) -> Result<(ManagedChild, Arc<Mutex<Vec<String>>>), Box<dyn std::error::Error>> {
    let mut command = Command::new(executable);
    command.stdout(Stdio::piped()).stderr(Stdio::piped());
    for (key, value) in envs {
        command.env(key, value);
    }

    let mut child = command.spawn()?;
    let log_buffer = Arc::new(Mutex::new(Vec::new()));

    if let Some(stdout) = child.stdout.take() {
        spawn_stdio_log_thread(
            log_buffer.clone(),
            label.to_string(),
            "stdout",
            stdout,
            file_logger.clone(),
        );
    }
    if let Some(stderr) = child.stderr.take() {
        spawn_stdio_log_thread(
            log_buffer.clone(),
            label.to_string(),
            "stderr",
            stderr,
            file_logger,
        );
    }

    Ok((ManagedChild::Std(child), log_buffer))
}

fn spawn_stdio_log_thread<R: std::io::Read + Send + 'static>(
    log_buffer: Arc<Mutex<Vec<String>>>,
    sidecar_label: String,
    stream: &'static str,
    reader: R,
    file_logger: Option<Arc<SidecarFileLogger>>,
) {
    thread::spawn(move || {
        let mut reader = BufReader::new(reader);
        let mut line = String::new();
        loop {
            line.clear();
            match reader.read_line(&mut line) {
                Ok(0) => break,
                Ok(_) => push_log_line(
                    &log_buffer,
                    &sidecar_label,
                    stream,
                    line.trim_end(),
                    file_logger.as_deref(),
                ),
                Err(err) => {
                    push_log_line(
                        &log_buffer,
                        &sidecar_label,
                        "error",
                        &format!("failed to read child output: {err}"),
                        file_logger.as_deref(),
                    );
                    break;
                }
            }
        }
    });
}

fn push_sidecar_log_line(
    log_buffer: &Arc<Mutex<Vec<String>>>,
    sidecar_label: &str,
    stream: &str,
    payload: &[u8],
    file_logger: Option<&SidecarFileLogger>,
) {
    let text = String::from_utf8_lossy(payload);
    push_log_line(log_buffer, sidecar_label, stream, text.trim(), file_logger);
}

fn push_log_line(
    log_buffer: &Arc<Mutex<Vec<String>>>,
    sidecar_label: &str,
    stream: &str,
    text: &str,
    file_logger: Option<&SidecarFileLogger>,
) {
    if text.is_empty() {
        return;
    }

    let formatted = format!("{stream}: {text}");
    eprintln!("[{sidecar_label}] {formatted}");
    if let Some(logger) = file_logger {
        logger.write_line(stream, text);
    }
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

    let sidecar_loggers = if release_sidecar_logs_enabled() {
        create_runtime_sidecar_loggers(&ns_bot_home.join("logs"))?
    } else {
        RuntimeSidecarLoggers::default()
    };

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
        fs::copy(&fd_source, &fd_target).map_err(|err| {
            format!(
                "copy fd from {} to {} failed: {err}",
                fd_source.display(),
                fd_target.display()
            )
        })?;
    }
    if !rg_target.exists() {
        fs::copy(&rg_source, &rg_target).map_err(|err| {
            format!(
                "copy rg from {} to {} failed: {err}",
                rg_source.display(),
                rg_target.display()
            )
        })?;
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
        sidecar_loggers,
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
