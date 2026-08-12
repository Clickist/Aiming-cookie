use rand::RngCore;
use serde::Serialize;
use serde_json::Value;
use std::ffi::OsString;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use crate::capture_coordinator::CaptureControlConnection;

const STARTUP_TIMEOUT: Duration = Duration::from_secs(15);
const SHUTDOWN_GRACE: Duration = Duration::from_secs(2);
const TOKEN_ENV: &str = "AIMING_COOKIE_DESKTOP_TOKEN";
const WATCH_PARENT_STDIN_ENV: &str = "AIMING_COOKIE_WATCH_PARENT_STDIN";
const CAPTURE_CONTROL_ADDRESS_ENV: &str = "AIMING_COOKIE_NATIVE_CAPTURE_CONTROL_ADDR";
const CAPTURE_CONTROL_SECRET_ENV: &str = "AIMING_COOKIE_NATIVE_CAPTURE_CONTROL_SECRET";
const COACH_SIDECAR_HOST_ENV: &str = "COACH_SIDECAR_HOST";
const COACH_SIDECAR_PORT_ENV: &str = "COACH_SIDECAR_PORT";
const COACH_SIDECAR_URL_ENV: &str = "COACH_SIDECAR_URL";
const SUPERVISOR_POLL_INTERVAL: Duration = Duration::from_millis(250);
const RESTART_BACKOFF: Duration = Duration::from_millis(500);
const MAX_RESTART_ATTEMPTS: u8 = 3;

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeConnection {
    pub base_url: String,
    pub token: String,
    pub sidecar_url: String,
}

pub struct RuntimeProcess {
    child: Option<Child>,
    coach_sidecar: Option<Child>,
    connection: RuntimeConnection,
}

#[derive(Clone, Debug)]
pub struct RuntimeLayout {
    working_dir: PathBuf,
    backend_program: OsString,
    backend_args: Vec<OsString>,
    coach_program: OsString,
    coach_args: Vec<OsString>,
    coach_loader: Option<PathBuf>,
    coach_entry: Option<PathBuf>,
    pi_source_dir: Option<PathBuf>,
    pi_tsconfig: Option<PathBuf>,
    resource_root: Option<PathBuf>,
}

#[derive(Clone, Debug)]
struct RuntimeLaunch {
    layout: RuntimeLayout,
    app_data_dir: PathBuf,
    capture_control: CaptureControlConnection,
}

impl RuntimeProcess {
    pub fn start(
        layout: &RuntimeLayout,
        app_data_dir: &Path,
        capture_control: &CaptureControlConnection,
    ) -> Result<Self, String> {
        std::fs::create_dir_all(app_data_dir)
            .map_err(|error| format!("failed to create app data directory: {error}"))?;

        let database_path = app_data_dir.join("aiming_cookie.db");
        let database_url = format!("sqlite+aiosqlite:///{}", database_path.display());
        let (mut coach_sidecar, coach_sidecar_url) = start_coach_sidecar(layout, app_data_dir, &database_url)?;
        let token = create_launch_token();
        let mut command = Command::new(&layout.backend_program);
        command
            .args(&layout.backend_args)
            .current_dir(&layout.working_dir)
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
            .env(COACH_SIDECAR_URL_ENV, coach_sidecar_url)
            .env(
                "CORS_ORIGINS",
                "http://localhost:3000,http://tauri.localhost,tauri://localhost",
            )
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        if let Some(resource_root) = &layout.resource_root {
            command.env("AIMING_COOKIE_RESOURCE_ROOT", resource_root);
        }
        configure_python_io(&mut command);
        configure_process_group(&mut command);

        let mut child = command.spawn().map_err(|error| {
            terminate_process_tree(&mut coach_sidecar);
            format!("failed to start local Python runtime: {error}")
        })?;
        let stdout = match child.stdout.take() {
            Some(stdout) => stdout,
            None => {
                terminate_process_tree(&mut child);
                terminate_process_tree(&mut coach_sidecar);
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
                terminate_process_tree(&mut coach_sidecar);
                return Err(error);
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                terminate_process_tree(&mut child);
                terminate_process_tree(&mut coach_sidecar);
                return Err("local runtime readiness timed out".to_string());
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                terminate_process_tree(&mut child);
                terminate_process_tree(&mut coach_sidecar);
                return Err("local runtime readiness channel closed".to_string());
            }
        };

        if let Err(error) = ensure_runtime_alive_after_ready(&mut child) {
            terminate_process_tree(&mut coach_sidecar);
            return Err(error);
        }

        Ok(Self {
            child: Some(child),
            coach_sidecar: Some(coach_sidecar),
            connection: RuntimeConnection {
                base_url: format!("http://127.0.0.1:{port}"),
                token,
                sidecar_url: coach_sidecar_url,
            },
        })
    }

    pub fn connection(&self) -> RuntimeConnection {
        self.connection.clone()
    }

    fn unexpected_exit_reason(&mut self) -> Option<String> {
        if self
            .child
            .as_mut()
            .is_some_and(|child| matches!(child.try_wait(), Ok(Some(_))))
        {
            return Some("local runtime exited unexpectedly".to_string());
        }
        if self
            .coach_sidecar
            .as_mut()
            .is_some_and(|child| matches!(child.try_wait(), Ok(Some(_))))
        {
            return Some("Coach sidecar exited unexpectedly".to_string());
        }
        None
    }

    pub fn shutdown(&mut self) {
        if let Some(mut child) = self.child.take() {
            terminate_process_tree(&mut child);
        }
        if let Some(mut coach_sidecar) = self.coach_sidecar.take() {
            terminate_process_tree(&mut coach_sidecar);
        }
    }
}

impl Drop for RuntimeProcess {
    fn drop(&mut self) {
        self.shutdown();
    }
}

struct RuntimeSupervisor {
    runtime: Mutex<Option<RuntimeProcess>>,
    launch: RuntimeLaunch,
    shutdown_requested: AtomicBool,
    restart_attempts: Mutex<u8>,
    terminal_error: Mutex<Option<String>>,
}

pub struct RuntimeState {
    supervisor: Arc<RuntimeSupervisor>,
}

impl RuntimeState {
    pub fn new(
        runtime: RuntimeProcess,
        layout: RuntimeLayout,
        app_data_dir: PathBuf,
        capture_control: CaptureControlConnection,
    ) -> Self {
        let supervisor = Arc::new(RuntimeSupervisor {
            runtime: Mutex::new(Some(runtime)),
            launch: RuntimeLaunch {
                layout,
                app_data_dir,
                capture_control,
            },
            shutdown_requested: AtomicBool::new(false),
            restart_attempts: Mutex::new(0),
            terminal_error: Mutex::new(None),
        });
        start_runtime_supervisor(Arc::clone(&supervisor));
        Self { supervisor }
    }

    pub fn connection(&self) -> Result<RuntimeConnection, String> {
        let mut runtime = self
            .supervisor
            .runtime
            .lock()
            .map_err(|_| "local runtime state is unavailable".to_string())?;
        if let Some(process) = runtime.as_mut() {
            if process.unexpected_exit_reason().is_none() {
                return Ok(process.connection());
            }
        }
        drop(runtime);

        if let Some(error) = self
            .supervisor
            .terminal_error
            .lock()
            .map_err(|_| "local runtime state is unavailable".to_string())?
            .clone()
        {
            return Err(error);
        }
        Err("local runtime is restarting".to_string())
    }

    pub fn shutdown(&self) {
        self.supervisor
            .shutdown_requested
            .store(true, Ordering::SeqCst);
        if let Ok(mut guard) = self.supervisor.runtime.lock() {
            guard.take();
        }
    }
}

fn start_runtime_supervisor(supervisor: Arc<RuntimeSupervisor>) {
    thread::spawn(move || loop {
        thread::sleep(SUPERVISOR_POLL_INTERVAL);
        if supervisor.shutdown_requested.load(Ordering::SeqCst) {
            return;
        }
        let exit_reason = supervisor
            .runtime
            .lock()
            .ok()
            .and_then(|mut runtime| runtime.as_mut()?.unexpected_exit_reason());
        if let Some(reason) = exit_reason {
            restart_runtime(&supervisor, reason);
        }
    });
}

fn restart_runtime(supervisor: &RuntimeSupervisor, exit_reason: String) {
    if supervisor.shutdown_requested.load(Ordering::SeqCst) {
        return;
    }
    if let Ok(mut runtime) = supervisor.runtime.lock() {
        runtime.take();
    }

    let attempt = match supervisor.restart_attempts.lock() {
        Ok(mut attempts) if restart_is_allowed(false, *attempts) => {
            *attempts += 1;
            *attempts
        }
        Ok(_) => {
            set_terminal_runtime_error(supervisor, exit_reason);
            return;
        }
        Err(_) => return,
    };
    thread::sleep(RESTART_BACKOFF);
    if supervisor.shutdown_requested.load(Ordering::SeqCst) {
        return;
    }

    match RuntimeProcess::start(
        &supervisor.launch.layout,
        &supervisor.launch.app_data_dir,
        &supervisor.launch.capture_control,
    ) {
        Ok(runtime) if !supervisor.shutdown_requested.load(Ordering::SeqCst) => {
            if let Ok(mut guard) = supervisor.runtime.lock() {
                let _ = guard.replace(runtime);
            }
        }
        Ok(runtime) => drop(runtime),
        Err(error) if attempt < MAX_RESTART_ATTEMPTS => {
            restart_runtime(
                supervisor,
                format!("{exit_reason}; restart attempt {attempt} failed: {error}"),
            );
        }
        Err(error) => set_terminal_runtime_error(
            supervisor,
            format!("{exit_reason}; restart attempt {attempt} failed: {error}"),
        ),
    }
}

fn restart_is_allowed(shutdown_requested: bool, attempts: u8) -> bool {
    !shutdown_requested && attempts < MAX_RESTART_ATTEMPTS
}

fn set_terminal_runtime_error(supervisor: &RuntimeSupervisor, cause: String) {
    if let Ok(mut error) = supervisor.terminal_error.lock() {
        *error = Some(format!(
            "local runtime is unavailable after {MAX_RESTART_ATTEMPTS} restart attempts: {cause}"
        ));
    }
}

pub fn project_root() -> PathBuf {
    std::env::var_os("AIMING_COOKIE_PROJECT_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../.."))
}

fn development_runtime_layout(
    project_root: &Path,
    python_override: Option<OsString>,
) -> RuntimeLayout {
    let pi_source_dir = project_root.join("third_party").join("pi");
    RuntimeLayout {
        working_dir: project_root.to_path_buf(),
        backend_program: python_override.unwrap_or_else(|| {
            if cfg!(windows) {
                OsString::from("python")
            } else {
                OsString::from("python3")
            }
        }),
        backend_args: vec!["-m".into(), "webapp.backend.desktop_runtime".into()],
        coach_program: "node".into(),
        coach_args: Vec::new(),
        coach_loader: Some(
            pi_source_dir
                .join("node_modules")
                .join("tsx")
                .join("dist")
                .join("loader.mjs"),
        ),
        coach_entry: Some(
            project_root
                .join("webapp")
                .join("coach-runtime")
                .join("start-sidecar.ts"),
        ),
        pi_tsconfig: Some(pi_source_dir.join("tsconfig.json")),
        pi_source_dir: Some(pi_source_dir),
        resource_root: None,
    }
}

fn packaged_runtime_layout(resource_dir: &Path) -> Result<RuntimeLayout, String> {
    let runtime_root = resource_dir.join("runtime");
    let backend_program = runtime_root
        .join("aiming-cookie-runtime")
        .join(if cfg!(windows) {
            "aiming-cookie-runtime.exe"
        } else {
            "aiming-cookie-runtime"
        });
    let coach_program = runtime_root.join(if cfg!(windows) {
        "coach-sidecar.exe"
    } else {
        "coach-sidecar"
    });
    for (label, path, directory) in [
        ("packaged backend runtime", &backend_program, false),
        ("packaged Coach runtime", &coach_program, false),
        ("packaged knowledge", &runtime_root.join("knowledge"), true),
        (
            "packaged Coach prompt",
            &runtime_root.join("coach-system.md"),
            false,
        ),
    ] {
        let valid = if directory {
            path.is_dir()
        } else {
            path.is_file()
        };
        if !valid {
            return Err(format!("{label} is missing: {}", path.display()));
        }
    }
    Ok(RuntimeLayout {
        working_dir: runtime_root.clone(),
        backend_program: backend_program.into_os_string(),
        backend_args: Vec::new(),
        coach_program: coach_program.into_os_string(),
        coach_args: Vec::new(),
        coach_loader: None,
        coach_entry: None,
        pi_source_dir: None,
        pi_tsconfig: None,
        resource_root: Some(runtime_root),
    })
}

pub fn runtime_layout(resource_dir: &Path) -> Result<RuntimeLayout, String> {
    if cfg!(debug_assertions) {
        Ok(development_runtime_layout(
            &project_root(),
            std::env::var_os("AIMING_COOKIE_PYTHON"),
        ))
    } else {
        packaged_runtime_layout(resource_dir)
    }
}

fn configure_python_io(command: &mut Command) {
    command
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8");
}

fn start_coach_sidecar(layout: &RuntimeLayout, app_data_dir: &Path, database_url: &str) -> Result<(Child, String), String> {
    let mut command = Command::new(&layout.coach_program);
    if let (Some(pi_source_dir), Some(tsx_loader), Some(sidecar_entry), Some(tsconfig)) = (
        &layout.pi_source_dir,
        &layout.coach_loader,
        &layout.coach_entry,
        &layout.pi_tsconfig,
    ) {
        for (label, path) in [
            ("Pi source", pi_source_dir),
            ("tsx loader", tsx_loader),
            ("Coach sidecar entry", sidecar_entry),
            ("Pi tsconfig", tsconfig),
        ] {
            if !path.exists() {
                return Err(format!(
                    "failed to start Coach sidecar: {label} is missing: {}",
                    path.display()
                ));
            }
        }
        let loader_url = file_url(tsx_loader)
            .map_err(|error| format!("failed to start Coach sidecar: {error}"))?;
        command
            .arg(format!("--import={loader_url}"))
            .arg(sidecar_entry)
            .env("PI_SOURCE_DIR", pi_source_dir)
            .env("TSX_TSCONFIG_PATH", tsconfig);
    } else {
        command.args(&layout.coach_args);
    }
    command
        .current_dir(&layout.working_dir)
        .env_remove(TOKEN_ENV)
        .env(COACH_SIDECAR_HOST_ENV, "127.0.0.1")
        .env(COACH_SIDECAR_PORT_ENV, "0")
        .env("DATABASE_URL", database_url)
        .env("DATA_ROOT", app_data_dir)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped());
    if let Some(resource_root) = &layout.resource_root {
        command.env("AIMING_COOKIE_RESOURCE_ROOT", resource_root);
    }
    configure_process_group(&mut command);

    let mut child = command
        .spawn()
        .map_err(|error| format!("failed to start Coach sidecar: {error}"))?;
    let stderr = match child.stderr.take() {
        Some(stderr) => stderr,
        None => {
            terminate_process_tree(&mut child);
            return Err("Coach sidecar stderr was unavailable".to_string());
        }
    };

    let (sender, receiver) = mpsc::sync_channel(1);
    thread::spawn(move || {
        let mut sent_readiness = false;
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            if !sent_readiness {
                if let Ok(url) = parse_sidecar_readiness_line(&line) {
                    let _ = sender.send(Ok(url));
                    sent_readiness = true;
                    continue;
                }
            }
            eprintln!("[coach-sidecar] {line}");
        }
        if !sent_readiness {
            let _ = sender.send(Err("Coach sidecar exited before readiness".to_string()));
        }
    });

    let url = match receiver.recv_timeout(STARTUP_TIMEOUT) {
        Ok(Ok(url)) => url,
        Ok(Err(error)) => {
            terminate_process_tree(&mut child);
            return Err(error);
        }
        Err(mpsc::RecvTimeoutError::Timeout) => {
            terminate_process_tree(&mut child);
            return Err("Coach sidecar readiness timed out".to_string());
        }
        Err(mpsc::RecvTimeoutError::Disconnected) => {
            terminate_process_tree(&mut child);
            return Err("Coach sidecar readiness channel closed".to_string());
        }
    };

    if matches!(child.try_wait(), Ok(Some(_))) {
        terminate_process_tree(&mut child);
        return Err("Coach sidecar exited immediately after readiness".to_string());
    }

    Ok((child, url))
}

fn file_url(path: &Path) -> Result<String, String> {
    let canonical = path
        .canonicalize()
        .map_err(|error| format!("failed to resolve tsx loader: {error}"))?;
    let value = canonical
        .to_str()
        .ok_or_else(|| "failed to resolve tsx loader as a Unicode path".to_string())?
        .replace('\\', "/");
    #[cfg(windows)]
    let value = value.strip_prefix("//?/").unwrap_or(&value).to_string();
    Ok(if value.starts_with('/') {
        format!("file://{value}")
    } else {
        format!("file:///{value}")
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

fn parse_sidecar_readiness_line(line: &str) -> Result<String, String> {
    let port_text = line
        .strip_prefix("coach sidecar listening on http://127.0.0.1:")
        .filter(|value| !value.is_empty() && value.bytes().all(|byte| byte.is_ascii_digit()))
        .ok_or_else(|| "Coach sidecar emitted malformed readiness message".to_string())?;
    let port = port_text
        .parse::<u16>()
        .ok()
        .filter(|port| *port != 0)
        .ok_or_else(|| "Coach sidecar emitted malformed readiness message".to_string())?;
    Ok(format!("http://127.0.0.1:{port}"))
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
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    command.creation_flags(CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW);
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
    use super::{
        configure_python_io, create_launch_token, development_runtime_layout, file_url,
        packaged_runtime_layout, parse_readiness_line, parse_sidecar_readiness_line,
        redact_secrets, restart_is_allowed, RuntimeConnection, RuntimeProcess,
        MAX_RESTART_ATTEMPTS,
    };
    use std::path::Path;

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
    fn development_layout_preserves_source_runtime_commands() {
        let layout = development_runtime_layout(
            Path::new(r"C:\src\Aiming-cookie"),
            Some("custom-python".into()),
        );

        assert_eq!(layout.working_dir, Path::new(r"C:\src\Aiming-cookie"));
        assert_eq!(layout.backend_program, "custom-python");
        assert_eq!(
            layout.backend_args,
            ["-m", "webapp.backend.desktop_runtime"]
        );
        assert_eq!(layout.coach_program, "node");
        assert_eq!(
            layout.coach_entry.as_deref(),
            Some(Path::new(
                r"C:\src\Aiming-cookie\webapp\coach-runtime\start-sidecar.ts"
            ))
        );
        assert_eq!(
            layout.pi_source_dir.as_deref(),
            Some(Path::new(r"C:\src\Aiming-cookie\third_party\pi"))
        );
        assert!(layout.resource_root.is_none());
    }

    #[test]
    fn packaged_layout_uses_only_validated_resource_paths() {
        let temp = std::env::temp_dir().join(format!(
            "aiming-cookie-packaged-layout-{}",
            create_launch_token()
        ));
        let runtime_root = temp.join("runtime");
        let backend = runtime_root
            .join("aiming-cookie-runtime")
            .join("aiming-cookie-runtime.exe");
        let coach = runtime_root.join("coach-sidecar.exe");
        let knowledge = runtime_root.join("knowledge");
        let prompt = runtime_root.join("coach-system.md");
        std::fs::create_dir_all(backend.parent().unwrap()).unwrap();
        std::fs::create_dir_all(&knowledge).unwrap();
        std::fs::write(&backend, b"backend").unwrap();
        std::fs::write(&coach, b"coach").unwrap();
        std::fs::write(&prompt, b"prompt").unwrap();

        let layout = packaged_runtime_layout(&temp).unwrap();
        assert_eq!(layout.working_dir, runtime_root);
        assert_eq!(layout.backend_program, backend.as_os_str());
        assert!(layout.backend_args.is_empty());
        assert_eq!(layout.coach_program, coach.as_os_str());
        assert!(layout.coach_args.is_empty());
        assert!(layout.pi_source_dir.is_none());
        assert_eq!(
            layout.resource_root.as_deref(),
            Some(layout.working_dir.as_path())
        );

        std::fs::remove_dir_all(temp).unwrap();
    }

    #[test]
    fn packaged_layout_fails_before_spawn_when_resources_are_missing() {
        let temp = std::env::temp_dir().join(format!(
            "aiming-cookie-missing-layout-{}",
            create_launch_token()
        ));
        std::fs::create_dir_all(temp.join("runtime")).unwrap();

        let error = packaged_runtime_layout(&temp).unwrap_err();
        assert!(error.contains("packaged backend runtime is missing"));

        std::fs::remove_dir_all(temp).unwrap();
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

    #[test]
    fn sidecar_readiness_accepts_only_exact_loopback_dynamic_port_message() {
        assert_eq!(
            parse_sidecar_readiness_line("coach sidecar listening on http://127.0.0.1:43127")
                .unwrap(),
            "http://127.0.0.1:43127"
        );
        for invalid in [
            "coach sidecar listening on http://localhost:43127",
            "coach sidecar listening on http://127.0.0.1:0",
            "coach sidecar listening on http://127.0.0.1:+43127",
            "coach sidecar listening on http://127.0.0.1:65536",
            "coach sidecar listening on http://127.0.0.1:43127/path",
            "coach sidecar failed: EADDRINUSE",
        ] {
            assert!(parse_sidecar_readiness_line(invalid).is_err());
        }
    }

    #[cfg(windows)]
    #[test]
    fn file_url_strips_windows_verbatim_path_prefix() {
        let executable = std::env::current_exe().expect("resolve current executable");
        let url = file_url(&executable).expect("convert existing Windows path to file URL");

        assert!(url.starts_with("file:///"));
        assert!(!url.contains("/?/"));
    }

    #[test]
    fn runtime_forces_utf8_python_stdio() {
        let mut command = std::process::Command::new("python");
        configure_python_io(&mut command);
        let envs = command.get_envs().collect::<Vec<_>>();

        assert!(envs.iter().any(|(key, value)| {
            *key == "PYTHONUTF8" && value.is_some_and(|value| value == "1")
        }));
        assert!(envs.iter().any(|(key, value)| {
            *key == "PYTHONIOENCODING" && value.is_some_and(|value| value == "utf-8")
        }));
    }

    #[test]
    fn restart_budget_is_bounded_and_normal_shutdown_suppresses_restart() {
        assert!(restart_is_allowed(false, 0));
        assert!(restart_is_allowed(false, MAX_RESTART_ATTEMPTS - 1));
        assert!(!restart_is_allowed(false, MAX_RESTART_ATTEMPTS));
        assert!(!restart_is_allowed(true, 0));
    }

    #[cfg(windows)]
    #[test]
    fn unexpected_child_exit_is_detected_before_the_connection_is_reused() {
        use std::process::{Command, Stdio};

        let mut child = Command::new("cmd")
            .args(["/C", "exit 0"])
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("spawn exiting child");
        child.wait().expect("wait for child exit");
        let mut runtime = RuntimeProcess {
            child: Some(child),
            coach_sidecar: None,
            connection: RuntimeConnection {
                base_url: "http://127.0.0.1:43127".to_string(),
                token: "test-token".to_string(),
                sidecar_url: "http://127.0.0.1:43128".to_string(),
            },
        };

        assert_eq!(
            runtime.unexpected_exit_reason().as_deref(),
            Some("local runtime exited unexpectedly")
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
