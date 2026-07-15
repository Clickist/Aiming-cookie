//! Windows-first, opt-in raw mouse capture for KovaaK runs.
#![allow(dead_code)]
//!
//! RefleK was evaluated as a capability reference, but this module is an
//! independent implementation built on the public Win32 Raw Input APIs. The
//! queue, rolling buffer, diagnostics, and snapshot codec are Aiming Cookie
//! contracts shared with the Python runtime.

use serde::Serialize;
use std::collections::VecDeque;
use std::fs;
#[cfg(windows)]
use std::fs::File;
use std::io;
#[cfg(windows)]
use std::io::Write;
#[cfg(windows)]
use std::path::Path;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub const SNAPSHOT_MAGIC: &[u8; 4] = b"ACRI";
pub const SNAPSHOT_VERSION: u8 = 1;
const DEFAULT_BUFFER_MINUTES: u64 = 10;
const MAX_SNAPSHOT_POINTS: usize = 1_000_000;
const MAX_SNAPSHOT_BYTES: usize = 32 * 1024 * 1024;
const MAX_SNAPSHOT_SPAN_MS: i64 = (DEFAULT_BUFFER_MINUTES * 60 * 1_000) as i64;
const SUPPORTED_BUTTON_MASK: u32 = 0b111;
const SNAPSHOT_MAX_DIRTY_INTERVAL: Duration = Duration::from_secs(30);
const SNAPSHOT_RETRY_INTERVAL: Duration = Duration::from_secs(5);
#[cfg(windows)]
const CAPTURE_QUEUE_CAPACITY: usize = 16_384;
const SNAPSHOT_IDLE_INTERVAL: Duration = Duration::from_secs(1);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct MousePoint {
    pub timestamp_ms: i64,
    pub dx: i32,
    pub dy: i32,
    pub buttons: u32,
}

#[derive(Debug)]
pub struct RingBuffer {
    points: VecDeque<MousePoint>,
    window: Duration,
}

impl RingBuffer {
    pub fn new(window: Duration) -> Self {
        Self {
            points: VecDeque::new(),
            window,
        }
    }

    pub fn push(&mut self, point: MousePoint) -> bool {
        if self
            .points
            .back()
            .is_some_and(|previous| point.timestamp_ms < previous.timestamp_ms)
        {
            return false;
        }
        self.points.push_back(point);
        let cutoff = point.timestamp_ms - self.window.as_millis() as i64;
        self.prune_before(cutoff);
        if self.points.len() > MAX_SNAPSHOT_POINTS {
            self.points.pop_front();
            return false;
        }
        true
    }

    pub fn clear(&mut self) {
        self.points.clear();
    }

    pub fn prune_before(&mut self, cutoff_ms: i64) -> usize {
        let before = self.points.len();
        while self
            .points
            .front()
            .is_some_and(|old| old.timestamp_ms < cutoff_ms)
        {
            self.points.pop_front();
        }
        before - self.points.len()
    }

    pub fn snapshot(&self) -> Vec<MousePoint> {
        self.points.iter().copied().collect()
    }

    pub fn len(&self) -> usize {
        self.points.len()
    }
}

pub fn encode_snapshot(points: &[MousePoint]) -> Vec<u8> {
    let mut out = Vec::with_capacity(12 + points.len() * 20);
    out.extend_from_slice(SNAPSHOT_MAGIC);
    out.push(SNAPSHOT_VERSION);
    out.extend_from_slice(&[0, 0, 0]);
    out.extend_from_slice(&(points.len() as u32).to_le_bytes());
    for point in points {
        out.extend_from_slice(&point.timestamp_ms.to_le_bytes());
        out.extend_from_slice(&point.dx.to_le_bytes());
        out.extend_from_slice(&point.dy.to_le_bytes());
        out.extend_from_slice(&point.buttons.to_le_bytes());
    }
    out
}

fn validate_snapshot_points(points: &[MousePoint]) -> io::Result<()> {
    if points.len() > MAX_SNAPSHOT_POINTS {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "raw input snapshot has too many points",
        ));
    }
    let mut first_timestamp = None;
    let mut previous_timestamp = None;
    for point in points {
        if point.buttons & !SUPPORTED_BUTTON_MASK != 0 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "raw input buttons use unsupported bits",
            ));
        }
        if previous_timestamp.is_some_and(|previous| point.timestamp_ms < previous) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "raw input timestamps must be monotonic",
            ));
        }
        if let Some(first) = first_timestamp {
            if point.timestamp_ms.saturating_sub(first) > MAX_SNAPSHOT_SPAN_MS {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "raw input snapshot exceeds retention window",
                ));
            }
        } else {
            first_timestamp = Some(point.timestamp_ms);
        }
        previous_timestamp = Some(point.timestamp_ms);
    }
    Ok(())
}

pub fn decode_snapshot(mut bytes: &[u8]) -> io::Result<Vec<MousePoint>> {
    if bytes.len() < 12 || &bytes[..4] != SNAPSHOT_MAGIC {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "invalid raw input snapshot",
        ));
    }
    if bytes[4] != SNAPSHOT_VERSION {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "unsupported raw input snapshot version",
        ));
    }
    if bytes[5..8] != [0, 0, 0] {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "unsupported raw input snapshot header",
        ));
    }
    let count = u32::from_le_bytes(bytes[8..12].try_into().unwrap()) as usize;
    if count > MAX_SNAPSHOT_POINTS {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "raw input snapshot has too many points",
        ));
    }
    bytes = &bytes[12..];
    let expected = count
        .checked_mul(20)
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "snapshot too large"))?;
    if bytes.len() != expected {
        return Err(io::Error::new(
            io::ErrorKind::UnexpectedEof,
            "truncated raw input snapshot",
        ));
    }
    let mut points = Vec::with_capacity(count);
    for chunk in bytes.chunks_exact(20) {
        points.push(MousePoint {
            timestamp_ms: i64::from_le_bytes(chunk[0..8].try_into().unwrap()),
            dx: i32::from_le_bytes(chunk[8..12].try_into().unwrap()),
            dy: i32::from_le_bytes(chunk[12..16].try_into().unwrap()),
            buttons: u32::from_le_bytes(chunk[16..20].try_into().unwrap()),
        });
    }
    validate_snapshot_points(&points)?;
    Ok(points)
}

fn read_snapshot_file(path: &std::path::Path) -> io::Result<Vec<MousePoint>> {
    let metadata = fs::metadata(path)?;
    if metadata.len() > MAX_SNAPSHOT_BYTES as u64 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "raw input snapshot exceeds byte limit",
        ));
    }
    decode_snapshot(&fs::read(path)?)
}

#[cfg(windows)]
fn write_snapshot_atomic(path: &Path, points: &[MousePoint]) -> io::Result<()> {
    validate_snapshot_points(points)?;
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "snapshot has no parent"))?;
    fs::create_dir_all(parent)?;
    let tmp = path.with_extension("bin.tmp");
    let mut file = File::create(&tmp)?;
    file.write_all(&encode_snapshot(points))?;
    file.sync_data()?;
    replace_snapshot_file(&tmp, path)?;
    Ok(())
}

#[cfg(windows)]
fn replace_snapshot_file(source: &Path, destination: &Path) -> io::Result<()> {
    use std::iter::once;
    use std::os::windows::ffi::OsStrExt;
    use winapi::um::winbase::{MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH};

    let source: Vec<u16> = source.as_os_str().encode_wide().chain(once(0)).collect();
    let destination: Vec<u16> = destination
        .as_os_str()
        .encode_wide()
        .chain(once(0))
        .collect();
    let result = unsafe {
        MoveFileExW(
            source.as_ptr(),
            destination.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if result == 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RawInputStatus {
    pub supported: bool,
    pub enabled: bool,
    pub kovaak_process_present: bool,
    pub capture_healthy: bool,
    pub buffered_points: usize,
    pub dropped_points: u64,
    pub expired_points: u64,
    pub last_snapshot_at_ms: Option<i64>,
    pub last_snapshot_points: usize,
    pub snapshot_failures: u64,
    pub snapshot_error_code: Option<String>,
    pub snapshot_error_at_ms: Option<i64>,
    pub snapshot_error: Option<String>,
}

#[derive(Clone)]
struct CaptureStatus {
    kovaak_process_present: bool,
    capture_healthy: bool,
    buffered_points: usize,
    dropped_points: u64,
    expired_points: u64,
    last_snapshot_at_ms: Option<i64>,
    last_snapshot_points: usize,
    snapshot_failures: u64,
    snapshot_error_code: Option<String>,
    snapshot_error_at_ms: Option<i64>,
    snapshot_error: Option<String>,
}

#[derive(Default)]
struct SnapshotStatus {
    last_snapshot_at_ms: Option<i64>,
    last_snapshot_points: usize,
    snapshot_failures: u64,
    snapshot_error_code: Option<String>,
    snapshot_error_at_ms: Option<i64>,
    snapshot_error: Option<String>,
}

struct CaptureDiagnostics {
    capture_running: std::sync::atomic::AtomicBool,
    kovaak_process_present: std::sync::atomic::AtomicBool,
    buffered_points: std::sync::atomic::AtomicUsize,
    dropped_points: std::sync::atomic::AtomicU64,
    expired_points: std::sync::atomic::AtomicU64,
    snapshot: Mutex<SnapshotStatus>,
}

impl CaptureDiagnostics {
    fn new() -> Self {
        Self {
            capture_running: std::sync::atomic::AtomicBool::new(false),
            kovaak_process_present: std::sync::atomic::AtomicBool::new(false),
            buffered_points: std::sync::atomic::AtomicUsize::new(0),
            dropped_points: std::sync::atomic::AtomicU64::new(0),
            expired_points: std::sync::atomic::AtomicU64::new(0),
            snapshot: Mutex::new(SnapshotStatus::default()),
        }
    }

    fn record_capture_started(&self) {
        self.capture_running
            .store(true, std::sync::atomic::Ordering::Release);
    }

    fn record_capture_stopped(&self) {
        self.capture_running
            .store(false, std::sync::atomic::Ordering::Release);
        self.kovaak_process_present
            .store(false, std::sync::atomic::Ordering::Release);
    }

    fn record_kovaak_process_present(&self, present: bool) {
        self.kovaak_process_present
            .store(present, std::sync::atomic::Ordering::Release);
    }

    fn record_buffered_points(&self, points: usize) {
        self.buffered_points
            .store(points, std::sync::atomic::Ordering::Release);
    }

    fn record_drop(&self) {
        self.dropped_points
            .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    }

    fn record_expired(&self, points: usize) {
        self.expired_points
            .fetch_add(points as u64, std::sync::atomic::Ordering::Relaxed);
    }

    fn record_snapshot_success(&self, timestamp_ms: i64, points: usize) {
        self.record_buffered_points(points);
        if let Ok(mut snapshot) = self.snapshot.lock() {
            snapshot.last_snapshot_at_ms = Some(timestamp_ms);
            snapshot.last_snapshot_points = points;
            snapshot.snapshot_error_code = None;
            snapshot.snapshot_error_at_ms = None;
            snapshot.snapshot_error = None;
        }
    }

    fn record_snapshot_failure(&self, code: &str, error: String) {
        if let Ok(mut snapshot) = self.snapshot.lock() {
            snapshot.snapshot_failures += 1;
            snapshot.snapshot_error_code = Some(code.to_string());
            snapshot.snapshot_error_at_ms = Some(now_ms());
            snapshot.snapshot_error = Some(error);
        }
    }

    fn status(&self) -> CaptureStatus {
        let snapshot = self.snapshot.lock().ok();
        CaptureStatus {
            kovaak_process_present: self
                .kovaak_process_present
                .load(std::sync::atomic::Ordering::Acquire),
            capture_healthy: self
                .capture_running
                .load(std::sync::atomic::Ordering::Acquire)
                && snapshot
                    .as_ref()
                    .is_some_and(|value| value.snapshot_error_code.is_none()),
            buffered_points: self
                .buffered_points
                .load(std::sync::atomic::Ordering::Acquire),
            dropped_points: self
                .dropped_points
                .load(std::sync::atomic::Ordering::Acquire),
            expired_points: self
                .expired_points
                .load(std::sync::atomic::Ordering::Acquire),
            last_snapshot_at_ms: snapshot
                .as_ref()
                .and_then(|value| value.last_snapshot_at_ms),
            last_snapshot_points: snapshot
                .as_ref()
                .map(|value| value.last_snapshot_points)
                .unwrap_or(0),
            snapshot_failures: snapshot
                .as_ref()
                .map(|value| value.snapshot_failures)
                .unwrap_or(0),
            snapshot_error_code: snapshot
                .as_ref()
                .and_then(|value| value.snapshot_error_code.clone()),
            snapshot_error_at_ms: snapshot
                .as_ref()
                .and_then(|value| value.snapshot_error_at_ms),
            snapshot_error: snapshot.and_then(|value| value.snapshot_error.clone()),
        }
    }
}

struct Inner {
    enabled: bool,
    snapshot_path: PathBuf,
    diagnostics: Arc<CaptureDiagnostics>,
    #[cfg(windows)]
    backend: Option<WindowsBackend>,
}

pub struct RawInputState {
    inner: Mutex<Inner>,
}

impl RawInputState {
    pub fn new(snapshot_path: PathBuf) -> Self {
        Self {
            inner: Mutex::new(Inner {
                enabled: false,
                snapshot_path,
                diagnostics: Arc::new(CaptureDiagnostics::new()),
                #[cfg(windows)]
                backend: None,
            }),
        }
    }

    fn status_for(inner: &Inner) -> RawInputStatus {
        let capture = inner.diagnostics.status();
        RawInputStatus {
            supported: cfg!(windows),
            enabled: inner.enabled,
            kovaak_process_present: capture.kovaak_process_present,
            capture_healthy: capture.capture_healthy,
            buffered_points: capture.buffered_points,
            dropped_points: capture.dropped_points,
            expired_points: capture.expired_points,
            last_snapshot_at_ms: capture.last_snapshot_at_ms,
            last_snapshot_points: capture.last_snapshot_points,
            snapshot_failures: capture.snapshot_failures,
            snapshot_error_code: capture.snapshot_error_code,
            snapshot_error_at_ms: capture.snapshot_error_at_ms,
            snapshot_error: capture.snapshot_error,
        }
    }

    pub fn status(&self) -> RawInputStatus {
        let inner = self.inner.lock().expect("raw input state poisoned");
        Self::status_for(&inner)
    }

    pub fn set_enabled(&self, enabled: bool) -> Result<RawInputStatus, String> {
        let mut inner = self
            .inner
            .lock()
            .map_err(|_| "raw input state unavailable")?;
        if enabled && !cfg!(windows) {
            return Err("Raw Input is only supported on Windows".to_string());
        }
        if inner.enabled == enabled {
            return Ok(Self::status_for(&inner));
        }
        #[cfg(windows)]
        if enabled {
            inner.backend = Some(WindowsBackend::start(
                inner.snapshot_path.clone(),
                inner.diagnostics.clone(),
            )?);
        }
        #[cfg(windows)]
        if !enabled {
            if let Some(backend) = inner.backend.take() {
                backend.stop();
            }
        }
        inner.enabled = enabled;
        Ok(Self::status_for(&inner))
    }

    pub fn shutdown(&self) {
        let _ = self.set_enabled(false);
    }
}

impl Drop for RawInputState {
    fn drop(&mut self) {
        self.shutdown();
    }
}

#[cfg(not(windows))]
mod platform {
    // The public state above is the explicit unsupported fallback. Keeping the
    // native implementation behind cfg(windows) makes macOS/Linux builds
    // compile without pretending to capture input.
}

#[cfg(windows)]
enum CaptureMessage {
    Point(MousePoint),
    Flush,
}

struct SnapshotCadence {
    dirty: bool,
    next_flush: Option<Instant>,
}

impl SnapshotCadence {
    fn new() -> Self {
        Self {
            dirty: false,
            next_flush: None,
        }
    }

    fn mark_dirty(&mut self, now: Instant) {
        if !self.dirty {
            self.dirty = true;
            self.next_flush = Some(now + SNAPSHOT_MAX_DIRTY_INTERVAL);
        }
    }

    fn should_flush(&self, now: Instant, force: bool) -> bool {
        self.dirty && (force || self.next_flush.is_some_and(|deadline| now >= deadline))
    }

    fn record_attempt(&mut self, now: Instant, succeeded: bool) {
        if succeeded {
            self.dirty = false;
            self.next_flush = None;
        } else {
            self.next_flush = Some(now + SNAPSHOT_RETRY_INTERVAL);
        }
    }

    fn receive_timeout(&self, now: Instant) -> Duration {
        self.next_flush
            .map(|deadline| deadline.saturating_duration_since(now))
            .unwrap_or(SNAPSHOT_IDLE_INTERVAL)
            .min(SNAPSHOT_IDLE_INTERVAL)
    }
}

#[cfg(windows)]
struct WindowsBackend {
    stop: Arc<std::sync::atomic::AtomicBool>,
    capture_join: Option<std::thread::JoinHandle<()>>,
    snapshot_join: Option<std::thread::JoinHandle<()>>,
}

#[cfg(windows)]
impl WindowsBackend {
    fn start(snapshot_path: PathBuf, diagnostics: Arc<CaptureDiagnostics>) -> Result<Self, String> {
        use std::sync::atomic::AtomicBool;
        use std::sync::mpsc::sync_channel;

        let stop = Arc::new(AtomicBool::new(false));
        let thread_stop = stop.clone();
        let (points_tx, points_rx) = sync_channel(CAPTURE_QUEUE_CAPACITY);
        let snapshot_diagnostics = diagnostics.clone();
        let snapshot_join = std::thread::Builder::new()
            .name("aiming-cookie-raw-input-snapshot".to_string())
            .spawn(move || snapshot_worker(snapshot_path, points_rx, snapshot_diagnostics))
            .map_err(|error| format!("failed to start Raw Input snapshot worker: {error}"))?;
        let (ready_tx, ready_rx) = std::sync::mpsc::sync_channel(1);
        let capture_join = match std::thread::Builder::new()
            .name("aiming-cookie-raw-input".to_string())
            .spawn(move || unsafe {
                raw_input_thread(thread_stop, points_tx, diagnostics, ready_tx)
            }) {
            Ok(join) => join,
            Err(error) => {
                let _ = snapshot_join.join();
                return Err(format!("failed to start Raw Input thread: {error}"));
            }
        };
        match ready_rx.recv_timeout(Duration::from_secs(3)) {
            Ok(Ok(())) => Ok(Self {
                stop,
                capture_join: Some(capture_join),
                snapshot_join: Some(snapshot_join),
            }),
            Ok(Err(error)) => {
                let _ = capture_join.join();
                let _ = snapshot_join.join();
                Err(error)
            }
            Err(_) => {
                stop.store(true, std::sync::atomic::Ordering::Release);
                let _ = capture_join.join();
                let _ = snapshot_join.join();
                Err("Raw Input startup timed out".to_string())
            }
        }
    }

    fn stop(mut self) {
        use std::sync::atomic::Ordering;
        self.stop.store(true, Ordering::Release);
        if let Some(join) = self.capture_join.take() {
            let _ = join.join();
        }
        if let Some(join) = self.snapshot_join.take() {
            let _ = join.join();
        }
    }
}

#[cfg(windows)]
fn snapshot_worker(
    snapshot_path: PathBuf,
    receiver: std::sync::mpsc::Receiver<CaptureMessage>,
    diagnostics: Arc<CaptureDiagnostics>,
) {
    use std::sync::mpsc::RecvTimeoutError;

    let mut ring = match read_snapshot_file(&snapshot_path) {
        Ok(points) => {
            let mut ring = RingBuffer::new(Duration::from_secs(DEFAULT_BUFFER_MINUTES * 60));
            for point in points {
                if !ring.push(point) {
                    diagnostics.record_drop();
                }
            }
            ring
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            RingBuffer::new(Duration::from_secs(DEFAULT_BUFFER_MINUTES * 60))
        }
        Err(error) => {
            diagnostics.record_snapshot_failure(
                if error.kind() == io::ErrorKind::InvalidData {
                    "trace_snapshot_invalid"
                } else {
                    "trace_snapshot_failed"
                },
                format!("failed to read raw input snapshot: {error}"),
            );
            RingBuffer::new(Duration::from_secs(DEFAULT_BUFFER_MINUTES * 60))
        }
    };
    let expired = ring.prune_before(now_ms() - MAX_SNAPSHOT_SPAN_MS);
    if expired > 0 {
        diagnostics.record_expired(expired);
        diagnostics.record_buffered_points(ring.len());
        let _ = write_worker_snapshot(&snapshot_path, &ring, &diagnostics);
    }
    diagnostics.record_buffered_points(ring.len());

    let mut cadence = SnapshotCadence::new();
    loop {
        match receiver.recv_timeout(cadence.receive_timeout(Instant::now())) {
            Ok(CaptureMessage::Point(point)) => {
                let expired = ring.prune_before(point.timestamp_ms - MAX_SNAPSHOT_SPAN_MS);
                if expired > 0 {
                    diagnostics.record_expired(expired);
                }
                if ring.push(point) {
                    diagnostics.record_buffered_points(ring.len());
                    cadence.mark_dirty(Instant::now());
                } else {
                    diagnostics.record_drop();
                }
            }
            Ok(CaptureMessage::Flush) => {
                let now = Instant::now();
                let expired = ring.prune_before(now_ms() - MAX_SNAPSHOT_SPAN_MS);
                if expired > 0 {
                    diagnostics.record_expired(expired);
                    diagnostics.record_buffered_points(ring.len());
                }
                if expired > 0 || cadence.should_flush(now, true) {
                    let succeeded = write_worker_snapshot(&snapshot_path, &ring, &diagnostics);
                    cadence.record_attempt(now, succeeded);
                }
            }
            Err(RecvTimeoutError::Timeout) => {
                let now = Instant::now();
                let expired = ring.prune_before(now_ms() - MAX_SNAPSHOT_SPAN_MS);
                if expired > 0 {
                    diagnostics.record_expired(expired);
                    diagnostics.record_buffered_points(ring.len());
                }
                if expired > 0 || cadence.should_flush(now, false) {
                    let succeeded = write_worker_snapshot(&snapshot_path, &ring, &diagnostics);
                    cadence.record_attempt(now, succeeded);
                }
            }
            Err(RecvTimeoutError::Disconnected) => {
                let now = Instant::now();
                let expired = ring.prune_before(now_ms() - MAX_SNAPSHOT_SPAN_MS);
                if expired > 0 {
                    diagnostics.record_expired(expired);
                    diagnostics.record_buffered_points(ring.len());
                }
                if expired > 0 || cadence.should_flush(now, true) {
                    let succeeded = write_worker_snapshot(&snapshot_path, &ring, &diagnostics);
                    cadence.record_attempt(now, succeeded);
                }
                break;
            }
        }
        let now = Instant::now();
        if cadence.should_flush(now, false) {
            let succeeded = write_worker_snapshot(&snapshot_path, &ring, &diagnostics);
            cadence.record_attempt(now, succeeded);
        }
    }
}

#[cfg(windows)]
fn write_worker_snapshot(
    snapshot_path: &Path,
    ring: &RingBuffer,
    diagnostics: &CaptureDiagnostics,
) -> bool {
    let points = ring.snapshot();
    match write_snapshot_atomic(snapshot_path, &points) {
        Ok(()) => {
            diagnostics.record_snapshot_success(now_ms(), points.len());
            true
        }
        Err(error) => {
            diagnostics.record_snapshot_failure(
                "trace_snapshot_failed",
                format!("failed to write raw input snapshot: {error}"),
            );
            false
        }
    }
}

#[cfg(windows)]
fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as i64
}

#[cfg(not(windows))]
#[allow(dead_code)]
fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as i64
}

#[cfg(windows)]
unsafe fn raw_input_thread(
    stop: Arc<std::sync::atomic::AtomicBool>,
    points: std::sync::mpsc::SyncSender<CaptureMessage>,
    diagnostics: Arc<CaptureDiagnostics>,
    ready: std::sync::mpsc::SyncSender<Result<(), String>>,
) {
    use std::mem::{size_of, zeroed};
    use std::ptr::{null, null_mut};
    use std::sync::atomic::Ordering;
    use std::thread;
    use std::time::Instant;
    use winapi::shared::minwindef::{LRESULT, UINT, WPARAM};
    use winapi::shared::windef::HWND;
    use winapi::um::libloaderapi::GetModuleHandleW;
    use winapi::um::winuser::*;

    struct ThreadState {
        points: std::sync::mpsc::SyncSender<CaptureMessage>,
        diagnostics: Arc<CaptureDiagnostics>,
        process_running: bool,
        buttons: u32,
    }

    static STATE: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);

    unsafe extern "system" fn window_proc(
        hwnd: HWND,
        message: UINT,
        wparam: WPARAM,
        lparam: isize,
    ) -> LRESULT {
        if message == WM_INPUT {
            let state = STATE.load(Ordering::Acquire) as *mut ThreadState;
            if !state.is_null() && (*state).process_running {
                let state = &mut *state;
                capture_raw_mouse(
                    lparam,
                    &state.points,
                    &state.diagnostics,
                    &mut state.buttons,
                );
            }
        }
        DefWindowProcW(hwnd, message, wparam, lparam)
    }

    let class_name: Vec<u16> = "AimingCookieRawInput\0".encode_utf16().collect();
    let title: Vec<u16> = "Aiming Cookie Raw Input\0".encode_utf16().collect();
    let instance = GetModuleHandleW(null());
    if instance.is_null() {
        let _ = ready.send(Err("GetModuleHandleW failed".to_string()));
        return;
    }
    let class = WNDCLASSW {
        style: 0,
        lpfnWndProc: Some(window_proc),
        cbClsExtra: 0,
        cbWndExtra: 0,
        hInstance: instance,
        hIcon: null_mut(),
        hCursor: null_mut(),
        hbrBackground: null_mut(),
        lpszMenuName: null(),
        lpszClassName: class_name.as_ptr(),
    };
    if RegisterClassW(&class) == 0 {
        let _ = ready.send(Err("RegisterClassW failed".to_string()));
        return;
    }
    let hwnd = CreateWindowExW(
        0,
        class_name.as_ptr(),
        title.as_ptr(),
        0,
        0,
        0,
        0,
        0,
        HWND_MESSAGE,
        null_mut(),
        instance,
        null_mut(),
    );
    if hwnd.is_null() {
        UnregisterClassW(class_name.as_ptr(), instance);
        let _ = ready.send(Err("CreateWindowExW failed".to_string()));
        return;
    }

    let device = RAWINPUTDEVICE {
        usUsagePage: 0x01,
        usUsage: 0x02,
        dwFlags: RIDEV_INPUTSINK,
        hwndTarget: hwnd,
    };
    if RegisterRawInputDevices(&device, 1, size_of::<RAWINPUTDEVICE>() as UINT) == 0 {
        DestroyWindow(hwnd);
        UnregisterClassW(class_name.as_ptr(), instance);
        let _ = ready.send(Err("RegisterRawInputDevices failed".to_string()));
        return;
    }

    let mut thread_state = Box::new(ThreadState {
        points,
        diagnostics,
        process_running: false,
        buttons: 0,
    });
    STATE.store(
        (&mut *thread_state) as *mut ThreadState as usize,
        Ordering::Release,
    );
    thread_state.diagnostics.record_capture_started();
    let _ = ready.send(Ok(()));
    let mut message: MSG = zeroed();
    let mut last_process_check = Instant::now();
    while !stop.load(Ordering::Acquire) {
        while PeekMessageW(&mut message, null_mut(), 0, 0, PM_REMOVE) != 0 {
            if message.message == WM_QUIT {
                break;
            }
            TranslateMessage(&message);
            DispatchMessageW(&message);
        }
        if last_process_check.elapsed() >= Duration::from_millis(500) {
            let was_running = thread_state.process_running;
            thread_state.process_running = is_kovaak_process_running();
            thread_state
                .diagnostics
                .record_kovaak_process_present(thread_state.process_running);
            last_process_check = Instant::now();
            if !thread_state.process_running {
                thread_state.buttons = 0;
            }
            if was_running && !thread_state.process_running {
                let _ = thread_state.points.send(CaptureMessage::Flush);
            }
        }
        thread::sleep(std::time::Duration::from_millis(5));
    }

    let remove_device = RAWINPUTDEVICE {
        usUsagePage: 0x01,
        usUsage: 0x02,
        dwFlags: RIDEV_REMOVE,
        hwndTarget: null_mut(),
    };
    RegisterRawInputDevices(&remove_device, 1, size_of::<RAWINPUTDEVICE>() as UINT);
    STATE.store(0, Ordering::Release);
    thread_state.diagnostics.record_capture_stopped();
    drop(thread_state);
    DestroyWindow(hwnd);
    UnregisterClassW(class_name.as_ptr(), instance);
}

#[cfg(windows)]
unsafe fn capture_raw_mouse(
    lparam: isize,
    points: &std::sync::mpsc::SyncSender<CaptureMessage>,
    diagnostics: &CaptureDiagnostics,
    buttons: &mut u32,
) {
    use std::mem::size_of;
    use std::ptr::null_mut;
    use winapi::shared::minwindef::UINT;
    use winapi::um::winuser::{
        GetRawInputData, HRAWINPUT, RAWINPUTHEADER, RID_INPUT, RIM_TYPEMOUSE,
        RI_MOUSE_LEFT_BUTTON_DOWN, RI_MOUSE_LEFT_BUTTON_UP, RI_MOUSE_MIDDLE_BUTTON_DOWN,
        RI_MOUSE_MIDDLE_BUTTON_UP, RI_MOUSE_RIGHT_BUTTON_DOWN, RI_MOUSE_RIGHT_BUTTON_UP,
    };

    let handle = lparam as HRAWINPUT;
    let mut size: UINT = 0;
    if GetRawInputData(
        handle,
        RID_INPUT,
        null_mut(),
        &mut size,
        size_of::<RAWINPUTHEADER>() as UINT,
    ) == u32::MAX
        || size == 0
    {
        return;
    }
    let mut bytes = vec![0u8; size as usize];
    if GetRawInputData(
        handle,
        RID_INPUT,
        bytes.as_mut_ptr() as *mut _,
        &mut size,
        size_of::<RAWINPUTHEADER>() as UINT,
    ) == u32::MAX
    {
        return;
    }
    let header = &*(bytes.as_ptr() as *const RAWINPUTHEADER);
    if header.dwType != RIM_TYPEMOUSE {
        return;
    }
    let mouse = bytes.as_ptr().add(size_of::<RAWINPUTHEADER>());
    let flags = std::ptr::read_unaligned(mouse.add(4) as *const u16);
    if flags & RI_MOUSE_LEFT_BUTTON_DOWN != 0 {
        *buttons |= 1;
    }
    if flags & RI_MOUSE_LEFT_BUTTON_UP != 0 {
        *buttons &= !1;
    }
    if flags & RI_MOUSE_RIGHT_BUTTON_DOWN != 0 {
        *buttons |= 2;
    }
    if flags & RI_MOUSE_RIGHT_BUTTON_UP != 0 {
        *buttons &= !2;
    }
    if flags & RI_MOUSE_MIDDLE_BUTTON_DOWN != 0 {
        *buttons |= 4;
    }
    if flags & RI_MOUSE_MIDDLE_BUTTON_UP != 0 {
        *buttons &= !4;
    }
    let dx = std::ptr::read_unaligned(mouse.add(12) as *const i32);
    let dy = std::ptr::read_unaligned(mouse.add(16) as *const i32);
    match points.try_send(CaptureMessage::Point(MousePoint {
        timestamp_ms: now_ms(),
        dx,
        dy,
        buttons: *buttons,
    })) {
        Ok(()) => {}
        Err(std::sync::mpsc::TrySendError::Full(_))
        | Err(std::sync::mpsc::TrySendError::Disconnected(_)) => diagnostics.record_drop(),
    }
}

#[cfg(windows)]
fn is_kovaak_process_running() -> bool {
    use std::mem::{size_of, zeroed};
    use winapi::shared::minwindef::MAX_PATH;
    use winapi::um::handleapi::{CloseHandle, INVALID_HANDLE_VALUE};
    use winapi::um::tlhelp32::{
        CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
        TH32CS_SNAPPROCESS,
    };

    unsafe {
        let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if snapshot == INVALID_HANDLE_VALUE {
            return false;
        }
        let mut entry: PROCESSENTRY32W = zeroed();
        entry.dwSize = size_of::<PROCESSENTRY32W>() as u32;
        let mut found = false;
        if Process32FirstW(snapshot, &mut entry) != 0 {
            loop {
                let len = entry
                    .szExeFile
                    .iter()
                    .position(|value| *value == 0)
                    .unwrap_or(MAX_PATH as usize);
                let name = String::from_utf16_lossy(&entry.szExeFile[..len]);
                if name.eq_ignore_ascii_case("FPSAimTrainer-Win64-Shipping.exe")
                    || name.eq_ignore_ascii_case("FPSAimTrainer.exe")
                {
                    found = true;
                    break;
                }
                if Process32NextW(snapshot, &mut entry) == 0 {
                    break;
                }
            }
        }
        CloseHandle(snapshot);
        found
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn raw_input_status_defaults_to_disabled_without_process_or_healthy_runtime() {
        let state = RawInputState::new(PathBuf::from("raw-input-status-defaults.bin"));

        let status = state.status();

        assert_eq!(status.supported, cfg!(windows));
        assert!(!status.enabled);
        assert!(!status.kovaak_process_present);
        assert!(!status.capture_healthy);
        assert_eq!(status.buffered_points, 0);
        assert_eq!(status.snapshot_failures, 0);
        assert_eq!(status.snapshot_error_code, None);
    }

    #[test]
    fn capture_diagnostics_distinguish_process_presence_and_runtime_health() {
        let diagnostics = CaptureDiagnostics::new();

        diagnostics.record_capture_started();
        diagnostics.record_kovaak_process_present(true);
        let running = diagnostics.status();
        assert!(running.kovaak_process_present);
        assert!(running.capture_healthy);

        diagnostics.record_snapshot_failure("trace_snapshot_failed", "disk full".to_string());
        let failed = diagnostics.status();
        assert!(failed.kovaak_process_present);
        assert!(!failed.capture_healthy);
        assert_eq!(failed.snapshot_failures, 1);
        assert_eq!(
            failed.snapshot_error_code.as_deref(),
            Some("trace_snapshot_failed")
        );

        diagnostics.record_snapshot_success(123, 7);
        let recovered = diagnostics.status();
        assert!(recovered.kovaak_process_present);
        assert!(recovered.capture_healthy);
        assert_eq!(recovered.last_snapshot_at_ms, Some(123));
        assert_eq!(recovered.last_snapshot_points, 7);
        assert_eq!(recovered.snapshot_error_code, None);

        diagnostics.record_capture_stopped();
        let stopped = diagnostics.status();
        assert!(!stopped.kovaak_process_present);
        assert!(!stopped.capture_healthy);
    }

    #[test]
    fn ring_buffer_prunes_old_points_and_can_clear() {
        let mut ring = RingBuffer::new(Duration::from_millis(100));
        ring.push(MousePoint {
            timestamp_ms: 1,
            dx: 1,
            dy: 2,
            buttons: 0,
        });
        ring.push(MousePoint {
            timestamp_ms: 50,
            dx: 3,
            dy: 4,
            buttons: 1,
        });
        ring.push(MousePoint {
            timestamp_ms: 150,
            dx: 5,
            dy: 6,
            buttons: 0,
        });
        assert_eq!(ring.snapshot().len(), 2);
        ring.clear();
        assert_eq!(ring.len(), 0);
    }

    #[test]
    fn ring_buffer_expires_points_without_new_input() {
        let mut ring = RingBuffer::new(Duration::from_secs(10));
        ring.push(MousePoint {
            timestamp_ms: 1_000,
            dx: 1,
            dy: 1,
            buttons: 0,
        });
        ring.push(MousePoint {
            timestamp_ms: 2_000,
            dx: 1,
            dy: 1,
            buttons: 0,
        });

        assert_eq!(ring.prune_before(1_500), 1);
        assert_eq!(ring.snapshot()[0].timestamp_ms, 2_000);
    }

    #[test]
    fn snapshot_codec_round_trips_points() {
        let points = vec![
            MousePoint {
                timestamp_ms: 10,
                dx: -2,
                dy: 4,
                buttons: 1,
            },
            MousePoint {
                timestamp_ms: 20,
                dx: 8,
                dy: -9,
                buttons: 0,
            },
        ];
        assert_eq!(decode_snapshot(&encode_snapshot(&points)).unwrap(), points);
    }

    #[test]
    fn snapshot_codec_matches_python_v1_golden_fixture() {
        let fixture = include_bytes!("../../../tests/fixtures/acri-v1-golden.bin");
        let points = vec![
            MousePoint {
                timestamp_ms: 1_700_000_000_000,
                dx: -2,
                dy: 4,
                buttons: 1,
            },
            MousePoint {
                timestamp_ms: 1_700_000_000_016,
                dx: 8,
                dy: -9,
                buttons: 0,
            },
        ];

        assert_eq!(decode_snapshot(fixture).unwrap(), points);
        assert_eq!(encode_snapshot(&points), fixture.as_slice());
    }

    #[test]
    fn snapshot_codec_rejects_truncation_and_unknown_version() {
        let points = vec![MousePoint {
            timestamp_ms: 1,
            dx: 0,
            dy: 0,
            buttons: 0,
        }];
        let mut bytes = encode_snapshot(&points);
        assert!(decode_snapshot(&bytes[..bytes.len() - 1]).is_err());
        bytes[4] = 99;
        assert!(decode_snapshot(&bytes).is_err());
    }

    #[test]
    fn snapshot_codec_rejects_non_monotonic_timestamps() {
        let points = [
            MousePoint {
                timestamp_ms: 20,
                dx: 1,
                dy: 2,
                buttons: 0,
            },
            MousePoint {
                timestamp_ms: 19,
                dx: 3,
                dy: 4,
                buttons: 1,
            },
        ];

        let error = decode_snapshot(&encode_snapshot(&points)).unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
        assert!(error.to_string().contains("monotonic"));
    }

    #[test]
    fn snapshot_codec_rejects_reserved_header_and_unsupported_buttons() {
        let mut reserved = encode_snapshot(&[MousePoint {
            timestamp_ms: 1,
            dx: 0,
            dy: 0,
            buttons: 0,
        }]);
        reserved[5] = 1;
        assert!(decode_snapshot(&reserved).is_err());

        let unsupported_buttons = encode_snapshot(&[MousePoint {
            timestamp_ms: 1,
            dx: 0,
            dy: 0,
            buttons: 8,
        }]);
        assert!(decode_snapshot(&unsupported_buttons).is_err());
    }

    #[test]
    fn snapshot_codec_rejects_point_count_over_resource_limit_before_allocating() {
        let mut bytes = Vec::new();
        bytes.extend_from_slice(SNAPSHOT_MAGIC);
        bytes.push(SNAPSHOT_VERSION);
        bytes.extend_from_slice(&[0, 0, 0]);
        bytes.extend_from_slice(&((MAX_SNAPSHOT_POINTS as u32) + 1).to_le_bytes());

        let error = decode_snapshot(&bytes).unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
        assert!(error.to_string().contains("too many"));
    }

    #[test]
    fn capture_diagnostics_expose_drop_and_snapshot_failure() {
        let diagnostics = CaptureDiagnostics::new();
        diagnostics.record_drop();
        diagnostics.record_expired(2);
        diagnostics.record_snapshot_failure("trace_snapshot_failed", "disk full".to_string());

        let failed = diagnostics.status();
        assert_eq!(failed.dropped_points, 1);
        assert_eq!(failed.expired_points, 2);
        assert_eq!(failed.snapshot_failures, 1);
        assert_eq!(
            failed.snapshot_error_code.as_deref(),
            Some("trace_snapshot_failed")
        );
        assert!(failed.snapshot_error_at_ms.is_some());
        assert_eq!(failed.snapshot_error.as_deref(), Some("disk full"));
        assert_eq!(failed.last_snapshot_at_ms, None);

        diagnostics.record_snapshot_success(42, 3);
        let recovered = diagnostics.status();
        assert_eq!(recovered.buffered_points, 3);
        assert_eq!(recovered.last_snapshot_points, 3);
        assert_eq!(recovered.last_snapshot_at_ms, Some(42));
        assert_eq!(recovered.snapshot_error, None);
        assert_eq!(recovered.snapshot_error_code, None);
    }

    #[test]
    fn snapshot_file_reader_rejects_oversized_file_before_allocating_payload() {
        let path = std::env::temp_dir().join(format!(
            "aiming-cookie-oversized-{}-{}.bin",
            std::process::id(),
            now_ms()
        ));
        let file = std::fs::File::create(&path).unwrap();
        file.set_len((MAX_SNAPSHOT_BYTES + 1) as u64).unwrap();
        drop(file);

        let error = read_snapshot_file(&path).unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
        assert!(error.to_string().contains("byte limit"));
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn snapshot_cadence_flushes_on_process_exit_without_per_event_writes() {
        let start = Instant::now();
        let mut cadence = SnapshotCadence::new();
        cadence.mark_dirty(start);

        assert!(!cadence.should_flush(start + Duration::from_secs(1), false));
        assert!(!cadence.should_flush(start + Duration::from_secs(29), false));
        assert!(cadence.should_flush(start + Duration::from_secs(30), false));
        assert!(cadence.should_flush(start + Duration::from_secs(1), true));

        cadence.record_attempt(start + Duration::from_secs(1), true);
        assert!(!cadence.should_flush(start + Duration::from_secs(60), true));
    }

    #[cfg(windows)]
    #[test]
    fn snapshot_writer_replaces_existing_file() {
        let path = std::env::temp_dir().join(format!(
            "aiming-cookie-raw-input-{}-{}.bin",
            std::process::id(),
            now_ms()
        ));
        let first = [MousePoint {
            timestamp_ms: 1,
            dx: 1,
            dy: 1,
            buttons: 0,
        }];
        let second = [MousePoint {
            timestamp_ms: 2,
            dx: 2,
            dy: 2,
            buttons: 1,
        }];

        write_snapshot_atomic(&path, &first).unwrap();
        write_snapshot_atomic(&path, &second).unwrap();

        assert_eq!(decode_snapshot(&fs::read(&path).unwrap()).unwrap(), second);
        let _ = fs::remove_file(path);
    }
}

#[cfg(all(test, not(windows)))]
mod non_windows_tests {
    use super::*;

    #[test]
    fn disabled_toggle_is_safe_and_enable_is_explicitly_unsupported() {
        let state = RawInputState::new(PathBuf::from("/tmp/aiming-cookie-raw-input.bin"));
        assert!(!state.status().enabled);
        let serialized = serde_json::to_value(state.status()).unwrap();
        assert!(serialized.get("snapshotPath").is_none());
        assert!(!state.set_enabled(false).unwrap().enabled);
        assert!(state.set_enabled(true).is_err());
    }
}
