use nutstore_bot_desktop::runtime::{
    app_ns_bot_home, base_sidecar_env_pairs, ensure_runtime_initialized,
    spawn_bundled_process,
};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::collections::HashSet;
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStderr, ChildStdin, Stdio};
use std::sync::mpsc::{self, Receiver, Sender, TryRecvError};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter, Manager, RunEvent, WindowEvent};
use tungstenite::Message;

const DEFAULT_SIDECAR_PORT: u16 = 18765;
const ACP_NOTIFICATION_EVENT: &str = "acp-notification";

fn acp_debug_enabled() -> bool {
    if cfg!(debug_assertions) {
        return true;
    }

    matches!(
        std::env::var("NSBOT_ACP_DEBUG"),
        Ok(value)
            if !matches!(
                value.trim().to_ascii_lowercase().as_str(),
                "" | "0" | "false" | "no" | "off"
            )
    )
}

fn acp_debug_log(message: impl AsRef<str>) {
    if acp_debug_enabled() {
        eprintln!("[acp-bridge] {}", message.as_ref());
    }
}

fn acp_payload_summary(payload: &Value) -> String {
    if let Some(error) = payload.get("error") {
        let code = error.get("code").and_then(Value::as_i64);
        let message = error
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("ACP request failed");
        return match code {
            Some(code) => format!("error code={} message={}", code, message),
            None => format!("error message={}", message),
        };
    }

    match payload.get("result") {
        Some(Value::Object(result)) => {
            let mut keys = result.keys().cloned().collect::<Vec<_>>();
            keys.sort();
            format!("result keys={}", keys.join(","))
        }
        Some(Value::Array(result)) => format!("result array_len={}", result.len()),
        Some(Value::String(result)) => format!("result string_len={}", result.len()),
        Some(Value::Null) | None => "result null".to_string(),
        Some(_) => "result scalar".to_string(),
    }
}

fn acp_incoming_payload_summary(payload: &Value) -> String {
    let request_id = payload.get("id").and_then(Value::as_u64);
    let method = payload.get("method").and_then(Value::as_str);

    match (method, request_id) {
        (Some(method), None) => format!("notification method={method}"),
        (Some(method), Some(request_id)) => {
            format!("server request id={} method={}", request_id, method)
        }
        (None, Some(response_id)) => {
            format!("response id={} {}", response_id, acp_payload_summary(payload))
        }
        (None, None) => "message without method or id".to_string(),
    }
}

fn forward_acp_sidecar_stderr(stderr: ChildStderr) {
    thread::spawn(move || {
        let mut reader = BufReader::new(stderr);
        let mut line = String::new();
        loop {
            line.clear();
            match reader.read_line(&mut line) {
                Ok(0) => break,
                Ok(_) => {
                    let trimmed = line.trim_end_matches(['\r', '\n']);
                    if !trimmed.is_empty() {
                        eprintln!("[acp-sidecar] {trimmed}");
                    }
                }
                Err(err) => {
                    acp_debug_log(format!("failed reading sidecar stderr: {err}"));
                    break;
                }
            }
        }
    });
}

#[derive(Clone, Debug)]
struct RuntimeConfig {
    runtime_root: PathBuf,
    sidecar_port: u16,
}

#[derive(Debug)]
struct RuntimeState {
    config: RuntimeConfig,
}

struct AcpBridgeWorker {
    command_tx: Sender<BridgeCommand>,
    join_handle: Option<thread::JoinHandle<()>>,
}

type BridgeSocket = Box<dyn BridgeTransport + Send>;

trait BridgeTransport {
    fn send_message(&mut self, message: Message) -> Result<(), String>;
    fn read_message(&mut self) -> Result<Option<Message>, String>;
}

impl<T: BridgeTransport + ?Sized> BridgeTransport for Box<T> {
    fn send_message(&mut self, message: Message) -> Result<(), String> {
        (**self).send_message(message)
    }

    fn read_message(&mut self) -> Result<Option<Message>, String> {
        (**self).read_message()
    }
}

struct StdioTransport {
    child: Child,
    writer: BufWriter<ChildStdin>,
    reader_rx: Receiver<String>,
}

impl StdioTransport {
    fn new(mut child: Child) -> Result<Self, String> {
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| "Missing ACP stdio stdin".to_string())?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "Missing ACP stdio stdout".to_string())?;
        if let Some(stderr) = child.stderr.take() {
            forward_acp_sidecar_stderr(stderr);
        }
        let (reader_tx, reader_rx) = mpsc::channel();
        thread::spawn(move || {
            let mut reader = BufReader::new(stdout);
            let mut line = String::new();
            loop {
                line.clear();
                match reader.read_line(&mut line) {
                    Ok(0) => break,
                    Ok(_) => {
                        let trimmed = line.trim_end_matches(['\r', '\n']).to_string();
                        if !trimmed.is_empty() {
                            let _ = reader_tx.send(trimmed);
                        }
                    }
                    Err(_) => break,
                }
            }
        });
        Ok(Self {
            child,
            writer: BufWriter::new(stdin),
            reader_rx,
        })
    }
}

impl BridgeTransport for StdioTransport {
    fn send_message(&mut self, message: Message) -> Result<(), String> {
        let Message::Text(text) = message else {
            return Ok(());
        };
        self.writer
            .write_all(text.as_bytes())
            .and_then(|_| self.writer.write_all(b"\n"))
            .and_then(|_| self.writer.flush())
            .map_err(|err| err.to_string())
    }

    fn read_message(&mut self) -> Result<Option<Message>, String> {
        match self.reader_rx.try_recv() {
            Ok(line) => Ok(Some(Message::Text(line.into()))),
            Err(TryRecvError::Empty) => Ok(None),
            Err(TryRecvError::Disconnected) => Err("ACP stdio pipe disconnected".to_string()),
        }
    }
}

impl Drop for StdioTransport {
    fn drop(&mut self) {
        let _ = self.child.kill();
    }
}

enum BridgeCommand {
    Request {
        request_id: u64,
        method: String,
        params: Value,
        response_tx: Sender<Result<Value, String>>,
    },
    Respond {
        request_id: u64,
        result: Value,
        ack_tx: Sender<Result<(), String>>,
    },
    Disconnect {
        ack_tx: Sender<()>,
    },
}

#[derive(Debug, PartialEq, Eq)]
enum IncomingPayloadRoute {
    EmitOnly,
    EmitAndAwaitResponse { request_id: u64 },
    ResolvePending { response_id: u64 },
    Ignore,
}

struct AcpBridgeState {
    subscribers: HashSet<String>,
    next_request_id: u64,
    worker: Option<AcpBridgeWorker>,
}

impl AcpBridgeState {
    fn new() -> Self {
        Self {
            subscribers: HashSet::new(),
            next_request_id: 1,
            worker: None,
        }
    }

    fn next_id(&mut self) -> u64 {
        let id = self.next_request_id;
        self.next_request_id += 1;
        id
    }

    fn has_subscribers(&self) -> bool {
        !self.subscribers.is_empty()
    }

    fn command_tx(&self) -> Result<Sender<BridgeCommand>, String> {
        self.worker
            .as_ref()
            .map(|worker| worker.command_tx.clone())
            .ok_or_else(|| "ACP bridge is not connected".to_string())
    }

    fn stop_worker(&mut self) {
        if let Some(mut worker) = self.worker.take() {
            let (ack_tx, ack_rx) = mpsc::channel();
            let _ = worker.command_tx.send(BridgeCommand::Disconnect { ack_tx });
            let _ = ack_rx.recv_timeout(Duration::from_secs(1));
            if let Some(handle) = worker.join_handle.take() {
                let _ = handle.join();
            }
        }
    }
}

#[tauri::command]
fn acp_connect(
    app: AppHandle,
    runtime_state: tauri::State<'_, Arc<Mutex<RuntimeState>>>,
    bridge_state: tauri::State<'_, Arc<Mutex<AcpBridgeState>>>,
) -> Result<bool, String> {
    ensure_bridge_worker(bridge_state.inner(), app, runtime_state.inner().clone())?;
    Ok(true)
}

#[tauri::command]
fn acp_subscribe(
    subscriber_id: String,
    bridge_state: tauri::State<'_, Arc<Mutex<AcpBridgeState>>>,
) -> Result<(), String> {
    if subscriber_id.trim().is_empty() {
        return Err("subscriberId is required".to_string());
    }
    let mut guard = bridge_state
        .lock()
        .map_err(|_| "Failed to acquire acp bridge state".to_string())?;
    guard.subscribers.insert(subscriber_id);
    Ok(())
}

#[tauri::command]
fn acp_unsubscribe(
    subscriber_id: String,
    bridge_state: tauri::State<'_, Arc<Mutex<AcpBridgeState>>>,
) -> Result<(), String> {
    let mut guard = bridge_state
        .lock()
        .map_err(|_| "Failed to acquire acp bridge state".to_string())?;
    guard.subscribers.remove(&subscriber_id);
    Ok(())
}

#[tauri::command]
fn acp_disconnect(
    bridge_state: tauri::State<'_, Arc<Mutex<AcpBridgeState>>>,
) -> Result<bool, String> {
    let mut guard = bridge_state
        .lock()
        .map_err(|_| "Failed to acquire acp bridge state".to_string())?;
    guard.stop_worker();
    Ok(true)
}

#[tauri::command]
fn acp_request(
    app: AppHandle,
    runtime_state: tauri::State<'_, Arc<Mutex<RuntimeState>>>,
    bridge_state: tauri::State<'_, Arc<Mutex<AcpBridgeState>>>,
    method: String,
    params: Option<Value>,
) -> Result<Value, String> {
    acp_debug_log(format!("ipc request method={method}"));
    let result = run_acp_rpc(
        &app,
        runtime_state.inner(),
        bridge_state.inner(),
        &method,
        params,
    );
    match &result {
        Ok(payload) => acp_debug_log(format!(
            "ipc response method={} {}",
            method,
            acp_payload_summary(&json!({"result": payload.clone()}))
        )),
        Err(err) => acp_debug_log(format!("ipc response method={} error={}", method, err)),
    }
    result
}

#[tauri::command]
fn acp_respond(
    bridge_state: tauri::State<'_, Arc<Mutex<AcpBridgeState>>>,
    request_id: u64,
    result: Value,
) -> Result<(), String> {
    let command_tx = {
        let guard = bridge_state
            .lock()
            .map_err(|_| "Failed to acquire acp bridge state".to_string())?;
        guard.command_tx()?
    };
    let (ack_tx, ack_rx) = mpsc::channel();
    command_tx
        .send(BridgeCommand::Respond {
            request_id,
            result,
            ack_tx,
        })
        .map_err(|_| "ACP bridge worker is unavailable".to_string())?;
    ack_rx
        .recv_timeout(Duration::from_secs(30))
        .map_err(|_| "Timed out waiting for ACP response delivery".to_string())?
}

#[tauri::command]
fn acp_read_text_file(path: String) -> Result<String, String> {
    std::fs::read_to_string(PathBuf::from(path)).map_err(|err| err.to_string())
}

#[tauri::command]
fn acp_write_text_file(path: String, content: String) -> Result<(), String> {
    let target = PathBuf::from(path);
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    }
    std::fs::write(target, content).map_err(|err| err.to_string())
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
            }));
            let acp_bridge_state = Arc::new(Mutex::new(AcpBridgeState::new()));

            let _ = show_main_window(app.handle());

            if let Some(main_window) = app.get_webview_window("main") {
                let bridge_state_for_close = acp_bridge_state.clone();
                main_window.on_window_event(move |event| {
                    if matches!(
                        event,
                        WindowEvent::CloseRequested { .. } | WindowEvent::Destroyed
                    ) {
                        if let Ok(mut guard) = bridge_state_for_close.lock() {
                            guard.stop_worker();
                        }
                    }
                });
            }

            app.manage(state);
            app.manage(acp_bridge_state);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            acp_read_text_file,
            acp_write_text_file,
            acp_connect,
            acp_request,
            acp_respond,
            acp_subscribe,
            acp_unsubscribe,
            acp_disconnect
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            if let Some(state) = app_handle.try_state::<Arc<Mutex<AcpBridgeState>>>() {
                if let Ok(mut guard) = state.inner().lock() {
                    guard.stop_worker();
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

fn bridge_next_id(bridge_state: &Arc<Mutex<AcpBridgeState>>) -> Result<u64, String> {
    let mut guard = bridge_state
        .lock()
        .map_err(|_| "Failed to acquire acp bridge state".to_string())?;
    Ok(guard.next_id())
}

fn ensure_bridge_worker(
    bridge_state: &Arc<Mutex<AcpBridgeState>>,
    app: AppHandle,
    runtime_state: Arc<Mutex<RuntimeState>>,
) -> Result<(), String> {
    let mut guard = bridge_state
        .lock()
        .map_err(|_| "Failed to acquire acp bridge state".to_string())?;
    if guard.worker.is_some() {
        return Ok(());
    }

    let (command_tx, command_rx) = mpsc::channel();
    let bridge_state_for_worker = Arc::clone(bridge_state);
    let join_handle = thread::spawn(move || {
        acp_bridge_worker_loop(app, runtime_state, bridge_state_for_worker, command_rx);
    });

    guard.worker = Some(AcpBridgeWorker {
        command_tx,
        join_handle: Some(join_handle),
    });
    Ok(())
}

fn run_acp_rpc(
    app: &AppHandle,
    runtime_state: &Arc<Mutex<RuntimeState>>,
    bridge_state: &Arc<Mutex<AcpBridgeState>>,
    method: &str,
    params: Option<Value>,
) -> Result<Value, String> {
    ensure_bridge_worker(bridge_state, app.clone(), runtime_state.clone())?;
    let request_id = bridge_next_id(bridge_state)?;
    let params = params.unwrap_or(Value::Null);
    let command_tx = {
        let guard = bridge_state
            .lock()
            .map_err(|_| "Failed to acquire acp bridge state".to_string())?;
        guard.command_tx()?
    };
    let (response_tx, response_rx) = mpsc::channel();
    command_tx
        .send(BridgeCommand::Request {
            request_id,
            method: method.to_string(),
            params,
            response_tx,
        })
        .map_err(|_| "ACP bridge worker is unavailable".to_string())?;
    let result = response_rx
        .recv_timeout(Duration::from_secs(60))
        .map_err(|_| "Timed out waiting for ACP response".to_string())?;
    match &result {
        Ok(payload) => acp_debug_log(format!(
            "completed request id={} method={} {}",
            request_id,
            method,
            acp_payload_summary(&json!({"result": payload.clone()}))
        )),
        Err(err) => acp_debug_log(format!(
            "completed request id={} method={} error={}",
            request_id, method, err
        )),
    }
    result
}

fn bridge_has_subscribers(bridge_state: &Arc<Mutex<AcpBridgeState>>) -> bool {
    bridge_state
        .lock()
        .map(|guard| guard.has_subscribers())
        .unwrap_or(false)
}

fn acp_bridge_worker_loop(
    app: AppHandle,
    runtime_state: Arc<Mutex<RuntimeState>>,
    bridge_state: Arc<Mutex<AcpBridgeState>>,
    command_rx: Receiver<BridgeCommand>,
) {
    let mut socket: Option<BridgeSocket> = None;
    let mut pending_requests: HashMap<u64, Sender<Result<Value, String>>> = HashMap::new();
    let mut last_connect_attempt = Instant::now() - Duration::from_secs(1);

    loop {
        loop {
            match command_rx.try_recv() {
                Ok(command) => {
                    if !handle_bridge_command(
                        &app,
                        &runtime_state,
                        &bridge_state,
                        &mut socket,
                        &mut pending_requests,
                        command,
                        &mut last_connect_attempt,
                    ) {
                        fail_pending_requests(&mut pending_requests, "ACP bridge disconnected");
                        return;
                    }
                }
                Err(TryRecvError::Empty) => break,
                Err(TryRecvError::Disconnected) => {
                    fail_pending_requests(
                        &mut pending_requests,
                        "ACP bridge command channel closed",
                    );
                    return;
                }
            }
        }

        if socket.is_none() && last_connect_attempt.elapsed() >= Duration::from_millis(500) {
            last_connect_attempt = Instant::now();
            socket = match connect_acp_stdio(&app, &runtime_state) {
                Ok(transport) => {
                    acp_debug_log("connected ACP stdio bridge");
                    Some(transport)
                }
                Err(err) => {
                    acp_debug_log(format!("failed to connect ACP stdio bridge: {err}"));
                    None
                }
            };
        }

        if let Some(active_socket) = socket.as_mut() {
            match active_socket.read_message() {
                Ok(Some(Message::Text(text))) => {
                    if let Ok(payload) = serde_json::from_str::<Value>(&text) {
                        acp_debug_log(format!(
                            "incoming {}",
                            acp_incoming_payload_summary(&payload)
                        ));
                        route_incoming_payload(&app, &bridge_state, &mut pending_requests, payload);
                    } else {
                        acp_debug_log(format!("incoming non-json payload={text}"));
                    }
                }
                Ok(Some(Message::Ping(payload))) => {
                    let _ = active_socket.send_message(Message::Pong(payload));
                }
                Ok(Some(Message::Close(_))) => {
                    acp_debug_log("ACP stdio bridge closed by sidecar");
                    socket = None;
                    fail_pending_requests(&mut pending_requests, "ACP bridge connection closed");
                }
                Ok(Some(_)) => {}
                Ok(None) => {}
                Err(_) => {
                    acp_debug_log("ACP stdio bridge lost while reading");
                    socket = None;
                    fail_pending_requests(&mut pending_requests, "ACP bridge connection lost");
                }
            }
        }

        thread::sleep(Duration::from_millis(10));
    }
}

fn handle_bridge_command(
    app: &AppHandle,
    runtime_state: &Arc<Mutex<RuntimeState>>,
    bridge_state: &Arc<Mutex<AcpBridgeState>>,
    socket: &mut Option<BridgeSocket>,
    pending_requests: &mut HashMap<u64, Sender<Result<Value, String>>>,
    command: BridgeCommand,
    last_connect_attempt: &mut Instant,
) -> bool {
    match command {
        BridgeCommand::Request {
            request_id,
            method,
            params,
            response_tx,
        } => {
            if socket.is_none() {
                *last_connect_attempt = Instant::now();
                *socket = match connect_acp_stdio(app, runtime_state) {
                    Ok(transport) => {
                        acp_debug_log("reconnected ACP stdio bridge");
                        Some(transport)
                    }
                    Err(err) => {
                        acp_debug_log(format!("failed to reconnect ACP stdio bridge: {err}"));
                        None
                    }
                };
            }
            let Some(active_socket) = socket.as_mut() else {
                let _ = response_tx.send(Err("ACP bridge is unavailable".to_string()));
                return true;
            };
            match send_bridge_request(
                active_socket,
                pending_requests,
                request_id,
                &method,
                params,
                response_tx,
            ) {
                Ok(()) => {}
                Err(err) => {
                    acp_debug_log(format!(
                        "request id={} method={} send failed: {}",
                        request_id, method, err
                    ));
                    *socket = None;
                    if !bridge_has_subscribers(bridge_state) {
                        fail_pending_requests(pending_requests, "ACP bridge connection lost");
                    }
                }
            }
            true
        }
        BridgeCommand::Respond {
            request_id,
            result,
            ack_tx,
        } => {
            let send_result = if let Some(active_socket) = socket.as_mut() {
                send_bridge_response(active_socket, request_id, result)
            } else {
                Err("ACP bridge is unavailable".to_string())
            };
            let _ = ack_tx.send(send_result);
            true
        }
        BridgeCommand::Disconnect { ack_tx } => {
            let _ = ack_tx.send(());
            false
        }
    }
}

fn route_incoming_payload(
    app: &AppHandle,
    bridge_state: &Arc<Mutex<AcpBridgeState>>,
    pending_requests: &mut HashMap<u64, Sender<Result<Value, String>>>,
    payload: Value,
) {
    match classify_incoming_payload(&payload) {
        IncomingPayloadRoute::EmitOnly | IncomingPayloadRoute::EmitAndAwaitResponse { .. } => {
            if bridge_has_subscribers(bridge_state) {
                let _ = app.emit(ACP_NOTIFICATION_EVENT, &payload);
            }
        }
        IncomingPayloadRoute::ResolvePending { response_id } => {
            let _ = resolve_pending_response(pending_requests, response_id, &payload);
        }
        IncomingPayloadRoute::Ignore => {}
    }
}

fn build_jsonrpc_request_payload(request_id: u64, method: &str, params: Value) -> Value {
    json!({
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    })
}

fn build_jsonrpc_response_payload(request_id: u64, result: Value) -> Value {
    json!({
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
    })
}

fn send_bridge_request<T: BridgeTransport + ?Sized>(
    socket: &mut T,
    pending_requests: &mut HashMap<u64, Sender<Result<Value, String>>>,
    request_id: u64,
    method: &str,
    params: Value,
    response_tx: Sender<Result<Value, String>>,
) -> Result<(), String> {
    let request_payload = build_jsonrpc_request_payload(request_id, method, params);
    acp_debug_log(format!(
        "sending request id={} method={} params_kind={}",
        request_id,
        method,
        match request_payload.get("params") {
            Some(Value::Object(_)) => "object",
            Some(Value::Array(_)) => "array",
            Some(Value::Null) | None => "null",
            Some(_) => "scalar",
        }
    ));
    if let Err(err) = socket.send_message(Message::Text(request_payload.to_string().into())) {
        let _ = response_tx.send(Err(err.clone()));
        return Err(err);
    }
    pending_requests.insert(request_id, response_tx);
    Ok(())
}

fn send_bridge_response<T: BridgeTransport + ?Sized>(
    socket: &mut T,
    request_id: u64,
    result: Value,
) -> Result<(), String> {
    let response = build_jsonrpc_response_payload(request_id, result);
    socket.send_message(Message::Text(response.to_string().into()))
}

fn classify_incoming_payload(payload: &Value) -> IncomingPayloadRoute {
    let request_id = payload.get("id").and_then(Value::as_u64);
    let method = payload.get("method").and_then(Value::as_str);

    match (method, request_id) {
        (Some("session/update"), None) => IncomingPayloadRoute::EmitOnly,
        (Some(_), Some(request_id)) => IncomingPayloadRoute::EmitAndAwaitResponse { request_id },
        (Some(_), None) => IncomingPayloadRoute::Ignore,
        (None, Some(response_id)) => IncomingPayloadRoute::ResolvePending { response_id },
        (None, None) => IncomingPayloadRoute::Ignore,
    }
}

fn fail_pending_requests(
    pending_requests: &mut HashMap<u64, Sender<Result<Value, String>>>,
    message: &str,
) {
    let error = message.to_string();
    for (_, response_tx) in pending_requests.drain() {
        let _ = response_tx.send(Err(error.clone()));
    }
}

fn resolve_pending_response(
    pending_requests: &mut HashMap<u64, Sender<Result<Value, String>>>,
    response_id: u64,
    payload: &Value,
) -> bool {
    let Some(response_tx) = pending_requests.remove(&response_id) else {
        acp_debug_log(format!("ignoring response for unknown request id={response_id}"));
        return false;
    };

    if let Some(error) = payload.get("error") {
        let message = error
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("ACP request failed")
            .to_string();
        acp_debug_log(format!(
            "resolved response id={} {}",
            response_id,
            acp_payload_summary(payload)
        ));
        let _ = response_tx.send(Err(message));
        return true;
    }

    acp_debug_log(format!(
        "resolved response id={} {}",
        response_id,
        acp_payload_summary(payload)
    ));
    let _ = response_tx.send(Ok(payload.get("result").cloned().unwrap_or(Value::Null)));
    true
}

fn connect_acp_stdio(
    app: &AppHandle,
    runtime_state: &Arc<Mutex<RuntimeState>>,
) -> Result<BridgeSocket, String> {
    let config = {
        let guard = runtime_state
            .lock()
            .map_err(|_| "Failed to acquire runtime state".to_string())?;
        guard.config.clone()
    };
    let ns_bot_home = app_ns_bot_home(
        &app.path()
            .app_data_dir()
            .map_err(|err| err.to_string())?,
    );
    let init_outputs = ensure_runtime_initialized(&config.runtime_root, &ns_bot_home)
        .map_err(|err| err.to_string())?;
    let stderr = if acp_debug_enabled() {
        Stdio::piped()
    } else {
        Stdio::null()
    };
    let mut sidecar_env = base_sidecar_env_pairs("127.0.0.1", config.sidecar_port, &init_outputs);
    sidecar_env.push(("NSBOT_ACP_TRANSPORT", "stdio".to_string()));
    sidecar_env.push((
        "NSBOT_ACP_DEBUG",
        if acp_debug_enabled() { "1" } else { "0" }.to_string(),
    ));
    let executable = nutstore_bot_desktop::runtime::resolve_bundled_executable(
        "nsbot-sidecar",
        Some(&config.runtime_root),
    )
    .ok_or_else(|| "Missing nsbot-sidecar executable for ACP stdio bridge".to_string())?;
    acp_debug_log(format!(
        "launching ACP stdio sidecar executable={}",
        executable.display()
    ));
    let child = spawn_bundled_process(
        "nsbot-sidecar",
        Some(&config.runtime_root),
        &sidecar_env,
        &["--acp"],
        Stdio::piped(),
        Stdio::piped(),
        stderr,
    )
    .map_err(|err| err.to_string())?
    .ok_or_else(|| "Missing nsbot-sidecar executable for ACP stdio bridge".to_string())?;
    let mut transport = StdioTransport::new(child)?;
    initialize_acp_transport(&mut transport)?;
    acp_debug_log("ACP stdio initialize handshake completed");
    Ok(Box::new(transport))
}

fn initialize_acp_transport<T: BridgeTransport + ?Sized>(socket: &mut T) -> Result<(), String> {
    let initialize_payload = json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": { "readTextFile": true, "writeTextFile": true },
                "terminal": false
            }
        }
    });
    socket.send_message(Message::Text(initialize_payload.to_string().into()))?;

    loop {
        let Some(message) = socket.read_message()? else {
            thread::sleep(Duration::from_millis(10));
            continue;
        };
        let Message::Text(text) = message else {
            continue;
        };
        let payload: Value = serde_json::from_str(&text).map_err(|err| err.to_string())?;
        if payload.get("id").and_then(Value::as_u64) == Some(1) {
            return Ok(());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::VecDeque;

    struct FakeTransport {
        sent: Vec<String>,
        reads: VecDeque<Result<Option<Message>, String>>,
        fail_send: Option<String>,
    }

    impl FakeTransport {
        fn new() -> Self {
            Self {
                sent: Vec::new(),
                reads: VecDeque::new(),
                fail_send: None,
            }
        }
    }

    impl BridgeTransport for FakeTransport {
        fn send_message(&mut self, message: Message) -> Result<(), String> {
            if let Some(err) = self.fail_send.clone() {
                return Err(err);
            }
            if let Message::Text(text) = message {
                self.sent.push(text.to_string());
            }
            Ok(())
        }

        fn read_message(&mut self) -> Result<Option<Message>, String> {
            self.reads.pop_front().unwrap_or(Ok(None))
        }
    }

    #[test]
    fn initialize_acp_transport_sends_initialize_request() {
        let mut socket = FakeTransport::new();
        socket.reads.push_back(Ok(Some(Message::Text(
            json!({"jsonrpc":"2.0","id":1,"result":{"protocolVersion":1}})
                .to_string()
                .into(),
        ))));
        initialize_acp_transport(&mut socket).expect("initialize");
        assert_eq!(socket.sent.len(), 1);
        assert!(socket.sent[0].contains("\"method\":\"initialize\""));
    }

    #[test]
    fn bridge_state_generates_monotonic_ids() {
        let mut state = AcpBridgeState::new();
        assert_eq!(state.next_id(), 1);
        assert_eq!(state.next_id(), 2);
        assert_eq!(state.next_id(), 3);
    }

    #[test]
    fn resolve_pending_response_sends_success_result() {
        let (tx, rx) = mpsc::channel();
        let mut pending = HashMap::new();
        pending.insert(7, tx);

        let resolved = resolve_pending_response(
            &mut pending,
            7,
            &json!({
                "jsonrpc": "2.0",
                "id": 7,
                "result": {"ok": true}
            }),
        );

        assert!(resolved);
        assert!(pending.is_empty());
        assert_eq!(
            rx.recv().expect("response").expect("ok"),
            json!({"ok": true})
        );
    }

    #[test]
    fn resolve_pending_response_sends_error_message() {
        let (tx, rx) = mpsc::channel();
        let mut pending = HashMap::new();
        pending.insert(8, tx);

        let resolved = resolve_pending_response(
            &mut pending,
            8,
            &json!({
                "jsonrpc": "2.0",
                "id": 8,
                "error": {"message": "boom"}
            }),
        );

        assert!(resolved);
        assert!(pending.is_empty());
        assert_eq!(rx.recv().expect("response").expect_err("err"), "boom");
    }

    #[test]
    fn resolve_pending_response_ignores_unknown_request() {
        let mut pending = HashMap::new();
        let resolved = resolve_pending_response(&mut pending, 999, &json!({"result": {}}));
        assert!(!resolved);
    }

    #[test]
    fn fail_pending_requests_broadcasts_same_error() {
        let (tx1, rx1) = mpsc::channel();
        let (tx2, rx2) = mpsc::channel();
        let mut pending = HashMap::new();
        pending.insert(1, tx1);
        pending.insert(2, tx2);

        fail_pending_requests(&mut pending, "ACP bridge connection lost");

        assert!(pending.is_empty());
        assert_eq!(
            rx1.recv()
                .expect("first response")
                .expect_err("first error"),
            "ACP bridge connection lost"
        );
        assert_eq!(
            rx2.recv()
                .expect("second response")
                .expect_err("second error"),
            "ACP bridge connection lost"
        );
    }

    #[test]
    fn classify_incoming_payload_marks_session_update_as_emit_only() {
        let route = classify_incoming_payload(&json!({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"sessionId": "sess_1"}
        }));
        assert_eq!(route, IncomingPayloadRoute::EmitOnly);
    }

    #[test]
    fn classify_incoming_payload_marks_server_request_for_emit_and_response() {
        let route = classify_incoming_payload(&json!({
            "jsonrpc": "2.0",
            "id": 42,
            "method": "session/request_permission",
            "params": {}
        }));
        assert_eq!(
            route,
            IncomingPayloadRoute::EmitAndAwaitResponse { request_id: 42 }
        );
    }

    #[test]
    fn classify_incoming_payload_marks_response_for_pending_resolution() {
        let route = classify_incoming_payload(&json!({
            "jsonrpc": "2.0",
            "id": 12,
            "result": {"ok": true}
        }));
        assert_eq!(
            route,
            IncomingPayloadRoute::ResolvePending { response_id: 12 }
        );
    }

    #[test]
    fn build_jsonrpc_request_payload_includes_method_and_params() {
        let payload = build_jsonrpc_request_payload(5, "workspace/list", json!({"limit": 10}));
        assert_eq!(payload["id"], json!(5));
        assert_eq!(payload["method"], json!("workspace/list"));
        assert_eq!(payload["params"], json!({"limit": 10}));
    }

    #[test]
    fn build_jsonrpc_response_payload_includes_result() {
        let payload = build_jsonrpc_response_payload(8, json!({"outcome": "ok"}));
        assert_eq!(payload["id"], json!(8));
        assert_eq!(payload["result"], json!({"outcome": "ok"}));
    }

    #[test]
    fn acp_payload_summary_reports_error_code_and_message() {
        let summary = acp_payload_summary(&json!({
            "error": {"code": -32601, "message": "Method not found: timeline/list"}
        }));
        assert_eq!(summary, "error code=-32601 message=Method not found: timeline/list");
    }

    #[test]
    fn acp_payload_summary_reports_sorted_result_keys() {
        let summary = acp_payload_summary(&json!({
            "result": {"b": true, "a": 1}
        }));
        assert_eq!(summary, "result keys=a,b");
    }

    #[test]
    fn acp_incoming_payload_summary_reports_notifications() {
        let summary = acp_incoming_payload_summary(&json!({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"sessionId": "sess-1"}
        }));
        assert_eq!(summary, "notification method=session/update");
    }

    #[test]
    fn acp_incoming_payload_summary_reports_responses() {
        let summary = acp_incoming_payload_summary(&json!({
            "jsonrpc": "2.0",
            "id": 23,
            "result": {"stopReason": "end_turn"}
        }));
        assert_eq!(summary, "response id=23 result keys=stopReason");
    }

    #[test]
    fn send_bridge_request_tracks_multiple_pending_requests() {
        let mut socket = FakeTransport::new();
        let (tx1, rx1) = mpsc::channel();
        let (tx2, rx2) = mpsc::channel();
        let mut pending = HashMap::new();

        send_bridge_request(
            &mut socket,
            &mut pending,
            1,
            "workspace/list",
            json!({"page": 1}),
            tx1,
        )
        .expect("first request");
        send_bridge_request(
            &mut socket,
            &mut pending,
            2,
            "timeline/list",
            json!({"sessionId": "sess_1"}),
            tx2,
        )
        .expect("second request");

        assert_eq!(pending.len(), 2);
        assert!(resolve_pending_response(
            &mut pending,
            2,
            &json!({"id": 2, "result": {"events": []}})
        ));
        assert!(resolve_pending_response(
            &mut pending,
            1,
            &json!({"id": 1, "result": {"workspaces": []}})
        ));
        assert_eq!(
            rx2.recv().expect("second recv").expect("second ok"),
            json!({"events": []})
        );
        assert_eq!(
            rx1.recv().expect("first recv").expect("first ok"),
            json!({"workspaces": []})
        );
        assert!(pending.is_empty());
    }

    #[test]
    fn send_bridge_request_reports_send_failure_without_registering_pending() {
        let mut socket = FakeTransport {
            sent: Vec::new(),
            reads: VecDeque::new(),
            fail_send: Some("socket down".to_string()),
        };
        let (tx, rx) = mpsc::channel();
        let mut pending = HashMap::new();

        let result = send_bridge_request(
            &mut socket,
            &mut pending,
            9,
            "workspace/list",
            json!({}),
            tx,
        );

        assert_eq!(result.expect_err("send failure"), "socket down");
        assert!(pending.is_empty());
        assert_eq!(
            rx.recv().expect("response").expect_err("caller error"),
            "socket down"
        );
    }

    #[test]
    fn send_bridge_response_serializes_jsonrpc_result() {
        let mut socket = FakeTransport::new();

        send_bridge_response(&mut socket, 55, json!({"outcome": "selected"})).expect("response");

        assert_eq!(socket.sent.len(), 1);
        let payload: Value = serde_json::from_str(&socket.sent[0]).expect("json payload");
        assert_eq!(payload["id"], json!(55));
        assert_eq!(payload["result"], json!({"outcome": "selected"}));
    }

    #[test]
    fn bridge_state_tracks_subscriber_presence() {
        let mut state = AcpBridgeState::new();
        assert!(!state.has_subscribers());
        state.subscribers.insert("frontend-main".to_string());
        assert!(state.has_subscribers());
        state.subscribers.remove("frontend-main");
        assert!(!state.has_subscribers());
    }

    #[test]
    fn classify_incoming_payload_ignores_method_without_id_or_supported_notification() {
        let route = classify_incoming_payload(&json!({
            "jsonrpc": "2.0",
            "method": "initialize"
        }));
        assert_eq!(route, IncomingPayloadRoute::Ignore);
    }

    #[test]
    fn fake_transport_ping_can_be_answered_with_pong() {
        let mut socket = FakeTransport {
            sent: Vec::new(),
            reads: VecDeque::from([Ok(Some(Message::Ping(vec![1, 2, 3].into())))]),
            fail_send: None,
        };

        let message = socket.read_message().expect("read ok").expect("has ping");
        if let Message::Ping(payload) = message {
            socket
                .send_message(Message::Pong(payload))
                .expect("send pong");
        } else {
            panic!("expected ping message");
        }

        assert!(socket.sent.is_empty());
    }

    #[test]
    fn server_request_and_response_use_jsonrpc_shapes() {
        let request_payload = json!({
            "jsonrpc": "2.0",
            "id": 88,
            "method": "session/request_permission",
            "params": {"sessionId": "sess_1"}
        });
        let route = classify_incoming_payload(&request_payload);
        assert_eq!(
            route,
            IncomingPayloadRoute::EmitAndAwaitResponse { request_id: 88 }
        );

        let mut socket = FakeTransport::new();
        send_bridge_response(
            &mut socket,
            88,
            json!({"outcome": {"outcome": "cancelled"}}),
        )
        .expect("response send");

        let payload: Value = serde_json::from_str(&socket.sent[0]).expect("json response");
        assert_eq!(payload["jsonrpc"], json!("2.0"));
        assert_eq!(payload["id"], json!(88));
        assert_eq!(
            payload["result"],
            json!({"outcome": {"outcome": "cancelled"}})
        );
    }
}
