use nutstore_bot_desktop::runtime::{
    ensure_runtime_initialized, executable_name, resolve_ns_bot_home, runtime_env_pairs,
};
use std::env;
use std::ffi::OsString;
#[cfg(test)]
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};
#[cfg(test)]
use std::time::{SystemTime, UNIX_EPOCH};

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

    let payload_dir = dist_root.join("binaries").join(PAYLOAD_BASENAME);
    let candidate = payload_dir.join(payload_binary_name());
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

#[cfg(test)]
fn unique_temp_dir(label: &str) -> PathBuf {
    let suffix = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("time")
        .as_nanos();
    std::env::temp_dir().join(format!("nsbot-cli-{label}-{suffix}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolve_payload_path_prefers_override_file() {
        let override_path = unique_temp_dir("override");
        fs::create_dir_all(&override_path).expect("mkdir");
        let payload = override_path.join(payload_binary_name());
        fs::write(&payload, "payload").expect("payload");
        std::env::set_var("NSBOT_CLI_PAYLOAD_PATH", &payload);

        let resolved = resolve_payload_path(Path::new("/unused")).expect("resolve override");

        std::env::remove_var("NSBOT_CLI_PAYLOAD_PATH");
        assert_eq!(resolved, payload);
        let _ = fs::remove_dir_all(&override_path);
    }

    #[test]
    fn resolve_payload_path_uses_onedir_layout() {
        let dist_root = unique_temp_dir("dist-root");
        let payload_dir = dist_root.join("binaries").join(PAYLOAD_BASENAME);
        fs::create_dir_all(&payload_dir).expect("mkdir payload dir");
        let payload = payload_dir.join(payload_binary_name());
        fs::write(&payload, "payload").expect("write payload");

        let resolved = resolve_payload_path(&dist_root).expect("resolve payload");

        assert_eq!(resolved, payload);
        let _ = fs::remove_dir_all(&dist_root);
    }

    #[test]
    fn resolve_payload_path_errors_when_onedir_layout_missing_executable() {
        let dist_root = unique_temp_dir("dist-root-missing");
        let payload_dir = dist_root.join("binaries").join(PAYLOAD_BASENAME);
        fs::create_dir_all(&payload_dir).expect("mkdir payload dir");

        let error = resolve_payload_path(&dist_root).expect_err("expected missing executable");

        assert!(error.contains("missing CLI payload executable"));
        let _ = fs::remove_dir_all(&dist_root);
    }
}
