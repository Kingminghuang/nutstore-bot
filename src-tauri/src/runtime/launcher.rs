use std::env;
use std::error::Error;
use std::ffi::OsString;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};

const APP_NAME: &str = "NutstoreBot";
const LEGACY_ROOT_NAME: &str = ".nsbot";

#[derive(Clone, Debug)]
pub struct InitOutputs {
    pub ns_bot_home: PathBuf,
    pub fd_executable: PathBuf,
    pub rg_executable: PathBuf,
}

pub fn app_ns_bot_home(app_data_dir: &Path) -> PathBuf {
    app_data_dir.join(APP_NAME)
}

pub fn ensure_runtime_initialized(
    runtime_root: &Path,
    ns_bot_home: &Path,
) -> Result<InitOutputs, Box<dyn Error>> {
    fs::create_dir_all(ns_bot_home)?;

    let bin_dir = ns_bot_home.join("bin");
    fs::create_dir_all(&bin_dir)?;

    let templates_source = runtime_root.join("templates");
    let templates_target = ns_bot_home.join("templates");
    if templates_source.exists() && !templates_target.exists() {
        copy_dir_recursive(&templates_source, &templates_target)?;
    }

    let fd_name = executable_name("fd");
    let rg_name = executable_name("rg");
    let fd_target = bin_dir.join(&fd_name);
    let rg_target = bin_dir.join(&rg_name);

    copy_executable_if_missing(
        &runtime_root.join("search-tools").join(&fd_name),
        &fd_target,
    )?;
    copy_executable_if_missing(
        &runtime_root.join("search-tools").join(&rg_name),
        &rg_target,
    )?;

    Ok(InitOutputs {
        ns_bot_home: ns_bot_home.to_path_buf(),
        fd_executable: fd_target,
        rg_executable: rg_target,
    })
}

pub fn resolve_ns_bot_home(args: &[OsString]) -> Result<PathBuf, Box<dyn Error>> {
    if let Some(value) = find_option_value(args, "--ns-bot-home") {
        return expand_to_absolute(&value);
    }

    if let Some(value) = env::var_os("NS_BOT_HOME") {
        return expand_to_absolute(&value);
    }

    if cfg!(target_os = "windows") {
        if let Some(appdata) = env::var_os("APPDATA") {
            let raw = PathBuf::from(appdata)
                .to_string_lossy()
                .replace("\\\\", "\\");
            return Ok(PathBuf::from(raw).join(APP_NAME));
        }
    }

    if cfg!(target_os = "macos") {
        if let Some(home) = env::var_os("HOME") {
            return Ok(PathBuf::from(home)
                .join("Library")
                .join("Application Support")
                .join(APP_NAME));
        }
    }

    if let Some(state_home) = env::var_os("XDG_STATE_HOME") {
        return Ok(PathBuf::from(state_home).join(APP_NAME));
    }
    if let Some(config_home) = env::var_os("XDG_CONFIG_HOME") {
        return Ok(PathBuf::from(config_home).join(APP_NAME));
    }
    if let Some(home) = env::var_os("HOME") {
        return Ok(PathBuf::from(home).join(LEGACY_ROOT_NAME));
    }

    Err("failed to resolve NS_BOT_HOME".into())
}

pub fn resolve_bundled_executable(name: &str, fallback_root: Option<&Path>) -> Option<PathBuf> {
    #[cfg(target_os = "macos")]
    {
        if let Ok(current_executable) = env::current_exe() {
            if let Some(executable_dir) = current_executable.parent() {
                let candidate = executable_dir.join(executable_name(name));
                if candidate.exists() {
                    return Some(candidate);
                }
            }
        }
    }

    fallback_root.and_then(|root| {
        let candidate = root.join("binaries").join(executable_name(name));
        candidate.exists().then_some(candidate)
    })
}

pub fn bundled_command(
    name: &str,
    fallback_root: Option<&Path>,
    envs: &[(&str, String)],
    args: &[&str],
) -> Option<Command> {
    let executable = resolve_bundled_executable(name, fallback_root)?;
    let mut command = Command::new(executable);
    command.args(args);
    for (key, value) in envs {
        command.env(key, value);
    }
    Some(command)
}

pub fn spawn_bundled_process(
    name: &str,
    fallback_root: Option<&Path>,
    envs: &[(&str, String)],
    args: &[&str],
    stdin: Stdio,
    stdout: Stdio,
    stderr: Stdio,
) -> Result<Option<Child>, Box<dyn Error>> {
    let Some(mut command) = bundled_command(name, fallback_root, envs, args) else {
        return Ok(None);
    };
    command.stdin(stdin).stdout(stdout).stderr(stderr);
    Ok(Some(command.spawn()?))
}

pub fn runtime_env_pairs(outputs: &InitOutputs) -> Vec<(&'static str, String)> {
    vec![
        (
            "NS_BOT_HOME",
            outputs.ns_bot_home.to_string_lossy().to_string(),
        ),
        (
            "NSBOT_FD_EXECUTABLE",
            outputs.fd_executable.to_string_lossy().to_string(),
        ),
        (
            "NSBOT_RG_EXECUTABLE",
            outputs.rg_executable.to_string_lossy().to_string(),
        ),
    ]
}

pub fn base_sidecar_env_pairs(
    host: &str,
    port: u16,
    outputs: &InitOutputs,
) -> Vec<(&'static str, String)> {
    let mut envs = vec![
        ("NS_BOT_HOST", host.to_string()),
        ("NS_BOT_PORT", port.to_string()),
    ];
    envs.extend(runtime_env_pairs(outputs));
    envs
}

pub fn executable_name(name: &str) -> String {
    if cfg!(target_os = "windows") {
        format!("{name}.exe")
    } else {
        name.to_string()
    }
}

fn find_option_value(args: &[OsString], option_name: &str) -> Option<OsString> {
    let option = OsString::from(option_name);
    let prefix = format!("{option_name}=");

    let mut index = 0;
    while index < args.len() {
        let token = &args[index];
        if token == &option {
            return args.get(index + 1).cloned();
        }
        if let Some(raw) = token.to_str() {
            if let Some(value) = raw.strip_prefix(&prefix) {
                return Some(OsString::from(value));
            }
        }
        index += 1;
    }

    None
}

fn expand_to_absolute(value: &OsString) -> Result<PathBuf, Box<dyn Error>> {
    let path = PathBuf::from(value);
    if path.is_absolute() {
        return Ok(path);
    }

    Ok(env::current_dir()?.join(path))
}

fn copy_dir_recursive(source: &Path, destination: &Path) -> Result<(), Box<dyn Error>> {
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

fn copy_executable_if_missing(source: &Path, destination: &Path) -> Result<(), Box<dyn Error>> {
    if destination.exists() {
        return Ok(());
    }
    if !source.is_file() {
        return Err(format!("missing bundled runtime tool: {}", source.display()).into());
    }
    fs::copy(source, destination)?;

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(destination, fs::Permissions::from_mode(0o755))?;
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn unique_temp_dir(label: &str) -> PathBuf {
        let suffix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time")
            .as_nanos();
        std::env::temp_dir().join(format!("nsbot-launcher-{label}-{suffix}"))
    }

    #[test]
    fn runtime_env_pairs_include_expected_paths() {
        let outputs = InitOutputs {
            ns_bot_home: PathBuf::from("/tmp/nsbot-home"),
            fd_executable: PathBuf::from("/tmp/nsbot-home/bin/fd"),
            rg_executable: PathBuf::from("/tmp/nsbot-home/bin/rg"),
        };

        let pairs = runtime_env_pairs(&outputs);

        assert!(pairs.contains(&("NS_BOT_HOME", "/tmp/nsbot-home".to_string())));
        assert!(pairs.contains(&(
            "NSBOT_FD_EXECUTABLE",
            "/tmp/nsbot-home/bin/fd".to_string()
        )));
        assert!(pairs.contains(&(
            "NSBOT_RG_EXECUTABLE",
            "/tmp/nsbot-home/bin/rg".to_string()
        )));
    }

    #[test]
    fn base_sidecar_env_pairs_include_host_port_and_runtime_paths() {
        let outputs = InitOutputs {
            ns_bot_home: PathBuf::from("/tmp/nsbot-home"),
            fd_executable: PathBuf::from("/tmp/nsbot-home/bin/fd"),
            rg_executable: PathBuf::from("/tmp/nsbot-home/bin/rg"),
        };

        let pairs = base_sidecar_env_pairs("127.0.0.1", 18765, &outputs);

        assert!(pairs.contains(&("NS_BOT_HOST", "127.0.0.1".to_string())));
        assert!(pairs.contains(&("NS_BOT_PORT", "18765".to_string())));
        assert!(pairs.contains(&("NS_BOT_HOME", "/tmp/nsbot-home".to_string())));
        assert!(pairs.contains(&(
            "NSBOT_FD_EXECUTABLE",
            "/tmp/nsbot-home/bin/fd".to_string()
        )));
        assert!(pairs.contains(&(
            "NSBOT_RG_EXECUTABLE",
            "/tmp/nsbot-home/bin/rg".to_string()
        )));
    }

    #[test]
    fn resolve_ns_bot_home_prefers_cli_option() {
        let args = vec![OsString::from("--ns-bot-home=./local-home")];
        let expected = std::env::current_dir()
            .expect("cwd")
            .join("local-home");

        let resolved = resolve_ns_bot_home(&args).expect("resolve path");

        assert_eq!(resolved, expected);
    }

    #[test]
    fn ensure_runtime_initialized_copies_templates_and_tools() {
        let runtime_root = unique_temp_dir("runtime-root");
        let ns_bot_home = unique_temp_dir("ns-bot-home");
        let templates_dir = runtime_root.join("templates");
        let search_tools_dir = runtime_root.join("search-tools");
        let template_file = templates_dir.join("USER.md");
        let fd_source = search_tools_dir.join(executable_name("fd"));
        let rg_source = search_tools_dir.join(executable_name("rg"));

        fs::create_dir_all(&templates_dir).expect("templates dir");
        fs::create_dir_all(&search_tools_dir).expect("search tools dir");
        fs::write(&template_file, "template").expect("template file");
        fs::write(&fd_source, "fd").expect("fd tool");
        fs::write(&rg_source, "rg").expect("rg tool");

        let outputs = ensure_runtime_initialized(&runtime_root, &ns_bot_home).expect("init");

        assert_eq!(outputs.ns_bot_home, ns_bot_home);
        assert!(ns_bot_home.join("templates").join("USER.md").is_file());
        assert!(outputs.fd_executable.is_file());
        assert!(outputs.rg_executable.is_file());

        let _ = fs::remove_dir_all(&runtime_root);
        let _ = fs::remove_dir_all(&ns_bot_home);
    }

    #[test]
    fn resolve_bundled_executable_uses_fallback_root() {
        let fallback_root = unique_temp_dir("fallback-root");
        let binaries_dir = fallback_root.join("binaries");
        let candidate = binaries_dir.join(executable_name("nsbot-sidecar"));
        fs::create_dir_all(&binaries_dir).expect("binaries dir");
        fs::write(&candidate, "binary").expect("candidate binary");

        let resolved =
            resolve_bundled_executable("nsbot-sidecar", Some(&fallback_root)).expect("path");

        assert_eq!(resolved, candidate);

        let _ = fs::remove_dir_all(&fallback_root);
    }

    #[test]
    fn bundled_command_uses_fallback_executable_and_envs() {
        let fallback_root = unique_temp_dir("bundled-command-root");
        let binaries_dir = fallback_root.join("binaries");
        let candidate = binaries_dir.join(executable_name("nsbot-sidecar"));
        fs::create_dir_all(&binaries_dir).expect("binaries dir");
        fs::write(&candidate, "binary").expect("candidate binary");

        let envs = vec![("NS_BOT_HOME", "/tmp/nsbot-home".to_string())];
        let command = bundled_command(
            "nsbot-sidecar",
            Some(&fallback_root),
            &envs,
            &["--acp"],
        )
        .expect("bundled command");

        assert_eq!(command.get_program(), candidate.as_os_str());
        let args = command
            .get_args()
            .map(|value| value.to_string_lossy().to_string())
            .collect::<Vec<_>>();
        assert_eq!(args, vec!["--acp".to_string()]);
        let env_pairs = command.get_envs().collect::<Vec<_>>();
        assert!(env_pairs.iter().any(|(key, value)| {
            *key == "NS_BOT_HOME"
                && value.map(|inner| inner.to_string_lossy().to_string())
                    == Some("/tmp/nsbot-home".to_string())
        }));

        let _ = fs::remove_dir_all(&fallback_root);
    }
}
