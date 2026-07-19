use rand::RngCore;
use serde::Serialize;
use serde_json::Value;
use std::ffi::OsString;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{mpsc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use crate::capture_coordinator::CaptureControlConnection;

const STARTUP_TIMEOUT: Duration = Duration::from_secs(15);
const SHUTDOWN_GRACE: Duration = Duration::from_secs(2);
const TOKEN_ENV: &str = "AIMING_COOKIE_DESKTOP_TOKEN";
const WATCH_PARENT_STDIN_ENV: &str = "AIMING_COOKIE_WATCH_PARENT_STDIN";
const CAPTURE_CONTROL_ADDRESS_ENV: &str = "AIMING_COOKIE_NATIVE_CAPTURE_CONTROL_ADDR";
const CAPTURE_CONTROL_SECRET_ENV: &str = "AIMING_COOKIE_NATIVE_CAPTURE_CONTROL_SECRET";

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeConnection {
    pub base_url: String,
    pub token: String,
}

pub struct RuntimeProcess {
    child: Option<Child>,
    connection: RuntimeConnection,
}

impl RuntimeProcess {
    pub fn start(
        project_root: &Path,
        app_data_dir: &Path,
        capture_control: &CaptureControlConnection,
    ) -> Result<Self, String> {
        std::fs::create_dir_all(app_data_dir)
            .map_err(|error| format!("failed to create app data directory: {error}"))?;

        let token = create_launch_token();
        let database_path = app_data_dir.join("aiming_cookie.db");
        let database_url = format!("sqlite+aiosqlite:///{}", database_path.display());
        let mut command = Command::new(python_executable());
        command
            .arg("-m")
            .arg("webapp.backend.desktop_runtime")
            .current_dir(project_root)
            .env(TOKEN_ENV, &token)
            .env(
                CAPTURE_CONTROL_ADDRESS_ENV,
                capture_control.address.to_string(),
            )
            .env(CAPTURE_CONTROL_SECRET_ENV, &capture_control.secret)
            .env(WATCH_PARENT_STDIN_ENV, "1")
            .env("DATA_ROOT", app_data_dir)
            .env("VIDEO_TMP_DIR", app_data_dir)
            .env("DATABASE_URL", database_url)
            .env(
                "CORS_ORIGINS",
                "http://localhost:3000,http://tauri.localhost,tauri://localhost",
            )
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        configure_process_group(&mut command);

        let mut child = command
            .spawn()
            .map_err(|error| format!("failed to start local Python runtime: {error}"))?;
        let stdout = match child.stdout.take() {
            Some(stdout) => stdout,
            None => {
                terminate_process_tree(&mut child);
                return Err("local runtime stdout was unavailable".to_string());
            }
        };
        let stderr = child.stderr.take();

        if let Some(stderr) = stderr {
            let stderr_secrets = vec![token.clone(), capture_control.secret.clone()];
            thread::spawn(move || {
                for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                    eprintln!(
                        "[desktop-runtime] {}",
                        redact_secrets(&line, &stderr_secrets),
                    );
                }
            });
        }

        let (sender, receiver) = mpsc::sync_channel(1);
        thread::spawn(move || {
            let mut line = String::new();
            let result = BufReader::new(stdout)
                .read_line(&mut line)
                .map_err(|error| format!("failed to read runtime readiness: {error}"))
                .and_then(|bytes| {
                    if bytes == 0 {
                        Err("local runtime exited before readiness".to_string())
                    } else {
                        parse_readiness_line(&line)
                    }
                });
            let _ = sender.send(result);
        });

        let port = match receiver.recv_timeout(STARTUP_TIMEOUT) {
            Ok(Ok(port)) => port,
            Ok(Err(error)) => {
                terminate_process_tree(&mut child);
                return Err(error);
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                terminate_process_tree(&mut child);
                return Err("local runtime readiness timed out".to_string());
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                terminate_process_tree(&mut child);
                return Err("local runtime readiness channel closed".to_string());
            }
        };

        ensure_runtime_alive_after_ready(&mut child)?;

        Ok(Self {
            child: Some(child),
            connection: RuntimeConnection {
                base_url: format!("http://127.0.0.1:{port}"),
                token,
            },
        })
    }

    pub fn connection(&self) -> RuntimeConnection {
        self.connection.clone()
    }

    pub fn shutdown(&mut self) {
        if let Some(mut child) = self.child.take() {
            terminate_process_tree(&mut child);
        }
    }
}

impl Drop for RuntimeProcess {
    fn drop(&mut self) {
        self.shutdown();
    }
}

pub struct RuntimeState {
    runtime: Mutex<Option<RuntimeProcess>>,
}

impl RuntimeState {
    pub fn new(runtime: RuntimeProcess) -> Self {
        Self {
            runtime: Mutex::new(Some(runtime)),
        }
    }

    pub fn connection(&self) -> Result<RuntimeConnection, String> {
        self.runtime
            .lock()
            .map_err(|_| "local runtime state is unavailable".to_string())?
            .as_ref()
            .map(RuntimeProcess::connection)
            .ok_or_else(|| "local runtime is not running".to_string())
    }

    pub fn shutdown(&self) {
        if let Ok(mut guard) = self.runtime.lock() {
            guard.take();
        }
    }
}

pub fn project_root() -> PathBuf {
    std::env::var_os("AIMING_COOKIE_PROJECT_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../.."))
}

fn python_executable() -> OsString {
    std::env::var_os("AIMING_COOKIE_PYTHON").unwrap_or_else(|| {
        if cfg!(windows) {
            OsString::from("python")
        } else {
            OsString::from("python3")
        }
    })
}

fn create_launch_token() -> String {
    let mut bytes = [0_u8; 32];
    rand::rng().fill_bytes(&mut bytes);
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn redact_secrets(text: &str, secrets: &[String]) -> String {
    secrets
        .iter()
        .filter(|secret| !secret.is_empty())
        .fold(text.to_string(), |redacted, secret| {
            redacted.replace(secret, "[REDACTED]")
        })
}

fn parse_readiness_line(line: &str) -> Result<u16, String> {
    let value: Value = serde_json::from_str(line)
        .map_err(|_| "local runtime emitted malformed readiness JSON".to_string())?;
    let object = value
        .as_object()
        .filter(|object| object.len() == 2)
        .ok_or_else(|| "local runtime emitted malformed readiness message".to_string())?;
    if object.get("type").and_then(Value::as_str) != Some("ready") {
        return Err("local runtime emitted malformed readiness message".to_string());
    }
    let port = object
        .get("port")
        .and_then(Value::as_u64)
        .filter(|port| (1..=u16::MAX as u64).contains(port))
        .ok_or_else(|| "local runtime emitted malformed readiness message".to_string())?;
    Ok(port as u16)
}

#[cfg(unix)]
fn configure_process_group(command: &mut Command) {
    use std::os::unix::process::CommandExt;
    command.process_group(0);
}

#[cfg(windows)]
fn configure_process_group(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
    command.creation_flags(CREATE_NEW_PROCESS_GROUP);
}

#[cfg(not(any(unix, windows)))]
fn configure_process_group(_command: &mut Command) {}

#[cfg(not(unix))]
fn wait_for_child_exit(child: &mut Child, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if matches!(child.try_wait(), Ok(Some(_))) {
            return true;
        }
        thread::sleep(Duration::from_millis(20));
    }
    matches!(child.try_wait(), Ok(Some(_)))
}

#[cfg(unix)]
fn process_group_exists(process_group: i32) -> bool {
    unsafe { libc::kill(-process_group, 0) == 0 }
}

#[cfg(unix)]
fn wait_for_process_group_exit(child: &mut Child, process_group: i32, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        let child_exited = matches!(child.try_wait(), Ok(Some(_)));
        if child_exited && !process_group_exists(process_group) {
            return true;
        }
        thread::sleep(Duration::from_millis(20));
    }
    let child_exited = matches!(child.try_wait(), Ok(Some(_)));
    child_exited && !process_group_exists(process_group)
}

fn ensure_runtime_alive_after_ready(child: &mut Child) -> Result<(), String> {
    if matches!(child.try_wait(), Ok(Some(_))) {
        terminate_process_tree(child);
        return Err("local runtime exited immediately after readiness".to_string());
    }
    Ok(())
}

fn terminate_process_tree(child: &mut Child) {
    // Closing the pipe is the normal shutdown protocol. The Python runtime
    // watches for EOF and stops its API and worker before exiting.
    child.stdin.take();

    #[cfg(unix)]
    {
        let process_group = child.id() as i32;
        if wait_for_process_group_exit(child, process_group, SHUTDOWN_GRACE) {
            return;
        }

        unsafe {
            libc::kill(-process_group, libc::SIGTERM);
        }
        if wait_for_process_group_exit(child, process_group, SHUTDOWN_GRACE) {
            return;
        }

        unsafe {
            libc::kill(-process_group, libc::SIGKILL);
        }
        let _ = child.kill();
        let _ = child.wait();
    }

    #[cfg(windows)]
    {
        if wait_for_child_exit(child, SHUTDOWN_GRACE) {
            return;
        }
        let _ = Command::new("taskkill")
            .args(["/PID", &child.id().to_string(), "/T", "/F"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
        let _ = child.kill();
        let _ = child.wait();
    }

    #[cfg(not(any(unix, windows)))]
    {
        if wait_for_child_exit(child, SHUTDOWN_GRACE) {
            return;
        }
        let _ = child.kill();
        let _ = child.wait();
    }
}

#[cfg(test)]
mod tests {
    use super::{create_launch_token, parse_readiness_line, redact_secrets};

    #[cfg(unix)]
    use super::{configure_process_group, terminate_process_tree};
    #[cfg(unix)]
    use std::io::{BufRead, BufReader};
    #[cfg(unix)]
    use std::process::{Command, Stdio};
    #[cfg(unix)]
    use std::thread;
    #[cfg(unix)]
    use std::time::{Duration, Instant};

    #[cfg(unix)]
    #[test]
    fn shutdown_closes_stdin_before_sending_signals() {
        let mut command = Command::new("sh");
        command
            .args(["-c", "trap 'exit 42' TERM; cat >/dev/null; exit 0"])
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        configure_process_group(&mut command);
        let mut child = command.spawn().expect("spawn stdin-watching child");

        terminate_process_tree(&mut child);

        assert_eq!(child.wait().expect("read child status").code(), Some(0));
    }

    #[cfg(unix)]
    #[test]
    fn shutdown_cleans_process_group_when_direct_child_already_exited() {
        let mut command = Command::new("sh");
        command
            .args(["-c", "trap '' HUP; sleep 30 & echo $!"])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        configure_process_group(&mut command);
        let mut child = command.spawn().expect("spawn process group leader");
        let process_group = child.id() as i32;
        let stdout = child.stdout.take().expect("capture descendant pid");
        let mut line = String::new();
        BufReader::new(stdout)
            .read_line(&mut line)
            .expect("read descendant pid");
        let descendant: i32 = line.trim().parse().expect("parse descendant pid");
        child.wait().expect("wait for direct child");

        unsafe {
            assert_eq!(libc::kill(descendant, 0), 0, "descendant should be alive");
        }
        terminate_process_tree(&mut child);

        let deadline = Instant::now() + Duration::from_secs(1);
        while Instant::now() < deadline {
            let status = Command::new("ps")
                .args(["-o", "stat=", "-p", &descendant.to_string()])
                .output()
                .expect("inspect descendant state");
            let state = String::from_utf8_lossy(&status.stdout);
            if state.trim().is_empty() || state.trim_start().starts_with('Z') {
                return;
            }
            thread::sleep(Duration::from_millis(20));
        }

        unsafe {
            libc::kill(-process_group, libc::SIGKILL);
        }
        panic!("descendant process survived runtime shutdown");
    }

    #[test]
    fn readiness_protocol_accepts_only_exact_dynamic_port_message() {
        assert_eq!(
            parse_readiness_line(r#"{"type":"ready","port":43127}"#).unwrap(),
            43127,
        );
        for invalid in [
            "not-json",
            r#"{"type":"ready"}"#,
            r#"{"type":"ready","port":0}"#,
            r#"{"type":"ready","port":65536}"#,
            r#"{"type":"ready","port":"43127"}"#,
            r#"{"type":"ready","port":43127,"token":"secret"}"#,
        ] {
            assert!(parse_readiness_line(invalid).is_err());
        }
    }

    #[test]
    fn launch_tokens_are_fresh_high_entropy_and_redacted() {
        let first = create_launch_token();
        let second = create_launch_token();
        assert_ne!(first, second);
        assert_eq!(first.len(), 64);
        assert_eq!(
            redact_secrets(
                &format!("token={first};capture=other"),
                &[first.clone(), "other".to_string()]
            ),
            "token=[REDACTED];capture=[REDACTED]"
        );
    }

    #[cfg(windows)]
    #[test]
    fn runtime_ready_then_exit_cleans_the_already_exited_child() {
        use super::ensure_runtime_alive_after_ready;
        use std::process::{Command, Stdio};

        let mut child = Command::new("cmd")
            .args(["/C", "exit 0"])
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("spawn exiting child");
        child.wait().expect("wait for child exit");

        assert_eq!(
            ensure_runtime_alive_after_ready(&mut child).unwrap_err(),
            "local runtime exited immediately after readiness"
        );
    }
}
