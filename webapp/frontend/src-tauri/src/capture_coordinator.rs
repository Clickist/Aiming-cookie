use crate::raw_input::{RawInputState, SnapshotBarrierReceipt};
use crate::window_capture::{
    CaptureClockMetadata, GeometryEvent, ReplayExportReceipt, WindowCaptureState,
    WindowCaptureStatus,
};
use rand::RngCore;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::VecDeque;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, Weak};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const CONTROL_MAX_MESSAGE_BYTES: usize = 16 * 1024;
const CONTROL_READ_TIMEOUT: Duration = Duration::from_secs(5);
const CONTROL_EXPORT_TIMEOUT: Duration = Duration::from_secs(60);
// 退出收尾时等待在途控制连接（最重的是 60s 导出）的硬上限；
// 超过即放弃剩余连接，保证进程退出永远不会被单个连接卡死。
const CONTROL_CONNECTION_JOIN_TIMEOUT: Duration = Duration::from_secs(65);
const MONITOR_INTERVAL: Duration = Duration::from_millis(500);
// Snapshot-worker death / sticky snapshot_error leaves capture_healthy false.
// Wait longer than one snapshot retry (5s) so a transient write failure can
// recover before we restart the backend.
const RAW_UNHEALTHY_RESTART_TIMEOUT: Duration = Duration::from_secs(6);
// 进入 Finalizing 后，若 Python 侧的 release 迟迟未到（控制通道被导出占用、
// 时序竞态等），超过该阈值强制释放采集源并回落到 WaitingForKovaak。
// 必须大于桌面后端的 release 硬 grace（30s），留出正常 release 的窗口。
const FINALIZING_STALE_TIMEOUT: Duration = Duration::from_secs(45);
// 尺寸重建安静门（0930 拍板：resize 终态化后按新尺寸自动重开采集）：
// 最后一个已编码 packet 距今 ≥30s 才允许重建——确证没有正在录/刚录完
// 待收尾的局会被重建毁掉证据。
const RESIZE_REBUILD_QUIET_DURATION: Duration = Duration::from_secs(30);
// 尺寸重建限频门：距上次重建尝试 ≥10s 才允许下一次（拖窗口边框的连续
// resize 抖动不会打爆重建）。
const RESIZE_REBUILD_MIN_INTERVAL: Duration = Duration::from_secs(10);
const DIAGNOSTIC_EVENT_LIMIT: usize = 64;
// 捕获总开关的持久化文件，落在 capture 数据根（= app_data_dir）。用户显式
// 关闭也是一种要记住的状态，因此只写这一个布尔位、没有删除语义。
const CAPTURE_ENABLED_FILE_NAME: &str = "capture-enabled.json";

#[derive(Serialize, Deserialize)]
struct StoredCaptureEnabled {
    enabled: bool,
}

fn capture_enabled_file_path(data_root: &Path) -> PathBuf {
    data_root.join(CAPTURE_ENABLED_FILE_NAME)
}

fn write_capture_enabled_file(data_root: &Path, enabled: bool) -> Result<(), String> {
    let payload = serde_json::to_vec(&StoredCaptureEnabled { enabled })
        .map_err(|error| format!("capture enabled serialization failed: {error}"))?;
    fs::create_dir_all(data_root)
        .map_err(|error| format!("capture enabled persistence failed: {error}"))?;
    crate::atomic_write_file(&capture_enabled_file_path(data_root), &payload)
        .map_err(|error| format!("capture enabled persistence failed: {error}"))
}

/// 读取持久化的总开关：Ok(Some(value)) 是正常读到的状态；Ok(None) 表示首
/// 启动尚无持久化文件（默认关）；Err 为损坏/不可读，调用方保守按关处理并
/// 落日志——排障现场「重启后捕获为什么关了」靠它定位。保持无日志副作用，
/// 便于单测覆盖且不污染全局日志 sink。
fn load_capture_enabled_file(data_root: &Path) -> Result<Option<bool>, String> {
    let bytes = match fs::read(capture_enabled_file_path(data_root)) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("stored state unreadable: {error}")),
    };
    serde_json::from_slice::<StoredCaptureEnabled>(&bytes)
        .map(|stored| Some(stored.enabled))
        .map_err(|error| format!("stored state malformed: {error}"))
}

fn diagnostic_now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis() as i64)
        .unwrap_or(0)
}

pub fn bounded_diagnostic_text(value: &str) -> String {
    let mut text = value
        .chars()
        .filter(|character| !character.is_control() || *character == '\n' || *character == '\t')
        .collect::<String>();
    if text.len() > 32 * 1024 {
        // truncate 只接受 char boundary；错误文本可能含多字节字符（中文路径、
        // 本地化消息），字节 32K 处落在字符中间会 panic，先回退到边界再截。
        let mut boundary = 32 * 1024;
        while !text.is_char_boundary(boundary) {
            boundary -= 1;
        }
        text.truncate(boundary);
        text.push_str("...");
    }
    text
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CapturePhase {
    Disabled,
    WaitingForKovaak,
    Capturing,
    Finalizing,
    Degraded,
    Error,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CaptureSourceState {
    Disabled,
    Waiting,
    Capturing,
    Finalizing,
    Degraded,
    Unavailable,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CaptureSourceStatus {
    pub state: CaptureSourceState,
    pub reason: Option<String>,
}

/// 病灶 A：硬编 letterbox 跟随生效时的 video 子状态 reason（稳定码，
/// 对齐 capture_resized_unsupported 的字符串风格）。
const CAPTURE_RESIZED_FOLLOWING: &str = "capture_resized_following";

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CaptureCoordinatorStatus {
    pub enabled: bool,
    pub phase: CapturePhase,
    pub capture_session_id: Option<String>,
    pub kovaak_process_present: bool,
    pub window_handle: Option<usize>,
    pub reason: Option<String>,
    pub raw: CaptureSourceStatus,
    pub video: CaptureSourceStatus,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CaptureDiagnosticEvent {
    pub timestamp_utc_ms: i64,
    pub phase: CapturePhase,
    pub reason: Option<String>,
    pub kovaak_process_present: bool,
    pub raw_state: CaptureSourceState,
    pub video_state: CaptureSourceState,
}

impl CaptureCoordinatorStatus {
    pub fn disabled() -> Self {
        Self {
            enabled: false,
            phase: CapturePhase::Disabled,
            capture_session_id: None,
            kovaak_process_present: false,
            window_handle: None,
            reason: None,
            raw: CaptureSourceStatus {
                state: CaptureSourceState::Disabled,
                reason: None,
            },
            video: CaptureSourceStatus {
                state: CaptureSourceState::Disabled,
                reason: None,
            },
        }
    }

    fn after_enable(&self, process_present: bool, hwnd: Option<usize>) -> Self {
        if process_present && hwnd.is_some() {
            Self {
                enabled: true,
                phase: CapturePhase::Capturing,
                capture_session_id: self.capture_session_id.clone(),
                kovaak_process_present: true,
                window_handle: hwnd,
                reason: None,
                raw: self.raw.clone(),
                video: self.video.clone(),
            }
        } else {
            Self {
                enabled: true,
                phase: CapturePhase::WaitingForKovaak,
                capture_session_id: self.capture_session_id.clone(),
                kovaak_process_present: process_present,
                window_handle: hwnd,
                reason: None,
                raw: CaptureSourceStatus {
                    state: CaptureSourceState::Waiting,
                    reason: None,
                },
                video: CaptureSourceStatus {
                    state: CaptureSourceState::Waiting,
                    reason: None,
                },
            }
        }
    }

    fn raw_only_degraded(capture_session_id: String) -> Self {
        Self {
            enabled: true,
            phase: CapturePhase::Degraded,
            capture_session_id: Some(capture_session_id),
            kovaak_process_present: true,
            window_handle: None,
            reason: Some("kovaak_window_unavailable".to_string()),
            raw: CaptureSourceStatus {
                state: CaptureSourceState::Capturing,
                reason: None,
            },
            video: CaptureSourceStatus {
                state: CaptureSourceState::Waiting,
                reason: Some("kovaak_window_unavailable".to_string()),
            },
        }
    }

    fn after_process_exit(&self) -> Self {
        Self {
            enabled: true,
            phase: CapturePhase::Finalizing,
            capture_session_id: self.capture_session_id.clone(),
            kovaak_process_present: false,
            window_handle: None,
            reason: None,
            raw: CaptureSourceStatus {
                state: CaptureSourceState::Finalizing,
                reason: None,
            },
            video: self.video.clone(),
        }
    }

    fn after_release(process_present: bool) -> Self {
        Self {
            enabled: true,
            phase: CapturePhase::WaitingForKovaak,
            capture_session_id: None,
            kovaak_process_present: process_present,
            window_handle: None,
            reason: None,
            raw: CaptureSourceStatus {
                state: CaptureSourceState::Waiting,
                reason: None,
            },
            video: CaptureSourceStatus {
                state: CaptureSourceState::Waiting,
                reason: None,
            },
        }
    }
}

fn monitor_start_failure_status() -> CaptureCoordinatorStatus {
    CaptureCoordinatorStatus {
        enabled: false,
        phase: CapturePhase::Error,
        capture_session_id: None,
        kovaak_process_present: false,
        window_handle: None,
        reason: Some("capture_monitor_unavailable".to_string()),
        raw: CaptureSourceStatus {
            state: CaptureSourceState::Unavailable,
            reason: Some("capture_monitor_unavailable".to_string()),
        },
        video: CaptureSourceStatus {
            state: CaptureSourceState::Unavailable,
            reason: Some("capture_monitor_unavailable".to_string()),
        },
    }
}

/// F6：Capturing 相位检测到录制会话因窗口尺寸漂移被诚实终态化时，仅把
/// video 子状态降级为显式原因；phase/raw 保持不动，不触发重启流程，下一
/// 局 release → start 链路自然恢复。非 Capturing 相位（如进程退出后的
/// Finalizing）不在本链路处理。返回 None 表示无需变更（已降级或非终态化
/// 事件），保证 tick 幂等、不重复刷事件流。
fn resized_video_degraded_status(
    current: &CaptureCoordinatorStatus,
    recording_terminated_by_resize: bool,
) -> Option<CaptureCoordinatorStatus> {
    if current.phase != CapturePhase::Capturing
        || !recording_terminated_by_resize
        || current.video.state == CaptureSourceState::Degraded
    {
        return None;
    }
    Some(CaptureCoordinatorStatus {
        video: CaptureSourceStatus {
            state: CaptureSourceState::Degraded,
            reason: Some("capture_resized_unsupported".to_string()),
        },
        ..current.clone()
    })
}

/// 收尾局（游戏退出、phase/raw 进入 finalizing）仍允许取 snapshot 覆盖回执：
/// raw 后端会保留到 release，回执的覆盖门与时间基校验不变，只放宽取回时机。
fn raw_snapshot_flush_allowed(phase: CapturePhase, raw_state: CaptureSourceState) -> bool {
    matches!(
        phase,
        CapturePhase::Capturing | CapturePhase::Degraded | CapturePhase::Finalizing
    ) && matches!(
        raw_state,
        CaptureSourceState::Capturing | CaptureSourceState::Finalizing
    )
}

/// 「现在」投影到 WGC packet PTS 时基（QPC/100ns）：安静门用 packet 年龄
/// 判证。时基锚不可用（异常环境）返回 None，安静门按不安静处理。
fn capture_clock_now_pts_100ns() -> Option<i64> {
    let anchor = crate::raw_input::capture_clock_anchor();
    i64::try_from(anchor.monotonic_elapsed_ns / 100).ok()
}

/// 尺寸重建安全门的纯判定（0930 拍板）。返回 None 表示四门全开、允许重建；
/// Some(reason) 为阻塞裸码（只进 dlog，不进控制面 reason 合同）。
/// 「recording_terminated_by_resize」门由调用方先行短路（tick 幂等语义），
/// 不在本函数重复。
fn resize_rebuild_block_reason(
    replay_export_in_flight: bool,
    last_packet_age_100ns: Option<i64>,
    since_last_rebuild: Option<Duration>,
) -> Option<&'static str> {
    if replay_export_in_flight {
        return Some("resize_rebuild_export_in_flight");
    }
    let quiet_100ns =
        i64::try_from(RESIZE_REBUILD_QUIET_DURATION.as_nanos() / 100).unwrap_or(i64::MAX);
    match last_packet_age_100ns {
        // 年龄未知（无 packet 或时基换算失败）→ 无法证明安静，推迟。
        None => return Some("resize_rebuild_buffer_unquiet"),
        // 负年龄意味着时基错位，同样按不安静处理。
        Some(age) if age < quiet_100ns => return Some("resize_rebuild_buffer_unquiet"),
        _ => {}
    }
    if since_last_rebuild.is_some_and(|elapsed| elapsed < RESIZE_REBUILD_MIN_INTERVAL) {
        return Some("resize_rebuild_rate_limited");
    }
    None
}

/// 一次尺寸重建尝试的结果。Deferred 表示执行瞬间安全门未开（不消耗限频
/// 配额，下一 tick 重估）；Failed 为真实启动失败，走现有 degraded 兜底。
#[derive(Debug, PartialEq, Eq)]
enum ResizeRebuildOutcome {
    Rebuilt,
    Deferred(&'static str),
    Failed(String),
}

#[derive(Clone, Debug)]
pub struct CaptureControlConnection {
    pub address: SocketAddr,
    pub secret: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ExportReplayRequest {
    pub request_id: String,
    pub run_id: u64,
    pub capture_session_id: String,
    pub start_epoch_ms: i64,
    pub end_epoch_ms: i64,
}

#[derive(Clone, Debug, PartialEq, Eq)]
enum ControlRequest {
    Status,
    FlushRawSnapshot { capture_session_id: String },
    ExportReplay(ExportReplayRequest),
    ReleaseCaptureSession { capture_session_id: String },
}

#[derive(Deserialize)]
#[serde(
    tag = "type",
    rename_all = "camelCase",
    rename_all_fields = "camelCase",
    deny_unknown_fields
)]
enum ControlRequestWire {
    #[serde(rename = "status")]
    Status { secret: String },
    #[serde(rename = "flushRawSnapshot")]
    FlushRawSnapshot {
        secret: String,
        capture_session_id: String,
    },
    #[serde(rename = "exportReplay")]
    ExportReplay {
        secret: String,
        request_id: String,
        run_id: u64,
        capture_session_id: String,
        start_epoch_ms: i64,
        end_epoch_ms: i64,
    },
    #[serde(rename = "releaseCaptureSession")]
    ReleaseCaptureSession {
        secret: String,
        capture_session_id: String,
    },
}

#[derive(Clone, Debug)]
struct ManagedExportPaths {
    mp4: PathBuf,
    receipt: PathBuf,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct FileFingerprint {
    size: u64,
    digest: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct StoredReplayReceipt {
    requested_start_100ns: i64,
    requested_end_100ns: i64,
    decode_start_100ns: i64,
    visible_duration_100ns: i64,
    decode_preroll_100ns: i64,
    packet_count: usize,
    encoded_bytes: usize,
    reencoded_frames: u64,
    capture_clock: StoredCaptureClock,
    // 病灶 A：窗口尺寸漂移 letterbox 跟随事件（epoch 毫秒轴，canonicalMs
    // 与 resizeEvents.atUtcMs 同源同轴）。#[serde(default)] 是历史兼容硬
    // 要求：旧落盘 receipt 无此键，读回必须成功并按无变换处理。
    #[serde(default)]
    geometry_events: Vec<GeometryEvent>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct StoredCaptureClock {
    utc_epoch_ms: i64,
    qpc_ns: u128,
    clock_source: String,
    timebase_version: String,
}

impl From<CaptureClockMetadata> for StoredCaptureClock {
    fn from(clock: CaptureClockMetadata) -> Self {
        Self {
            utc_epoch_ms: clock.utc_epoch_ms,
            qpc_ns: clock.qpc_ns,
            clock_source: clock.clock_source.to_string(),
            timebase_version: clock.timebase_version.to_string(),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ReceiptRecord {
    version: String,
    request_digest: String,
    request_id: String,
    run_id: u64,
    capture_session_id: String,
    start_epoch_ms: i64,
    end_epoch_ms: i64,
    replay: StoredReplayReceipt,
    file: FileFingerprint,
}

impl ReceiptRecord {
    fn placeholder(request: &ExportReplayRequest) -> Self {
        Self {
            version: "capture_receipt.v1".to_string(),
            request_digest: request_digest(request),
            request_id: request.request_id.clone(),
            run_id: request.run_id,
            capture_session_id: request.capture_session_id.clone(),
            start_epoch_ms: request.start_epoch_ms,
            end_epoch_ms: request.end_epoch_ms,
            replay: StoredReplayReceipt {
                requested_start_100ns: 0,
                requested_end_100ns: 0,
                decode_start_100ns: 0,
                visible_duration_100ns: 0,
                decode_preroll_100ns: 0,
                packet_count: 0,
                encoded_bytes: 0,
                reencoded_frames: 0,
                capture_clock: StoredCaptureClock {
                    utc_epoch_ms: 0,
                    qpc_ns: 0,
                    clock_source: "unavailable".to_string(),
                    timebase_version: "time_alignment.v2".to_string(),
                },
                geometry_events: Vec::new(),
            },
            file: FileFingerprint {
                size: 0,
                digest: String::new(),
            },
        }
    }

    #[cfg(test)]
    fn fixture(request: ExportReplayRequest) -> Self {
        Self {
            version: "capture_receipt.v1".to_string(),
            request_digest: request_digest(&request),
            request_id: request.request_id,
            run_id: request.run_id,
            capture_session_id: request.capture_session_id,
            start_epoch_ms: request.start_epoch_ms,
            end_epoch_ms: request.end_epoch_ms,
            replay: StoredReplayReceipt {
                requested_start_100ns: 0,
                requested_end_100ns: 10_000_000,
                decode_start_100ns: 0,
                visible_duration_100ns: 10_000_000,
                decode_preroll_100ns: 0,
                packet_count: 1,
                encoded_bytes: 3,
                reencoded_frames: 0,
                capture_clock: StoredCaptureClock {
                    utc_epoch_ms: 1_000,
                    qpc_ns: 0,
                    clock_source: "test".to_string(),
                    timebase_version: "time_alignment.v2".to_string(),
                },
                geometry_events: Vec::new(),
            },
            file: FileFingerprint::from_bytes(b"mp4"),
        }
    }

    fn from_export(
        request: &ExportReplayRequest,
        receipt: ReplayExportReceipt,
        path: &Path,
    ) -> Result<Self, String> {
        Ok(Self {
            version: "capture_receipt.v1".to_string(),
            request_digest: request_digest(request),
            request_id: request.request_id.clone(),
            run_id: request.run_id,
            capture_session_id: request.capture_session_id.clone(),
            start_epoch_ms: request.start_epoch_ms,
            end_epoch_ms: request.end_epoch_ms,
            replay: StoredReplayReceipt {
                requested_start_100ns: receipt.requested_start_100ns,
                requested_end_100ns: receipt.requested_end_100ns,
                decode_start_100ns: receipt.decode_start_100ns,
                visible_duration_100ns: receipt.visible_duration_100ns,
                decode_preroll_100ns: receipt.decode_preroll_100ns,
                packet_count: receipt.packet_count,
                encoded_bytes: receipt.encoded_bytes,
                reencoded_frames: receipt.reencoded_frames,
                capture_clock: receipt.capture_clock.into(),
                geometry_events: receipt.geometry_events,
            },
            file: FileFingerprint::from_file(path)?,
        })
    }

    fn write_atomic(&self, path: &Path) -> Result<(), String> {
        let parent = path
            .parent()
            .ok_or_else(|| "receipt path has no parent".to_string())?;
        let temporary = parent.join(format!(
            ".{}.partial",
            path.file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("receipt")
        ));
        let payload = serde_json::to_vec(self)
            .map_err(|error| format!("capture receipt serialization failed: {error}"))?;
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)
            .map_err(|error| format!("capture receipt partial creation failed: {error}"))?;
        file.write_all(&payload)
            .and_then(|()| file.flush())
            .and_then(|()| file.sync_all())
            .map_err(|error| format!("capture receipt write failed: {error}"))?;
        drop(file);
        fs::rename(&temporary, path)
            .map_err(|error| format!("capture receipt publication failed: {error}"))
    }

    fn read_matching(paths: &ManagedExportPaths, request: &Self) -> Result<bool, String> {
        if !paths.mp4.is_file() || !paths.receipt.is_file() {
            return Ok(false);
        }
        let bytes = fs::read(&paths.receipt)
            .map_err(|error| format!("capture receipt read failed: {error}"))?;
        let observed: Self = serde_json::from_slice(&bytes)
            .map_err(|_| "capture receipt is malformed".to_string())?;
        if !observed.matches_request_record(request) {
            return Err("existing capture artifact conflicts with the export request".to_string());
        }
        if FileFingerprint::from_file(&paths.mp4)? != observed.file {
            return Err(
                "existing capture artifact fingerprint does not match its receipt".to_string(),
            );
        }
        Ok(true)
    }

    fn matches_request_record(&self, expected: &Self) -> bool {
        self.version == expected.version
            && self.request_digest == expected.request_digest
            && self.request_id == expected.request_id
            && self.run_id == expected.run_id
            && self.capture_session_id == expected.capture_session_id
            && self.start_epoch_ms == expected.start_epoch_ms
            && self.end_epoch_ms == expected.end_epoch_ms
    }

    fn read(path: &Path) -> Result<Self, String> {
        serde_json::from_slice(
            &fs::read(path).map_err(|error| format!("capture receipt read failed: {error}"))?,
        )
        .map_err(|_| "capture receipt is malformed".to_string())
    }
}

impl FileFingerprint {
    #[cfg(test)]
    fn from_bytes(bytes: &[u8]) -> Self {
        Self {
            size: bytes.len() as u64,
            digest: sha256_hex(bytes),
        }
    }

    fn from_file(path: &Path) -> Result<Self, String> {
        let mut file =
            File::open(path).map_err(|error| format!("capture artifact read failed: {error}"))?;
        let mut buffer = [0_u8; 64 * 1024];
        let mut size = 0_u64;
        let mut hasher = StreamingSha256::new();
        loop {
            let count = file
                .read(&mut buffer)
                .map_err(|error| format!("capture artifact read failed: {error}"))?;
            if count == 0 {
                break;
            }
            size = size
                .checked_add(count as u64)
                .ok_or_else(|| "capture artifact size overflow".to_string())?;
            hasher.update(&buffer[..count])?;
        }
        Ok(Self {
            size,
            digest: hasher.finish()?,
        })
    }
}

fn request_digest(request: &ExportReplayRequest) -> String {
    sha256_hex(
        format!(
            "capture_export.v1|{}|{}|{}|{}|{}",
            request.request_id,
            request.run_id,
            request.capture_session_id,
            request.start_epoch_ms,
            request.end_epoch_ms,
        )
        .as_bytes(),
    )
}

fn sha256_hex(bytes: &[u8]) -> String {
    to_hex(Sha256::digest(bytes).as_slice())
}

fn to_hex(bytes: &[u8]) -> String {
    let mut hex = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        hex.push_str(&format!("{byte:02x}"));
    }
    hex
}

struct StreamingSha256(Sha256);

impl StreamingSha256 {
    fn new() -> Self {
        Self(Sha256::new())
    }

    fn update(&mut self, bytes: &[u8]) -> Result<(), String> {
        self.0.update(bytes);
        Ok(())
    }

    fn finish(self) -> Result<String, String> {
        Ok(to_hex(self.0.finalize().as_slice()))
    }
}

pub struct CaptureCoordinatorState {
    data_root: PathBuf,
    raw_input: Arc<RawInputState>,
    window_capture: Arc<Mutex<WindowCaptureState>>,
    status: Mutex<CaptureCoordinatorStatus>,
    diagnostic_events: Mutex<VecDeque<CaptureDiagnosticEvent>>,
    finalizing_since: Mutex<Option<Instant>>,
    raw_unhealthy_since: Mutex<Option<Instant>>,
    // video 采集 start 失败 dlog 的限频锚点：协调器按相位推进可能每局
    // 触发多次 start 失败，日志按「首条 + 每 30s 一条」限频，人类可读细节
    // 同时完整留在启动失败快照（诊断包 v7）里。
    video_capture_failure_log_at: Mutex<Option<Instant>>,
    // raw 健康重启事件的限频锚点：recover_unhealthy_raw 的 6s 起搏在最坏
    // 负载下可能形成分钟级重启循环，事件按「首条 + 每 30s 一条」限频，
    // 避免 6s 周期的重启风暴刷爆 64 条事件环；错误细节同时完整进 dlog。
    raw_health_restart_event_at: Mutex<Option<Instant>>,
    // 尺寸重建的限频锚点（0930 拍板）：记录上次重建尝试时刻，10s 内不再
    // 重试，防止拖窗口边框的连续 resize 打爆重建。
    last_resize_rebuild_at: Mutex<Option<Instant>>,
    shutdown: Arc<AtomicBool>,
    monitor: Mutex<Option<JoinHandle<()>>>,
    control: Mutex<Option<ControlServer>>,
}

/// 失败 dlog 限频判据：首条必发，之后每 30s 放行一条。
const VIDEO_CAPTURE_FAILURE_LOG_INTERVAL: Duration = Duration::from_secs(30);

fn video_capture_failure_log_allowed(last: Option<Instant>, now: Instant) -> bool {
    last.is_none_or(|at| now.duration_since(at) >= VIDEO_CAPTURE_FAILURE_LOG_INTERVAL)
}

/// raw 健康重启事件限频判据：与失败 dlog 同参（首条 + 每 30s 一条）。
/// recover_unhealthy_raw 的 6s 起搏在最坏负载下可能形成分钟级重启循环，
/// 不限频会刷爆 64 条事件环、挤掉相位迁移证据。
const RAW_HEALTH_RESTART_EVENT_INTERVAL: Duration = Duration::from_secs(30);

fn raw_health_restart_event_allowed(last: Option<Instant>, now: Instant) -> bool {
    last.is_none_or(|at| now.duration_since(at) >= RAW_HEALTH_RESTART_EVENT_INTERVAL)
}

/// raw 健康重启的诊断事件：reason 固定裸码（控制面合同 ^[a-z][a-z0-9_]{0,63}$，
/// 人类可读细节只进 dlog），相位/子态取重启决策时刻的协调器状态。
fn raw_health_restart_event(status: &CaptureCoordinatorStatus) -> CaptureDiagnosticEvent {
    CaptureDiagnosticEvent {
        timestamp_utc_ms: diagnostic_now_ms(),
        phase: status.phase,
        reason: Some("raw_health_restart".to_string()),
        kovaak_process_present: status.kovaak_process_present,
        raw_state: status.raw.state,
        video_state: status.video.state,
    }
}

impl CaptureCoordinatorState {
    pub fn new(
        data_root: PathBuf,
        raw_input: Arc<RawInputState>,
        window_capture: Arc<Mutex<WindowCaptureState>>,
    ) -> Result<Arc<Self>, String> {
        if !data_root.is_absolute() {
            return Err("capture data root must be absolute".to_string());
        }
        fs::create_dir_all(&data_root)
            .map_err(|error| format!("capture data root creation failed: {error}"))?;
        let coordinator = Arc::new(Self {
            data_root,
            raw_input,
            window_capture,
            status: Mutex::new(CaptureCoordinatorStatus::disabled()),
            diagnostic_events: Mutex::new(VecDeque::from([CaptureDiagnosticEvent {
                timestamp_utc_ms: diagnostic_now_ms(),
                phase: CapturePhase::Disabled,
                reason: None,
                kovaak_process_present: false,
                raw_state: CaptureSourceState::Disabled,
                video_state: CaptureSourceState::Disabled,
            }])),
            finalizing_since: Mutex::new(None),
            raw_unhealthy_since: Mutex::new(None),
            video_capture_failure_log_at: Mutex::new(None),
            raw_health_restart_event_at: Mutex::new(None),
            last_resize_rebuild_at: Mutex::new(None),
            shutdown: Arc::new(AtomicBool::new(false)),
            monitor: Mutex::new(None),
            control: Mutex::new(None),
        });
        let server = ControlServer::bind(Arc::downgrade(&coordinator))?;
        coordinator
            .control
            .lock()
            .map_err(|_| "capture control state is unavailable".to_string())?
            .replace(server);
        Ok(coordinator)
    }

    pub fn control_connection(&self) -> Result<CaptureControlConnection, String> {
        self.control
            .lock()
            .map_err(|_| "capture control state is unavailable".to_string())?
            .as_ref()
            .map(ControlServer::connection)
            .ok_or_else(|| "capture control server is unavailable".to_string())
    }

    /// 失败 dlog 限频：首条必发，之后每 30s 放行一条；放行时顺带记账。
    /// 槽位中毒按不放行处理（宁可少一条日志，不 panic 采集面）。
    fn video_capture_failure_log_allowed(&self) -> bool {
        let Ok(mut slot) = self.video_capture_failure_log_at.lock() else {
            return false;
        };
        let allowed = video_capture_failure_log_allowed(*slot, Instant::now());
        if allowed {
            *slot = Some(Instant::now());
        }
        allowed
    }

    /// raw 健康重启事件限频：与失败 dlog 同构（首条必发，之后每 30s 一条，
    /// 放行时顺带记账）；槽位中毒按不放行处理。
    fn raw_health_restart_event_allowed(&self) -> bool {
        let Ok(mut slot) = self.raw_health_restart_event_at.lock() else {
            return false;
        };
        let allowed = raw_health_restart_event_allowed(*slot, Instant::now());
        if allowed {
            *slot = Some(Instant::now());
        }
        allowed
    }

    /// 追加一条诊断事件，维持 64 条环形上限（旧事件先出）。
    fn push_diagnostic_event(&self, event: CaptureDiagnosticEvent) {
        if let Ok(mut events) = self.diagnostic_events.lock() {
            events.push_back(event);
            while events.len() > DIAGNOSTIC_EVENT_LIMIT {
                events.pop_front();
            }
        }
    }

    pub fn status(&self) -> CaptureCoordinatorStatus {
        self.status
            .lock()
            .map(|status| status.clone())
            .unwrap_or_else(|_| CaptureCoordinatorStatus {
                enabled: false,
                phase: CapturePhase::Error,
                capture_session_id: None,
                kovaak_process_present: false,
                window_handle: None,
                reason: Some("capture coordinator state is unavailable".to_string()),
                raw: CaptureSourceStatus {
                    state: CaptureSourceState::Unavailable,
                    reason: Some("coordinator_state_unavailable".to_string()),
                },
                video: CaptureSourceStatus {
                    state: CaptureSourceState::Unavailable,
                    reason: Some("coordinator_state_unavailable".to_string()),
                },
            })
    }

    pub fn diagnostic_events(&self) -> Vec<CaptureDiagnosticEvent> {
        self.diagnostic_events
            .lock()
            .map(|events| events.iter().cloned().collect())
            .unwrap_or_default()
    }

    pub fn diagnostic_data_root(&self) -> String {
        self.data_root.to_string_lossy().into_owned()
    }

    pub fn set_enabled(
        self: &Arc<Self>,
        enabled: bool,
    ) -> Result<CaptureCoordinatorStatus, String> {
        let previously_enabled = self.status().enabled;
        if !enabled {
            self.disable()?;
            if previously_enabled {
                self.persist_capture_enabled(false);
            }
            return Ok(self.status());
        }
        let next_status = {
            let status = self
                .status
                .lock()
                .map_err(|_| "capture coordinator state is unavailable".to_string())?;
            if status.enabled {
                return Ok(status.clone());
            }
            status.after_enable(false, None)
        };
        self.replace_status(next_status);
        if let Err(error) = self.start_monitor() {
            self.replace_status(monitor_start_failure_status());
            return Err(error);
        }
        self.persist_capture_enabled(true);
        Ok(self.status())
    }

    /// 启动时恢复持久化的总开关（setup 里、窗口展示之前调用）。文件缺失或
    /// 损坏保持默认关；恢复失败同样不阻塞启动，残留的 enabled 文件会让下一
    /// 次启动继续尝试。与前端后续的重复 enable 幂等共存。
    pub fn restore_persisted_enabled(self: &Arc<Self>) {
        match load_capture_enabled_file(&self.data_root) {
            Ok(Some(true)) => {
                if let Err(error) = self.set_enabled(true) {
                    crate::dlog!("[capture-enabled] restore failed, staying disabled: {error}");
                } else {
                    crate::dlog!("[capture-enabled] restored enabled=true from persisted state");
                }
            }
            Ok(_) => {}
            Err(error) => {
                crate::dlog!("[capture-enabled] {error}, staying disabled");
            }
        }
    }

    /// 总开关持久化是 best-effort：写失败只落日志，不回滚已生效的内存状态。
    fn persist_capture_enabled(&self, enabled: bool) {
        if let Err(error) = write_capture_enabled_file(&self.data_root, enabled) {
            crate::dlog!("[capture-enabled] persistence failed: {error}");
        }
    }

    fn start_monitor(self: &Arc<Self>) -> Result<(), String> {
        let mut monitor = self
            .monitor
            .lock()
            .map_err(|_| "capture monitor state is unavailable".to_string())?;
        if monitor.is_some() {
            return Ok(());
        }
        let coordinator = Arc::downgrade(self);
        let shutdown = Arc::clone(&self.shutdown);
        *monitor = Some(
            thread::Builder::new()
                .name("aiming-cookie-capture-coordinator".to_string())
                .spawn(move || {
                    while !shutdown.load(Ordering::Acquire) {
                        let Some(coordinator) = coordinator.upgrade() else {
                            break;
                        };
                        coordinator.monitor_once();
                        thread::sleep(MONITOR_INTERVAL);
                    }
                })
                .map_err(|_| "capture_monitor_unavailable".to_string())?,
        );
        Ok(())
    }

    fn monitor_once(&self) {
        let (process_present, hwnd) = match find_kovaak_window() {
            Ok(result) => result,
            Err(code) => {
                self.replace_status(CaptureCoordinatorStatus {
                    enabled: true,
                    phase: CapturePhase::Error,
                    capture_session_id: None,
                    kovaak_process_present: false,
                    window_handle: None,
                    reason: Some(code.to_string()),
                    raw: CaptureSourceStatus {
                        state: CaptureSourceState::Unavailable,
                        reason: Some(code.to_string()),
                    },
                    video: CaptureSourceStatus {
                        state: CaptureSourceState::Unavailable,
                        reason: Some(code.to_string()),
                    },
                });
                return;
            }
        };
        let current = self.status();
        if !current.enabled {
            return;
        }
        if current.phase == CapturePhase::Finalizing && self.release_stale_finalizing() {
            // 本轮已强制回落，下一轮按新状态重新评估采集。
            return;
        }
        if !process_present {
            if matches!(
                current.phase,
                CapturePhase::Capturing | CapturePhase::Degraded
            ) {
                // 收尾期间保留 raw 后端：Python finalizer 要在 finalizing 相位取回
                // 覆盖回执才能立即附加 trace（否则收尾局要等满快照保留期）。
                // raw 真正的终点是 release / stale / disable，见对应分支。
                self.replace_status(current.after_process_exit());
            } else if current.phase != CapturePhase::Finalizing {
                self.replace_status(current.after_enable(process_present, hwnd));
            }
            return;
        }
        if current.phase == CapturePhase::Finalizing {
            return;
        }
        if current.phase == CapturePhase::Capturing {
            self.recover_unhealthy_raw();
            self.report_following_resize(&current);
            self.report_or_rebuild_resized_video(&current, hwnd, |capture, hwnd| {
                capture.stop();
                capture.start_for_window(hwnd)
            });
            return;
        }
        if let Err(error) = self.raw_input.set_enabled(true) {
            // reason 只放纯错误码（Python 控制面合同 ^[a-z][a-z0-9_]{0,63}$），
            // 人类可读细节进 native 日志，不得拼进 reason（0929 合同违规整改）。
            crate::dlog!(
                "[capture-coordinator] raw input enable failed: {}",
                bounded_diagnostic_text(&error)
            );
            let reason = "raw_input_unavailable".to_string();
            self.replace_status(CaptureCoordinatorStatus {
                enabled: true,
                phase: CapturePhase::Error,
                capture_session_id: None,
                kovaak_process_present: true,
                window_handle: hwnd,
                reason: Some(reason.clone()),
                raw: CaptureSourceStatus {
                    state: CaptureSourceState::Unavailable,
                    reason: Some(reason),
                },
                video: current.video,
            });
            return;
        }
        let capture_session_id = current
            .capture_session_id
            .clone()
            .unwrap_or_else(create_ephemeral_secret);
        let Some(hwnd) = hwnd else {
            self.replace_status(CaptureCoordinatorStatus::raw_only_degraded(
                capture_session_id,
            ));
            return;
        };
        if !is_current_kovaak_window(hwnd) {
            self.replace_status(current.after_enable(true, None));
            return;
        }
        let capture = self.window_capture.lock();
        let outcome = capture
            .map_err(|_| "window capture state is unavailable".to_string())
            .and_then(|mut capture| capture.start_for_window(hwnd));
        match outcome {
            Ok(_) => self.replace_status(CaptureCoordinatorStatus {
                enabled: true,
                phase: CapturePhase::Capturing,
                capture_session_id: Some(capture_session_id),
                kovaak_process_present: true,
                window_handle: Some(hwnd),
                reason: None,
                raw: CaptureSourceStatus {
                    state: CaptureSourceState::Capturing,
                    reason: None,
                },
                video: CaptureSourceStatus {
                    state: CaptureSourceState::Capturing,
                    reason: None,
                },
            }),
            Err(error) => {
                // reason 只放纯错误码（Python 控制面合同 ^[a-z][a-z0-9_]{0,63}$），
                // 人类可读细节进 native 日志，不得拼进 reason（0929 合同违规整改：
                // 带文案 reason 曾使后端判 schema_invalid，连锁导致死会话不释放、
                // 后续每局视频轨迹全灭）。
                if self.video_capture_failure_log_allowed() {
                    crate::dlog!(
                        "[capture-coordinator] window capture start failed: {}",
                        bounded_diagnostic_text(&error)
                    );
                }
                let reason = "video_capture_unavailable".to_string();
                self.replace_status(CaptureCoordinatorStatus {
                    enabled: true,
                    phase: CapturePhase::Degraded,
                    capture_session_id: Some(capture_session_id),
                    kovaak_process_present: true,
                    window_handle: Some(hwnd),
                    reason: Some(reason.clone()),
                    raw: CaptureSourceStatus {
                        state: CaptureSourceState::Capturing,
                        reason: None,
                    },
                    video: CaptureSourceStatus {
                        state: CaptureSourceState::Degraded,
                        reason: Some(reason),
                    },
                })
            }
        }
    }

    fn replace_status(&self, replacement: CaptureCoordinatorStatus) {
        if let Ok(mut status) = self.status.lock() {
            let enters_finalizing = status.phase != CapturePhase::Finalizing
                && replacement.phase == CapturePhase::Finalizing;
            let exits_finalizing = status.phase == CapturePhase::Finalizing
                && replacement.phase != CapturePhase::Finalizing;
            if status.phase != replacement.phase {
                crate::dlog!(
                    "[capture-export] phase {:?} -> {:?} session={:?}",
                    status.phase,
                    replacement.phase,
                    replacement.capture_session_id
                );
            }
            let event = if *status != replacement {
                Some(CaptureDiagnosticEvent {
                    timestamp_utc_ms: diagnostic_now_ms(),
                    phase: replacement.phase,
                    reason: replacement
                        .reason
                        .clone()
                        .map(|value| bounded_diagnostic_text(&value)),
                    kovaak_process_present: replacement.kovaak_process_present,
                    raw_state: replacement.raw.state,
                    video_state: replacement.video.state,
                })
            } else {
                None
            };
            *status = replacement;
            drop(status);
            if enters_finalizing || exits_finalizing {
                if let Ok(mut since) = self.finalizing_since.lock() {
                    *since = if enters_finalizing {
                        Some(Instant::now())
                    } else {
                        None
                    };
                }
            }
            if let Some(event) = event {
                if let Ok(mut events) = self.diagnostic_events.lock() {
                    events.push_back(event);
                    while events.len() > DIAGNOSTIC_EVENT_LIMIT {
                        events.pop_front();
                    }
                }
            }
        }
    }

    // 进入 Finalizing 后若 release 迟迟未到（控制通道被导出占用或时序竞态），
    // 强制释放采集源并回落到 WaitingForKovaak，让后续每局都能重新采集。
    fn release_stale_finalizing(&self) -> bool {
        let stale = self
            .finalizing_since
            .lock()
            .ok()
            .and_then(|since| *since)
            .map(|since| since.elapsed() >= FINALIZING_STALE_TIMEOUT)
            .unwrap_or(false);
        if !stale {
            return false;
        }
        let _ = self.raw_input.set_enabled(false);
        if let Ok(mut capture) = self.window_capture.lock() {
            capture.stop();
        }
        let (process_present, _hwnd) = find_kovaak_window().unwrap_or((false, None));
        self.replace_status(CaptureCoordinatorStatus::after_release(process_present));
        true
    }

    // 病灶 A：硬编 letterbox 跟随生效时把 video 子状态标注为
    // capture_resized_following（state 维持 Capturing）；漂移解除后清除
    // 标注。终态化在场时完全不触碰 video 状态——degraded/rebuild 路径
    // 拥有它，两者不得互相覆盖。
    fn report_following_resize(&self, current: &CaptureCoordinatorStatus) {
        if current.phase != CapturePhase::Capturing {
            return;
        }
        let (terminated, following) = match self.window_capture.lock() {
            Ok(capture) => (
                capture.recording_terminated_by_resize(),
                capture.recording_following_resize(),
            ),
            Err(_) => return,
        };
        if terminated {
            return;
        }
        if following {
            // 非本链路造成的 video 降级不碰；已在跟随标注则 tick 幂等。
            if current.video.state != CaptureSourceState::Capturing
                || current.video.reason.as_deref() == Some(CAPTURE_RESIZED_FOLLOWING)
            {
                return;
            }
            self.replace_status(CaptureCoordinatorStatus {
                video: CaptureSourceStatus {
                    state: CaptureSourceState::Capturing,
                    reason: Some(CAPTURE_RESIZED_FOLLOWING.to_string()),
                },
                ..current.clone()
            });
        } else if current.video.reason.as_deref() == Some(CAPTURE_RESIZED_FOLLOWING) {
            // 漂移解除（内容回到会话尺寸）：清除跟随标注，state 不动。
            self.replace_status(CaptureCoordinatorStatus {
                video: CaptureSourceStatus {
                    state: CaptureSourceState::Capturing,
                    reason: None,
                },
                ..current.clone()
            });
        }
    }

    // F6 联动：录制会话因窗口尺寸漂移被诚实终态化后，先把 video 子状态
    // 降级为显式原因（运行中的状态与事件流可见，而不是“静默断流但显示
    // 采集中”）；安全门全开时再按当前窗口尺寸自动重建采集（0930 拍板），
    // 后续局不再因一次 resize 全灭。capture_session_id 与 raw 后端完全不动。
    fn report_or_rebuild_resized_video(
        &self,
        current: &CaptureCoordinatorStatus,
        hwnd: Option<usize>,
        restart: impl FnOnce(&mut WindowCaptureState, usize) -> Result<WindowCaptureStatus, String>,
    ) {
        let (terminated, export_in_flight, last_packet_pts_100ns) = match self.window_capture.lock()
        {
            Ok(capture) => (
                capture.recording_terminated_by_resize(),
                capture.replay_export_in_flight(),
                capture.status().last_packet_pts_100ns,
            ),
            Err(_) => return,
        };
        // 无终态化事件时不做任何事：tick 幂等，不重复刷状态与事件流。
        if !terminated {
            return;
        }
        if let Some(replacement) = resized_video_degraded_status(current, true) {
            self.replace_status(replacement);
        }
        let Some(hwnd) = hwnd else {
            return;
        };
        // 安静门：packet 年龄 = 「现在」- 最后已编码 packet PTS（同 QPC/100ns
        // 时基）。年龄未知或为负（时基错位）一律按不安静推迟。
        let quiet_age_100ns =
            capture_clock_now_pts_100ns().and_then(|now| now.checked_sub(last_packet_pts_100ns?));
        if resize_rebuild_block_reason(
            export_in_flight,
            quiet_age_100ns,
            self.resize_rebuild_rate_limit_elapsed(),
        )
        .is_some()
        {
            return;
        }
        match self.attempt_resize_rebuild(hwnd, restart) {
            ResizeRebuildOutcome::Rebuilt => {
                // 重建成功：video 回 capturing；phase/session/raw 保持不动。
                self.replace_status(CaptureCoordinatorStatus {
                    video: CaptureSourceStatus {
                        state: CaptureSourceState::Capturing,
                        reason: None,
                    },
                    ..current.clone()
                });
            }
            ResizeRebuildOutcome::Deferred(reason) => {
                crate::dlog!(
                    "[capture-coordinator] resize rebuild deferred at execution: {reason}"
                );
            }
            ResizeRebuildOutcome::Failed(error) => {
                // reason 只放纯错误码（控制面合同 ^[a-z][a-z0-9_]{0,63}$），
                // 人类可读细节进 native 日志，不得拼进 reason（0929 整改）。
                if self.video_capture_failure_log_allowed() {
                    crate::dlog!(
                        "[capture-coordinator] resize rebuild start failed: {}",
                        bounded_diagnostic_text(&error)
                    );
                }
                // 复用现有 video start 失败语义（phase 降级 → 主路径逐 tick
                // 重试 start；session id 不变），不发明新错误码。
                let reason = "video_capture_unavailable".to_string();
                self.replace_status(CaptureCoordinatorStatus {
                    enabled: true,
                    phase: CapturePhase::Degraded,
                    capture_session_id: current.capture_session_id.clone(),
                    kovaak_process_present: true,
                    window_handle: Some(hwnd),
                    reason: Some(reason.clone()),
                    raw: CaptureSourceStatus {
                        state: CaptureSourceState::Capturing,
                        reason: None,
                    },
                    video: CaptureSourceStatus {
                        state: CaptureSourceState::Degraded,
                        reason: Some(reason),
                    },
                });
            }
        }
    }

    /// 尺寸重建限频锚点的已流逝时长；从未重建过为 None。
    fn resize_rebuild_rate_limit_elapsed(&self) -> Option<Duration> {
        self.last_resize_rebuild_at
            .lock()
            .ok()
            .and_then(|slot| *slot)
            .map(|at| at.elapsed())
    }

    /// 执行一次重建尝试：停当前 window capture 后按当前窗口尺寸重新
    /// start（新尺寸自动生效）。导出在途复检放在同一把锁内，与
    /// handle_export 的排队互斥，杜绝「门检通过 → 导出插入 → stop 毁
    /// 证据」的亚 tick 竞态；推迟不消耗限频配额。
    fn attempt_resize_rebuild(
        &self,
        hwnd: usize,
        restart: impl FnOnce(&mut WindowCaptureState, usize) -> Result<WindowCaptureStatus, String>,
    ) -> ResizeRebuildOutcome {
        let Ok(mut capture) = self.window_capture.lock() else {
            return ResizeRebuildOutcome::Failed("window capture state is unavailable".to_string());
        };
        if capture.replay_export_in_flight() {
            return ResizeRebuildOutcome::Deferred("replay_export_in_flight");
        }
        if let Ok(mut slot) = self.last_resize_rebuild_at.lock() {
            *slot = Some(Instant::now());
        }
        crate::dlog!(
            "[capture-coordinator] resize rebuild: restarting window capture at the current window size"
        );
        match restart(&mut capture, hwnd) {
            Ok(_) => ResizeRebuildOutcome::Rebuilt,
            Err(error) => ResizeRebuildOutcome::Failed(error),
        }
    }

    fn recover_unhealthy_raw(&self) {
        let raw_status = self.raw_input.status();
        let healthy = raw_status.capture_healthy;
        let should_restart = {
            let Ok(mut since) = self.raw_unhealthy_since.lock() else {
                return;
            };
            if healthy {
                *since = None;
                false
            } else {
                let started = since.get_or_insert_with(Instant::now);
                if started.elapsed() < RAW_UNHEALTHY_RESTART_TIMEOUT {
                    false
                } else {
                    *since = None;
                    true
                }
            }
        };
        if !should_restart {
            return;
        }
        // 重启必须有痕：dlog 带错误码与细节（诊断包 native 日志尾部可见），
        // 事件流带裸码（限频首条 + 每 30s 一条），否则 raw-only 静默重启在
        // 诊断包里完全不可见（本路径不迁移 phase，事件环原本对它失明）。
        crate::dlog!(
            "[capture-recovery] raw health restart: code={:?} error={:?} failures={} anchor_utc_ms={}",
            raw_status.snapshot_error_code,
            raw_status
                .snapshot_error
                .as_deref()
                .map(bounded_diagnostic_text),
            raw_status.snapshot_failures,
            raw_status.clock_anchor_utc_ms
        );
        if self.raw_health_restart_event_allowed() {
            self.push_diagnostic_event(raw_health_restart_event(&self.status()));
        }
        // 重启前先请求一次 barrier flush，把 ring 里未落盘的点先写盘
        //（已有 5s 超时保护，超时/失败即放弃；写失败型不健康下 barrier
        // 同样可能失败——这里收窄而非消灭丢失窗，诚实量化仍靠 receipt）。
        let _ = self.raw_input.flush_snapshot_barrier();
        // Force-cycle past set_enabled's no-op when enabled is already true.
        // Video capture is left running; only the raw backend is restarted.
        let _ = self.raw_input.set_enabled(false);
        let _ = self.raw_input.set_enabled(true);
    }

    fn disable(&self) -> Result<(), String> {
        if let Ok(mut since) = self.raw_unhealthy_since.lock() {
            *since = None;
        }
        self.raw_input.set_enabled(false)?;
        self.window_capture
            .lock()
            .map_err(|_| "window capture state is unavailable".to_string())?
            .stop();
        self.replace_status(CaptureCoordinatorStatus::disabled());
        Ok(())
    }

    fn flush_raw_snapshot(
        &self,
        capture_session_id: &str,
    ) -> Result<SnapshotBarrierReceipt, String> {
        let status = self.status();
        if status.capture_session_id.as_deref() != Some(capture_session_id) {
            return Err("capture_session_mismatch".to_string());
        }
        if !raw_snapshot_flush_allowed(status.phase, status.raw.state) {
            return Err("raw_snapshot_unavailable".to_string());
        }
        self.raw_input.flush_snapshot_barrier()
    }

    fn handle_export(&self, request: ExportReplayRequest) -> Result<ReceiptRecord, String> {
        let started = Instant::now();
        crate::dlog!(
            "[capture-export] handle_export: id={} run={} session={}",
            request.request_id,
            request.run_id,
            request.capture_session_id
        );
        let status = self.status();
        if !matches!(
            status.phase,
            CapturePhase::Capturing | CapturePhase::Finalizing
        ) {
            crate::dlog!(
                "[capture-export] handle_export: phase={:?} rejects export",
                status.phase
            );
            return Err("capture_unavailable".to_string());
        }
        if status.capture_session_id.as_deref() != Some(request.capture_session_id.as_str()) {
            crate::dlog!(
                "[capture-export] handle_export: session mismatch current={:?} requested={}",
                status.capture_session_id,
                request.capture_session_id
            );
            return Err("capture_session_mismatch".to_string());
        }
        let paths = managed_export_paths(&self.data_root, request.run_id, &request.request_id)?;
        let placeholder = ReceiptRecord::placeholder(&request);
        if paths.mp4.exists() || paths.receipt.exists() {
            crate::dlog!(
                "[capture-export] handle_export: artifacts already exist, revalidating {}",
                paths.mp4.display()
            );
            return match ReceiptRecord::read_matching(&paths, &placeholder) {
                Ok(true) => ReceiptRecord::read(&paths.receipt),
                Ok(false) => Err("existing capture artifact is incomplete".to_string()),
                Err(error) => Err(error),
            };
        }
        // 重建安全门 (b)：导出在途计数从排入 mux 队列前开始，到 receipt
        // 收尾为止；guard Drop 保证 panic/提前返回都不会漏减，停采集的
        // 重建决策据此避开在途导出。
        let _export_in_flight = ReplayExportInFlightGuard::begin(&self.window_capture);
        let receiver = {
            let capture = self
                .window_capture
                .lock()
                .map_err(|_| "window capture state is unavailable".to_string())?;
            let (start_100ns, end_100ns) = capture
                .epoch_window_to_replay_pts(request.start_epoch_ms, request.end_epoch_ms)
                .map_err(|_| "capture_window_invalid".to_string())?;
            crate::dlog!(
                "[capture-export] handle_export: pts window {}..{} path={}",
                start_100ns,
                end_100ns,
                paths.mp4.display()
            );
            capture
                .request_replay_export(start_100ns, end_100ns, paths.mp4.clone())
                .map_err(|error| replay_failure_code(error.kind).to_string())?
        };
        crate::dlog!("[capture-export] handle_export: queued, waiting for mux worker");
        let receipt = self.wait_for_export(receiver)?;
        let record = ReceiptRecord::from_export(&request, receipt, &paths.mp4)?;
        record.write_atomic(&paths.receipt)?;
        crate::dlog!(
            "[capture-export] handle_export: receipt published {} elapsed_ms={}",
            paths.receipt.display(),
            started.elapsed().as_millis()
        );
        Ok(record)
    }

    fn release_capture_session(
        &self,
        capture_session_id: &str,
    ) -> Result<CaptureCoordinatorStatus, String> {
        let current = self.status();
        if current.phase != CapturePhase::Finalizing
            || current.capture_session_id.as_deref() != Some(capture_session_id)
        {
            return Err("capture_session_mismatch".to_string());
        }
        self.window_capture
            .lock()
            .map_err(|_| "window capture state is unavailable".to_string())?
            .stop();
        // finalizing 期间 raw 后端为收尾局的 snapshot 回执而保留，release 才是它的终点。
        let _ = self.raw_input.set_enabled(false);
        let (process_present, _hwnd) = find_kovaak_window().map_err(str::to_string)?;
        let waiting = CaptureCoordinatorStatus::after_release(process_present);
        self.replace_status(waiting.clone());
        Ok(waiting)
    }

    fn wait_for_export(
        &self,
        receiver: std::sync::mpsc::Receiver<
            Result<ReplayExportReceipt, crate::window_capture::ReplayExportFailure>,
        >,
    ) -> Result<ReplayExportReceipt, String> {
        let deadline = std::time::Instant::now() + CONTROL_EXPORT_TIMEOUT;
        let started = std::time::Instant::now();
        crate::dlog!("[capture-export] wait_for_export: begin");
        let mut draining = false;
        loop {
            if self.shutdown.load(Ordering::Acquire) && !draining {
                draining = true;
                crate::dlog!(
                    "[capture-export] wait_for_export: shutdown requested, draining in-flight mux until receipt or export timeout"
                );
            }
            let remaining = deadline.saturating_duration_since(std::time::Instant::now());
            if remaining.is_zero() {
                crate::dlog!("[capture-export] wait_for_export: timed out");
                return Err("capture_export_timed_out".to_string());
            }
            match receiver.recv_timeout(remaining.min(Duration::from_millis(50))) {
                Ok(Ok(receipt)) => {
                    crate::dlog!(
                        "[capture-export] wait_for_export: receipt packets={} elapsed_ms={}",
                        receipt.packet_count,
                        started.elapsed().as_millis()
                    );
                    return Ok(receipt);
                }
                Ok(Err(error)) => {
                    crate::dlog!(
                        "[capture-export] wait_for_export: mux failed kind={:?} elapsed_ms={}",
                        error.kind,
                        started.elapsed().as_millis()
                    );
                    return Err(replay_failure_code(error.kind).to_string());
                }
                Err(std::sync::mpsc::RecvTimeoutError::Timeout) => continue,
                Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
                    crate::dlog!(
                        "[capture-export] wait_for_export: mux worker dropped the channel"
                    );
                    return Err("capture_export_failed".to_string());
                }
            }
        }
    }

    pub fn shutdown(&self) {
        self.shutdown.store(true, Ordering::Release);
        if let Ok(mut control) = self.control.lock() {
            if let Some(server) = control.take() {
                server.shutdown();
            }
        }
        if let Ok(mut monitor) = self.monitor.lock() {
            if let Some(join) = monitor.take() {
                let _ = join.join();
            }
        }
    }
}

/// 在途导出记账的 RAII guard：handle_export 的导出段（排队 → mux →
/// receipt 落盘）持有一个，Drop 时计数 -1，panic/提前返回也不会漏减。
struct ReplayExportInFlightGuard {
    capture: Arc<Mutex<WindowCaptureState>>,
}

impl ReplayExportInFlightGuard {
    fn begin(capture: &Arc<Mutex<WindowCaptureState>>) -> Self {
        if let Ok(state) = capture.lock() {
            state.replay_export_begin();
        }
        Self {
            capture: Arc::clone(capture),
        }
    }
}

impl Drop for ReplayExportInFlightGuard {
    fn drop(&mut self) {
        if let Ok(state) = self.capture.lock() {
            state.replay_export_end();
        }
    }
}

fn replay_failure_code(kind: crate::window_capture::ReplayExportFailureKind) -> &'static str {
    use crate::window_capture::ReplayExportFailureKind;

    match kind {
        ReplayExportFailureKind::ExportBusy => "capture_export_busy",
        ReplayExportFailureKind::CaptureUnavailable => "capture_unavailable",
        ReplayExportFailureKind::InvalidWindow | ReplayExportFailureKind::WindowTooLong => {
            "capture_window_invalid"
        }
        ReplayExportFailureKind::MissingKeyframeCoverage
        | ReplayExportFailureKind::IncompleteCoverage
        | ReplayExportFailureKind::CoverageGap => "capture_coverage_gap",
        ReplayExportFailureKind::MissingCodecConfiguration
        | ReplayExportFailureKind::UnsupportedCodecProfile
        | ReplayExportFailureKind::UnsupportedBitstreamFormat
        | ReplayExportFailureKind::UnsupportedPacketTiming
        | ReplayExportFailureKind::InvalidSnapshot
        | ReplayExportFailureKind::TimelineOverflow => "capture_video_invalid",
        ReplayExportFailureKind::IoFailure | ReplayExportFailureKind::FinalizationFailure => {
            "capture_export_failed"
        }
    }
}

impl Drop for CaptureCoordinatorState {
    fn drop(&mut self) {
        self.shutdown();
    }
}

struct ControlServer {
    connection: CaptureControlConnection,
    shutdown: Arc<AtomicBool>,
    join: Option<JoinHandle<()>>,
    connection_joins: Arc<Mutex<Vec<JoinHandle<()>>>>,
}

// [capture-export] 诊断：GUI 子进程里 panic 输出通常进不了日志，
// catch_unwind 后用本函数还原 panic 消息打到 stderr。
fn panic_message(panic: Box<dyn std::any::Any + Send>) -> String {
    panic
        .downcast_ref::<&str>()
        .map(|message| (*message).to_string())
        .or_else(|| panic.downcast_ref::<String>().cloned())
        .unwrap_or_else(|| "unknown panic payload".to_string())
}

// 记录在途连接线程；已完成的句柄立即清理，避免长会话下无限增长。
fn track_control_connection_thread(
    connection_joins: &Mutex<Vec<JoinHandle<()>>>,
    join: JoinHandle<()>,
) {
    if let Ok(mut joins) = connection_joins.lock() {
        joins.retain(|join| !join.is_finished());
        joins.push(join);
    }
}

// 在 deadline 前等待每个在途连接收尾（导出最长 60s），超时的连接放弃
// 等待（句柄丢弃即脱离，线程随进程退出），保证退出不卡死。
fn join_control_connections(connection_joins: &Mutex<Vec<JoinHandle<()>>>, deadline: Instant) {
    let Ok(mut joins) = connection_joins.lock() else {
        return;
    };
    for join in joins.drain(..) {
        while !join.is_finished() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(20));
        }
        if join.is_finished() {
            let _ = join.join();
        }
    }
}

impl ControlServer {
    fn bind(coordinator: Weak<CaptureCoordinatorState>) -> Result<Self, String> {
        let listener = TcpListener::bind("127.0.0.1:0")
            .map_err(|error| format!("capture control loopback bind failed: {error}"))?;
        let address = listener
            .local_addr()
            .map_err(|error| format!("capture control local address failed: {error}"))?;
        if !address.ip().is_loopback() {
            return Err("capture control must bind loopback".to_string());
        }
        listener
            .set_nonblocking(true)
            .map_err(|error| format!("capture control nonblocking setup failed: {error}"))?;
        let connection = CaptureControlConnection {
            address,
            secret: create_ephemeral_secret(),
        };
        let shutdown = Arc::new(AtomicBool::new(false));
        let thread_shutdown = Arc::clone(&shutdown);
        let thread_connection = connection.clone();
        let connection_joins = Arc::new(Mutex::new(Vec::new()));
        let thread_connection_joins = Arc::clone(&connection_joins);
        let join = thread::Builder::new()
            .name("aiming-cookie-capture-control".to_string())
            .spawn(move || {
                while !thread_shutdown.load(Ordering::Acquire) {
                    match listener.accept() {
                        Ok((stream, _)) => {
                            // 每个连接独立线程处理：导出（最长 60s）不再独占
                            // accept 循环，status / release 始终能及时响应。
                            crate::dlog!(
                                "[capture-export] accept: {}",
                                stream
                                    .peer_addr()
                                    .map(|address| address.to_string())
                                    .unwrap_or_else(|_| "?".to_string())
                            );
                            let secret = thread_connection.secret.clone();
                            let coordinator = coordinator.clone();
                            let connection_shutdown = Arc::clone(&thread_shutdown);
                            match thread::Builder::new()
                                .name("aiming-cookie-capture-connection".to_string())
                                .spawn(move || {
                                    let result = std::panic::catch_unwind(
                                        std::panic::AssertUnwindSafe(|| {
                                            handle_control_connection(
                                                stream,
                                                &secret,
                                                coordinator,
                                                connection_shutdown,
                                            );
                                        }),
                                    );
                                    if let Err(panic) = result {
                                        crate::dlog!(
                                            "[capture-export] connection thread panicked: {}",
                                            panic_message(panic)
                                        );
                                    }
                                }) {
                                Ok(join) => {
                                    track_control_connection_thread(&thread_connection_joins, join)
                                }
                                // spawn 失败时请求尚未读取即丢弃连接：
                                // 对端 recv 表现为 10053 断连，必须显式记录。
                                Err(error) => {
                                    crate::dlog!(
                                        "[capture-export] connection spawn failed: {error}"
                                    );
                                }
                            }
                        }
                        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                            thread::sleep(Duration::from_millis(20));
                        }
                        Err(_) => break,
                    }
                }
            })
            .map_err(|error| format!("capture control startup failed: {error}"))?;
        Ok(Self {
            connection,
            shutdown,
            join: Some(join),
            connection_joins,
        })
    }

    fn connection(&self) -> CaptureControlConnection {
        self.connection.clone()
    }

    fn shutdown(mut self) {
        self.shutdown.store(true, Ordering::Release);
        if let Some(join) = self.join.take() {
            let _ = join.join();
        }
        // 先 join accept 线程保证不再有新连接，再等待在途连接收尾。
        join_control_connections(
            &self.connection_joins,
            Instant::now() + CONTROL_CONNECTION_JOIN_TIMEOUT,
        );
    }
}

fn handle_control_connection(
    mut stream: TcpStream,
    secret: &str,
    coordinator: Weak<CaptureCoordinatorState>,
    shutdown: Arc<AtomicBool>,
) {
    // 0930 提案 C：控制协议走持久长连接（业界标准做法，gRPC/Redis/CDP 同
    // 理）——部分用户机器上本机回环新建 TCP 会被安全软件随机 RST（实测
    // 8%~39%），一次一连的旧模式会让整局轨迹取不回。行协议、鉴权、单请求
    // 单响应语义全部不变，写完响应不关连接、继续读下一行，因此老客户端
    // （一请求即关）天然向后兼容：读完响应关闭连接后，服务端下一次 read
    // 得到干净 EOF，直接退出。
    //
    // 监听器为 accept 而设为非阻塞，Windows 上 accept 出来的 stream 会
    // 继承该模式——读超时对非阻塞 socket 无效（read 直接 WouldBlock）。
    // 持久连接必须真阻塞等下一个请求，这里显式切回阻塞模式。
    let _ = stream.set_nonblocking(false);
    let _ = stream.set_nodelay(true);
    let _ = stream.set_read_timeout(Some(CONTROL_READ_TIMEOUT));
    loop {
        let started = Instant::now();
        crate::dlog!("[capture-export] conn: reading request");
        let line = match read_control_line(&mut stream) {
            Ok(Some(line)) => line,
            Ok(None) => {
                crate::dlog!("[capture-export] conn: peer closed, cleaning up");
                break;
            }
            // 读超时是持久连接的空闲心跳，不是错误：不回写任何字节（避免
            // 打乱客户端严格的一问一答配对），继续等下一个请求；仅在进程
            // 收尾时借超时唤醒退出（最坏退出延迟 = 一个读超时）。
            Err(ControlReadError::Idle) => {
                if shutdown.load(Ordering::Acquire) {
                    break;
                }
                continue;
            }
            Err(ControlReadError::Failed(code)) => {
                crate::dlog!("[capture-export] conn: request rejected: {code}");
                let response = control_error_response("controlError", &code);
                write_control_response(&mut stream, &response, started);
                // 读侧错误（IO 错误/截断/超限）：行协议已无法可靠续读，
                // 错误响应写完即退出，避免半死连接空转。
                break;
            }
        };
        let request = match parse_control_request(&line, secret) {
            Ok(request) => request,
            Err(code) => {
                crate::dlog!("[capture-export] conn: request rejected: {code}");
                let response = control_error_response("controlError", &code);
                if !write_control_response(&mut stream, &response, started) {
                    break;
                }
                // 协议级拒绝（鉴权/格式/窗口）不终止持久连接：传输层健康，
                // 老客户端读完即关（下一次 read 得到干净 EOF 退出），新
                // 客户端可继续复用同一连接。
                continue;
            }
        };
        crate::dlog!("[capture-export] conn: request accepted: {request:?}");
        let response_type = response_type_for_request(&request);
        let result = match request {
            ControlRequest::Status => coordinator
                .upgrade()
                .map(|coordinator| {
                    serde_json::json!({
                        "type": "statusResult",
                        "ok": true,
                        "status": coordinator.status(),
                    })
                })
                .ok_or_else(|| "capture_unavailable".to_string()),
            ControlRequest::FlushRawSnapshot { capture_session_id } => coordinator
                .upgrade()
                .ok_or_else(|| "capture_unavailable".to_string())
                .and_then(|coordinator| {
                    coordinator
                        .flush_raw_snapshot(&capture_session_id)
                        .map(|snapshot| {
                            serde_json::json!({
                                "type": "flushRawSnapshotResult",
                                "ok": true,
                                "captureSessionId": capture_session_id,
                                "snapshot": snapshot,
                            })
                        })
                }),
            ControlRequest::ExportReplay(request) => coordinator
                .upgrade()
                .ok_or_else(|| "capture_unavailable".to_string())
                .and_then(|coordinator| {
                    coordinator.handle_export(request).map(|receipt| {
                        serde_json::json!({
                            "type": "exportReplayResult",
                            "ok": true,
                            "requestDigest": receipt.request_digest,
                            "captureSessionId": receipt.capture_session_id,
                            "requestedStartEpochMs": receipt.start_epoch_ms,
                            "requestedEndEpochMs": receipt.end_epoch_ms,
                            "replay": receipt.replay,
                            "file": receipt.file,
                        })
                    })
                }),
            ControlRequest::ReleaseCaptureSession { capture_session_id } => coordinator
                .upgrade()
                .ok_or_else(|| "capture_unavailable".to_string())
                .and_then(|coordinator| {
                    coordinator
                        .release_capture_session(&capture_session_id)
                        .map(|status| {
                            serde_json::json!({
                                "type": "releaseCaptureSessionResult",
                                "ok": true,
                                "status": status,
                            })
                        })
                }),
        };
        let response = result.unwrap_or_else(|code| {
            crate::dlog!("[capture-export] conn: request failed: {code}");
            control_error_response(response_type, &code)
        });
        if !write_control_response(&mut stream, &response, started) {
            break;
        }
    }
}

/// 写一个完整响应行（payload + '\n' + flush）。返回 false 表示响应没能
/// 送达（序列化失败或对端不可达），调用方必须终止连接。
fn write_control_response(
    stream: &mut TcpStream,
    response: &serde_json::Value,
    started: Instant,
) -> bool {
    let payload = match serde_json::to_vec(response) {
        Ok(payload) => payload,
        Err(error) => {
            crate::dlog!("[capture-export] conn: response serialize failed: {error}");
            return false;
        }
    };
    crate::dlog!(
        "[capture-export] conn: writing response type={} ok={} bytes={} elapsed_ms={}",
        response
            .get("type")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("?"),
        response
            .get("ok")
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false),
        payload.len(),
        started.elapsed().as_millis()
    );
    let write = stream
        .write_all(&payload)
        .and_then(|()| stream.write_all(b"\n"))
        .and_then(|()| stream.flush());
    if let Err(error) = write {
        crate::dlog!("[capture-export] conn: response write failed: {error}");
        return false;
    }
    true
}

fn response_type_for_request(request: &ControlRequest) -> &'static str {
    match request {
        ControlRequest::Status => "statusResult",
        ControlRequest::FlushRawSnapshot { .. } => "flushRawSnapshotResult",
        ControlRequest::ExportReplay(_) => "exportReplayResult",
        ControlRequest::ReleaseCaptureSession { .. } => "releaseCaptureSessionResult",
    }
}

fn control_error_response(response_type: &'static str, code: &str) -> serde_json::Value {
    let code = if response_type == "statusResult" {
        "capture_unavailable"
    } else {
        sanitize_code(code)
    };
    serde_json::json!({
        "type": response_type,
        "ok": false,
        "code": code,
    })
}

/// read_control_line 的读侧失败分类：Idle 是读超时（持久连接的空闲心跳，
/// 继续等下一个请求）；Failed 是真实读侧失败，code 保持线上错误码合同
/// （control_read_failed / control_message_invalid）。
#[derive(Debug, PartialEq, Eq)]
enum ControlReadError {
    Idle,
    Failed(String),
}

/// 读一行请求。Ok(None) 表示对端在本请求开始前干净关闭连接（持久连接
/// 的正常退出路径）；Ok(Some(line)) 是完整一行；Err 为空闲超时/截断/
/// 超限/IO 错误。
fn read_control_line(reader: &mut impl Read) -> Result<Option<Vec<u8>>, ControlReadError> {
    let mut line = Vec::new();
    let mut buffer = [0_u8; 1024];
    loop {
        let count = reader.read(&mut buffer).map_err(|error| {
            match error.kind() {
                // 阻塞 socket 的读超时（Windows 为 TimedOut）：空闲而非故障。
                std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut => {
                    ControlReadError::Idle
                }
                _ => {
                    // 线上错误码合同不变，只丰富日志：kind + raw_os_error
                    // 是定位安全软件随机 RST（10054 等）的关键证据。
                    crate::dlog!(
                        "[capture-export] control read failed: kind={:?} raw_os_error={:?}",
                        error.kind(),
                        error.raw_os_error()
                    );
                    ControlReadError::Failed("control_read_failed".to_string())
                }
            }
        })?;
        if count == 0 {
            if line.is_empty() {
                return Ok(None);
            }
            return Err(ControlReadError::Failed(
                "control_message_invalid".to_string(),
            ));
        }
        for (index, byte) in buffer[..count].iter().copied().enumerate() {
            if line.len() == CONTROL_MAX_MESSAGE_BYTES {
                return Err(ControlReadError::Failed(
                    "control_message_invalid".to_string(),
                ));
            }
            line.push(byte);
            if byte == b'\n' {
                if index + 1 != count {
                    return Err(ControlReadError::Failed(
                        "control_message_invalid".to_string(),
                    ));
                }
                return Ok(Some(line));
            }
        }
    }
}

fn parse_control_request(line: &[u8], expected_secret: &str) -> Result<ControlRequest, String> {
    if line.len() > CONTROL_MAX_MESSAGE_BYTES || line.last() != Some(&b'\n') {
        return Err("control_message_invalid".to_string());
    }
    let request: ControlRequestWire =
        serde_json::from_slice(line).map_err(|_| "control_message_invalid".to_string())?;
    match request {
        ControlRequestWire::Status { secret } => {
            if secret != expected_secret {
                return Err("control_auth_failed".to_string());
            }
            Ok(ControlRequest::Status)
        }
        ControlRequestWire::FlushRawSnapshot {
            secret,
            capture_session_id,
        } => {
            if secret != expected_secret {
                return Err("control_auth_failed".to_string());
            }
            if !is_strict_identifier(&capture_session_id, 8, 128) {
                return Err("control_message_invalid".to_string());
            }
            Ok(ControlRequest::FlushRawSnapshot { capture_session_id })
        }
        ControlRequestWire::ExportReplay {
            secret,
            request_id,
            run_id,
            capture_session_id,
            start_epoch_ms,
            end_epoch_ms,
        } => {
            if secret != expected_secret {
                return Err("control_auth_failed".to_string());
            }
            if run_id == 0
                || !is_strict_identifier(&request_id, 1, 64)
                || !is_strict_identifier(&capture_session_id, 8, 128)
            {
                return Err("control_message_invalid".to_string());
            }
            if end_epoch_ms <= start_epoch_ms {
                return Err("control_window_invalid".to_string());
            }
            Ok(ControlRequest::ExportReplay(ExportReplayRequest {
                request_id,
                run_id,
                capture_session_id,
                start_epoch_ms,
                end_epoch_ms,
            }))
        }
        ControlRequestWire::ReleaseCaptureSession {
            secret,
            capture_session_id,
        } => {
            if secret != expected_secret {
                return Err("control_auth_failed".to_string());
            }
            if !is_strict_identifier(&capture_session_id, 8, 128) {
                return Err("control_message_invalid".to_string());
            }
            Ok(ControlRequest::ReleaseCaptureSession { capture_session_id })
        }
    }
}

fn is_strict_identifier(value: &str, minimum: usize, maximum: usize) -> bool {
    value.len() >= minimum
        && value.len() <= maximum
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
}

fn managed_export_paths(
    data_root: &Path,
    run_id: u64,
    request_id: &str,
) -> Result<ManagedExportPaths, String> {
    if !data_root.is_absolute() || run_id == 0 || !is_strict_identifier(request_id, 1, 64) {
        return Err("managed_path_invalid".to_string());
    }
    fs::create_dir_all(data_root)
        .map_err(|error| format!("managed data root creation failed: {error}"))?;
    let canonical_data_root = data_root
        .canonicalize()
        .map_err(|error| format!("managed data root resolution failed: {error}"))?;
    let runs_root = data_root.join("runs");
    let run_root = runs_root.join(run_id.to_string());
    fs::create_dir_all(&run_root)
        .map_err(|error| format!("managed run directory creation failed: {error}"))?;
    let canonical_runs = runs_root
        .canonicalize()
        .map_err(|error| format!("managed runs root resolution failed: {error}"))?;
    if !canonical_runs.starts_with(&canonical_data_root) {
        return Err("managed_path_invalid".to_string());
    }
    let canonical_run = run_root
        .canonicalize()
        .map_err(|error| format!("managed run root resolution failed: {error}"))?;
    if !canonical_run.starts_with(&canonical_runs)
        || !canonical_run.starts_with(&canonical_data_root)
    {
        return Err("managed_path_invalid".to_string());
    }
    let stem = format!("video-{request_id}");
    Ok(ManagedExportPaths {
        mp4: canonical_run.join(format!("{stem}.mp4")),
        receipt: canonical_run.join(format!("{stem}.receipt.json")),
    })
}

fn sanitize_code(code: &str) -> &'static str {
    match code {
        "capture_unavailable" => "capture_unavailable",
        "control_read_failed" => "capture_unavailable",
        "capture_session_mismatch" => "capture_session_mismatch",
        "control_auth_failed" => "control_auth_failed",
        "control_message_invalid" => "control_message_invalid",
        "control_window_invalid" => "control_window_invalid",
        "capture_window_invalid" => "capture_window_invalid",
        "capture_coverage_gap" => "capture_coverage_gap",
        "capture_video_invalid" => "capture_video_invalid",
        "capture_export_busy" => "capture_export_busy",
        "capture_export_cancelled" => "capture_export_cancelled",
        "capture_export_timed_out" => "capture_export_timed_out",
        "raw_snapshot_busy" => "raw_snapshot_busy",
        "raw_snapshot_timed_out" => "raw_snapshot_timed_out",
        "raw_snapshot_failed" => "raw_snapshot_failed",
        "raw_snapshot_unavailable" => "raw_snapshot_unavailable",
        "managed_path_invalid" => "managed_path_invalid",
        _ => "capture_export_failed",
    }
}

fn create_ephemeral_secret() -> String {
    let mut bytes = [0_u8; 32];
    rand::rng().fill_bytes(&mut bytes);
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[cfg(windows)]
fn find_kovaak_window() -> Result<(bool, Option<usize>), &'static str> {
    use std::collections::HashSet;
    use std::mem::{size_of, zeroed};
    use winapi::shared::minwindef::{BOOL, DWORD, LPARAM, MAX_PATH};
    use winapi::shared::windef::HWND;
    use winapi::um::handleapi::{CloseHandle, INVALID_HANDLE_VALUE};
    use winapi::um::tlhelp32::{
        CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
        TH32CS_SNAPPROCESS,
    };
    use winapi::um::winuser::{
        EnumWindows, GetWindow, GetWindowThreadProcessId, IsWindowVisible, GW_OWNER,
    };

    unsafe {
        let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if snapshot == INVALID_HANDLE_VALUE {
            return Err("kovaak_process_probe_failed");
        }
        let mut entry: PROCESSENTRY32W = zeroed();
        entry.dwSize = size_of::<PROCESSENTRY32W>() as u32;
        let mut pids = HashSet::new();
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
                    pids.insert(entry.th32ProcessID);
                }
                if Process32NextW(snapshot, &mut entry) == 0 {
                    break;
                }
            }
        }
        CloseHandle(snapshot);
        if pids.is_empty() {
            return Ok((false, None));
        }
        struct WindowSearch {
            pids: HashSet<DWORD>,
            hwnd: Option<usize>,
        }
        unsafe extern "system" fn visit(hwnd: HWND, lparam: LPARAM) -> BOOL {
            let search = &mut *(lparam as *mut WindowSearch);
            if IsWindowVisible(hwnd) == 0 || !GetWindow(hwnd, GW_OWNER).is_null() {
                return 1;
            }
            let mut pid = 0;
            GetWindowThreadProcessId(hwnd, &mut pid);
            if search.pids.contains(&pid) {
                search.hwnd = Some(hwnd as usize);
                return 0;
            }
            1
        }
        let mut search = WindowSearch { pids, hwnd: None };
        EnumWindows(Some(visit), &mut search as *mut WindowSearch as LPARAM);
        Ok((true, search.hwnd))
    }
}

#[cfg(windows)]
fn is_current_kovaak_window(hwnd: usize) -> bool {
    matches!(find_kovaak_window(), Ok((true, Some(current))) if current == hwnd)
}

#[cfg(not(windows))]
fn find_kovaak_window() -> Result<(bool, Option<usize>), &'static str> {
    Ok((false, None))
}

#[cfg(not(windows))]
fn is_current_kovaak_window(_hwnd: usize) -> bool {
    false
}

#[cfg(test)]
mod tests {
    use super::{
        bounded_diagnostic_text, capture_clock_now_pts_100ns, capture_enabled_file_path,
        control_error_response, join_control_connections, load_capture_enabled_file,
        managed_export_paths, monitor_start_failure_status, parse_control_request,
        raw_health_restart_event, raw_health_restart_event_allowed, raw_snapshot_flush_allowed,
        read_control_line, replay_failure_code, resize_rebuild_block_reason,
        resized_video_degraded_status, response_type_for_request, sha256_hex,
        track_control_connection_thread, video_capture_failure_log_allowed,
        write_capture_enabled_file, CaptureCoordinatorState, CaptureCoordinatorStatus,
        CapturePhase, CaptureSourceState, CaptureSourceStatus, ControlRequest, ExportReplayRequest,
        FileFingerprint, ReceiptRecord, ResizeRebuildOutcome, StreamingSha256,
        CONTROL_MAX_MESSAGE_BYTES, RESIZE_REBUILD_MIN_INTERVAL, RESIZE_REBUILD_QUIET_DURATION,
    };
    use crate::window_capture::{
        CaptureClockMetadata, GeometryEvent, HardwareEncoderFailure, ReplayExportFailureKind,
        ReplayExportReceipt, WindowCaptureState, DEFAULT_FRAME_QUEUE_CAPACITY,
    };
    use std::fs;
    use std::io::{Cursor, Read, Write};
    use std::net::TcpStream;
    use std::sync::{Arc, Mutex};
    use std::time::{Duration, Instant};

    #[test]
    fn video_capture_failure_log_rate_limits_to_first_and_every_30s() {
        // 首条必发；30s 内的后续失败被吞；跨过 30s 门槛再放行一条。
        let now = Instant::now();
        assert!(video_capture_failure_log_allowed(None, now));
        assert!(!video_capture_failure_log_allowed(
            Some(now),
            now + std::time::Duration::from_secs(29)
        ));
        assert!(video_capture_failure_log_allowed(
            Some(now),
            now + std::time::Duration::from_secs(30)
        ));
        // 很久以前记过账同样放行（Instant 减法饱和，不会 panic）。
        assert!(video_capture_failure_log_allowed(
            Some(now),
            now + std::time::Duration::from_secs(3_600)
        ));
    }

    #[test]
    fn raw_health_restart_event_rate_limits_to_first_and_every_30s() {
        // 与失败 dlog 限频同参：6s 起搏的重启风暴每 30s 至多进一条事件。
        let now = Instant::now();
        assert!(raw_health_restart_event_allowed(None, now));
        assert!(!raw_health_restart_event_allowed(
            Some(now),
            now + std::time::Duration::from_secs(29)
        ));
        assert!(raw_health_restart_event_allowed(
            Some(now),
            now + std::time::Duration::from_secs(30)
        ));
        assert!(raw_health_restart_event_allowed(
            Some(now),
            now + std::time::Duration::from_secs(3_600)
        ));
    }

    #[test]
    fn raw_health_restart_event_carries_bare_reason_and_current_states() {
        // reason 必须是裸码（控制面合同 ^[a-z][a-z0-9_]{0,63}$），
        // 相位/子态原样取自重启决策时刻的协调器状态。
        let status = CaptureCoordinatorStatus {
            enabled: true,
            phase: CapturePhase::Capturing,
            capture_session_id: Some("session-1".to_string()),
            kovaak_process_present: true,
            window_handle: Some(0x1234),
            reason: None,
            raw: CaptureSourceStatus {
                state: CaptureSourceState::Capturing,
                reason: None,
            },
            video: CaptureSourceStatus {
                state: CaptureSourceState::Capturing,
                reason: None,
            },
        };
        let event = raw_health_restart_event(&status);
        assert_eq!(event.reason.as_deref(), Some("raw_health_restart"));
        assert!(event.timestamp_utc_ms > 0);
        assert_eq!(event.phase, CapturePhase::Capturing);
        assert!(event.kovaak_process_present);
        assert_eq!(event.raw_state, CaptureSourceState::Capturing);
        assert_eq!(event.video_state, CaptureSourceState::Capturing);
        // 会话密钥不得借道事件泄漏（诊断包隐私边界与 status 一致）。
        assert!(!serde_json::to_string(&event)
            .expect("event serializes")
            .contains("session-1"));
    }

    #[test]
    fn sha256_matches_known_vectors() {
        assert_eq!(
            sha256_hex(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert_eq!(
            sha256_hex(&[0x61_u8; 1_000_000]),
            "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"
        );
    }

    #[test]
    fn streaming_sha256_matches_one_shot() {
        let data: Vec<u8> = (0..=255_u8).cycle().take(100_003).collect();
        let mut streaming = StreamingSha256::new();
        for chunk in data.chunks(997) {
            streaming.update(chunk).expect("streaming update succeeds");
        }
        assert_eq!(
            streaming.finish().expect("streaming finish"),
            sha256_hex(&data)
        );
    }

    #[test]
    fn coordinator_defaults_disabled_and_waits_only_after_explicit_enable() {
        let disabled = CaptureCoordinatorStatus::disabled();
        assert_eq!(disabled.phase, CapturePhase::Disabled);
        assert!(!disabled.enabled);

        let waiting = disabled.after_enable(false, None);
        assert_eq!(waiting.phase, CapturePhase::WaitingForKovaak);
        assert!(waiting.enabled);
    }

    #[test]
    fn diagnostic_text_keeps_paths_and_is_bounded() {
        let value = r#"WGC startup failed at C:\Program Files\KovaaK\capture.dll"#;
        assert!(bounded_diagnostic_text(value).contains(r"C:\Program Files\KovaaK"));
        let long = "x".repeat(40 * 1024);
        assert!(bounded_diagnostic_text(&long).len() <= 32 * 1024 + 3);
        // 3 字节中文字符使字节 32K 处落在字符中间，必须回退到边界而不是 panic。
        let long_cjk = "袜".repeat(20 * 1024) + "x";
        let bounded_cjk = bounded_diagnostic_text(&long_cjk);
        assert!(bounded_cjk.len() <= 32 * 1024 + 3);
        assert!(bounded_cjk.ends_with("..."));
    }

    #[test]
    fn monitor_start_failure_rolls_back_enabled_state_for_an_explicit_retry() {
        let failed = monitor_start_failure_status();
        assert!(!failed.enabled);
        assert_eq!(failed.phase, CapturePhase::Error);
        assert_eq!(
            failed.reason.as_deref(),
            Some("capture_monitor_unavailable")
        );
        assert_eq!(failed.raw.state, CaptureSourceState::Unavailable);

        let retry = failed.after_enable(false, None);
        assert!(retry.enabled);
        assert_eq!(retry.phase, CapturePhase::WaitingForKovaak);
    }

    #[test]
    fn resized_video_only_degrades_the_video_substate_for_explicit_codes() {
        let capturing = CaptureCoordinatorStatus {
            enabled: true,
            phase: CapturePhase::Capturing,
            capture_session_id: Some("session-1".to_string()),
            kovaak_process_present: true,
            window_handle: Some(1),
            reason: None,
            raw: CaptureSourceStatus {
                state: CaptureSourceState::Capturing,
                reason: None,
            },
            video: CaptureSourceStatus {
                state: CaptureSourceState::Capturing,
                reason: None,
            },
        };

        // F6：尺寸漂移终态化只降级 video，phase/raw 保持 Capturing（不触发
        // 重启流程），下一局 release → start 链路自然恢复。
        let degraded = resized_video_degraded_status(&capturing, true).unwrap();
        assert_eq!(degraded.phase, CapturePhase::Capturing);
        assert_eq!(degraded.raw.state, CaptureSourceState::Capturing);
        assert_eq!(degraded.capture_session_id, capturing.capture_session_id);
        assert_eq!(degraded.video.state, CaptureSourceState::Degraded);
        assert!(degraded
            .video
            .reason
            .as_deref()
            .is_some_and(|reason| reason.starts_with("capture_resized_unsupported")));

        // 无终态化事件时不产生替换。
        assert!(resized_video_degraded_status(&capturing, false).is_none());
        // 幂等：已降级不再重复替换，tick 不刷事件流。
        assert!(resized_video_degraded_status(&degraded, true).is_none());
        // 非 Capturing 相位（如进程退出后的 Finalizing）不在此链路处理。
        let waiting = CaptureCoordinatorStatus::after_release(true);
        assert!(resized_video_degraded_status(&waiting, true).is_none());
    }

    #[test]
    fn process_exit_finalization_release_allows_a_new_capture_session() {
        let capturing = CaptureCoordinatorStatus {
            enabled: true,
            phase: CapturePhase::Capturing,
            capture_session_id: Some("session-1".to_string()),
            kovaak_process_present: true,
            window_handle: Some(1),
            reason: None,
            raw: CaptureSourceStatus {
                state: CaptureSourceState::Capturing,
                reason: None,
            },
            video: CaptureSourceStatus {
                state: CaptureSourceState::Capturing,
                reason: None,
            },
        };

        let finalizing = capturing.after_process_exit();
        assert_eq!(finalizing.phase, CapturePhase::Finalizing);
        assert_eq!(finalizing.capture_session_id.as_deref(), Some("session-1"));

        let waiting = CaptureCoordinatorStatus::after_release(false);
        assert_eq!(waiting.phase, CapturePhase::WaitingForKovaak);
        assert!(waiting.capture_session_id.is_none());
        assert_eq!(waiting.raw.state, CaptureSourceState::Waiting);
        assert_eq!(waiting.video.state, CaptureSourceState::Waiting);
    }

    #[test]
    fn control_request_rejects_bad_secret_shape_and_oversized_messages() {
        assert!(parse_control_request(b"not-json\n", "expected").is_err());
        assert!(parse_control_request(
            br#"{"type":"exportReplay","secret":"wrong","requestId":"a","runId":1,"captureSessionId":"session-1","startEpochMs":1,"endEpochMs":2}
"#,
            "expected",
        )
        .is_err());
        assert!(parse_control_request(&vec![b'x'; 16 * 1024 + 1], "expected").is_err());
    }

    #[test]
    fn control_protocol_supports_status_export_and_release_with_strict_shapes() {
        assert!(matches!(
            parse_control_request(
                br#"{"type":"status","secret":"expected"}
"#,
                "expected"
            ),
            Ok(ControlRequest::Status)
        ));
        let request = parse_control_request(
            br#"{"type":"exportReplay","secret":"expected","requestId":"request-1","runId":7,"captureSessionId":"session-1","startEpochMs":1000,"endEpochMs":2000}
"#,
            "expected",
        )
        .expect("valid request");
        let ControlRequest::ExportReplay(request) = request else {
            panic!("expected export request");
        };
        assert_eq!(request.run_id, 7);
        assert_eq!(request.request_id, "request-1");
        assert!(matches!(
            parse_control_request(
                br#"{"type":"releaseCaptureSession","secret":"expected","captureSessionId":"session-1"}
"#,
                "expected",
            ),
            Ok(ControlRequest::ReleaseCaptureSession { .. })
        ));
        assert!(parse_control_request(
            br#"{"type":"exportReplay","secret":"expected","requestId":"request-1","runId":7,"captureSessionId":"session-1","startEpochMs":1000,"endEpochMs":2000,"path":"C:\\escape.mp4"}
"#,
            "expected",
        )
        .is_err());
        assert!(parse_control_request(
            br#"{"type":"status","secret":"expected","secret":"expected"}
"#,
            "expected",
        )
        .is_err());
    }

    #[test]
    fn control_protocol_accepts_only_session_bound_raw_snapshot_flushes() {
        let request = parse_control_request(
            br#"{"type":"flushRawSnapshot","secret":"expected","captureSessionId":"session-1"}
"#,
            "expected",
        )
        .expect("valid session-bound Raw snapshot flush");
        assert!(matches!(
            request,
            ControlRequest::FlushRawSnapshot { ref capture_session_id }
                if capture_session_id == "session-1"
        ));
        assert_eq!(
            response_type_for_request(&request),
            "flushRawSnapshotResult"
        );

        assert!(parse_control_request(
            br#"{"type":"flushRawSnapshot","secret":"expected","captureSessionId":"session-1","path":"C:\\escape.bin"}
"#,
            "expected",
        )
        .is_err());
        assert!(parse_control_request(
            br#"{"type":"flushRawSnapshot","secret":"expected","captureSessionId":"session-1","unknown":true}
"#,
            "expected",
        )
        .is_err());
        assert!(parse_control_request(
            br#"{"type":"flushRawSnapshot","secret":"expected"}
"#,
            "expected",
        )
        .is_err());
    }

    #[test]
    fn control_errors_keep_the_request_response_type_and_hide_internal_details() {
        let status = ControlRequest::Status;
        let release = ControlRequest::ReleaseCaptureSession {
            capture_session_id: "session-1".to_string(),
        };
        assert_eq!(response_type_for_request(&status), "statusResult");
        assert_eq!(
            response_type_for_request(&release),
            "releaseCaptureSessionResult"
        );

        let response = control_error_response(
            response_type_for_request(&release),
            "C:\\private\\capture path leaked",
        );
        assert_eq!(response["type"], "releaseCaptureSessionResult");
        assert_eq!(response["ok"], false);
        assert_eq!(response["code"], "capture_export_failed");
        assert!(!response.to_string().contains("private"));

        let status_response = control_error_response(
            response_type_for_request(&status),
            "C:\\private\\capture status unavailable",
        );
        assert_eq!(status_response["type"], "statusResult");
        assert_eq!(status_response["ok"], false);
        assert_eq!(status_response["code"], "capture_unavailable");
        assert!(!status_response.to_string().contains("private"));

        let read_failure = control_error_response("controlError", "control_read_failed");
        assert_eq!(read_failure["type"], "controlError");
        assert_eq!(read_failure["code"], "capture_unavailable");

        let malformed = control_error_response("controlError", "control_message_invalid");
        assert_eq!(malformed["type"], "controlError");
        assert_eq!(malformed["code"], "control_message_invalid");

        for raw_code in [
            "raw_snapshot_busy",
            "raw_snapshot_timed_out",
            "raw_snapshot_failed",
            "raw_snapshot_unavailable",
        ] {
            let response = control_error_response("flushRawSnapshotResult", raw_code);
            assert_eq!(response["type"], "flushRawSnapshotResult");
            assert_eq!(response["code"], raw_code);
        }
    }

    #[test]
    fn replay_failures_preserve_terminal_coverage_and_window_codes() {
        assert_eq!(
            replay_failure_code(ReplayExportFailureKind::CoverageGap),
            "capture_coverage_gap"
        );
        assert_eq!(
            replay_failure_code(ReplayExportFailureKind::MissingKeyframeCoverage),
            "capture_coverage_gap"
        );
        assert_eq!(
            replay_failure_code(ReplayExportFailureKind::WindowTooLong),
            "capture_window_invalid"
        );
        assert_eq!(
            replay_failure_code(ReplayExportFailureKind::UnsupportedCodecProfile),
            "capture_video_invalid"
        );
        assert_eq!(
            replay_failure_code(ReplayExportFailureKind::FinalizationFailure),
            "capture_export_failed"
        );
    }

    #[test]
    fn control_reader_bounds_before_allocating_and_requires_newline() {
        // 干净 EOF（一个字节都没读）= 持久连接的正常退出路径，不再是错误。
        assert_eq!(
            read_control_line(&mut Cursor::new(Vec::new())).unwrap(),
            None
        );
        // 没有换行符的不完整消息仍是错误（连接在半途被杀不得当成干净 EOF）。
        assert!(read_control_line(&mut Cursor::new(b"{}".to_vec())).is_err());
        assert!(
            read_control_line(&mut Cursor::new(vec![b'x'; CONTROL_MAX_MESSAGE_BYTES + 1])).is_err()
        );
        assert_eq!(
            read_control_line(&mut Cursor::new(b"{}\n".to_vec())).unwrap(),
            Some(b"{}\n".to_vec())
        );
        assert!(read_control_line(&mut Cursor::new(br#"{"type":"status"}"#.to_vec())).is_err());
    }

    #[test]
    fn control_reader_maps_read_timeouts_to_idle_not_fatal() {
        use super::ControlReadError;
        // 阻塞 socket 的读超时（WouldBlock/TimedOut）是持久连接的空闲心跳，
        // 必须归为 Idle 而不是致命读错误；超时解除后同一 reader 继续可读。
        struct WouldBlockOnce {
            payload: Vec<u8>,
            blocked: bool,
        }
        impl Read for WouldBlockOnce {
            fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
                if self.blocked {
                    self.blocked = false;
                    return Err(std::io::Error::from(std::io::ErrorKind::WouldBlock));
                }
                let count = buffer.len().min(self.payload.len());
                buffer[..count].copy_from_slice(&self.payload[..count]);
                self.payload.drain(..count);
                Ok(count)
            }
        }
        let mut reader = WouldBlockOnce {
            payload: b"{}\n".to_vec(),
            blocked: true,
        };
        assert_eq!(read_control_line(&mut reader), Err(ControlReadError::Idle));
        assert_eq!(
            read_control_line(&mut reader).unwrap(),
            Some(b"{}\n".to_vec())
        );
    }

    #[test]
    fn managed_export_paths_are_contained_and_reject_traversal() {
        let root = std::env::temp_dir().join(format!(
            "aiming-cookie-coordinator-paths-{}",
            std::process::id()
        ));
        let paths = managed_export_paths(&root, 42, "request-1").expect("managed path");
        let canonical_run = root
            .join("runs")
            .join("42")
            .canonicalize()
            .expect("canonical run");
        assert!(paths.mp4.starts_with(&canonical_run));
        assert!(paths.receipt.starts_with(&canonical_run));
        assert!(managed_export_paths(&root, 42, "../escape").is_err());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn matching_receipt_is_idempotent_but_conflicting_digest_fails_closed() {
        let root = std::env::temp_dir().join(format!(
            "aiming-cookie-coordinator-receipt-{}",
            std::process::id()
        ));
        let paths = managed_export_paths(&root, 42, "request-1").expect("managed path");
        fs::create_dir_all(paths.mp4.parent().expect("parent")).expect("run directory");
        fs::write(&paths.mp4, b"mp4").expect("fixture mp4");
        let receipt = ReceiptRecord::fixture(ExportReplayRequest {
            request_id: "request-1".to_string(),
            run_id: 42,
            capture_session_id: "session-1".to_string(),
            start_epoch_ms: 1_000,
            end_epoch_ms: 2_000,
        });
        receipt.write_atomic(&paths.receipt).expect("receipt");
        assert!(ReceiptRecord::read_matching(&paths, &receipt).expect("matching receipt"));

        fs::write(&paths.mp4, b"changed").expect("tamper fixture mp4");
        assert!(ReceiptRecord::read_matching(&paths, &receipt).is_err());
        fs::write(&paths.mp4, b"mp4").expect("restore fixture mp4");
        fs::remove_file(&paths.mp4).expect("remove fixture mp4");
        assert!(!ReceiptRecord::read_matching(&paths, &receipt).expect("missing is not complete"));
        fs::write(&paths.mp4, b"mp4").expect("restore missing fixture mp4");

        let conflicting = ReceiptRecord::fixture(ExportReplayRequest {
            request_id: "request-1".to_string(),
            run_id: 42,
            capture_session_id: "session-1".to_string(),
            start_epoch_ms: 1_001,
            end_epoch_ms: 2_000,
        });
        assert!(ReceiptRecord::read_matching(&paths, &conflicting).is_err());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn receipt_record_serializes_geometry_events_inside_replay() {
        // 病灶 A：geometryEvents 必须落进 replay 对象内（Python 消费端的
        // replay 白名单已覆盖该位置），不得成为需要新白名单的顶层新键。
        let mut record = ReceiptRecord::fixture(ExportReplayRequest {
            request_id: "request-geometry".to_string(),
            run_id: 7,
            capture_session_id: "session-1".to_string(),
            start_epoch_ms: 1_000,
            end_epoch_ms: 2_000,
        });
        record.replay.geometry_events = vec![GeometryEvent {
            canonical_ms: 1_791_280_000_000,
            src_width: 1280,
            src_height: 800,
            dst_x: 0,
            dst_y: 0,
            dst_width: 2560,
            dst_height: 1600,
            scale: 2.0,
        }];
        let value: serde_json::Value = serde_json::to_value(&record).expect("serialize");
        let geometry = &value["replay"]["geometryEvents"];
        assert!(
            geometry.is_array(),
            "geometryEvents must live inside replay"
        );
        assert_eq!(
            geometry[0]["canonicalMs"].as_f64(),
            Some(1_791_280_000_000.0)
        );
        assert_eq!(geometry[0]["srcWidth"], 1280);
        assert_eq!(geometry[0]["dstX"], 0);
        assert_eq!(geometry[0]["dstHeight"], 1600);
        assert_eq!(geometry[0]["scale"].as_f64(), Some(2.0));
    }

    #[test]
    fn legacy_receipt_without_geometry_events_reads_back_as_empty() {
        // 历史落盘 receipt（无 geometryEvents 键）必须继续可读：
        // serde(default) 兜底为空数组，消费端按无变换处理。
        let record = ReceiptRecord::fixture(ExportReplayRequest {
            request_id: "request-legacy".to_string(),
            run_id: 8,
            capture_session_id: "session-1".to_string(),
            start_epoch_ms: 1_000,
            end_epoch_ms: 2_000,
        });
        let mut legacy = serde_json::to_value(&record).expect("serialize");
        legacy["replay"]
            .as_object_mut()
            .expect("replay object")
            .remove("geometryEvents");
        let read_back: ReceiptRecord =
            serde_json::from_value(legacy).expect("legacy receipt must deserialize");
        assert!(read_back.replay.geometry_events.is_empty());
    }

    #[test]
    fn from_export_forwards_geometry_events_from_the_export_receipt() {
        // export 返回值 → StoredReplayReceipt 的转发必须携带跟随事件，
        // 否则落盘 receipt 静默丢字段、Python letterbox 消费整链失效。
        let request = ExportReplayRequest {
            request_id: "request-forward".to_string(),
            run_id: 9,
            capture_session_id: "session-1".to_string(),
            start_epoch_ms: 1_000,
            end_epoch_ms: 2_000,
        };
        let export_receipt = ReplayExportReceipt {
            requested_start_100ns: 250,
            requested_end_100ns: 450,
            decode_start_100ns: 200,
            visible_duration_100ns: 200,
            decode_preroll_100ns: 50,
            packet_count: 2,
            encoded_bytes: 17,
            reencoded_frames: 0,
            tolerated_coverage_gaps: 0,
            capture_clock: CaptureClockMetadata {
                utc_epoch_ms: 1_700_000_000_000,
                qpc_ns: 5_000_000_000,
                clock_source: "utc_epoch_ms+qpc+wgc_system_relative_time",
                timebase_version: "time_alignment.v2",
            },
            geometry_events: vec![GeometryEvent {
                canonical_ms: 1_791_280_000_000,
                src_width: 1280,
                src_height: 800,
                dst_x: 0,
                dst_y: 0,
                dst_width: 2560,
                dst_height: 1600,
                scale: 2.0,
            }],
        };
        let mp4 = std::env::temp_dir().join(format!(
            "aiming-cookie-receipt-forward-{}.mp4",
            std::process::id()
        ));
        fs::write(&mp4, b"mp4").expect("fixture mp4");
        let record = ReceiptRecord::from_export(&request, export_receipt, &mp4).expect("record");
        assert_eq!(record.replay.geometry_events.len(), 1);
        assert_eq!(
            record.replay.geometry_events[0].canonical_ms,
            1_791_280_000_000
        );
        assert_eq!((record.replay.geometry_events[0].src_width), 1280);
        let _ = fs::remove_file(mp4);
    }

    #[test]
    fn file_fingerprint_streams_large_files_without_changing_the_digest() {
        let path = std::env::temp_dir().join(format!(
            "aiming-cookie-coordinator-fingerprint-{}",
            std::process::id()
        ));
        let bytes = vec![0x5a; 256 * 1024 + 17];
        fs::write(&path, &bytes).expect("write fixture");
        assert_eq!(
            FileFingerprint::from_file(&path).expect("stream fingerprint"),
            FileFingerprint::from_bytes(&bytes),
        );
        let _ = fs::remove_file(path);
    }

    #[test]
    fn control_shutdown_waits_for_inflight_connections_but_never_blocks_forever() {
        let joins = std::sync::Mutex::new(Vec::new());
        // track 每次记录都会清理已完成的句柄，finished 线程必须确定性地
        // 活到两次 track 之后，len 断言才不依赖线程调度时序。
        let (release, released) = std::sync::mpsc::channel::<()>();
        let finished = std::thread::spawn(move || {
            let _ = released.recv();
        });
        track_control_connection_thread(&joins, finished);
        let stalled = std::thread::spawn(|| std::thread::sleep(std::time::Duration::from_secs(5)));
        track_control_connection_thread(&joins, stalled);
        assert_eq!(joins.lock().expect("tracked joins").len(), 2);

        let _ = release.send(());
        let started = std::time::Instant::now();
        join_control_connections(&joins, started + std::time::Duration::from_millis(200));
        // stalled 连接超时后被放弃，等待时间以 deadline 为硬上限。
        assert!(started.elapsed() < std::time::Duration::from_secs(2));
        assert!(joins.lock().expect("drained joins").is_empty());
    }

    #[test]
    fn control_connection_tracking_drops_finished_handles_to_stay_bounded() {
        let joins = std::sync::Mutex::new(Vec::new());
        let short = std::thread::spawn(|| {});
        track_control_connection_thread(&joins, short);
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
        while !joins
            .lock()
            .expect("tracked joins")
            .first()
            .expect("tracked handle")
            .is_finished()
        {
            assert!(
                std::time::Instant::now() < deadline,
                "tracked connection never finished"
            );
            std::thread::sleep(std::time::Duration::from_millis(5));
        }

        let stalled = std::thread::spawn(|| std::thread::sleep(std::time::Duration::from_secs(5)));
        track_control_connection_thread(&joins, stalled);
        // 已完成的句柄在下一次记录时被清理，只剩活跃连接。
        assert_eq!(joins.lock().expect("live joins").len(), 1);
    }

    #[test]
    fn raw_only_degradation_and_finalizing_release_preserve_session_boundaries() {
        let degraded = CaptureCoordinatorStatus::raw_only_degraded("session-1".to_string());
        assert_eq!(degraded.phase, CapturePhase::Degraded);
        assert_eq!(degraded.capture_session_id.as_deref(), Some("session-1"));
        assert_eq!(degraded.raw.state, CaptureSourceState::Capturing);
        assert_eq!(degraded.video.state, CaptureSourceState::Waiting);

        let finalizing = degraded.after_process_exit();
        assert_eq!(finalizing.phase, CapturePhase::Finalizing);
        assert_eq!(finalizing.capture_session_id.as_deref(), Some("session-1"));
        assert_eq!(finalizing.raw.state, CaptureSourceState::Finalizing);

        let released = CaptureCoordinatorStatus::after_release(false);
        assert_eq!(released.phase, CapturePhase::WaitingForKovaak);
        assert!(released.capture_session_id.is_none());
    }

    #[test]
    fn snapshot_flush_is_allowed_through_finalizing_but_not_after_release() {
        // 收尾局：phase/raw 任一进入 finalizing 仍要能取回覆盖回执。
        assert!(raw_snapshot_flush_allowed(
            CapturePhase::Finalizing,
            CaptureSourceState::Capturing,
        ));
        assert!(raw_snapshot_flush_allowed(
            CapturePhase::Finalizing,
            CaptureSourceState::Finalizing,
        ));
        assert!(raw_snapshot_flush_allowed(
            CapturePhase::Capturing,
            CaptureSourceState::Capturing,
        ));
        assert!(raw_snapshot_flush_allowed(
            CapturePhase::Degraded,
            CaptureSourceState::Capturing,
        ));
        // release 之后（waiting_for_kovaak / raw waiting）不再有可取的会话缓冲。
        assert!(!raw_snapshot_flush_allowed(
            CapturePhase::WaitingForKovaak,
            CaptureSourceState::Waiting,
        ));
        assert!(!raw_snapshot_flush_allowed(
            CapturePhase::Finalizing,
            CaptureSourceState::Waiting,
        ));
        assert!(!raw_snapshot_flush_allowed(
            CapturePhase::Disabled,
            CaptureSourceState::Disabled,
        ));
    }

    #[test]
    fn capture_enabled_state_roundtrips_and_leaves_no_temp_artifacts() {
        let root = std::env::temp_dir().join(format!(
            "aiming-cookie-coordinator-capture-enabled-{}",
            std::process::id()
        ));
        // 缺省关：首启动没有持久化文件时保持 disabled。
        assert_eq!(load_capture_enabled_file(&root), Ok(None));
        write_capture_enabled_file(&root, true).expect("persist enabled");
        assert_eq!(load_capture_enabled_file(&root), Ok(Some(true)));
        // 用户显式关闭也是要记住的状态。
        write_capture_enabled_file(&root, false).expect("persist disabled");
        assert_eq!(load_capture_enabled_file(&root), Ok(Some(false)));
        // 原子写不残留临时文件：目录里只有 capture-enabled.json 本身。
        let entries: Vec<String> = fs::read_dir(&root)
            .expect("read root")
            .filter_map(Result::ok)
            .map(|entry| entry.file_name().to_string_lossy().into_owned())
            .collect();
        assert_eq!(
            entries,
            vec![capture_enabled_file_path(&root)
                .file_name()
                .expect("name")
                .to_string_lossy()
                .into_owned()]
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn corrupted_capture_enabled_state_falls_back_to_disabled() {
        let root = std::env::temp_dir().join(format!(
            "aiming-cookie-coordinator-capture-corrupt-{}",
            std::process::id()
        ));
        fs::create_dir_all(&root).expect("mkdir");
        let path = capture_enabled_file_path(&root);
        for payload in [
            &b"{not json"[..],
            b"",
            // 合法 JSON 但缺 enabled 字段同样不可解析。
            b"{}",
            // 类型不对的 enabled 也按损坏处理，而不是 panic 或误判为开。
            br#"{"enabled":"yes"}"#,
        ] {
            fs::write(&path, payload).expect("write corrupt fixture");
            assert!(
                load_capture_enabled_file(&root).is_err(),
                "corrupt payload must be rejected: {payload:?}"
            );
        }
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn coordinator_reason_codes_stay_machine_readable() {
        // 0929 合同违规整改的回归锁：Python 控制面校验器要求 reason 是纯
        // snake_case 错误码（^[a-z][a-z0-9_]{0,63}$）。带文案的拼接 reason
        // 曾使后端判 schema_invalid，连锁导致死会话不释放、后续每局视频
        // 轨迹全灭（线上报障）。禁止这两个 code 再以 format! 拼接形态出现。
        let source = std::fs::read_to_string(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/src/capture_coordinator.rs"
        ))
        .expect("read own source");
        assert!(
            !source.contains("format!(\"raw_input_unavailable"),
            "raw_input_unavailable reason must stay a bare code (no message suffix)"
        );
        assert!(
            !source.contains("format!(\"video_capture_unavailable"),
            "video_capture_unavailable reason must stay a bare code (no message suffix)"
        );
        assert!(source.contains("\"raw_input_unavailable\".to_string()"));
        assert!(source.contains("\"video_capture_unavailable\".to_string()"));
    }

    // ---- 尺寸重建（0930 拍板）与持久控制连接的测试 ----

    fn test_coordinator(label: &str) -> Arc<CaptureCoordinatorState> {
        let data_root = std::env::temp_dir().join(format!(
            "aiming-cookie-coordinator-{label}-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&data_root);
        let raw = crate::raw_input::RawInputState::new(data_root.join("raw-input.bin"));
        let window_capture =
            WindowCaptureState::new(DEFAULT_FRAME_QUEUE_CAPACITY).expect("window capture state");
        CaptureCoordinatorState::new(
            data_root,
            Arc::new(raw),
            Arc::new(Mutex::new(window_capture)),
        )
        .expect("coordinator")
    }

    fn capturing_status_fixture(session: &str) -> CaptureCoordinatorStatus {
        CaptureCoordinatorStatus {
            enabled: true,
            phase: CapturePhase::Capturing,
            capture_session_id: Some(session.to_string()),
            kovaak_process_present: true,
            window_handle: Some(0x1234),
            reason: None,
            raw: CaptureSourceStatus {
                state: CaptureSourceState::Capturing,
                reason: None,
            },
            video: CaptureSourceStatus {
                state: CaptureSourceState::Capturing,
                reason: None,
            },
        }
    }

    /// 把 F6 终态化标志 + 「31s 前的最后一个 packet」一起种进队列探针，
    /// 使重建四门中除限频外的门全部满足。
    fn seed_terminated_and_quiet(coordinator: &CaptureCoordinatorState) {
        let now_pts = capture_clock_now_pts_100ns().expect("capture clock");
        let quiet_pts = now_pts - 31 * 10_000_000;
        coordinator
            .window_capture
            .lock()
            .expect("window capture state")
            .set_resize_rebuild_probe_for_test(
                Some(HardwareEncoderFailure::CaptureResizedUnsupported),
                Some(quiet_pts),
            );
    }

    #[test]
    fn resize_rebuild_gate_blocks_until_every_condition_is_safe() {
        // 纯判定契约：四门必须同时满足才放行；任一门单独不满足都以显式
        // 裸码阻塞（只进 dlog，不进控制面 reason 合同）。
        let quiet_boundary =
            i64::try_from(RESIZE_REBUILD_QUIET_DURATION.as_nanos() / 100).expect("fits i64");
        let quiet = quiet_boundary + 1;
        assert_eq!(resize_rebuild_block_reason(false, Some(quiet), None), None);

        assert_eq!(
            resize_rebuild_block_reason(true, Some(quiet), None),
            Some("resize_rebuild_export_in_flight")
        );
        assert_eq!(
            resize_rebuild_block_reason(false, None, None),
            Some("resize_rebuild_buffer_unquiet")
        );
        // 差 100ns 不满 30s：按不安静处理（年龄按 ≥ 判安静）。
        assert_eq!(
            resize_rebuild_block_reason(false, Some(quiet_boundary - 1), None),
            Some("resize_rebuild_buffer_unquiet")
        );
        // 负年龄意味着时基错位，同样按不安静处理。
        assert_eq!(
            resize_rebuild_block_reason(false, Some(-1), None),
            Some("resize_rebuild_buffer_unquiet")
        );
        assert_eq!(
            resize_rebuild_block_reason(
                false,
                Some(quiet),
                Some(RESIZE_REBUILD_MIN_INTERVAL - Duration::from_millis(1)),
            ),
            Some("resize_rebuild_rate_limited")
        );
        // 恰好达到 ≥10s 限频门槛即放行。
        assert_eq!(
            resize_rebuild_block_reason(false, Some(quiet), Some(RESIZE_REBUILD_MIN_INTERVAL)),
            None
        );
    }

    #[test]
    fn resize_rebuild_restarts_capture_and_preserves_the_session_identity() {
        let coordinator = test_coordinator("resize-rebuild");
        let current = capturing_status_fixture("session-1");
        seed_terminated_and_quiet(&coordinator);
        assert!(coordinator
            .window_capture
            .lock()
            .expect("window capture state")
            .recording_terminated_by_resize());

        coordinator.report_or_rebuild_resized_video(&current, Some(0x1234), |capture, _hwnd| {
            // 仿真真实 start 入口对队列的效果（start 会 reset 队列，F6 标志
            // 随之清除，见 window_capture::start 与
            // resize_termination_surfaces_explicit_code_until_queue_reset）。
            capture.stop();
            capture.set_resize_rebuild_probe_for_test(None, None);
            Ok(capture.status())
        });

        // 重建成功：phase/raw/session 完全不动，video 回 capturing。
        let status = coordinator.status();
        assert_eq!(status.phase, CapturePhase::Capturing);
        assert_eq!(status.capture_session_id.as_deref(), Some("session-1"));
        assert_eq!(status.raw.state, CaptureSourceState::Capturing);
        assert_eq!(status.video.state, CaptureSourceState::Capturing);
        assert_eq!(status.video.reason, None);
        // F6 终态标志随重建（start 的队列 reset）清除，不跨重建粘连。
        assert!(!coordinator
            .window_capture
            .lock()
            .expect("window capture state")
            .recording_terminated_by_resize());
        // 限频锚点已按尝试记账。
        assert!(coordinator.resize_rebuild_rate_limit_elapsed().is_some());
    }

    #[test]
    fn resize_rebuild_defers_while_a_replay_export_is_in_flight() {
        let coordinator = test_coordinator("resize-rebuild-defer");
        {
            let capture = coordinator.window_capture.lock().expect("window capture");
            capture.replay_export_begin();
        }
        let outcome = coordinator.attempt_resize_rebuild(0x1234, |_capture, _hwnd| {
            panic!("restart must not run while a replay export is in flight");
        });
        assert_eq!(
            outcome,
            ResizeRebuildOutcome::Deferred("replay_export_in_flight")
        );
        // 推迟不消耗限频配额：导出收尾后下一 tick 立即可重建。
        assert!(coordinator.resize_rebuild_rate_limit_elapsed().is_none());
    }

    #[test]
    fn resize_rebuild_failure_falls_back_to_the_existing_degraded_path() {
        let coordinator = test_coordinator("resize-rebuild-fail");
        let current = capturing_status_fixture("session-1");
        seed_terminated_and_quiet(&coordinator);

        coordinator.report_or_rebuild_resized_video(&current, Some(0x1234), |_capture, _hwnd| {
            Err("window capture startup timed out".to_string())
        });

        // 复用现有 video start 失败语义（phase 降级 → 主路径逐 tick 重试
        // start；session id 不变；reason 保持既有裸码，不发明新码）。
        let status = coordinator.status();
        assert_eq!(status.phase, CapturePhase::Degraded);
        assert_eq!(status.capture_session_id.as_deref(), Some("session-1"));
        assert_eq!(status.raw.state, CaptureSourceState::Capturing);
        assert_eq!(status.video.state, CaptureSourceState::Degraded);
        assert_eq!(
            status.video.reason.as_deref(),
            Some("video_capture_unavailable")
        );
        // 失败的尝试同样消耗限频配额。
        assert!(coordinator.resize_rebuild_rate_limit_elapsed().is_some());
    }

    #[test]
    fn resize_rebuild_rate_limits_successive_attempts_within_ten_seconds() {
        let coordinator = test_coordinator("resize-rebuild-rate");
        let current = capturing_status_fixture("session-1");
        seed_terminated_and_quiet(&coordinator);
        let restarts = std::cell::Cell::new(0);

        coordinator.report_or_rebuild_resized_video(&current, Some(0x1234), |capture, _hwnd| {
            restarts.set(restarts.get() + 1);
            capture.set_resize_rebuild_probe_for_test(None, None);
            Ok(capture.status())
        });
        assert_eq!(restarts.get(), 1);

        // 10s 内再次 resize（复种 F6 标志与安静 packet 锚）：限频门拦截，
        // 不得在拖窗口边框的连续 resize 下打爆重建。
        seed_terminated_and_quiet(&coordinator);
        coordinator.report_or_rebuild_resized_video(
            &coordinator.status(),
            Some(0x1234),
            |capture, _hwnd| {
                restarts.set(restarts.get() + 1);
                Ok(capture.status())
            },
        );
        assert_eq!(restarts.get(), 1, "rate limit must suppress the retry");
        // 限频阻塞轮里 video 保持降级可见（等待下一 tick 重估）。
        assert_eq!(
            coordinator.status().video.state,
            CaptureSourceState::Degraded
        );
    }

    #[test]
    fn resize_following_reports_video_reason_while_capturing() {
        let coordinator = test_coordinator("resize-follow-reason");
        let current = capturing_status_fixture("session-1");
        coordinator
            .window_capture
            .lock()
            .expect("window capture state")
            .set_resize_following_for_test(true);

        coordinator.report_following_resize(&current);
        let status = coordinator.status();
        assert_eq!(status.phase, CapturePhase::Capturing);
        assert_eq!(status.video.state, CaptureSourceState::Capturing);
        assert_eq!(
            status.video.reason.as_deref(),
            Some("capture_resized_following")
        );

        // tick 幂等：跟随已标注时不再刷事件流。
        let events_before = coordinator.diagnostic_events().len();
        coordinator.report_following_resize(&status);
        assert_eq!(coordinator.diagnostic_events().len(), events_before);
    }

    #[test]
    fn resize_following_clears_reason_when_window_returns_to_session_size() {
        let coordinator = test_coordinator("resize-follow-clear");
        let mut current = capturing_status_fixture("session-1");
        current.video.reason = Some("capture_resized_following".to_string());
        coordinator
            .window_capture
            .lock()
            .expect("window capture state")
            .set_resize_following_for_test(false);

        coordinator.report_following_resize(&current);
        let status = coordinator.status();
        assert_eq!(status.phase, CapturePhase::Capturing);
        assert_eq!(status.video.state, CaptureSourceState::Capturing);
        assert_eq!(status.video.reason, None);
    }

    #[test]
    fn resize_following_defers_to_terminated_resize_status() {
        // 终态化在场：跟随报告不得触碰 video 状态（degraded/rebuild 路径
        // 完全不动，两者不得互相覆盖）。
        let coordinator = test_coordinator("resize-follow-terminated");
        let current = capturing_status_fixture("session-1");
        coordinator.replace_status(current.clone());
        {
            let capture = coordinator.window_capture.lock().expect("window capture");
            capture.set_resize_following_for_test(true);
            capture.set_resize_rebuild_probe_for_test(
                Some(HardwareEncoderFailure::CaptureResizedUnsupported),
                None,
            );
        }
        assert!(coordinator
            .window_capture
            .lock()
            .expect("window capture state")
            .recording_terminated_by_resize());

        coordinator.report_following_resize(&current);
        let status = coordinator.status();
        assert_eq!(status.video.state, CaptureSourceState::Capturing);
        assert_eq!(status.video.reason, None);
    }

    fn read_tcp_line(stream: &mut TcpStream) -> Vec<u8> {
        let mut line = Vec::new();
        let mut buffer = [0_u8; 1024];
        loop {
            let count = stream.read(&mut buffer).expect("read response");
            assert!(count > 0, "peer closed before a full response line");
            for (index, byte) in buffer[..count].iter().copied().enumerate() {
                line.push(byte);
                if byte == b'\n' {
                    assert_eq!(index + 1, count, "unexpected bytes after the newline");
                    return line;
                }
            }
        }
    }

    #[test]
    fn control_connection_serves_multiple_requests_on_one_stream() {
        let coordinator = test_coordinator("control-persistent");
        let connection = coordinator.control_connection().expect("control server");
        let mut stream = TcpStream::connect(connection.address).expect("connect");
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .expect("read timeout");
        stream
            .set_write_timeout(Some(Duration::from_secs(5)))
            .expect("write timeout");
        let request = format!(
            "{{\"type\":\"status\",\"secret\":\"{}\"}}\n",
            connection.secret
        );

        // 持久连接新行为：同一条 TcpStream 上连发两个请求，得到两个独立
        // 响应（一次一连的旧实现会在首个响应后关闭，第二个读必为 EOF）。
        for _ in 0..2 {
            stream.write_all(request.as_bytes()).expect("write request");
            let response: serde_json::Value =
                serde_json::from_slice(&read_tcp_line(&mut stream)).expect("response json");
            assert_eq!(response["type"], "statusResult");
            assert_eq!(response["ok"], true);
        }

        // 协议级拒绝（错误 secret）不终止持久连接：连接仍继续服务下一请求。
        stream
            .write_all(b"{\"type\":\"status\",\"secret\":\"wrong\"}\n")
            .expect("write rejected request");
        let rejected: serde_json::Value =
            serde_json::from_slice(&read_tcp_line(&mut stream)).expect("rejected json");
        assert_eq!(rejected["type"], "controlError");
        assert_eq!(rejected["code"], "control_auth_failed");

        stream.write_all(request.as_bytes()).expect("write request");
        let response: serde_json::Value =
            serde_json::from_slice(&read_tcp_line(&mut stream)).expect("response json");
        assert_eq!(response["type"], "statusResult");
        assert_eq!(response["ok"], true);

        drop(stream);
        coordinator.shutdown();
    }

    #[test]
    fn control_connection_cleans_up_after_legacy_single_request_client() {
        let coordinator = test_coordinator("control-legacy");
        let connection = coordinator.control_connection().expect("control server");
        {
            // 老客户端行为：一请求一连接，读完响应立即关闭。服务端必须在
            // 干净 EOF 后静默清理连接线程，不影响后续连接。
            let mut stream = TcpStream::connect(connection.address).expect("connect");
            stream
                .set_read_timeout(Some(Duration::from_secs(5)))
                .expect("read timeout");
            let request = format!(
                "{{\"type\":\"status\",\"secret\":\"{}\"}}\n",
                connection.secret
            );
            stream.write_all(request.as_bytes()).expect("write request");
            let response: serde_json::Value =
                serde_json::from_slice(&read_tcp_line(&mut stream)).expect("response json");
            assert_eq!(response["type"], "statusResult");
            assert_eq!(response["ok"], true);
        }
        // 已关闭的连接不得拖住退出：shutdown 在连接线程 EOF 退出后立即完成。
        let started = Instant::now();
        coordinator.shutdown();
        assert!(
            started.elapsed() < Duration::from_secs(10),
            "shutdown must not be wedged by a closed legacy connection"
        );
    }
}
