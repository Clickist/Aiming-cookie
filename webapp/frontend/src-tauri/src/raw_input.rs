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
use std::sync::OnceLock;
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
const SNAPSHOT_BARRIER_TIMEOUT: Duration = Duration::from_secs(5);
#[cfg(windows)]
const CAPTURE_QUEUE_CAPACITY: usize = 16_384;
#[cfg(windows)]
const CONTROL_QUEUE_CAPACITY: usize = 1;
const SNAPSHOT_IDLE_INTERVAL: Duration = Duration::from_secs(1);
#[cfg(windows)]
const RAW_INPUT_WM_QUIT: u32 = 0x0012;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct MousePoint {
    pub timestamp_ms: i64,
    pub dx: i32,
    pub dy: i32,
    pub buttons: u32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SnapshotBarrierReceipt {
    #[serde(rename = "coveredThroughEpochMs")]
    pub covered_through_ms: i64,
    #[serde(rename = "snapshotAtEpochMs")]
    pub snapshot_at_ms: i64,
    pub point_count: usize,
    pub clock_source: &'static str,
    pub timebase_version: &'static str,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CaptureClockAnchor {
    pub utc_epoch_ms: i64,
    pub monotonic_elapsed_ns: u128,
    pub clock_source: &'static str,
    pub timebase_version: &'static str,
}

fn clock_sample_to_utc_ms(anchor: &CaptureClockAnchor, sample_monotonic_ns: u128) -> i64 {
    let delta_ns = if sample_monotonic_ns >= anchor.monotonic_elapsed_ns {
        (sample_monotonic_ns - anchor.monotonic_elapsed_ns) as i128
    } else {
        -((anchor.monotonic_elapsed_ns - sample_monotonic_ns) as i128)
    };
    let delta_ms = delta_ns / 1_000_000;
    (anchor.utc_epoch_ms as i128 + delta_ms).clamp(i64::MIN as i128, i64::MAX as i128) as i64
}

#[cfg(windows)]
static QPC_FREQUENCY: OnceLock<i64> = OnceLock::new();

#[cfg(windows)]
fn qpc_ticks() -> Option<i64> {
    use winapi::um::profileapi::QueryPerformanceCounter;
    use winapi::um::winnt::LARGE_INTEGER;
    let mut value: LARGE_INTEGER = unsafe { std::mem::zeroed() };
    if unsafe { QueryPerformanceCounter(&mut value) } == 0 {
        None
    } else {
        Some(unsafe { *value.QuadPart() })
    }
}

#[cfg(windows)]
fn qpc_frequency() -> Option<i64> {
    use winapi::um::profileapi::QueryPerformanceFrequency;
    use winapi::um::winnt::LARGE_INTEGER;
    let value = QPC_FREQUENCY.get_or_init(|| {
        let mut frequency: LARGE_INTEGER = unsafe { std::mem::zeroed() };
        if unsafe { QueryPerformanceFrequency(&mut frequency) } == 0 {
            0
        } else {
            unsafe { *frequency.QuadPart() }
        }
    });
    (*value > 0).then_some(*value)
}

pub fn capture_clock_anchor() -> CaptureClockAnchor {
    #[cfg(windows)]
    if let (Some(ticks), Some(frequency)) = (qpc_ticks(), qpc_frequency()) {
        return CaptureClockAnchor {
            utc_epoch_ms: now_ms(),
            monotonic_elapsed_ns: (ticks as u128).saturating_mul(1_000_000_000) / frequency as u128,
            clock_source: "utc_epoch_ms+qpc",
            timebase_version: "time_alignment.v2",
        };
    }

    static MONOTONIC_ORIGIN: OnceLock<Instant> = OnceLock::new();
    let origin = MONOTONIC_ORIGIN.get_or_init(Instant::now);
    CaptureClockAnchor {
        utc_epoch_ms: now_ms(),
        monotonic_elapsed_ns: origin.elapsed().as_nanos(),
        clock_source: "utc_epoch_ms+monotonic_fallback",
        timebase_version: "time_alignment.v2",
    }
}

fn capture_clock_now_ms(anchor: &CaptureClockAnchor) -> i64 {
    #[cfg(windows)]
    if anchor.clock_source == "utc_epoch_ms+qpc" {
        if let (Some(ticks), Some(frequency)) = (qpc_ticks(), qpc_frequency()) {
            let sample_ns = (ticks as u128).saturating_mul(1_000_000_000) / frequency as u128;
            return clock_sample_to_utc_ms(anchor, sample_ns);
        }
    }
    now_ms()
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
    pub timebase_version: &'static str,
    pub clock_source: &'static str,
    pub clock_anchor_utc_ms: i64,
    pub clock_anchor_monotonic_ns: u128,
    pub capture_clock: CaptureClockAnchor,
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
    clock_anchor: CaptureClockAnchor,
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
            clock_anchor: capture_clock_anchor(),
        }
    }

    fn record_capture_started(&self) {
        self.capture_running
            .store(true, std::sync::atomic::Ordering::Release);
    }

    fn capture_timestamp_ms(&self) -> i64 {
        capture_clock_now_ms(&self.clock_anchor)
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

    #[cfg(windows)]
    fn record_runtime_failure(&self, failure: RawInputFailure) {
        self.record_snapshot_failure(failure.code(), failure.code().to_string());
    }

    #[cfg(windows)]
    fn clear_runtime_failure(&self) {
        if let Ok(mut snapshot) = self.snapshot.lock() {
            if matches!(
                snapshot.snapshot_error_code.as_deref(),
                Some("kovaak_process_probe_failed")
                    | Some("raw_input_data_failed")
                    | Some("raw_input_registration_failed")
            ) {
                snapshot.snapshot_error_code = None;
                snapshot.snapshot_error_at_ms = None;
                snapshot.snapshot_error = None;
            }
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
            timebase_version: inner.diagnostics.clock_anchor.timebase_version,
            clock_source: inner.diagnostics.clock_anchor.clock_source,
            clock_anchor_utc_ms: inner.diagnostics.clock_anchor.utc_epoch_ms,
            clock_anchor_monotonic_ns: inner.diagnostics.clock_anchor.monotonic_elapsed_ns,
            capture_clock: inner.diagnostics.clock_anchor,
        }
    }

    pub fn status(&self) -> RawInputStatus {
        let inner = self.inner.lock().expect("raw input state poisoned");
        Self::status_for(&inner)
    }

    pub fn flush_snapshot_barrier(&self) -> Result<SnapshotBarrierReceipt, String> {
        let inner = self
            .inner
            .lock()
            .map_err(|_| "raw_snapshot_unavailable".to_string())?;
        if !inner.enabled {
            return Err("raw_snapshot_unavailable".to_string());
        }
        #[cfg(windows)]
        {
            inner
                .backend
                .as_ref()
                .ok_or_else(|| "raw_snapshot_unavailable".to_string())?
                .flush_snapshot_barrier()
        }
        #[cfg(not(windows))]
        {
            Err("raw_snapshot_unavailable".to_string())
        }
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
            // Establish the sidecar anchor at the beginning of each capture session.
            inner.diagnostics = Arc::new(CaptureDiagnostics::new());
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
    Barrier {
        covered_through_ms: i64,
        ack: std::sync::mpsc::SyncSender<Result<SnapshotBarrierReceipt, String>>,
    },
}

#[cfg(windows)]
enum RawControlRequest {
    FlushSnapshot {
        ack: std::sync::mpsc::SyncSender<Result<SnapshotBarrierReceipt, String>>,
    },
}

#[cfg(windows)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum RawInputFailure {
    ProcessProbe,
    DataRead,
    Registration,
}

#[cfg(windows)]
impl RawInputFailure {
    fn code(self) -> &'static str {
        match self {
            Self::ProcessProbe => "kovaak_process_probe_failed",
            Self::DataRead => "raw_input_data_failed",
            Self::Registration => "raw_input_registration_failed",
        }
    }
}

#[cfg(windows)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum SnapshotBarrierError {
    Busy,
    Disconnected,
}

#[cfg(windows)]
fn try_send_snapshot_barrier(
    sender: &std::sync::mpsc::SyncSender<CaptureMessage>,
    covered_through_ms: i64,
    ack: std::sync::mpsc::SyncSender<Result<SnapshotBarrierReceipt, String>>,
) -> Result<(), SnapshotBarrierError> {
    let message = CaptureMessage::Barrier {
        covered_through_ms,
        ack,
    };
    match sender.try_send(message) {
        Ok(()) => Ok(()),
        Err(std::sync::mpsc::TrySendError::Full(CaptureMessage::Barrier { ack, .. })) => {
            let _ = ack.send(Err("raw_snapshot_busy".to_string()));
            Err(SnapshotBarrierError::Busy)
        }
        Err(std::sync::mpsc::TrySendError::Disconnected(CaptureMessage::Barrier {
            ack, ..
        })) => {
            let _ = ack.send(Err("raw_snapshot_unavailable".to_string()));
            Err(SnapshotBarrierError::Disconnected)
        }
        Err(_) => unreachable!("try_send returns the original barrier message"),
    }
}

#[cfg(windows)]
fn enqueue_snapshot_barrier(
    sender: &std::sync::mpsc::SyncSender<CaptureMessage>,
    covered_through_ms: i64,
) -> Result<std::sync::mpsc::Receiver<Result<SnapshotBarrierReceipt, String>>, SnapshotBarrierError>
{
    let (ack, receiver) = std::sync::mpsc::sync_channel(1);
    try_send_snapshot_barrier(sender, covered_through_ms, ack)?;
    Ok(receiver)
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
    control: std::sync::mpsc::SyncSender<RawControlRequest>,
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
        let (control_tx, control_rx) = sync_channel(CONTROL_QUEUE_CAPACITY);
        let snapshot_diagnostics = diagnostics.clone();
        let snapshot_join = std::thread::Builder::new()
            .name("aiming-cookie-raw-input-snapshot".to_string())
            .spawn(move || snapshot_worker(snapshot_path, points_rx, snapshot_diagnostics))
            .map_err(|error| format!("failed to start Raw Input snapshot worker: {error}"))?;
        let (ready_tx, ready_rx) = std::sync::mpsc::sync_channel(1);
        let capture_join = match std::thread::Builder::new()
            .name("aiming-cookie-raw-input".to_string())
            .spawn(move || unsafe {
                raw_input_thread(thread_stop, points_tx, control_rx, diagnostics, ready_tx)
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
                control: control_tx,
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

    fn flush_snapshot_barrier(&self) -> Result<SnapshotBarrierReceipt, String> {
        let (ack, receiver) = std::sync::mpsc::sync_channel(1);
        match self
            .control
            .try_send(RawControlRequest::FlushSnapshot { ack })
        {
            Ok(()) => {}
            Err(std::sync::mpsc::TrySendError::Full(_)) => {
                return Err("raw_snapshot_busy".to_string());
            }
            Err(std::sync::mpsc::TrySendError::Disconnected(_)) => {
                return Err("raw_snapshot_unavailable".to_string());
            }
        }
        match receiver.recv_timeout(SNAPSHOT_BARRIER_TIMEOUT) {
            Ok(result) => result,
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {
                Err("raw_snapshot_timed_out".to_string())
            }
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
                Err("raw_snapshot_unavailable".to_string())
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
                    let succeeded =
                        write_worker_snapshot(&snapshot_path, &ring, &diagnostics).is_ok();
                    cadence.record_attempt(now, succeeded);
                }
            }
            Ok(CaptureMessage::Barrier {
                covered_through_ms,
                ack,
            }) => {
                let now = Instant::now();
                let result =
                    write_worker_snapshot(&snapshot_path, &ring, &diagnostics).map(|snapshot| {
                        SnapshotBarrierReceipt {
                            covered_through_ms,
                            snapshot_at_ms: snapshot.snapshot_at_ms,
                            point_count: snapshot.point_count,
                            clock_source: diagnostics.clock_anchor.clock_source,
                            timebase_version: diagnostics.clock_anchor.timebase_version,
                        }
                    });
                let succeeded = result.is_ok();
                let _ = ack.send(result);
                cadence.record_attempt(now, succeeded);
            }
            Err(RecvTimeoutError::Timeout) => {
                let now = Instant::now();
                let expired = ring.prune_before(now_ms() - MAX_SNAPSHOT_SPAN_MS);
                if expired > 0 {
                    diagnostics.record_expired(expired);
                    diagnostics.record_buffered_points(ring.len());
                }
                if expired > 0 || cadence.should_flush(now, false) {
                    let succeeded =
                        write_worker_snapshot(&snapshot_path, &ring, &diagnostics).is_ok();
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
                    let succeeded =
                        write_worker_snapshot(&snapshot_path, &ring, &diagnostics).is_ok();
                    cadence.record_attempt(now, succeeded);
                }
                break;
            }
        }
        let now = Instant::now();
        if cadence.should_flush(now, false) {
            let succeeded = write_worker_snapshot(&snapshot_path, &ring, &diagnostics).is_ok();
            cadence.record_attempt(now, succeeded);
        }
    }
}

struct SnapshotWriteReceipt {
    snapshot_at_ms: i64,
    point_count: usize,
}

#[cfg(windows)]
fn write_worker_snapshot(
    snapshot_path: &Path,
    ring: &RingBuffer,
    diagnostics: &CaptureDiagnostics,
) -> Result<SnapshotWriteReceipt, String> {
    let points = ring.snapshot();
    match write_snapshot_atomic(snapshot_path, &points) {
        Ok(()) => {
            let snapshot_at_ms = diagnostics.capture_timestamp_ms();
            diagnostics.record_snapshot_success(snapshot_at_ms, points.len());
            Ok(SnapshotWriteReceipt {
                snapshot_at_ms,
                point_count: points.len(),
            })
        }
        Err(error) => {
            diagnostics.record_snapshot_failure(
                "trace_snapshot_failed",
                format!("failed to write raw input snapshot: {error}"),
            );
            Err("raw_snapshot_failed".to_string())
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
    control: std::sync::mpsc::Receiver<RawControlRequest>,
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
        process_probe_failed: bool,
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
                if let Err(error) = capture_raw_mouse(
                    lparam,
                    &state.points,
                    &state.diagnostics,
                    &mut state.buttons,
                ) {
                    state.diagnostics.record_runtime_failure(error);
                }
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
        diagnostics.record_runtime_failure(RawInputFailure::Registration);
        let _ = ready.send(Err(RawInputFailure::Registration.code().to_string()));
        return;
    }

    let mut thread_state = Box::new(ThreadState {
        points,
        diagnostics,
        process_running: false,
        process_probe_failed: false,
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
        let pending_barrier = match control.try_recv() {
            Ok(RawControlRequest::FlushSnapshot { ack }) => {
                Some((thread_state.diagnostics.capture_timestamp_ms(), ack))
            }
            Err(std::sync::mpsc::TryRecvError::Empty)
            | Err(std::sync::mpsc::TryRecvError::Disconnected) => None,
        };
        while PeekMessageW(&mut message, null_mut(), 0, 0, PM_REMOVE) != 0 {
            if should_stop_for_message(message.message) {
                stop.store(true, Ordering::Release);
                break;
            }
            TranslateMessage(&message);
            DispatchMessageW(&message);
        }
        if let Some((covered_through_ms, ack)) = pending_barrier {
            let _ = try_send_snapshot_barrier(&thread_state.points, covered_through_ms, ack);
        }
        if last_process_check.elapsed() >= Duration::from_millis(500) {
            let was_running = thread_state.process_running;
            let process_probe = is_kovaak_process_running();
            last_process_check = Instant::now();
            match process_probe {
                Ok(process_running) => {
                    thread_state.process_running = process_running;
                    thread_state.process_probe_failed = false;
                    thread_state.diagnostics.clear_runtime_failure();
                    thread_state
                        .diagnostics
                        .record_kovaak_process_present(process_running);
                    if !process_running {
                        thread_state.buttons = 0;
                    }
                    if was_running && !process_running {
                        let _ = thread_state.points.send(CaptureMessage::Flush);
                    }
                }
                Err(error) => {
                    thread_state.process_running = false;
                    thread_state
                        .diagnostics
                        .record_kovaak_process_present(false);
                    if !thread_state.process_probe_failed {
                        thread_state.diagnostics.record_runtime_failure(error);
                        thread_state.process_probe_failed = true;
                    }
                }
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
) -> Result<(), RawInputFailure> {
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
    {
        return Err(RawInputFailure::DataRead);
    }
    if size == 0 {
        return Ok(());
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
        return Err(RawInputFailure::DataRead);
    }
    diagnostics.clear_runtime_failure();
    let header = &*(bytes.as_ptr() as *const RAWINPUTHEADER);
    if header.dwType != RIM_TYPEMOUSE {
        return Ok(());
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
        timestamp_ms: diagnostics.capture_timestamp_ms(),
        dx,
        dy,
        buttons: *buttons,
    })) {
        Ok(()) => {}
        Err(std::sync::mpsc::TrySendError::Full(_))
        | Err(std::sync::mpsc::TrySendError::Disconnected(_)) => diagnostics.record_drop(),
    }
    Ok(())
}

#[cfg(windows)]
fn should_stop_for_message(message: u32) -> bool {
    message == RAW_INPUT_WM_QUIT
}

#[cfg(windows)]
fn is_kovaak_process_running() -> Result<bool, RawInputFailure> {
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
            return Err(RawInputFailure::ProcessProbe);
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
                    .unwrap_or(MAX_PATH);
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
        Ok(found)
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

    #[cfg(windows)]
    #[test]
    fn win32_failures_are_typed_without_becoming_kovaak_absence() {
        assert_eq!(
            RawInputFailure::ProcessProbe.code(),
            "kovaak_process_probe_failed"
        );
        assert_eq!(RawInputFailure::DataRead.code(), "raw_input_data_failed");
        assert_eq!(
            RawInputFailure::Registration.code(),
            "raw_input_registration_failed"
        );

        let diagnostics = CaptureDiagnostics::new();
        diagnostics.record_capture_started();
        diagnostics.record_kovaak_process_present(true);
        diagnostics.record_runtime_failure(RawInputFailure::ProcessProbe);
        let failed = diagnostics.status();
        assert!(failed.kovaak_process_present);
        assert!(!failed.capture_healthy);
        assert_eq!(
            failed.snapshot_error_code.as_deref(),
            Some("kovaak_process_probe_failed")
        );

        diagnostics.clear_runtime_failure();
        assert!(diagnostics.status().capture_healthy);
    }

    #[cfg(windows)]
    #[test]
    fn wm_quit_stops_raw_thread_and_allows_registration_cleanup() {
        assert!(should_stop_for_message(RAW_INPUT_WM_QUIT));
        assert!(!should_stop_for_message(0));
    }

    #[test]
    fn capture_clock_mapping_preserves_epoch_delta_without_changing_acri_shape() {
        let anchor = CaptureClockAnchor {
            utc_epoch_ms: 10_000,
            monotonic_elapsed_ns: 5_000_000_000,
            clock_source: "test",
            timebase_version: "time_alignment.v2",
        };
        assert_eq!(clock_sample_to_utc_ms(&anchor, 5_125_000_000), 10_125);
        assert_eq!(clock_sample_to_utc_ms(&anchor, 4_875_000_000), 9_875);
    }

    #[test]
    fn capture_clock_mapping_clamps_epoch_overflow() {
        let anchor = CaptureClockAnchor {
            utc_epoch_ms: i64::MAX,
            monotonic_elapsed_ns: 0,
            clock_source: "test",
            timebase_version: "time_alignment.v2",
        };
        assert_eq!(clock_sample_to_utc_ms(&anchor, u128::MAX), i64::MAX);
    }

    #[test]
    fn raw_status_exposes_versioned_capture_clock_provenance() {
        let state = RawInputState::new(PathBuf::from("raw-input-clock-status.bin"));
        let status = state.status();
        assert_eq!(status.timebase_version, "time_alignment.v2");
        assert!(status.clock_source.starts_with("utc_epoch_ms+"));
        assert!(status.clock_anchor_utc_ms > 0);
        assert_eq!(
            status.capture_clock.utc_epoch_ms,
            status.clock_anchor_utc_ms
        );
        assert_eq!(
            status.capture_clock.monotonic_elapsed_ns,
            status.clock_anchor_monotonic_ns
        );
    }

    #[test]
    fn capture_clock_serializes_as_explicit_sidecar_metadata() {
        let anchor = CaptureClockAnchor {
            utc_epoch_ms: 10_000,
            monotonic_elapsed_ns: 5_000_000_000,
            clock_source: "utc_epoch_ms+qpc",
            timebase_version: "time_alignment.v2",
        };
        let serialized = serde_json::to_value(anchor).unwrap();
        assert_eq!(serialized["utcEpochMs"], 10_000);
        assert_eq!(serialized["monotonicElapsedNs"], 5_000_000_000u64);
        assert_eq!(serialized["clockSource"], "utc_epoch_ms+qpc");
        assert_eq!(serialized["timebaseVersion"], "time_alignment.v2");
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
    fn snapshot_worker_barrier_publishes_a_clean_ring_before_acknowledging_coverage() {
        use std::sync::mpsc::sync_channel;
        use std::thread;

        let path = std::env::temp_dir().join(format!(
            "aiming-cookie-raw-barrier-clean-{}-{}.bin",
            std::process::id(),
            now_ms()
        ));
        let diagnostics = Arc::new(CaptureDiagnostics::new());
        let (sender, receiver) = sync_channel(2);
        let worker_diagnostics = Arc::clone(&diagnostics);
        let worker_path = path.clone();
        let worker = thread::spawn(move || {
            snapshot_worker(worker_path, receiver, worker_diagnostics);
        });
        let (ack_sender, ack_receiver) = sync_channel(1);
        let covered_through_ms = now_ms() - 1;

        sender
            .send(CaptureMessage::Barrier {
                covered_through_ms,
                ack: ack_sender,
            })
            .expect("queue barrier");
        let receipt = ack_receiver
            .recv_timeout(Duration::from_secs(2))
            .expect("barrier ack after atomic snapshot publish")
            .expect("barrier snapshot succeeds");

        assert_eq!(receipt.covered_through_ms, covered_through_ms);
        assert!(receipt.snapshot_at_ms >= covered_through_ms);
        assert_eq!(receipt.point_count, 0);
        assert!(path.is_file());
        assert!(decode_snapshot(&fs::read(&path).unwrap())
            .unwrap()
            .is_empty());

        drop(sender);
        worker.join().expect("snapshot worker exits");
        let _ = fs::remove_file(path);
    }

    #[cfg(windows)]
    #[test]
    fn snapshot_worker_barrier_observes_preceding_points_in_channel_fifo_order() {
        use std::sync::mpsc::sync_channel;
        use std::thread;

        let path = std::env::temp_dir().join(format!(
            "aiming-cookie-raw-barrier-fifo-{}-{}.bin",
            std::process::id(),
            now_ms()
        ));
        let diagnostics = Arc::new(CaptureDiagnostics::new());
        let (sender, receiver) = sync_channel(3);
        let worker_diagnostics = Arc::clone(&diagnostics);
        let worker_path = path.clone();
        let worker = thread::spawn(move || {
            snapshot_worker(worker_path, receiver, worker_diagnostics);
        });
        let covered_through_ms = now_ms() - 1;
        let point = MousePoint {
            timestamp_ms: covered_through_ms - 1,
            dx: 7,
            dy: -3,
            buttons: 1,
        };
        let (ack_sender, ack_receiver) = sync_channel(1);

        sender
            .send(CaptureMessage::Point(point))
            .expect("queue point");
        sender
            .send(CaptureMessage::Barrier {
                covered_through_ms,
                ack: ack_sender,
            })
            .expect("queue barrier after point");
        let receipt = ack_receiver
            .recv_timeout(Duration::from_secs(2))
            .expect("barrier ack")
            .expect("barrier snapshot succeeds");

        assert_eq!(receipt.point_count, 1);
        assert_eq!(decode_snapshot(&fs::read(&path).unwrap()).unwrap(), [point]);

        drop(sender);
        worker.join().expect("snapshot worker exits");
        let _ = fs::remove_file(path);
    }

    #[cfg(windows)]
    #[test]
    fn enqueue_snapshot_barrier_is_nonblocking_when_busy_or_disconnected() {
        use std::sync::mpsc::sync_channel;

        let (busy_sender, busy_receiver) = sync_channel(1);
        busy_sender.send(CaptureMessage::Flush).expect("fill queue");
        assert!(matches!(
            enqueue_snapshot_barrier(&busy_sender, 1_000),
            Err(SnapshotBarrierError::Busy)
        ));
        drop(busy_receiver);
        assert!(matches!(
            enqueue_snapshot_barrier(&busy_sender, 1_000),
            Err(SnapshotBarrierError::Disconnected)
        ));
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "requires a live KovaaK process"]
    fn live_kovaak_raw_snapshot_barrier_smoke() {
        let path = std::env::temp_dir().join(format!(
            "aiming-cookie-live-raw-barrier-{}-{}.bin",
            std::process::id(),
            now_ms()
        ));
        let state = RawInputState::new(path.clone());
        state.set_enabled(true).expect("start live Raw Input");

        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            let status = state.status();
            if status.kovaak_process_present && status.capture_healthy {
                break;
            }
            assert!(
                Instant::now() < deadline,
                "live KovaaK process was not observed"
            );
            std::thread::sleep(Duration::from_millis(50));
        }

        let receipt = state
            .flush_snapshot_barrier()
            .expect("publish live Raw snapshot barrier");
        let points = decode_snapshot(&fs::read(&path).expect("read published snapshot"))
            .expect("decode published ACRI v1 snapshot");

        assert!(receipt.covered_through_ms > 0);
        assert!(receipt.snapshot_at_ms >= receipt.covered_through_ms);
        assert_eq!(receipt.point_count, points.len());
        assert_eq!(receipt.clock_source, "utc_epoch_ms+qpc");
        assert_eq!(receipt.timebase_version, "time_alignment.v2");

        state.shutdown();
        let _ = fs::remove_file(path);
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
