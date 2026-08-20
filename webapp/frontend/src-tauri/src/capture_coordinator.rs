use crate::raw_input::{RawInputState, SnapshotBarrierReceipt};
use crate::window_capture::{CaptureClockMetadata, ReplayExportReceipt, WindowCaptureState};
use rand::RngCore;
use serde::{Deserialize, Serialize};
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
const DIAGNOSTIC_EVENT_LIMIT: usize = 64;

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

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
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

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
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
    const INITIAL: [u32; 8] = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab,
        0x5be0cd19,
    ];
    const K: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
        0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
        0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
        0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
        0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
        0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
        0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
        0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
        0xc67178f2,
    ];
    let bit_length = (bytes.len() as u128).saturating_mul(8);
    let mut padded = bytes.to_vec();
    padded.push(0x80);
    while !(padded.len() + 8).is_multiple_of(64) {
        padded.push(0);
    }
    padded.extend_from_slice(&(bit_length as u64).to_be_bytes());
    let mut hash = INITIAL;
    for block in padded.chunks_exact(64) {
        let mut words = [0_u32; 64];
        for (index, chunk) in block.chunks_exact(4).take(16).enumerate() {
            words[index] = u32::from_be_bytes(chunk.try_into().expect("word length"));
        }
        for index in 16..64 {
            let s0 = words[index - 15].rotate_right(7)
                ^ words[index - 15].rotate_right(18)
                ^ (words[index - 15] >> 3);
            let s1 = words[index - 2].rotate_right(17)
                ^ words[index - 2].rotate_right(19)
                ^ (words[index - 2] >> 10);
            words[index] = words[index - 16]
                .wrapping_add(s0)
                .wrapping_add(words[index - 7])
                .wrapping_add(s1);
        }
        let mut work = hash;
        for index in 0..64 {
            let s1 = work[4].rotate_right(6) ^ work[4].rotate_right(11) ^ work[4].rotate_right(25);
            let choose = (work[4] & work[5]) ^ ((!work[4]) & work[6]);
            let temporary_one = work[7]
                .wrapping_add(s1)
                .wrapping_add(choose)
                .wrapping_add(K[index])
                .wrapping_add(words[index]);
            let s0 = work[0].rotate_right(2) ^ work[0].rotate_right(13) ^ work[0].rotate_right(22);
            let majority = (work[0] & work[1]) ^ (work[0] & work[2]) ^ (work[1] & work[2]);
            let temporary_two = s0.wrapping_add(majority);
            work = [
                temporary_one.wrapping_add(temporary_two),
                work[0],
                work[1],
                work[2],
                work[3].wrapping_add(temporary_one),
                work[4],
                work[5],
                work[6],
            ];
        }
        for index in 0..8 {
            hash[index] = hash[index].wrapping_add(work[index]);
        }
    }
    hash.iter().map(|word| format!("{word:08x}")).collect()
}

struct StreamingSha256 {
    state: [u32; 8],
    buffered: [u8; 64],
    buffered_len: usize,
    total_len: u64,
}

impl StreamingSha256 {
    fn new() -> Self {
        Self {
            state: [
                0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab,
                0x5be0cd19,
            ],
            buffered: [0; 64],
            buffered_len: 0,
            total_len: 0,
        }
    }

    fn update(&mut self, mut bytes: &[u8]) -> Result<(), String> {
        self.total_len = self
            .total_len
            .checked_add(u64::try_from(bytes.len()).map_err(|_| "capture artifact size overflow")?)
            .ok_or_else(|| "capture artifact size overflow".to_string())?;
        if self.buffered_len > 0 {
            let copied = (64 - self.buffered_len).min(bytes.len());
            self.buffered[self.buffered_len..self.buffered_len + copied]
                .copy_from_slice(&bytes[..copied]);
            self.buffered_len += copied;
            bytes = &bytes[copied..];
            if self.buffered_len == 64 {
                sha256_compress(&mut self.state, &self.buffered);
                self.buffered_len = 0;
            }
        }
        while bytes.len() >= 64 {
            let (block, remainder) = bytes.split_at(64);
            sha256_compress(
                &mut self.state,
                block.try_into().expect("SHA-256 block has fixed length"),
            );
            bytes = remainder;
        }
        self.buffered[..bytes.len()].copy_from_slice(bytes);
        self.buffered_len = bytes.len();
        Ok(())
    }

    fn finish(mut self) -> Result<String, String> {
        let bit_length = self
            .total_len
            .checked_mul(8)
            .ok_or_else(|| "capture artifact size overflow".to_string())?;
        self.buffered[self.buffered_len] = 0x80;
        self.buffered_len += 1;
        if self.buffered_len > 56 {
            self.buffered[self.buffered_len..].fill(0);
            sha256_compress(&mut self.state, &self.buffered);
            self.buffered_len = 0;
        }
        self.buffered[self.buffered_len..56].fill(0);
        self.buffered[56..].copy_from_slice(&bit_length.to_be_bytes());
        sha256_compress(&mut self.state, &self.buffered);
        Ok(self
            .state
            .iter()
            .map(|word| format!("{word:08x}"))
            .collect())
    }
}

fn sha256_compress(state: &mut [u32; 8], block: &[u8; 64]) {
    const K: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
        0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
        0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
        0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
        0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
        0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
        0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
        0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
        0xc67178f2,
    ];
    let mut words = [0_u32; 64];
    for (index, chunk) in block.chunks_exact(4).take(16).enumerate() {
        words[index] = u32::from_be_bytes(chunk.try_into().expect("SHA-256 word length"));
    }
    for index in 16..64 {
        let s0 = words[index - 15].rotate_right(7)
            ^ words[index - 15].rotate_right(18)
            ^ (words[index - 15] >> 3);
        let s1 = words[index - 2].rotate_right(17)
            ^ words[index - 2].rotate_right(19)
            ^ (words[index - 2] >> 10);
        words[index] = words[index - 16]
            .wrapping_add(s0)
            .wrapping_add(words[index - 7])
            .wrapping_add(s1);
    }
    let mut work = *state;
    for index in 0..64 {
        let s1 = work[4].rotate_right(6) ^ work[4].rotate_right(11) ^ work[4].rotate_right(25);
        let choose = (work[4] & work[5]) ^ ((!work[4]) & work[6]);
        let temporary_one = work[7]
            .wrapping_add(s1)
            .wrapping_add(choose)
            .wrapping_add(K[index])
            .wrapping_add(words[index]);
        let s0 = work[0].rotate_right(2) ^ work[0].rotate_right(13) ^ work[0].rotate_right(22);
        let majority = (work[0] & work[1]) ^ (work[0] & work[2]) ^ (work[1] & work[2]);
        let temporary_two = s0.wrapping_add(majority);
        work = [
            temporary_one.wrapping_add(temporary_two),
            work[0],
            work[1],
            work[2],
            work[3].wrapping_add(temporary_one),
            work[4],
            work[5],
            work[6],
        ];
    }
    for index in 0..8 {
        state[index] = state[index].wrapping_add(work[index]);
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
    shutdown: Arc<AtomicBool>,
    monitor: Mutex<Option<JoinHandle<()>>>,
    control: Mutex<Option<ControlServer>>,
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
        if !enabled {
            self.disable()?;
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
        Ok(self.status())
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
                let _ = self.raw_input.set_enabled(false);
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
            return;
        }
        if let Err(error) = self.raw_input.set_enabled(true) {
            let reason = format!("raw_input_unavailable: {}", bounded_diagnostic_text(&error));
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
                let reason = format!(
                    "video_capture_unavailable: {}",
                    bounded_diagnostic_text(&error)
                );
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
                eprintln!(
                    "[capture-export] phase {:?} -> {:?} session={:?}",
                    status.phase, replacement.phase, replacement.capture_session_id
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

    fn recover_unhealthy_raw(&self) {
        let healthy = self.raw_input.status().capture_healthy;
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
        if !matches!(
            status.phase,
            CapturePhase::Capturing | CapturePhase::Degraded
        ) || status.raw.state != CaptureSourceState::Capturing
        {
            return Err("raw_snapshot_unavailable".to_string());
        }
        self.raw_input.flush_snapshot_barrier()
    }

    fn handle_export(&self, request: ExportReplayRequest) -> Result<ReceiptRecord, String> {
        let started = Instant::now();
        eprintln!(
            "[capture-export] handle_export: id={} run={} session={}",
            request.request_id, request.run_id, request.capture_session_id
        );
        let status = self.status();
        if !matches!(
            status.phase,
            CapturePhase::Capturing | CapturePhase::Finalizing
        ) {
            eprintln!(
                "[capture-export] handle_export: phase={:?} rejects export",
                status.phase
            );
            return Err("capture_unavailable".to_string());
        }
        if status.capture_session_id.as_deref() != Some(request.capture_session_id.as_str()) {
            eprintln!(
                "[capture-export] handle_export: session mismatch current={:?} requested={}",
                status.capture_session_id, request.capture_session_id
            );
            return Err("capture_session_mismatch".to_string());
        }
        let paths = managed_export_paths(&self.data_root, request.run_id, &request.request_id)?;
        let placeholder = ReceiptRecord::placeholder(&request);
        if paths.mp4.exists() || paths.receipt.exists() {
            eprintln!(
                "[capture-export] handle_export: artifacts already exist, revalidating {}",
                paths.mp4.display()
            );
            return match ReceiptRecord::read_matching(&paths, &placeholder) {
                Ok(true) => ReceiptRecord::read(&paths.receipt),
                Ok(false) => Err("existing capture artifact is incomplete".to_string()),
                Err(error) => Err(error),
            };
        }
        let receiver = {
            let capture = self
                .window_capture
                .lock()
                .map_err(|_| "window capture state is unavailable".to_string())?;
            let (start_100ns, end_100ns) = capture
                .epoch_window_to_replay_pts(request.start_epoch_ms, request.end_epoch_ms)
                .map_err(|_| "capture_window_invalid".to_string())?;
            eprintln!(
                "[capture-export] handle_export: pts window {}..{} path={}",
                start_100ns,
                end_100ns,
                paths.mp4.display()
            );
            capture
                .request_replay_export(start_100ns, end_100ns, paths.mp4.clone())
                .map_err(|error| replay_failure_code(error.kind).to_string())?
        };
        eprintln!("[capture-export] handle_export: queued, waiting for mux worker");
        let receipt = self.wait_for_export(receiver)?;
        let record = ReceiptRecord::from_export(&request, receipt, &paths.mp4)?;
        record.write_atomic(&paths.receipt)?;
        eprintln!(
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
        eprintln!("[capture-export] wait_for_export: begin");
        let mut draining = false;
        loop {
            if self.shutdown.load(Ordering::Acquire) && !draining {
                draining = true;
                eprintln!(
                    "[capture-export] wait_for_export: shutdown requested, draining in-flight mux until receipt or export timeout"
                );
            }
            let remaining = deadline.saturating_duration_since(std::time::Instant::now());
            if remaining.is_zero() {
                eprintln!("[capture-export] wait_for_export: timed out");
                return Err("capture_export_timed_out".to_string());
            }
            match receiver.recv_timeout(remaining.min(Duration::from_millis(50))) {
                Ok(Ok(receipt)) => {
                    eprintln!(
                        "[capture-export] wait_for_export: receipt packets={} elapsed_ms={}",
                        receipt.packet_count,
                        started.elapsed().as_millis()
                    );
                    return Ok(receipt);
                }
                Ok(Err(error)) => {
                    eprintln!(
                        "[capture-export] wait_for_export: mux failed kind={:?} elapsed_ms={}",
                        error.kind,
                        started.elapsed().as_millis()
                    );
                    return Err(replay_failure_code(error.kind).to_string());
                }
                Err(std::sync::mpsc::RecvTimeoutError::Timeout) => continue,
                Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
                    eprintln!("[capture-export] wait_for_export: mux worker dropped the channel");
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
                            eprintln!(
                                "[capture-export] accept: {}",
                                stream
                                    .peer_addr()
                                    .map(|address| address.to_string())
                                    .unwrap_or_else(|_| "?".to_string())
                            );
                            let secret = thread_connection.secret.clone();
                            let coordinator = coordinator.clone();
                            match thread::Builder::new()
                                .name("aiming-cookie-capture-connection".to_string())
                                .spawn(move || {
                                    let result = std::panic::catch_unwind(
                                        std::panic::AssertUnwindSafe(|| {
                                            handle_control_connection(stream, &secret, coordinator);
                                        }),
                                    );
                                    if let Err(panic) = result {
                                        eprintln!(
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
                                    eprintln!("[capture-export] connection spawn failed: {error}");
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
) {
    let started = Instant::now();
    eprintln!("[capture-export] conn: reading request");
    let _ = stream.set_read_timeout(Some(CONTROL_READ_TIMEOUT));
    let request =
        read_control_line(&mut stream).and_then(|line| parse_control_request(&line, secret));
    let response = match request {
        Ok(request) => {
            eprintln!("[capture-export] conn: request accepted: {request:?}");
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
            result.unwrap_or_else(|code| {
                eprintln!("[capture-export] conn: request failed: {code}");
                control_error_response(response_type, &code)
            })
        }
        Err(code) => {
            eprintln!("[capture-export] conn: request rejected: {code}");
            control_error_response("controlError", &code)
        }
    };
    match serde_json::to_vec(&response) {
        Ok(payload) => {
            eprintln!(
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
                eprintln!("[capture-export] conn: response write failed: {error}");
            }
        }
        Err(error) => {
            eprintln!("[capture-export] conn: response serialize failed: {error}");
        }
    }
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

fn read_control_line(reader: &mut impl Read) -> Result<Vec<u8>, String> {
    let mut line = Vec::new();
    let mut buffer = [0_u8; 1024];
    loop {
        let count = reader
            .read(&mut buffer)
            .map_err(|_| "control_read_failed".to_string())?;
        if count == 0 {
            return Err("control_message_invalid".to_string());
        }
        for (index, byte) in buffer[..count].iter().copied().enumerate() {
            if line.len() == CONTROL_MAX_MESSAGE_BYTES {
                return Err("control_message_invalid".to_string());
            }
            line.push(byte);
            if byte == b'\n' {
                if index + 1 != count {
                    return Err("control_message_invalid".to_string());
                }
                return Ok(line);
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
        bounded_diagnostic_text, control_error_response, join_control_connections,
        managed_export_paths, monitor_start_failure_status, parse_control_request,
        read_control_line, replay_failure_code, response_type_for_request,
        track_control_connection_thread, CaptureCoordinatorStatus, CapturePhase,
        CaptureSourceState, CaptureSourceStatus, ControlRequest, ExportReplayRequest,
        FileFingerprint, ReceiptRecord, CONTROL_MAX_MESSAGE_BYTES,
    };
    use crate::window_capture::ReplayExportFailureKind;
    use std::fs;
    use std::io::Cursor;

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
        assert!(read_control_line(&mut Cursor::new(b"{}".to_vec())).is_err());
        assert!(
            read_control_line(&mut Cursor::new(vec![b'x'; CONTROL_MAX_MESSAGE_BYTES + 1])).is_err()
        );
        assert_eq!(
            read_control_line(&mut Cursor::new(b"{}\n".to_vec())).unwrap(),
            b"{}\n"
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
}
