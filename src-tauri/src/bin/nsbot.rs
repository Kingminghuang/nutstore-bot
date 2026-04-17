use nutstore_bot_desktop::runtime::{
    ensure_runtime_initialized, executable_name, resolve_ns_bot_home, runtime_env_pairs,
};
use std::env;
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};

const PAYLOAD_BASENAME: &str = "nsbot-sidecar-cli-payload";

fn main() -> ExitCode {
    let args = env::args_os().skip(1).collect::<Vec<_>>();

    match run(args) {
        Ok(code) => ExitCode::from(code as u8),
        Err(message) => {
            eprintln!("[nsbot] {message}");
            ExitCode::from(1)
        }
    }
}

fn run(args: Vec<OsString>) -> Result<i32, String> {
    let executable = env::current_exe().map_err(|err| err.to_string())?;
    let dist_root = executable
        .parent()
        .ok_or_else(|| "failed to resolve launcher directory".to_string())?
        .to_path_buf();
    let payload_path = resolve_payload_path(&dist_root)?;
    let runtime_root = dist_root.join("runtime");

    let ns_bot_home = resolve_ns_bot_home(&args).map_err(|err| err.to_string())?;
    let init_outputs =
        ensure_runtime_initialized(&runtime_root, &ns_bot_home).map_err(|err| err.to_string())?;

    let mut command = Command::new(&payload_path);
    command.args(&args);
    for (key, value) in runtime_env_pairs(&init_outputs) {
        command.env(key, value);
    }

    let status = command.status().map_err(|err| {
        format!(
            "failed to start Python CLI payload {}: {err}",
            payload_path.display()
        )
    })?;
    Ok(status.code().unwrap_or(1))
}

fn resolve_payload_path(dist_root: &Path) -> Result<PathBuf, String> {
    if let Some(override_path) = env::var_os("NSBOT_CLI_PAYLOAD_PATH") {
        let path = PathBuf::from(override_path);
        if path.is_file() {
            return Ok(path);
        }
        return Err(format!(
            "payload override path is not a file: {}",
            path.display()
        ));
    }

    let binary_name = payload_binary_name();
    let candidate = dist_root.join("binaries").join(binary_name);
    if candidate.is_file() {
        return Ok(candidate);
    }

    Err(format!(
        "missing CLI payload executable: {}",
        candidate.display()
    ))
}

fn payload_binary_name() -> String {
    executable_name(PAYLOAD_BASENAME)
}