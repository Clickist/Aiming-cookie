//! Bounded Windows window-capture boundary.
//!
//! Windows.Graphics.Capture boundary for a bounded, non-blocking frame probe.
//! The current task records frame metadata only. Pixel readback and MP4
//! encoding are separate later steps so the Raw Input path stays priority.

use serde::Serialize;
use std::collections::VecDeque;
use std::io::{self, Write};
use std::ops::Range;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

#[cfg(windows)]
use std::path::Path;
#[cfg(windows)]
use std::sync::atomic::{AtomicBool, AtomicI64, AtomicPtr, AtomicU64, Ordering};
#[cfg(windows)]
use std::thread::{self, JoinHandle};

pub const FRAME_PIXEL_BYTES: usize = 4;
// 硬件帧队列的元素是 WGC 表面引用 + 空像素元数据（bgra8 为空），CPU 侧
// 每元素约 150B；持有的 GPU 表面数受 WGC frame pool（2 个 buffer）约束，
// 不随本容量增长。60fps 下 32 帧 ≈ 533ms 余量，覆盖 GPU 瞬时挤压。
pub const DEFAULT_FRAME_QUEUE_CAPACITY: usize = 32;
// 写入队列的元素是完整未压缩 BGRA FrameSample（1080p 约 8.3MB/帧），
// 不是压缩 packet：8 槽在 1080p 下峰值约 66MB，放大到 32 会到 ~260MB，
// 故只取 2 倍余量（60fps 下约 133ms）。
pub const DEFAULT_WRITER_QUEUE_CAPACITY: usize = 8;
pub const DEFAULT_HARDWARE_EVENT_QUEUE_CAPACITY: usize = 32;
pub const DEFAULT_RECORDING_FPS_NUMERATOR: u32 = 60;
pub const DEFAULT_RECORDING_FPS_DENOMINATOR: u32 = 1;
pub const REPLAY_MAX_DURATION_100NS: i64 = 300 * 10_000_000;
pub const REPLAY_MAX_BYTES: usize = 384 * 1024 * 1024;
// 导出窗口内容忍的时间线缺口：丢 1 帧（60fps 下 16.7ms）不应废掉整个
// 导出，缺口由前一 sample 的时长自然吸收；超过 100ms 仍按 CoverageGap 失败。
pub const REPLAY_TOLERATED_GAP_100NS: i64 = 1_000_000;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CaptureClockMetadata {
    pub utc_epoch_ms: i64,
    pub qpc_ns: u128,
    pub clock_source: &'static str,
    pub timebase_version: &'static str,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FrameSample {
    pub sequence: u64,
    pub width: u32,
    pub height: u32,
    pub system_relative_time_100ns: i64,
    pub clock: CaptureClockMetadata,
    pub bgra8: Vec<u8>,
}

impl FrameSample {
    pub fn validate(&self) -> io::Result<()> {
        if self.sequence == 0 {
            return Err(invalid_frame("frame sequence must be positive"));
        }
        if self.width == 0 || self.height == 0 {
            return Err(invalid_frame("frame dimensions must be positive"));
        }
        if self.system_relative_time_100ns < 0 {
            return Err(invalid_frame("frame timestamp must be non-negative"));
        }
        let expected = (self.width as usize)
            .checked_mul(self.height as usize)
            .and_then(|pixels| pixels.checked_mul(FRAME_PIXEL_BYTES))
            .ok_or_else(|| invalid_frame("frame dimensions overflow pixel size"))?;
        // Metadata-only WGC samples intentionally omit GPU readback. A writer
        // may later provide a full BGRA payload, which must match exactly.
        if !self.bgra8.is_empty() && self.bgra8.len() != expected {
            return Err(invalid_frame("frame pixel payload has unexpected length"));
        }
        Ok(())
    }
}

#[cfg(windows)]
pub struct Mp4Writer {
    sink: windows::Win32::Media::MediaFoundation::IMFSinkWriter,
    stream_index: u32,
    width: u32,
    height: u32,
    frame_duration_100ns: i64,
    first_system_relative_time_100ns: Option<i64>,
    last_system_relative_time_100ns: Option<i64>,
    finalized: bool,
    mf_started: bool,
}

#[cfg(windows)]
impl Mp4Writer {
    pub fn start(path: impl AsRef<Path>, width: u32, height: u32) -> Result<Self, String> {
        validate_recording_dimensions(width, height)?;
        let path = path.as_ref().to_path_buf();
        if !path.is_absolute() {
            return Err("recording output path must be absolute".to_string());
        }
        if !path
            .extension()
            .and_then(|extension| extension.to_str())
            .is_some_and(|extension| extension.eq_ignore_ascii_case("mp4"))
        {
            return Err("recording output path must use the .mp4 extension".to_string());
        }
        if !path.parent().is_some_and(Path::is_dir) {
            return Err("recording output directory does not exist".to_string());
        }
        let fps_num = DEFAULT_RECORDING_FPS_NUMERATOR;
        let fps_den = DEFAULT_RECORDING_FPS_DENOMINATOR;
        let frame_duration_100ns = (10_000_000i64 * fps_den as i64) / fps_num as i64;

        use windows::core::PCWSTR;
        use windows::Win32::Media::MediaFoundation::{
            MFCreateAttributes, MFCreateSinkWriterFromURL, MFMediaType_Video, MFStartup,
            MFVideoFormat_H264, MFVideoFormat_RGB32, MFVideoInterlace_Progressive, MFSTARTUP_FULL,
            MF_MT_ALL_SAMPLES_INDEPENDENT, MF_MT_DEFAULT_STRIDE, MF_MT_FIXED_SIZE_SAMPLES,
            MF_MT_INTERLACE_MODE, MF_READWRITE_ENABLE_HARDWARE_TRANSFORMS,
            MF_SINK_WRITER_DISABLE_THROTTLING, MF_VERSION,
        };

        unsafe { MFStartup(MF_VERSION, MFSTARTUP_FULL) }
            .map_err(|error| format!("MFStartup failed: {error}"))?;
        let mut writer = None;
        let mut path_wide: Vec<u16> = path.as_os_str().to_string_lossy().encode_utf16().collect();
        path_wide.push(0);
        let startup_result = (|| {
            let mut attributes = None;
            unsafe { MFCreateAttributes(&mut attributes, 2) }
                .map_err(|error| format!("MFCreateAttributes failed: {error}"))?;
            let attributes =
                attributes.ok_or_else(|| "MF attributes were not returned".to_string())?;
            unsafe {
                attributes
                    .SetUINT32(&MF_READWRITE_ENABLE_HARDWARE_TRANSFORMS, 1)
                    .map_err(|error| format!("hardware transform preference failed: {error}"))?;
                attributes
                    .SetUINT32(&MF_SINK_WRITER_DISABLE_THROTTLING, 1)
                    .map_err(|error| format!("sink throttling configuration failed: {error}"))?;
            }
            let sink =
                unsafe { MFCreateSinkWriterFromURL(PCWSTR(path_wide.as_ptr()), None, &attributes) }
                    .map_err(|error| format!("MFCreateSinkWriterFromURL failed: {error}"))?;
            let output_type = create_video_type(
                MFMediaType_Video,
                MFVideoFormat_H264,
                width,
                height,
                fps_num,
                fps_den,
                8_000_000,
            )?;
            unsafe {
                output_type
                    .SetUINT32(&MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive.0 as u32)
                    .map_err(|error| format!("output interlace mode failed: {error}"))?;
                output_type
                    .SetUINT32(&MF_MT_ALL_SAMPLES_INDEPENDENT, 1)
                    .map_err(|error| format!("output sample independence failed: {error}"))?;
            }
            let input_type = create_video_type(
                MFMediaType_Video,
                MFVideoFormat_RGB32,
                width,
                height,
                fps_num,
                fps_den,
                0,
            )?;
            unsafe {
                input_type
                    .SetUINT32(&MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive.0 as u32)
                    .map_err(|error| format!("input interlace mode failed: {error}"))?;
                input_type
                    .SetUINT32(&MF_MT_FIXED_SIZE_SAMPLES, 1)
                    .map_err(|error| format!("input fixed-size samples failed: {error}"))?;
                input_type
                    .SetUINT32(
                        &MF_MT_DEFAULT_STRIDE,
                        width.saturating_mul(FRAME_PIXEL_BYTES as u32),
                    )
                    .map_err(|error| format!("input default stride failed: {error}"))?;
            }
            let stream_index = unsafe { sink.AddStream(&output_type) }
                .map_err(|error| format!("sink AddStream failed: {error}"))?;
            unsafe {
                sink.SetInputMediaType(stream_index, &input_type, None)
                    .map_err(|error| format!("sink SetInputMediaType failed: {error}"))?;
                sink.BeginWriting()
                    .map_err(|error| format!("sink BeginWriting failed: {error}"))?;
            }
            writer = Some(Self {
                sink,
                stream_index,
                width,
                height,
                frame_duration_100ns,
                first_system_relative_time_100ns: None,
                last_system_relative_time_100ns: None,
                finalized: false,
                mf_started: true,
            });
            Ok::<(), String>(())
        })();
        if let Err(error) = startup_result {
            unsafe { windows::Win32::Media::MediaFoundation::MFShutdown() }.ok();
            return Err(error);
        }
        Ok(writer.expect("writer initialized after successful Media Foundation startup"))
    }

    pub fn write_frame(&mut self, frame: &FrameSample) -> Result<(), String> {
        frame.validate().map_err(|error| error.to_string())?;
        if frame.width != self.width || frame.height != self.height {
            return Err(format!(
                "frame dimensions {}x{} do not match writer {}x{}",
                frame.width, frame.height, self.width, self.height
            ));
        }
        if frame.bgra8.is_empty() {
            return Err("MP4 writer requires BGRA8 pixel payload".to_string());
        }
        if self
            .last_system_relative_time_100ns
            .is_some_and(|previous| frame.system_relative_time_100ns < previous)
        {
            return Err("frame timestamps must be monotonic".to_string());
        }
        let first = *self
            .first_system_relative_time_100ns
            .get_or_insert(frame.system_relative_time_100ns);
        let pts = frame
            .system_relative_time_100ns
            .checked_sub(first)
            .ok_or_else(|| "frame timestamp is before writer anchor".to_string())?;
        let buffer = unsafe {
            windows::Win32::Media::MediaFoundation::MFCreateMemoryBuffer(frame.bgra8.len() as u32)
        }
        .map_err(|error| format!("MFCreateMemoryBuffer failed: {error}"))?;
        let mut destination = std::ptr::null_mut();
        unsafe {
            buffer
                .Lock(&mut destination, None, None)
                .map_err(|error| format!("media buffer Lock failed: {error}"))?;
            std::ptr::copy_nonoverlapping(frame.bgra8.as_ptr(), destination, frame.bgra8.len());
            buffer
                .Unlock()
                .map_err(|error| format!("media buffer Unlock failed: {error}"))?;
            buffer
                .SetCurrentLength(frame.bgra8.len() as u32)
                .map_err(|error| format!("media buffer SetCurrentLength failed: {error}"))?;
            let sample = windows::Win32::Media::MediaFoundation::MFCreateSample()
                .map_err(|error| format!("MFCreateSample failed: {error}"))?;
            sample
                .AddBuffer(&buffer)
                .map_err(|error| format!("sample AddBuffer failed: {error}"))?;
            sample
                .SetSampleTime(pts)
                .map_err(|error| format!("sample SetSampleTime failed: {error}"))?;
            sample
                .SetSampleDuration(self.frame_duration_100ns)
                .map_err(|error| format!("sample SetSampleDuration failed: {error}"))?;
            self.sink
                .WriteSample(self.stream_index, &sample)
                .map_err(|error| format!("sink WriteSample failed: {error}"))?;
        }
        self.last_system_relative_time_100ns = Some(frame.system_relative_time_100ns);
        Ok(())
    }

    pub fn finalize(&mut self) -> Result<(), String> {
        if self.finalized {
            return Ok(());
        }
        let result = unsafe { self.sink.Finalize() }
            .map_err(|error| format!("sink Finalize failed: {error}"));
        self.finalized = true;
        if self.mf_started {
            unsafe { windows::Win32::Media::MediaFoundation::MFShutdown() }
                .map_err(|error| format!("MFShutdown failed: {error}"))?;
            self.mf_started = false;
        }
        result
    }
}

#[cfg(windows)]
impl Drop for Mp4Writer {
    fn drop(&mut self) {
        let _ = self.finalize();
    }
}

#[cfg(windows)]
fn validate_recording_dimensions(width: u32, height: u32) -> Result<(), String> {
    if width == 0 || height == 0 {
        return Err("recording dimensions must be positive".to_string());
    }
    if !width.is_multiple_of(2) || !height.is_multiple_of(2) {
        return Err("H.264 recording dimensions must be even".to_string());
    }
    Ok(())
}

#[cfg(windows)]
fn create_video_type(
    major_type: windows::core::GUID,
    subtype: windows::core::GUID,
    width: u32,
    height: u32,
    fps_num: u32,
    fps_den: u32,
    bitrate: u32,
) -> Result<windows::Win32::Media::MediaFoundation::IMFMediaType, String> {
    use windows::Win32::Media::MediaFoundation::{
        MFCreateMediaType, MF_MT_AVG_BITRATE, MF_MT_FRAME_RATE, MF_MT_FRAME_SIZE, MF_MT_MAJOR_TYPE,
        MF_MT_PIXEL_ASPECT_RATIO, MF_MT_SUBTYPE,
    };
    let media_type = unsafe { MFCreateMediaType() }
        .map_err(|error| format!("MFCreateMediaType failed: {error}"))?;
    unsafe {
        media_type
            .SetGUID(&MF_MT_MAJOR_TYPE, &major_type)
            .map_err(|error| format!("media type major type failed: {error}"))?;
        media_type
            .SetGUID(&MF_MT_SUBTYPE, &subtype)
            .map_err(|error| format!("media type subtype failed: {error}"))?;
        media_type
            .SetUINT64(&MF_MT_FRAME_SIZE, pack_u64_pair(width, height))
            .map_err(|error| format!("media type frame size failed: {error}"))?;
        media_type
            .SetUINT64(&MF_MT_FRAME_RATE, pack_u64_pair(fps_num, fps_den))
            .map_err(|error| format!("media type frame rate failed: {error}"))?;
        media_type
            .SetUINT64(&MF_MT_PIXEL_ASPECT_RATIO, pack_u64_pair(1, 1))
            .map_err(|error| format!("media type pixel aspect ratio failed: {error}"))?;
        if bitrate > 0 {
            media_type
                .SetUINT32(&MF_MT_AVG_BITRATE, bitrate)
                .map_err(|error| format!("media type bitrate failed: {error}"))?;
        }
    }
    Ok(media_type)
}

#[cfg(windows)]
fn pack_u64_pair(first: u32, second: u32) -> u64 {
    ((first as u64) << 32) | second as u64
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FrameEnqueueResult {
    Enqueued,
    DroppedBackpressure,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum HardwareEncoderPath {
    MediaFoundationHardwareH264,
    #[allow(dead_code)] // Explicit CPU-only baseline; automatic capture rejects it.
    D3dFrameReadbackSinkWriter,
}

impl HardwareEncoderPath {
    pub fn require_automatic_hardware(self) -> Result<Self, HardwareEncoderFailure> {
        match self {
            Self::MediaFoundationHardwareH264 => Ok(self),
            Self::D3dFrameReadbackSinkWriter => Err(HardwareEncoderFailure::CpuFallbackDenied),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum HardwareEncoderFailure {
    HardwareUnavailable,
    AdapterMismatch,
    GpuConversionFailure,
    EncoderSetupFailure,
    EncoderRuntimeFailure,
    Backpressure,
    InvalidPacket,
    UnsupportedPacketTiming,
    CpuFallbackDenied,
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct EncodedH264Packet {
    bytes: Arc<[u8]>,
    pts_100ns: i64,
    duration_100ns: i64,
    keyframe: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[cfg_attr(not(test), allow(dead_code))]
enum ReplayBufferError {
    InvalidLimits,
    InvalidPacket,
    TimestampRegression,
    ByteOverflow,
    InvalidWindow,
    WindowTooLong,
    MissingKeyframeCoverage,
    IncompleteCoverage,
    CoverageGap,
}

#[derive(Clone, Debug, PartialEq, Eq)]
#[cfg_attr(not(test), allow(dead_code))]
struct ReplaySnapshot {
    packets: Vec<Arc<EncodedH264Packet>>,
    requested_start_100ns: i64,
    requested_end_100ns: i64,
    decode_start_100ns: i64,
    start_offset_100ns: i64,
    end_offset_100ns: i64,
    total_bytes: usize,
    // 窗口内被容忍（≤ REPLAY_TOLERATED_GAP_100NS）的缺口数。
    tolerated_gaps: u64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum ReplayExportFailureKind {
    InvalidWindow,
    WindowTooLong,
    MissingKeyframeCoverage,
    IncompleteCoverage,
    CoverageGap,
    MissingCodecConfiguration,
    UnsupportedCodecProfile,
    UnsupportedBitstreamFormat,
    UnsupportedPacketTiming,
    InvalidSnapshot,
    TimelineOverflow,
    IoFailure,
    FinalizationFailure,
    CaptureUnavailable,
    ExportBusy,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReplayExportFailure {
    pub kind: ReplayExportFailureKind,
    pub message: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReplayExportReceipt {
    pub requested_start_100ns: i64,
    pub requested_end_100ns: i64,
    pub decode_start_100ns: i64,
    pub visible_duration_100ns: i64,
    pub decode_preroll_100ns: i64,
    pub packet_count: usize,
    pub encoded_bytes: usize,
    pub reencoded_frames: u64,
    // 窗口内被容忍（≤ REPLAY_TOLERATED_GAP_100NS）的缺口数；仅留在
    // Rust 侧，控制协议与落盘 receipt 的形状保持不变。
    pub tolerated_coverage_gaps: u64,
    pub capture_clock: CaptureClockMetadata,
}

#[derive(Clone, Debug)]
struct ReplayMuxInput {
    snapshot: ReplaySnapshot,
    sequence_header: Arc<[u8]>,
    width: u32,
    height: u32,
    capture_clock: CaptureClockMetadata,
}

fn replay_export_failure(
    kind: ReplayExportFailureKind,
    message: impl Into<String>,
) -> ReplayExportFailure {
    ReplayExportFailure {
        kind,
        message: message.into(),
    }
}

const MP4_TIMESCALE: u32 = 10_000_000;

#[derive(Debug)]
struct Mp4SamplePlan {
    nal_ranges: Vec<Range<usize>>,
    size: u32,
    duration: u32,
    keyframe: bool,
}

#[derive(Debug)]
struct ReplayMp4Plan {
    samples: Vec<Mp4SamplePlan>,
    avcc: Vec<u8>,
    visible_duration: u32,
    media_duration: u32,
    edit_media_time: i64,
    mdat_payload_size: u32,
    tolerated_gaps: u64,
}

fn annex_b_start_code(bytes: &[u8], offset: usize) -> Option<usize> {
    if bytes.get(offset..offset + 4) == Some(&[0, 0, 0, 1]) {
        Some(4)
    } else if bytes.get(offset..offset + 3) == Some(&[0, 0, 1]) {
        Some(3)
    } else {
        None
    }
}

fn find_annex_b_start_code(bytes: &[u8], from: usize) -> Option<(usize, usize)> {
    (from..bytes.len()).find_map(|offset| {
        annex_b_start_code(bytes, offset).map(|start_code_size| (offset, start_code_size))
    })
}

fn annex_b_nal_ranges(bytes: &[u8]) -> Result<Vec<Range<usize>>, ReplayExportFailure> {
    let Some((mut offset, mut start_code_size)) = find_annex_b_start_code(bytes, 0) else {
        return Err(replay_export_failure(
            ReplayExportFailureKind::UnsupportedBitstreamFormat,
            "H.264 access unit is not Annex B",
        ));
    };
    if bytes[..offset].iter().any(|byte| *byte != 0) {
        return Err(replay_export_failure(
            ReplayExportFailureKind::UnsupportedBitstreamFormat,
            "Annex B access unit contains bytes before its first start code",
        ));
    }
    let mut ranges = Vec::new();
    loop {
        let nal_start = offset.checked_add(start_code_size).ok_or_else(|| {
            replay_export_failure(
                ReplayExportFailureKind::TimelineOverflow,
                "Annex B NAL offset overflowed",
            )
        })?;
        let next = find_annex_b_start_code(bytes, nal_start);
        let mut nal_end = next.map_or(bytes.len(), |(next_offset, _)| next_offset);
        while nal_end > nal_start && bytes[nal_end - 1] == 0 {
            nal_end -= 1;
        }
        if nal_start == nal_end {
            return Err(replay_export_failure(
                ReplayExportFailureKind::UnsupportedBitstreamFormat,
                "Annex B access unit contains an empty NAL unit",
            ));
        }
        ranges.push(nal_start..nal_end);
        let Some((next_offset, next_start_code_size)) = next else {
            break;
        };
        offset = next_offset;
        start_code_size = next_start_code_size;
    }
    Ok(ranges)
}

#[cfg(test)]
fn annex_b_to_avcc(bytes: &[u8]) -> Result<Vec<u8>, ReplayExportFailure> {
    let ranges = annex_b_nal_ranges(bytes)?;
    let mut converted = Vec::new();
    for range in ranges {
        let length = u32::try_from(range.len()).map_err(|_| {
            replay_export_failure(
                ReplayExportFailureKind::TimelineOverflow,
                "H.264 NAL unit exceeds the MP4 sample limit",
            )
        })?;
        converted.extend_from_slice(&length.to_be_bytes());
        converted.extend_from_slice(&bytes[range]);
    }
    Ok(converted)
}

fn avcc_from_sequence_header(bytes: &[u8]) -> Result<Vec<u8>, ReplayExportFailure> {
    if bytes.is_empty() {
        return Err(replay_export_failure(
            ReplayExportFailureKind::MissingCodecConfiguration,
            "hardware H.264 sequence header is unavailable",
        ));
    }
    let ranges = annex_b_nal_ranges(bytes)?;
    let mut parameter_sets: Vec<&[u8]> = ranges.iter().map(|range| &bytes[range.clone()]).collect();
    let mut sequence_sets = Vec::new();
    let mut picture_sets = Vec::new();
    for parameter_set in parameter_sets.drain(..) {
        match parameter_set.first().map(|byte| byte & 0x1f) {
            Some(7) => sequence_sets.push(parameter_set),
            Some(8) => picture_sets.push(parameter_set),
            _ => {}
        }
    }
    let Some(primary_sps) = sequence_sets.first() else {
        return Err(replay_export_failure(
            ReplayExportFailureKind::MissingCodecConfiguration,
            "hardware H.264 sequence header does not contain SPS",
        ));
    };
    if primary_sps.len() < 4 || picture_sets.is_empty() || sequence_sets.len() > 31 {
        return Err(replay_export_failure(
            ReplayExportFailureKind::MissingCodecConfiguration,
            "hardware H.264 sequence header does not contain usable SPS/PPS",
        ));
    }
    if primary_sps[1] != 66 {
        return Err(replay_export_failure(
            ReplayExportFailureKind::UnsupportedCodecProfile,
            "replay MP4 v1 requires the configured no-B H.264 Baseline profile",
        ));
    }
    let mut avcc = vec![
        1,
        primary_sps[1],
        primary_sps[2],
        primary_sps[3],
        0xff,
        0xe0 | sequence_sets.len() as u8,
    ];
    for parameter_set in sequence_sets {
        let length = u16::try_from(parameter_set.len()).map_err(|_| {
            replay_export_failure(
                ReplayExportFailureKind::TimelineOverflow,
                "H.264 SPS exceeds the AVC configuration limit",
            )
        })?;
        avcc.extend_from_slice(&length.to_be_bytes());
        avcc.extend_from_slice(parameter_set);
    }
    avcc.push(u8::try_from(picture_sets.len()).map_err(|_| {
        replay_export_failure(
            ReplayExportFailureKind::TimelineOverflow,
            "too many H.264 PPS entries",
        )
    })?);
    for parameter_set in picture_sets {
        let length = u16::try_from(parameter_set.len()).map_err(|_| {
            replay_export_failure(
                ReplayExportFailureKind::TimelineOverflow,
                "H.264 PPS exceeds the AVC configuration limit",
            )
        })?;
        avcc.extend_from_slice(&length.to_be_bytes());
        avcc.extend_from_slice(parameter_set);
    }
    Ok(avcc)
}

fn u32_timeline(value: i64, description: &str) -> Result<u32, ReplayExportFailure> {
    u32::try_from(value).map_err(|_| {
        replay_export_failure(
            ReplayExportFailureKind::TimelineOverflow,
            format!("{description} exceeds the MP4 v0 timeline"),
        )
    })
}

fn prepare_replay_mp4(input: &ReplayMuxInput) -> Result<ReplayMp4Plan, ReplayExportFailure> {
    let snapshot = &input.snapshot;
    if input.width == 0
        || input.height == 0
        || input.width > u16::MAX as u32
        || input.height > u16::MAX as u32
        || input.capture_clock.clock_source.is_empty()
        || input.capture_clock.timebase_version != "time_alignment.v2"
    {
        return Err(replay_export_failure(
            ReplayExportFailureKind::InvalidSnapshot,
            "replay snapshot dimensions or capture-clock provenance are invalid",
        ));
    }
    if snapshot.requested_start_100ns < 0
        || snapshot.requested_end_100ns <= snapshot.requested_start_100ns
        || snapshot.decode_start_100ns < 0
        || snapshot.decode_start_100ns > snapshot.requested_start_100ns
    {
        return Err(replay_export_failure(
            ReplayExportFailureKind::InvalidWindow,
            "replay export window is invalid",
        ));
    }
    let visible_duration_100ns = snapshot.requested_end_100ns - snapshot.requested_start_100ns;
    if visible_duration_100ns > REPLAY_MAX_DURATION_100NS {
        return Err(replay_export_failure(
            ReplayExportFailureKind::WindowTooLong,
            "replay export window exceeds 300 seconds",
        ));
    }
    if snapshot.start_offset_100ns != snapshot.requested_start_100ns - snapshot.decode_start_100ns
        || snapshot.end_offset_100ns != snapshot.requested_end_100ns - snapshot.decode_start_100ns
    {
        return Err(replay_export_failure(
            ReplayExportFailureKind::InvalidSnapshot,
            "replay snapshot offsets do not match the requested window",
        ));
    }
    let Some(first_packet) = snapshot.packets.first() else {
        return Err(replay_export_failure(
            ReplayExportFailureKind::IncompleteCoverage,
            "replay snapshot has no encoded packets",
        ));
    };
    if !first_packet.keyframe || first_packet.pts_100ns != snapshot.decode_start_100ns {
        return Err(replay_export_failure(
            ReplayExportFailureKind::MissingKeyframeCoverage,
            "replay snapshot does not begin at its decode keyframe",
        ));
    }

    let mut samples = Vec::with_capacity(snapshot.packets.len());
    let mut encoded_bytes = 0usize;
    let mut mdat_payload_size = 0u64;
    let mut covered_until = snapshot.decode_start_100ns;
    let mut tolerated_gaps = 0u64;
    for (index, packet) in snapshot.packets.iter().enumerate() {
        if packet.pts_100ns < snapshot.decode_start_100ns || packet.duration_100ns <= 0 {
            return Err(replay_export_failure(
                ReplayExportFailureKind::InvalidSnapshot,
                "replay snapshot contains an invalid packet",
            ));
        }
        if index > 0 && packet.pts_100ns <= snapshot.packets[index - 1].pts_100ns {
            return Err(replay_export_failure(
                ReplayExportFailureKind::UnsupportedPacketTiming,
                "reordered H.264 packets require DTS/CTS support",
            ));
        }
        if packet.pts_100ns > covered_until {
            if packet.pts_100ns - covered_until > REPLAY_TOLERATED_GAP_100NS {
                return Err(replay_export_failure(
                    ReplayExportFailureKind::CoverageGap,
                    "replay snapshot contains a packet coverage gap",
                ));
            }
            // 小缺口由前一 sample 的时长（next.pts - pts）自然吸收。
            tolerated_gaps += 1;
        }
        covered_until = covered_until.max(
            packet
                .pts_100ns
                .checked_add(packet.duration_100ns)
                .ok_or_else(|| {
                    replay_export_failure(
                        ReplayExportFailureKind::TimelineOverflow,
                        "encoded packet end timestamp overflowed",
                    )
                })?,
        );
        let nal_ranges = annex_b_nal_ranges(&packet.bytes)?;
        let sample_size = nal_ranges.iter().try_fold(0u64, |total, range| {
            total.checked_add(4 + range.len() as u64).ok_or_else(|| {
                replay_export_failure(
                    ReplayExportFailureKind::TimelineOverflow,
                    "MP4 sample size overflowed",
                )
            })
        })?;
        let duration = if let Some(next) = snapshot.packets.get(index + 1) {
            if next.pts_100ns <= packet.pts_100ns {
                return Err(replay_export_failure(
                    ReplayExportFailureKind::UnsupportedPacketTiming,
                    "reordered H.264 packets require DTS/CTS support",
                ));
            }
            next.pts_100ns - packet.pts_100ns
        } else {
            packet.duration_100ns
        };
        samples.push(Mp4SamplePlan {
            nal_ranges,
            size: u32_timeline(sample_size as i64, "MP4 sample size")?,
            duration: u32_timeline(duration, "MP4 sample duration")?,
            keyframe: packet.keyframe,
        });
        encoded_bytes = encoded_bytes
            .checked_add(packet.bytes.len())
            .ok_or_else(|| {
                replay_export_failure(
                    ReplayExportFailureKind::TimelineOverflow,
                    "encoded replay byte count overflowed",
                )
            })?;
        mdat_payload_size = mdat_payload_size.checked_add(sample_size).ok_or_else(|| {
            replay_export_failure(
                ReplayExportFailureKind::TimelineOverflow,
                "MP4 media payload size overflowed",
            )
        })?;
    }
    if encoded_bytes != snapshot.total_bytes {
        return Err(replay_export_failure(
            ReplayExportFailureKind::InvalidSnapshot,
            "replay snapshot byte count changed after snapshotting",
        ));
    }
    if covered_until < snapshot.requested_end_100ns {
        return Err(replay_export_failure(
            ReplayExportFailureKind::IncompleteCoverage,
            "replay snapshot ends before the requested window",
        ));
    }
    let last = snapshot.packets.last().expect("non-empty snapshot checked");
    let media_duration = last
        .pts_100ns
        .checked_sub(snapshot.decode_start_100ns)
        .and_then(|value| value.checked_add(last.duration_100ns))
        .ok_or_else(|| {
            replay_export_failure(
                ReplayExportFailureKind::TimelineOverflow,
                "MP4 media duration overflowed",
            )
        })?;
    Ok(ReplayMp4Plan {
        samples,
        avcc: avcc_from_sequence_header(&input.sequence_header)?,
        visible_duration: u32_timeline(visible_duration_100ns, "visible duration")?,
        media_duration: u32_timeline(media_duration, "media duration")?,
        edit_media_time: snapshot.start_offset_100ns,
        mdat_payload_size: u32::try_from(mdat_payload_size).map_err(|_| {
            replay_export_failure(
                ReplayExportFailureKind::TimelineOverflow,
                "MP4 media payload exceeds the v1 file limit",
            )
        })?,
        tolerated_gaps,
    })
}

fn mp4_box(kind: [u8; 4], payload: Vec<u8>) -> Result<Vec<u8>, ReplayExportFailure> {
    let size = u32::try_from(payload.len().checked_add(8).ok_or_else(|| {
        replay_export_failure(
            ReplayExportFailureKind::TimelineOverflow,
            "MP4 box size overflowed",
        )
    })?)
    .map_err(|_| {
        replay_export_failure(
            ReplayExportFailureKind::TimelineOverflow,
            "MP4 box exceeds the v1 size limit",
        )
    })?;
    let mut bytes = Vec::with_capacity(size as usize);
    bytes.extend_from_slice(&size.to_be_bytes());
    bytes.extend_from_slice(&kind);
    bytes.extend_from_slice(&payload);
    Ok(bytes)
}

fn full_box(version: u8, flags: u32, mut payload: Vec<u8>) -> Vec<u8> {
    let mut bytes = Vec::with_capacity(payload.len() + 4);
    bytes.push(version);
    bytes.extend_from_slice(&(flags & 0x00ff_ffff).to_be_bytes()[1..]);
    bytes.append(&mut payload);
    bytes
}

fn push_u16(bytes: &mut Vec<u8>, value: u16) {
    bytes.extend_from_slice(&value.to_be_bytes());
}

fn push_u32(bytes: &mut Vec<u8>, value: u32) {
    bytes.extend_from_slice(&value.to_be_bytes());
}

fn build_ftyp() -> Result<Vec<u8>, ReplayExportFailure> {
    let mut payload = Vec::new();
    payload.extend_from_slice(b"isom");
    push_u32(&mut payload, 512);
    payload.extend_from_slice(b"isomiso2avc1mp41");
    mp4_box(*b"ftyp", payload)
}

fn push_unity_matrix(bytes: &mut Vec<u8>) {
    for value in [0x0001_0000u32, 0, 0, 0, 0x0001_0000, 0, 0, 0, 0x4000_0000] {
        push_u32(bytes, value);
    }
}

fn build_moov(
    input: &ReplayMuxInput,
    plan: &ReplayMp4Plan,
    chunk_offset: u32,
) -> Result<Vec<u8>, ReplayExportFailure> {
    let mut mvhd = Vec::new();
    push_u32(&mut mvhd, 0);
    push_u32(&mut mvhd, 0);
    push_u32(&mut mvhd, MP4_TIMESCALE);
    push_u32(&mut mvhd, plan.visible_duration);
    push_u32(&mut mvhd, 0x0001_0000);
    push_u16(&mut mvhd, 0x0100);
    push_u16(&mut mvhd, 0);
    mvhd.extend_from_slice(&[0; 8]);
    push_unity_matrix(&mut mvhd);
    mvhd.extend_from_slice(&[0; 24]);
    push_u32(&mut mvhd, 2);
    let mvhd = mp4_box(*b"mvhd", full_box(0, 0, mvhd))?;

    let mut tkhd = Vec::new();
    push_u32(&mut tkhd, 0);
    push_u32(&mut tkhd, 0);
    push_u32(&mut tkhd, 1);
    push_u32(&mut tkhd, 0);
    push_u32(&mut tkhd, plan.visible_duration);
    tkhd.extend_from_slice(&[0; 8]);
    push_u16(&mut tkhd, 0);
    push_u16(&mut tkhd, 0);
    push_u16(&mut tkhd, 0);
    push_u16(&mut tkhd, 0);
    push_unity_matrix(&mut tkhd);
    push_u32(&mut tkhd, input.width << 16);
    push_u32(&mut tkhd, input.height << 16);
    let tkhd = mp4_box(*b"tkhd", full_box(0, 7, tkhd))?;

    let mut elst = Vec::new();
    push_u32(&mut elst, 1);
    elst.extend_from_slice(&(plan.visible_duration as u64).to_be_bytes());
    elst.extend_from_slice(&plan.edit_media_time.to_be_bytes());
    push_u16(&mut elst, 1);
    push_u16(&mut elst, 0);
    let elst = mp4_box(*b"elst", full_box(1, 0, elst))?;
    let edts = mp4_box(*b"edts", elst)?;

    let mut mdhd = Vec::new();
    push_u32(&mut mdhd, 0);
    push_u32(&mut mdhd, 0);
    push_u32(&mut mdhd, MP4_TIMESCALE);
    push_u32(&mut mdhd, plan.media_duration);
    push_u16(&mut mdhd, 0x55c4);
    push_u16(&mut mdhd, 0);
    let mdhd = mp4_box(*b"mdhd", full_box(0, 0, mdhd))?;

    let mut hdlr = Vec::new();
    push_u32(&mut hdlr, 0);
    hdlr.extend_from_slice(b"vide");
    hdlr.extend_from_slice(&[0; 12]);
    hdlr.extend_from_slice(b"VideoHandler\0");
    let hdlr = mp4_box(*b"hdlr", full_box(0, 0, hdlr))?;

    let mut avc1 = vec![0; 6];
    push_u16(&mut avc1, 1);
    avc1.extend_from_slice(&[0; 16]);
    push_u16(&mut avc1, input.width as u16);
    push_u16(&mut avc1, input.height as u16);
    push_u32(&mut avc1, 0x0048_0000);
    push_u32(&mut avc1, 0x0048_0000);
    push_u32(&mut avc1, 0);
    push_u16(&mut avc1, 1);
    avc1.extend_from_slice(&[0; 32]);
    push_u16(&mut avc1, 0x0018);
    push_u16(&mut avc1, 0xffff);
    avc1.extend_from_slice(&mp4_box(*b"avcC", plan.avcc.clone())?);
    let avc1 = mp4_box(*b"avc1", avc1)?;
    let mut stsd = Vec::new();
    push_u32(&mut stsd, 1);
    stsd.extend_from_slice(&avc1);
    let stsd = mp4_box(*b"stsd", full_box(0, 0, stsd))?;

    let mut stts_entries = Vec::<(u32, u32)>::new();
    for sample in &plan.samples {
        if let Some((count, duration)) = stts_entries.last_mut() {
            if *duration == sample.duration {
                *count = count.checked_add(1).ok_or_else(|| {
                    replay_export_failure(
                        ReplayExportFailureKind::TimelineOverflow,
                        "MP4 timing entry count overflowed",
                    )
                })?;
                continue;
            }
        }
        stts_entries.push((1, sample.duration));
    }
    let mut stts = Vec::new();
    push_u32(&mut stts, stts_entries.len() as u32);
    for (count, duration) in stts_entries {
        push_u32(&mut stts, count);
        push_u32(&mut stts, duration);
    }
    let stts = mp4_box(*b"stts", full_box(0, 0, stts))?;

    let mut stss = Vec::new();
    let keyframes: Vec<u32> = plan
        .samples
        .iter()
        .enumerate()
        .filter_map(|(index, sample)| sample.keyframe.then_some(index as u32 + 1))
        .collect();
    push_u32(&mut stss, keyframes.len() as u32);
    for sample_number in keyframes {
        push_u32(&mut stss, sample_number);
    }
    let stss = mp4_box(*b"stss", full_box(0, 0, stss))?;

    let mut stsc = Vec::new();
    push_u32(&mut stsc, 1);
    push_u32(&mut stsc, 1);
    push_u32(&mut stsc, plan.samples.len() as u32);
    push_u32(&mut stsc, 1);
    let stsc = mp4_box(*b"stsc", full_box(0, 0, stsc))?;

    let mut stsz = Vec::new();
    push_u32(&mut stsz, 0);
    push_u32(&mut stsz, plan.samples.len() as u32);
    for sample in &plan.samples {
        push_u32(&mut stsz, sample.size);
    }
    let stsz = mp4_box(*b"stsz", full_box(0, 0, stsz))?;

    let mut stco = Vec::new();
    push_u32(&mut stco, 1);
    push_u32(&mut stco, chunk_offset);
    let stco = mp4_box(*b"stco", full_box(0, 0, stco))?;

    let mut stbl = Vec::new();
    for child in [stsd, stts, stss, stsc, stsz, stco] {
        stbl.extend_from_slice(&child);
    }
    let stbl = mp4_box(*b"stbl", stbl)?;

    let vmhd = mp4_box(*b"vmhd", full_box(0, 1, vec![0; 8]))?;
    let url = mp4_box(*b"url ", full_box(0, 1, Vec::new()))?;
    let mut dref = Vec::new();
    push_u32(&mut dref, 1);
    dref.extend_from_slice(&url);
    let dref = mp4_box(*b"dref", full_box(0, 0, dref))?;
    let dinf = mp4_box(*b"dinf", dref)?;
    let mut minf = Vec::new();
    minf.extend_from_slice(&vmhd);
    minf.extend_from_slice(&dinf);
    minf.extend_from_slice(&stbl);
    let minf = mp4_box(*b"minf", minf)?;

    let mut mdia = Vec::new();
    mdia.extend_from_slice(&mdhd);
    mdia.extend_from_slice(&hdlr);
    mdia.extend_from_slice(&minf);
    let mdia = mp4_box(*b"mdia", mdia)?;

    let mut trak = Vec::new();
    trak.extend_from_slice(&tkhd);
    trak.extend_from_slice(&edts);
    trak.extend_from_slice(&mdia);
    let trak = mp4_box(*b"trak", trak)?;

    let mut moov = Vec::new();
    moov.extend_from_slice(&mvhd);
    moov.extend_from_slice(&trak);
    mp4_box(*b"moov", moov)
}

#[cfg(test)]
fn write_replay_mp4(
    writer: &mut impl Write,
    input: &ReplayMuxInput,
) -> Result<ReplayExportReceipt, ReplayExportFailure> {
    let plan = prepare_replay_mp4(input)?;
    write_prepared_replay_mp4(writer, input, &plan)
}

fn write_prepared_replay_mp4(
    writer: &mut impl Write,
    input: &ReplayMuxInput,
    plan: &ReplayMp4Plan,
) -> Result<ReplayExportReceipt, ReplayExportFailure> {
    let ftyp = build_ftyp()?;
    let chunk_offset = u32::try_from(ftyp.len() + 8).map_err(|_| {
        replay_export_failure(
            ReplayExportFailureKind::TimelineOverflow,
            "MP4 chunk offset overflowed",
        )
    })?;
    writer.write_all(&ftyp).map_err(|error| {
        replay_export_failure(
            ReplayExportFailureKind::IoFailure,
            format!("MP4 ftyp write failed: {error}"),
        )
    })?;
    writer
        .write_all(&(plan.mdat_payload_size + 8).to_be_bytes())
        .and_then(|_| writer.write_all(b"mdat"))
        .map_err(|error| {
            replay_export_failure(
                ReplayExportFailureKind::IoFailure,
                format!("MP4 mdat header write failed: {error}"),
            )
        })?;
    for (packet, sample) in input.snapshot.packets.iter().zip(&plan.samples) {
        for range in &sample.nal_ranges {
            let length = u32::try_from(range.len()).expect("NAL range was validated");
            writer
                .write_all(&length.to_be_bytes())
                .and_then(|_| writer.write_all(&packet.bytes[range.clone()]))
                .map_err(|error| {
                    replay_export_failure(
                        ReplayExportFailureKind::IoFailure,
                        format!("MP4 sample write failed: {error}"),
                    )
                })?;
        }
    }
    let moov = build_moov(input, plan, chunk_offset)?;
    writer.write_all(&moov).map_err(|error| {
        replay_export_failure(
            ReplayExportFailureKind::IoFailure,
            format!("MP4 moov write failed: {error}"),
        )
    })?;
    Ok(ReplayExportReceipt {
        requested_start_100ns: input.snapshot.requested_start_100ns,
        requested_end_100ns: input.snapshot.requested_end_100ns,
        decode_start_100ns: input.snapshot.decode_start_100ns,
        visible_duration_100ns: input.snapshot.requested_end_100ns
            - input.snapshot.requested_start_100ns,
        decode_preroll_100ns: input.snapshot.start_offset_100ns,
        packet_count: input.snapshot.packets.len(),
        encoded_bytes: input.snapshot.total_bytes,
        reencoded_frames: 0,
        tolerated_coverage_gaps: plan.tolerated_gaps,
        capture_clock: input.capture_clock,
    })
}

#[cfg(test)]
fn build_replay_mp4(
    input: &ReplayMuxInput,
) -> Result<(Vec<u8>, ReplayExportReceipt), ReplayExportFailure> {
    let mut bytes = Vec::new();
    let receipt = write_replay_mp4(&mut bytes, input)?;
    Ok((bytes, receipt))
}

#[cfg(windows)]
static REPLAY_PARTIAL_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[cfg(windows)]
struct ReplayPartialFile {
    path: PathBuf,
    published: bool,
}

#[cfg(windows)]
impl Drop for ReplayPartialFile {
    fn drop(&mut self) {
        if !self.published {
            // This is an unpublished, app-created temporary artifact, never Run evidence.
            let _ = std::fs::remove_file(&self.path);
        }
    }
}

#[cfg(windows)]
fn create_replay_partial_file(
    output_path: &Path,
) -> Result<(ReplayPartialFile, std::fs::File), ReplayExportFailure> {
    let parent = output_path.parent().expect("validated output parent");
    let file_name = output_path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| {
            replay_export_failure(
                ReplayExportFailureKind::IoFailure,
                "replay output file name is not valid UTF-8",
            )
        })?;
    for _ in 0..16 {
        let sequence = REPLAY_PARTIAL_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let partial_path = parent.join(format!(
            ".{file_name}.partial-{}-{sequence}",
            std::process::id()
        ));
        match std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&partial_path)
        {
            Ok(file) => {
                return Ok((
                    ReplayPartialFile {
                        path: partial_path,
                        published: false,
                    },
                    file,
                ));
            }
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
            Err(error) => {
                return Err(replay_export_failure(
                    ReplayExportFailureKind::IoFailure,
                    format!("replay MP4 partial creation failed: {error}"),
                ));
            }
        }
    }
    Err(replay_export_failure(
        ReplayExportFailureKind::IoFailure,
        "replay MP4 could not reserve a unique partial file",
    ))
}

// [capture-export] 诊断：mux 线程 panic 还原为可读消息打到 stderr，
// 避免导出线程静默死亡被误判为通道断开。
#[cfg(windows)]
fn panic_message(panic: Box<dyn std::any::Any + Send>) -> String {
    panic
        .downcast_ref::<&str>()
        .map(|message| (*message).to_string())
        .or_else(|| panic.downcast_ref::<String>().cloned())
        .unwrap_or_else(|| "unknown panic payload".to_string())
}

#[cfg(windows)]
fn export_replay_mp4_file(
    input: ReplayMuxInput,
    output_path: PathBuf,
) -> Result<ReplayExportReceipt, ReplayExportFailure> {
    if !output_path.is_absolute()
        || !output_path
            .extension()
            .and_then(|extension| extension.to_str())
            .is_some_and(|extension| extension.eq_ignore_ascii_case("mp4"))
        || !output_path.parent().is_some_and(Path::is_dir)
    {
        return Err(replay_export_failure(
            ReplayExportFailureKind::IoFailure,
            "replay output must be a new absolute .mp4 in an existing directory",
        ));
    }
    let plan = prepare_replay_mp4(&input)?;
    if output_path.exists() {
        return Err(replay_export_failure(
            ReplayExportFailureKind::IoFailure,
            "replay MP4 output already exists",
        ));
    }
    let (mut partial, file) = create_replay_partial_file(&output_path)?;
    let mut writer = std::io::BufWriter::new(file);
    let receipt = write_prepared_replay_mp4(&mut writer, &input, &plan)?;
    writer.flush().map_err(|error| {
        replay_export_failure(
            ReplayExportFailureKind::FinalizationFailure,
            format!("replay MP4 flush failed: {error}"),
        )
    })?;
    writer.get_ref().sync_all().map_err(|error| {
        replay_export_failure(
            ReplayExportFailureKind::FinalizationFailure,
            format!("replay MP4 finalization failed: {error}"),
        )
    })?;
    drop(writer);
    std::fs::rename(&partial.path, &output_path).map_err(|error| {
        replay_export_failure(
            ReplayExportFailureKind::FinalizationFailure,
            format!("replay MP4 atomic publication failed: {error}"),
        )
    })?;
    partial.published = true;
    Ok(receipt)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[cfg_attr(not(test), allow(dead_code))]
struct ReplayBufferStatus {
    packet_count: usize,
    total_bytes: usize,
    first_packet_pts_100ns: Option<i64>,
    last_packet_pts_100ns: Option<i64>,
    keyframes: usize,
    evicted_packets: u64,
    coverage_gaps: u64,
}

#[derive(Debug)]
struct EncodedReplayBuffer {
    packets: VecDeque<Arc<EncodedH264Packet>>,
    total_bytes: usize,
    max_duration_100ns: i64,
    max_bytes: usize,
    evicted_packets: u64,
    coverage_gaps: u64,
}

impl EncodedReplayBuffer {
    fn new() -> Self {
        Self::with_limits(REPLAY_MAX_DURATION_100NS, REPLAY_MAX_BYTES)
            .expect("frozen replay limits are positive")
    }

    fn with_limits(max_duration_100ns: i64, max_bytes: usize) -> Result<Self, ReplayBufferError> {
        if max_duration_100ns <= 0 || max_bytes == 0 {
            return Err(ReplayBufferError::InvalidLimits);
        }
        Ok(Self {
            packets: VecDeque::new(),
            total_bytes: 0,
            max_duration_100ns,
            max_bytes,
            evicted_packets: 0,
            coverage_gaps: 0,
        })
    }

    fn push(&mut self, packet: EncodedH264Packet) -> Result<(), ReplayBufferError> {
        if packet.bytes.is_empty() || packet.pts_100ns < 0 || packet.duration_100ns <= 0 {
            return Err(ReplayBufferError::InvalidPacket);
        }
        if packet.bytes.len() > self.max_bytes {
            return Err(ReplayBufferError::ByteOverflow);
        }
        let packet_end = packet
            .pts_100ns
            .checked_add(packet.duration_100ns)
            .ok_or(ReplayBufferError::InvalidPacket)?;
        if let Some(previous) = self.packets.back() {
            if packet.pts_100ns < previous.pts_100ns {
                return Err(ReplayBufferError::TimestampRegression);
            }
            let previous_end = previous
                .pts_100ns
                .checked_add(previous.duration_100ns)
                .ok_or(ReplayBufferError::InvalidPacket)?;
            if packet.pts_100ns > previous_end {
                self.coverage_gaps += 1;
            }
        }
        self.total_bytes = self
            .total_bytes
            .checked_add(packet.bytes.len())
            .ok_or(ReplayBufferError::ByteOverflow)?;
        self.packets.push_back(Arc::new(packet));
        self.evict_to_limits(packet_end);
        Ok(())
    }

    #[cfg_attr(not(test), allow(dead_code))]
    fn snapshot(
        &self,
        requested_start_100ns: i64,
        requested_end_100ns: i64,
    ) -> Result<ReplaySnapshot, ReplayBufferError> {
        if requested_start_100ns < 0 || requested_end_100ns <= requested_start_100ns {
            return Err(ReplayBufferError::InvalidWindow);
        }
        if requested_end_100ns - requested_start_100ns > self.max_duration_100ns {
            return Err(ReplayBufferError::WindowTooLong);
        }
        let keyframe_index = self
            .packets
            .iter()
            .rposition(|packet| packet.keyframe && packet.pts_100ns <= requested_start_100ns)
            .ok_or(ReplayBufferError::MissingKeyframeCoverage)?;
        let decode_start_100ns = self.packets[keyframe_index].pts_100ns;
        let mut packets = Vec::new();
        let mut total_bytes = 0usize;
        let mut covered_until = decode_start_100ns;
        let mut tolerated_gaps = 0u64;
        for packet in self.packets.iter().skip(keyframe_index) {
            if packet.pts_100ns >= requested_end_100ns {
                break;
            }
            if packet.pts_100ns > covered_until {
                if packet.pts_100ns - covered_until > REPLAY_TOLERATED_GAP_100NS {
                    return Err(ReplayBufferError::CoverageGap);
                }
                tolerated_gaps += 1;
            }
            covered_until = covered_until.max(
                packet
                    .pts_100ns
                    .checked_add(packet.duration_100ns)
                    .ok_or(ReplayBufferError::InvalidPacket)?,
            );
            total_bytes = total_bytes
                .checked_add(packet.bytes.len())
                .ok_or(ReplayBufferError::ByteOverflow)?;
            packets.push(Arc::clone(packet));
        }
        if covered_until < requested_end_100ns {
            return Err(ReplayBufferError::IncompleteCoverage);
        }
        Ok(ReplaySnapshot {
            packets,
            requested_start_100ns,
            requested_end_100ns,
            decode_start_100ns,
            start_offset_100ns: requested_start_100ns - decode_start_100ns,
            end_offset_100ns: requested_end_100ns - decode_start_100ns,
            total_bytes,
            tolerated_gaps,
        })
    }

    #[cfg_attr(not(test), allow(dead_code))]
    fn status(&self) -> ReplayBufferStatus {
        ReplayBufferStatus {
            packet_count: self.packets.len(),
            total_bytes: self.total_bytes,
            first_packet_pts_100ns: self.packets.front().map(|packet| packet.pts_100ns),
            last_packet_pts_100ns: self.packets.back().map(|packet| packet.pts_100ns),
            keyframes: self.packets.iter().filter(|packet| packet.keyframe).count(),
            evicted_packets: self.evicted_packets,
            coverage_gaps: self.coverage_gaps,
        }
    }

    fn evict_to_limits(&mut self, latest_end_100ns: i64) {
        let mut evicted = false;
        while self.exceeds_limits(latest_end_100ns) {
            self.pop_front();
            evicted = true;
        }
        if evicted {
            while self.packets.front().is_some_and(|packet| !packet.keyframe) {
                self.pop_front();
            }
        }
    }

    fn exceeds_limits(&self, latest_end_100ns: i64) -> bool {
        self.total_bytes > self.max_bytes
            || self
                .packets
                .front()
                .is_some_and(|first| latest_end_100ns - first.pts_100ns > self.max_duration_100ns)
    }

    fn pop_front(&mut self) {
        if let Some(packet) = self.packets.pop_front() {
            self.total_bytes -= packet.bytes.len();
            self.evicted_packets += 1;
        }
    }
}

fn replay_buffer_export_failure(error: ReplayBufferError) -> ReplayExportFailure {
    let kind = match error {
        ReplayBufferError::InvalidLimits | ReplayBufferError::InvalidPacket => {
            ReplayExportFailureKind::InvalidSnapshot
        }
        ReplayBufferError::TimestampRegression => ReplayExportFailureKind::UnsupportedPacketTiming,
        ReplayBufferError::ByteOverflow => ReplayExportFailureKind::TimelineOverflow,
        ReplayBufferError::InvalidWindow => ReplayExportFailureKind::InvalidWindow,
        ReplayBufferError::WindowTooLong => ReplayExportFailureKind::WindowTooLong,
        ReplayBufferError::MissingKeyframeCoverage => {
            ReplayExportFailureKind::MissingKeyframeCoverage
        }
        ReplayBufferError::IncompleteCoverage => ReplayExportFailureKind::IncompleteCoverage,
        ReplayBufferError::CoverageGap => ReplayExportFailureKind::CoverageGap,
    };
    replay_export_failure(kind, format!("replay snapshot failed: {error:?}"))
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WindowCaptureStatus {
    pub supported: bool,
    pub enabled: bool,
    pub recording: bool,
    pub queued_frames: usize,
    pub metadata_dropped_frames: u64,
    pub invalid_frames: u64,
    pub captured_frames: u64,
    pub writer_submitted_frames: u64,
    pub writer_first_system_relative_time_100ns: Option<i64>,
    pub writer_last_system_relative_time_100ns: Option<i64>,
    pub encoder_errors: u64,
    pub writer_dropped_frames: u64,
    pub adapter_identity: Option<String>,
    pub encoder_path: Option<HardwareEncoderPath>,
    pub first_packet_pts_100ns: Option<i64>,
    pub last_packet_pts_100ns: Option<i64>,
    pub submitted_packets: u64,
    pub dropped_packets: u64,
    pub last_encoder_failure: Option<HardwareEncoderFailure>,
    pub first_system_relative_time_100ns: Option<i64>,
    pub last_system_relative_time_100ns: Option<i64>,
    pub clock_source: &'static str,
    pub timebase_version: &'static str,
    pub clock_anchor_utc_ms: Option<i64>,
    pub clock_anchor_qpc_ns: Option<u128>,
}

pub struct FrameQueue {
    capacity: usize,
    frames: VecDeque<FrameSample>,
    metadata_dropped_frames: u64,
    invalid_frames: u64,
    captured_frames: u64,
    writer_submitted_frames: u64,
    writer_first_system_relative_time_100ns: Option<i64>,
    writer_last_system_relative_time_100ns: Option<i64>,
    encoder_errors: u64,
    writer_dropped_frames: u64,
    adapter_identity: Option<String>,
    encoder_path: Option<HardwareEncoderPath>,
    first_packet_pts_100ns: Option<i64>,
    last_packet_pts_100ns: Option<i64>,
    submitted_packets: u64,
    dropped_packets: u64,
    last_encoder_failure: Option<HardwareEncoderFailure>,
    first_system_relative_time_100ns: Option<i64>,
    last_system_relative_time_100ns: Option<i64>,
}

impl FrameQueue {
    pub fn new(capacity: usize) -> io::Result<Self> {
        if capacity == 0 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "frame queue capacity must be positive",
            ));
        }
        Ok(Self {
            capacity,
            frames: VecDeque::with_capacity(capacity),
            metadata_dropped_frames: 0,
            invalid_frames: 0,
            captured_frames: 0,
            writer_submitted_frames: 0,
            writer_first_system_relative_time_100ns: None,
            writer_last_system_relative_time_100ns: None,
            encoder_errors: 0,
            writer_dropped_frames: 0,
            adapter_identity: None,
            encoder_path: None,
            first_packet_pts_100ns: None,
            last_packet_pts_100ns: None,
            submitted_packets: 0,
            dropped_packets: 0,
            last_encoder_failure: None,
            first_system_relative_time_100ns: None,
            last_system_relative_time_100ns: None,
        })
    }

    pub fn try_push(&mut self, frame: FrameSample) -> io::Result<FrameEnqueueResult> {
        self.observe_frame(&frame)?;
        if self.frames.len() >= self.capacity {
            self.metadata_dropped_frames += 1;
            return Ok(FrameEnqueueResult::DroppedBackpressure);
        }
        self.frames.push_back(frame);
        Ok(FrameEnqueueResult::Enqueued)
    }

    pub fn record_metadata(&mut self, frame: &FrameSample) -> io::Result<()> {
        self.observe_frame(frame)
    }

    fn observe_frame(&mut self, frame: &FrameSample) -> io::Result<()> {
        frame.validate().inspect_err(|_| {
            self.invalid_frames += 1;
        })?;
        if self
            .last_system_relative_time_100ns
            .is_some_and(|previous| frame.system_relative_time_100ns < previous)
        {
            self.invalid_frames += 1;
            return Err(invalid_frame("frame timestamps must be monotonic"));
        }
        self.last_system_relative_time_100ns = Some(frame.system_relative_time_100ns);
        self.captured_frames += 1;
        self.first_system_relative_time_100ns
            .get_or_insert(frame.system_relative_time_100ns);
        Ok(())
    }

    #[cfg(test)]
    pub fn len(&self) -> usize {
        self.frames.len()
    }

    pub fn record_writer_submission(&mut self, timestamp: i64) {
        self.writer_submitted_frames += 1;
        self.writer_first_system_relative_time_100ns
            .get_or_insert(timestamp);
        self.writer_last_system_relative_time_100ns = Some(timestamp);
    }

    pub fn record_encoder_error(&mut self) {
        self.encoder_errors += 1;
    }

    pub fn record_writer_drop(&mut self) {
        self.writer_dropped_frames += 1;
    }

    pub fn configure_hardware_encoder(
        &mut self,
        adapter_identity: impl Into<String>,
        path: HardwareEncoderPath,
    ) -> Result<(), HardwareEncoderFailure> {
        let path = path.require_automatic_hardware()?;
        let adapter_identity = adapter_identity.into();
        if adapter_identity.is_empty() {
            return Err(HardwareEncoderFailure::AdapterMismatch);
        }
        self.adapter_identity = Some(adapter_identity);
        self.encoder_path = Some(path);
        Ok(())
    }

    pub fn record_hardware_packet(&mut self, pts_100ns: i64) -> Result<(), HardwareEncoderFailure> {
        if self.encoder_path.is_none() {
            self.record_hardware_failure(HardwareEncoderFailure::EncoderSetupFailure);
            return Err(HardwareEncoderFailure::EncoderSetupFailure);
        }
        if pts_100ns < 0 {
            self.record_hardware_failure(HardwareEncoderFailure::InvalidPacket);
            return Err(HardwareEncoderFailure::InvalidPacket);
        }
        self.first_packet_pts_100ns.get_or_insert(pts_100ns);
        self.last_packet_pts_100ns = Some(pts_100ns);
        self.submitted_packets += 1;
        Ok(())
    }

    pub fn record_hardware_failure(&mut self, failure: HardwareEncoderFailure) {
        self.last_encoder_failure = Some(failure);
        match failure {
            HardwareEncoderFailure::Backpressure => self.dropped_packets += 1,
            _ => self.encoder_errors += 1,
        }
    }

    pub fn reset(&mut self) {
        self.frames.clear();
        self.metadata_dropped_frames = 0;
        self.invalid_frames = 0;
        self.captured_frames = 0;
        self.writer_submitted_frames = 0;
        self.writer_first_system_relative_time_100ns = None;
        self.writer_last_system_relative_time_100ns = None;
        self.encoder_errors = 0;
        self.writer_dropped_frames = 0;
        self.adapter_identity = None;
        self.encoder_path = None;
        self.first_packet_pts_100ns = None;
        self.last_packet_pts_100ns = None;
        self.submitted_packets = 0;
        self.dropped_packets = 0;
        self.last_encoder_failure = None;
        self.first_system_relative_time_100ns = None;
        self.last_system_relative_time_100ns = None;
    }

    pub fn status(&self, enabled: bool, recording: bool) -> WindowCaptureStatus {
        WindowCaptureStatus {
            supported: cfg!(windows),
            enabled,
            recording,
            queued_frames: self.frames.len(),
            metadata_dropped_frames: self.metadata_dropped_frames,
            invalid_frames: self.invalid_frames,
            captured_frames: self.captured_frames,
            writer_submitted_frames: self.writer_submitted_frames,
            writer_first_system_relative_time_100ns: self.writer_first_system_relative_time_100ns,
            writer_last_system_relative_time_100ns: self.writer_last_system_relative_time_100ns,
            encoder_errors: self.encoder_errors,
            writer_dropped_frames: self.writer_dropped_frames,
            adapter_identity: self.adapter_identity.clone(),
            encoder_path: self.encoder_path,
            first_packet_pts_100ns: self.first_packet_pts_100ns,
            last_packet_pts_100ns: self.last_packet_pts_100ns,
            submitted_packets: self.submitted_packets,
            dropped_packets: self.dropped_packets,
            last_encoder_failure: self.last_encoder_failure,
            first_system_relative_time_100ns: self.first_system_relative_time_100ns,
            last_system_relative_time_100ns: self.last_system_relative_time_100ns,
            clock_source: "utc_epoch_ms+qpc+wgc_system_relative_time",
            timebase_version: "time_alignment.v2",
            clock_anchor_utc_ms: None,
            clock_anchor_qpc_ns: None,
        }
    }
}

pub struct WindowCaptureState {
    enabled: bool,
    recording: bool,
    queue: Arc<Mutex<FrameQueue>>,
    clock_metadata: Option<CaptureClockMetadata>,
    #[cfg(windows)]
    worker: Option<WindowCaptureWorker>,
}

#[cfg(windows)]
#[allow(dead_code)] // Reserved for the later Capture Coordinator, not renderer exposure.
enum WindowCaptureCommand {
    ExportReplay {
        requested_start_100ns: i64,
        requested_end_100ns: i64,
        output_path: PathBuf,
        response: std::sync::mpsc::SyncSender<Result<ReplayExportReceipt, ReplayExportFailure>>,
    },
}

#[cfg(windows)]
struct WindowCaptureWorker {
    stop: Arc<AtomicBool>,
    join: Option<JoinHandle<Result<(), String>>>,
    #[allow(dead_code)] // Used by the later native Capture Coordinator.
    command_sender: std::sync::mpsc::SyncSender<WindowCaptureCommand>,
}

#[cfg(windows)]
impl Drop for WindowCaptureWorker {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(join) = self.join.take() {
            let _ = join.join();
        }
    }
}

impl WindowCaptureState {
    pub fn new(capacity: usize) -> io::Result<Self> {
        Ok(Self {
            enabled: false,
            recording: false,
            queue: Arc::new(Mutex::new(FrameQueue::new(capacity)?)),
            clock_metadata: None,
            #[cfg(windows)]
            worker: None,
        })
    }

    pub fn status(&self) -> WindowCaptureStatus {
        let mut status = self
            .queue
            .lock()
            .expect("window capture queue mutex poisoned")
            .status(self.enabled, self.recording);
        if let Some(clock) = self.clock_metadata {
            status.clock_anchor_utc_ms = Some(clock.utc_epoch_ms);
            status.clock_anchor_qpc_ns = Some(clock.qpc_ns);
        }
        status
    }

    #[allow(dead_code)] // Task 3 native boundary; Run finalization wiring is out of scope.
    pub fn request_replay_export(
        &self,
        requested_start_100ns: i64,
        requested_end_100ns: i64,
        output_path: PathBuf,
    ) -> Result<
        std::sync::mpsc::Receiver<Result<ReplayExportReceipt, ReplayExportFailure>>,
        ReplayExportFailure,
    > {
        #[cfg(windows)]
        {
            let worker = self.worker.as_ref().ok_or_else(|| {
                replay_export_failure(
                    ReplayExportFailureKind::CaptureUnavailable,
                    "hardware window capture is not running",
                )
            })?;
            let (response, receiver) = std::sync::mpsc::sync_channel(1);
            worker
                .command_sender
                .try_send(WindowCaptureCommand::ExportReplay {
                    requested_start_100ns,
                    requested_end_100ns,
                    output_path,
                    response,
                })
                .map_err(|error| match error {
                    std::sync::mpsc::TrySendError::Full(_) => replay_export_failure(
                        ReplayExportFailureKind::ExportBusy,
                        "hardware replay export queue is busy",
                    ),
                    std::sync::mpsc::TrySendError::Disconnected(_) => replay_export_failure(
                        ReplayExportFailureKind::CaptureUnavailable,
                        "hardware window capture worker is unavailable",
                    ),
                })?;
            Ok(receiver)
        }
        #[cfg(not(windows))]
        {
            let _ = (requested_start_100ns, requested_end_100ns, output_path);
            Err(replay_export_failure(
                ReplayExportFailureKind::CaptureUnavailable,
                "hardware replay export is only supported on Windows",
            ))
        }
    }

    pub fn epoch_window_to_replay_pts(
        &self,
        start_epoch_ms: i64,
        end_epoch_ms: i64,
    ) -> Result<(i64, i64), String> {
        if end_epoch_ms <= start_epoch_ms {
            return Err("capture window is invalid".to_string());
        }
        let duration_100ns = i128::from(end_epoch_ms)
            .checked_sub(i128::from(start_epoch_ms))
            .and_then(|duration_ms| duration_ms.checked_mul(10_000))
            .ok_or_else(|| "capture window duration overflow".to_string())?;
        if duration_100ns > i128::from(REPLAY_MAX_DURATION_100NS) {
            return Err("capture window exceeds replay retention".to_string());
        }
        let clock = self
            .clock_metadata
            .ok_or_else(|| "capture clock is unavailable".to_string())?;
        if clock.clock_source != "utc_epoch_ms+qpc+wgc_system_relative_time" {
            return Err("capture clock source is unsupported".to_string());
        }
        let first_source_pts = self
            .queue
            .lock()
            .map_err(|_| "window capture queue is unavailable".to_string())?
            .first_system_relative_time_100ns
            .ok_or_else(|| "capture source PTS anchor is unavailable".to_string())?;
        let qpc_anchor_100ns = i128::try_from(clock.qpc_ns / 100)
            .map_err(|_| "capture clock anchor overflow".to_string())?;
        let delta_start_100ns = i128::from(start_epoch_ms)
            .checked_sub(i128::from(clock.utc_epoch_ms))
            .and_then(|delta_ms| delta_ms.checked_mul(10_000))
            .ok_or_else(|| "capture start mapping overflow".to_string())?;
        let start_100ns = qpc_anchor_100ns
            .checked_add(delta_start_100ns)
            .ok_or_else(|| "capture start mapping overflow".to_string())?;
        let end_100ns = start_100ns
            .checked_add(duration_100ns)
            .ok_or_else(|| "capture end mapping overflow".to_string())?;
        let start_100ns =
            i64::try_from(start_100ns).map_err(|_| "capture start mapping overflow".to_string())?;
        let end_100ns =
            i64::try_from(end_100ns).map_err(|_| "capture end mapping overflow".to_string())?;
        if end_100ns <= start_100ns || first_source_pts < 0 {
            return Err("capture source PTS anchor is invalid".to_string());
        }
        if start_100ns < first_source_pts {
            return Err("capture window precedes the first source PTS".to_string());
        }
        Ok((start_100ns, end_100ns))
    }

    pub fn start_for_window(&mut self, hwnd: usize) -> Result<WindowCaptureStatus, String> {
        self.start(hwnd, None)
    }

    #[allow(dead_code)] // Explicit CPU-backed recording remains a manual diagnostic baseline.
    pub fn start_recording_for_window(
        &mut self,
        hwnd: usize,
        output_path: PathBuf,
    ) -> Result<WindowCaptureStatus, String> {
        self.start(hwnd, Some(output_path))
    }

    fn start(
        &mut self,
        hwnd: usize,
        recording_path: Option<PathBuf>,
    ) -> Result<WindowCaptureStatus, String> {
        if self.enabled {
            return Err("window capture is already enabled".to_string());
        }
        self.queue
            .lock()
            .map_err(|_| "window capture queue is unavailable".to_string())?
            .reset();
        #[cfg(windows)]
        {
            let clock = crate::raw_input::capture_clock_anchor();
            let clock_source = match clock.clock_source {
                "utc_epoch_ms+qpc" => "utc_epoch_ms+qpc+wgc_system_relative_time",
                _ => "utc_epoch_ms+monotonic_fallback+wgc_system_relative_time",
            };
            let clock_metadata = CaptureClockMetadata {
                utc_epoch_ms: clock.utc_epoch_ms,
                qpc_ns: clock.monotonic_elapsed_ns,
                clock_source,
                timebase_version: "time_alignment.v2",
            };
            let stop = Arc::new(AtomicBool::new(false));
            let queue = Arc::clone(&self.queue);
            let thread_stop = Arc::clone(&stop);
            let (ready_tx, ready_rx) = std::sync::mpsc::sync_channel(1);
            let (command_sender, command_receiver) = std::sync::mpsc::sync_channel(1);
            let join = thread::spawn(move || {
                run_wgc_window_capture(
                    hwnd,
                    queue,
                    thread_stop,
                    ready_tx,
                    recording_path,
                    clock_metadata,
                    command_receiver,
                )
            });
            match ready_rx.recv_timeout(std::time::Duration::from_secs(5)) {
                Ok(Ok(())) => {
                    self.worker = Some(WindowCaptureWorker {
                        stop,
                        join: Some(join),
                        command_sender,
                    });
                    self.clock_metadata = Some(clock_metadata);
                    self.enabled = true;
                    self.recording = true;
                    Ok(self.status())
                }
                Ok(Err(error)) => {
                    let _ = join.join();
                    Err(error)
                }
                Err(error) => {
                    stop.store(true, Ordering::Release);
                    let _ = join.join();
                    Err(format!("window capture startup timed out: {error}"))
                }
            }
        }
        #[cfg(not(windows))]
        {
            let _ = hwnd;
            let _ = recording_path;
            Err("Windows.Graphics.Capture is only supported on Windows".to_string())
        }
    }

    pub fn stop(&mut self) -> WindowCaptureStatus {
        #[cfg(windows)]
        if let Some(mut worker) = self.worker.take() {
            worker.stop.store(true, Ordering::Release);
            if let Some(join) = worker.join.take() {
                let _ = join.join();
            }
        }
        self.enabled = false;
        self.recording = false;
        self.status()
    }
}

#[cfg(windows)]
struct D3dFrameReadback {
    context: windows::Win32::Graphics::Direct3D11::ID3D11DeviceContext,
    staging: Vec<windows::Win32::Graphics::Direct3D11::ID3D11Texture2D>,
    width: u32,
    height: u32,
    next_staging: usize,
    pending: VecDeque<PendingReadback>,
}

#[cfg(windows)]
struct PendingReadback {
    staging_index: usize,
    sample: FrameSample,
}

#[cfg(windows)]
struct ReadbackSubmission {
    completed: Option<FrameSample>,
    queued: bool,
}

#[cfg(windows)]
enum ReadbackMapError {
    NotReady,
    Message(String),
}

#[cfg(windows)]
const READBACK_TEXTURE_COUNT: usize = 3;

#[cfg(windows)]
impl D3dFrameReadback {
    fn new(
        device: &windows::Win32::Graphics::Direct3D11::ID3D11Device,
        context: &windows::Win32::Graphics::Direct3D11::ID3D11DeviceContext,
        width: u32,
        height: u32,
    ) -> Result<Self, String> {
        use windows::Win32::Graphics::Direct3D11::{
            D3D11_CPU_ACCESS_READ, D3D11_TEXTURE2D_DESC, D3D11_USAGE_STAGING,
        };
        use windows::Win32::Graphics::Dxgi::Common::{
            DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_SAMPLE_DESC,
        };

        let description = D3D11_TEXTURE2D_DESC {
            Width: width,
            Height: height,
            MipLevels: 1,
            ArraySize: 1,
            Format: DXGI_FORMAT_B8G8R8A8_UNORM,
            SampleDesc: DXGI_SAMPLE_DESC {
                Count: 1,
                Quality: 0,
            },
            Usage: D3D11_USAGE_STAGING,
            BindFlags: 0,
            CPUAccessFlags: D3D11_CPU_ACCESS_READ.0 as u32,
            MiscFlags: 0,
        };
        let mut staging = Vec::with_capacity(READBACK_TEXTURE_COUNT);
        for _ in 0..READBACK_TEXTURE_COUNT {
            let mut texture = None;
            unsafe { device.CreateTexture2D(&description, None, Some(&mut texture)) }
                .map_err(|error| format!("recording staging texture creation failed: {error}"))?;
            staging.push(
                texture.ok_or_else(|| "recording staging texture was not returned".to_string())?,
            );
        }
        Ok(Self {
            context: context.clone(),
            staging,
            width,
            height,
            next_staging: 0,
            pending: VecDeque::with_capacity(READBACK_TEXTURE_COUNT),
        })
    }

    fn submit_frame(
        &mut self,
        frame: &windows::Graphics::Capture::Direct3D11CaptureFrame,
        sample: FrameSample,
    ) -> Result<ReadbackSubmission, String> {
        use windows::core::Interface;
        use windows::Win32::Graphics::Direct3D11::ID3D11Texture2D;
        use windows::Win32::System::WinRT::Direct3D11::IDirect3DDxgiInterfaceAccess;

        let completed = self.try_map_pending()?;
        let Some(staging_index) = self.find_free_staging() else {
            return Ok(ReadbackSubmission {
                completed,
                queued: false,
            });
        };
        let surface = frame
            .Surface()
            .map_err(|error| format!("capture frame surface failed: {error}"))?;
        let access = surface
            .cast::<IDirect3DDxgiInterfaceAccess>()
            .map_err(|error| format!("capture surface DXGI access failed: {error}"))?;
        let source: ID3D11Texture2D = unsafe { access.GetInterface() }
            .map_err(|error| format!("capture surface texture access failed: {error}"))?;
        unsafe {
            self.context
                .CopyResource(&self.staging[staging_index], &source);
        }

        self.pending.push_back(PendingReadback {
            staging_index,
            sample,
        });
        self.next_staging = (staging_index + 1) % self.staging.len();
        Ok(ReadbackSubmission {
            completed,
            queued: true,
        })
    }

    fn find_free_staging(&self) -> Option<usize> {
        if self.pending.len() >= self.staging.len() {
            return None;
        }
        (0..self.staging.len())
            .map(|offset| (self.next_staging + offset) % self.staging.len())
            .find(|index| {
                !self
                    .pending
                    .iter()
                    .any(|pending| pending.staging_index == *index)
            })
    }

    fn try_map_pending(&mut self) -> Result<Option<FrameSample>, String> {
        let Some(pending) = self.pending.front() else {
            return Ok(None);
        };
        let staging_index = pending.staging_index;
        let pixels = match self.map_bgra8(staging_index) {
            Ok(pixels) => pixels,
            Err(ReadbackMapError::NotReady) => return Ok(None),
            Err(ReadbackMapError::Message(error)) => return Err(error),
        };
        let mut completed = self
            .pending
            .pop_front()
            .expect("pending readback exists after mapping front");
        completed.sample.bgra8 = pixels;
        Ok(Some(completed.sample))
    }

    fn map_bgra8(&self, staging_index: usize) -> Result<Vec<u8>, ReadbackMapError> {
        use std::mem::MaybeUninit;
        use windows::Win32::Graphics::Direct3D11::{
            D3D11_MAPPED_SUBRESOURCE, D3D11_MAP_FLAG_DO_NOT_WAIT, D3D11_MAP_READ,
        };
        let mut mapped = D3D11_MAPPED_SUBRESOURCE::default();
        unsafe {
            self.context.Map(
                &self.staging[staging_index],
                0,
                D3D11_MAP_READ,
                D3D11_MAP_FLAG_DO_NOT_WAIT.0 as u32,
                Some(&mut mapped),
            )
        }
        .map_err(|error| {
            if error.code() == windows::Win32::Graphics::Dxgi::DXGI_ERROR_WAS_STILL_DRAWING {
                ReadbackMapError::NotReady
            } else {
                ReadbackMapError::Message(format!("recording staging texture map failed: {error}"))
            }
        })?;

        let row_bytes = (self.width as usize)
            .checked_mul(FRAME_PIXEL_BYTES)
            .ok_or_else(|| {
                ReadbackMapError::Message("recording row byte size overflow".to_string())
            })?;
        let total_bytes = row_bytes.checked_mul(self.height as usize).ok_or_else(|| {
            ReadbackMapError::Message("recording frame byte size overflow".to_string())
        })?;
        let result = if mapped.pData.is_null() || mapped.RowPitch < row_bytes as u32 {
            Err(ReadbackMapError::Message(
                "recording staging texture returned an invalid mapping".to_string(),
            ))
        } else {
            let mut pixels = Vec::<MaybeUninit<u8>>::with_capacity(total_bytes);
            unsafe { pixels.set_len(total_bytes) };
            if mapped.RowPitch == row_bytes as u32 {
                // The common BGRA8 staging layout is tightly packed. Copying it
                // in one operation avoids a per-row call and pointer arithmetic.
                unsafe {
                    std::ptr::copy_nonoverlapping(
                        mapped.pData.cast::<u8>(),
                        pixels.as_mut_ptr().cast::<u8>(),
                        total_bytes,
                    );
                }
            } else {
                for row in 0..self.height as usize {
                    unsafe {
                        std::ptr::copy_nonoverlapping(
                            (mapped.pData as *const u8).add(row * mapped.RowPitch as usize),
                            pixels.as_mut_ptr().cast::<u8>().add(row * row_bytes),
                            row_bytes,
                        );
                    }
                }
            }
            let pixels = unsafe {
                let pointer = pixels.as_mut_ptr().cast::<u8>();
                let length = pixels.len();
                let capacity = pixels.capacity();
                std::mem::forget(pixels);
                Vec::from_raw_parts(pointer, length, capacity)
            };
            Ok(pixels)
        };
        unsafe { self.context.Unmap(&self.staging[staging_index], 0) };
        result
    }
}

#[cfg(windows)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum HardwareMftEvent {
    NeedInput,
    HaveOutput,
    DrainComplete,
    Error,
}

#[cfg(windows)]
#[derive(Debug)]
struct HardwareEncoderError {
    failure: HardwareEncoderFailure,
    message: String,
}

#[cfg(windows)]
impl HardwareEncoderError {
    fn new(failure: HardwareEncoderFailure, message: impl Into<String>) -> Self {
        Self {
            failure,
            message: message.into(),
        }
    }
}

#[cfg(windows)]
#[derive(Clone)]
struct HardwareMftCallbackHandle {
    callback: Arc<AtomicPtr<std::ffi::c_void>>,
}

#[cfg(windows)]
#[windows::core::implement(windows::Win32::Media::MediaFoundation::IMFAsyncCallback)]
struct HardwareMftEventCallback {
    generator: windows::Win32::Media::MediaFoundation::IMFMediaEventGenerator,
    sender: std::sync::mpsc::SyncSender<HardwareMftEvent>,
    handle: HardwareMftCallbackHandle,
}

#[cfg(windows)]
impl windows::Win32::Media::MediaFoundation::IMFAsyncCallback_Impl
    for HardwareMftEventCallback_Impl
{
    fn GetParameters(&self, flags: *mut u32, queue: *mut u32) -> windows::core::Result<()> {
        unsafe {
            if !flags.is_null() {
                *flags = 0;
            }
            if !queue.is_null() {
                *queue = windows::Win32::Media::MediaFoundation::MFASYNC_CALLBACK_QUEUE_STANDARD;
            }
        }
        Ok(())
    }

    fn Invoke(
        &self,
        result: windows::core::Ref<'_, windows::Win32::Media::MediaFoundation::IMFAsyncResult>,
    ) -> windows::core::Result<()> {
        use windows::core::Interface;
        use windows::Win32::Media::MediaFoundation::{
            METransformDrainComplete, METransformHaveOutput, METransformNeedInput,
        };

        let result = result
            .as_ref()
            .ok_or_else(windows::core::Error::from_win32)?;
        let event = unsafe { self.generator.EndGetEvent(result) };
        let signal = match event {
            Ok(event) if unsafe { event.GetStatus() }.is_ok_and(|status| status.is_ok()) => {
                match unsafe { event.GetType() } {
                    Ok(kind) if kind == METransformNeedInput.0 as u32 => {
                        HardwareMftEvent::NeedInput
                    }
                    Ok(kind) if kind == METransformHaveOutput.0 as u32 => {
                        HardwareMftEvent::HaveOutput
                    }
                    Ok(kind) if kind == METransformDrainComplete.0 as u32 => {
                        HardwareMftEvent::DrainComplete
                    }
                    _ => HardwareMftEvent::Error,
                }
            }
            Err(_) => HardwareMftEvent::Error,
            Ok(_) => HardwareMftEvent::Error,
        };
        let _ = self.sender.try_send(signal);

        // The encoder owns the callback reference; borrow it atomically so the
        // Media Foundation callback never waits on a producer-side mutex.
        let raw_callback = self.handle.callback.load(Ordering::Acquire);
        let callback = unsafe {
            windows::Win32::Media::MediaFoundation::IMFAsyncCallback::from_raw_borrowed(
                &raw_callback,
            )
        };
        if let Some(callback) = callback {
            if unsafe { self.generator.BeginGetEvent(callback, None) }.is_err() {
                let _ = self.sender.try_send(HardwareMftEvent::Error);
            }
        }
        Ok(())
    }
}

#[cfg(windows)]
struct GpuBgraToNv12Converter {
    video_device: windows::Win32::Graphics::Direct3D11::ID3D11VideoDevice,
    video_context: windows::Win32::Graphics::Direct3D11::ID3D11VideoContext,
    enumerator: windows::Win32::Graphics::Direct3D11::ID3D11VideoProcessorEnumerator,
    processor: windows::Win32::Graphics::Direct3D11::ID3D11VideoProcessor,
    output_texture: windows::Win32::Graphics::Direct3D11::ID3D11Texture2D,
    output_view: windows::Win32::Graphics::Direct3D11::ID3D11VideoProcessorOutputView,
}

#[cfg(windows)]
impl GpuBgraToNv12Converter {
    fn new(
        device: &windows::Win32::Graphics::Direct3D11::ID3D11Device,
        context: &windows::Win32::Graphics::Direct3D11::ID3D11DeviceContext,
        width: u32,
        height: u32,
    ) -> Result<Self, HardwareEncoderError> {
        use windows::core::Interface;
        use windows::Win32::Graphics::Direct3D11::{
            D3D11_BIND_RENDER_TARGET, D3D11_BIND_VIDEO_ENCODER, D3D11_TEX2D_VPOV,
            D3D11_TEXTURE2D_DESC, D3D11_USAGE_DEFAULT, D3D11_VIDEO_FRAME_FORMAT_PROGRESSIVE,
            D3D11_VIDEO_PROCESSOR_CONTENT_DESC, D3D11_VIDEO_PROCESSOR_FORMAT_SUPPORT_INPUT,
            D3D11_VIDEO_PROCESSOR_FORMAT_SUPPORT_OUTPUT, D3D11_VIDEO_PROCESSOR_OUTPUT_VIEW_DESC,
            D3D11_VIDEO_PROCESSOR_OUTPUT_VIEW_DESC_0, D3D11_VIDEO_USAGE_PLAYBACK_NORMAL,
            D3D11_VPOV_DIMENSION_TEXTURE2D,
        };
        use windows::Win32::Graphics::Dxgi::Common::{
            DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_FORMAT_NV12, DXGI_RATIONAL, DXGI_SAMPLE_DESC,
        };

        let video_device: windows::Win32::Graphics::Direct3D11::ID3D11VideoDevice =
            device.cast().map_err(|error| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::GpuConversionFailure,
                    format!("D3D11 device does not expose ID3D11VideoDevice: {error}"),
                )
            })?;
        let video_context: windows::Win32::Graphics::Direct3D11::ID3D11VideoContext =
            context.cast().map_err(|error| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::GpuConversionFailure,
                    format!("D3D11 context does not expose ID3D11VideoContext: {error}"),
                )
            })?;
        let content = D3D11_VIDEO_PROCESSOR_CONTENT_DESC {
            InputFrameFormat: D3D11_VIDEO_FRAME_FORMAT_PROGRESSIVE,
            InputFrameRate: DXGI_RATIONAL {
                Numerator: DEFAULT_RECORDING_FPS_NUMERATOR,
                Denominator: DEFAULT_RECORDING_FPS_DENOMINATOR,
            },
            InputWidth: width,
            InputHeight: height,
            OutputFrameRate: DXGI_RATIONAL {
                Numerator: DEFAULT_RECORDING_FPS_NUMERATOR,
                Denominator: DEFAULT_RECORDING_FPS_DENOMINATOR,
            },
            OutputWidth: width,
            OutputHeight: height,
            Usage: D3D11_VIDEO_USAGE_PLAYBACK_NORMAL,
        };
        let enumerator =
            unsafe { video_device.CreateVideoProcessorEnumerator(&content) }.map_err(|error| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::GpuConversionFailure,
                    format!("GPU video processor enumeration failed: {error}"),
                )
            })?;
        let bgra_support =
            unsafe { enumerator.CheckVideoProcessorFormat(DXGI_FORMAT_B8G8R8A8_UNORM) }.map_err(
                |error| {
                    HardwareEncoderError::new(
                        HardwareEncoderFailure::GpuConversionFailure,
                        format!("GPU video processor BGRA capability check failed: {error}"),
                    )
                },
            )?;
        let nv12_support = unsafe { enumerator.CheckVideoProcessorFormat(DXGI_FORMAT_NV12) }
            .map_err(|error| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::GpuConversionFailure,
                    format!("GPU video processor NV12 capability check failed: {error}"),
                )
            })?;
        if bgra_support & D3D11_VIDEO_PROCESSOR_FORMAT_SUPPORT_INPUT.0 as u32 == 0
            || nv12_support & D3D11_VIDEO_PROCESSOR_FORMAT_SUPPORT_OUTPUT.0 as u32 == 0
        {
            return Err(HardwareEncoderError::new(
                HardwareEncoderFailure::GpuConversionFailure,
                "GPU video processor does not support BGRA input and NV12 output",
            ));
        }
        let processor =
            unsafe { video_device.CreateVideoProcessor(&enumerator, 0) }.map_err(|error| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::GpuConversionFailure,
                    format!("GPU video processor creation failed: {error}"),
                )
            })?;
        unsafe {
            video_context.VideoProcessorSetStreamFrameFormat(
                &processor,
                0,
                D3D11_VIDEO_FRAME_FORMAT_PROGRESSIVE,
            );
        }
        let description = D3D11_TEXTURE2D_DESC {
            Width: width,
            Height: height,
            MipLevels: 1,
            ArraySize: 1,
            Format: DXGI_FORMAT_NV12,
            SampleDesc: DXGI_SAMPLE_DESC {
                Count: 1,
                Quality: 0,
            },
            Usage: D3D11_USAGE_DEFAULT,
            BindFlags: (D3D11_BIND_RENDER_TARGET.0 | D3D11_BIND_VIDEO_ENCODER.0) as u32,
            CPUAccessFlags: 0,
            MiscFlags: 0,
        };
        let mut output_texture = None;
        unsafe { device.CreateTexture2D(&description, None, Some(&mut output_texture)) }.map_err(
            |error| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::GpuConversionFailure,
                    format!("GPU NV12 texture creation failed: {error}"),
                )
            },
        )?;
        let output_texture = output_texture.ok_or_else(|| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::GpuConversionFailure,
                "GPU NV12 texture was not returned",
            )
        })?;
        let output_resource: windows::Win32::Graphics::Direct3D11::ID3D11Resource =
            output_texture.cast().map_err(|error| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::GpuConversionFailure,
                    format!("GPU NV12 texture does not expose ID3D11Resource: {error}"),
                )
            })?;
        let output_description = D3D11_VIDEO_PROCESSOR_OUTPUT_VIEW_DESC {
            ViewDimension: D3D11_VPOV_DIMENSION_TEXTURE2D,
            Anonymous: D3D11_VIDEO_PROCESSOR_OUTPUT_VIEW_DESC_0 {
                Texture2D: D3D11_TEX2D_VPOV { MipSlice: 0 },
            },
        };
        let mut output_view = None;
        unsafe {
            video_device.CreateVideoProcessorOutputView(
                &output_resource,
                &enumerator,
                &output_description,
                Some(&mut output_view),
            )
        }
        .map_err(|error| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::GpuConversionFailure,
                format!("GPU NV12 output view creation failed: {error}"),
            )
        })?;
        Ok(Self {
            video_device,
            video_context,
            enumerator,
            processor,
            output_texture,
            output_view: output_view.ok_or_else(|| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::GpuConversionFailure,
                    "GPU NV12 output view was not returned",
                )
            })?,
        })
    }

    fn convert(
        &self,
        source: &windows::Win32::Graphics::Direct3D11::ID3D11Texture2D,
    ) -> Result<&windows::Win32::Graphics::Direct3D11::ID3D11Texture2D, HardwareEncoderError> {
        use std::mem::ManuallyDrop;
        use windows::core::Interface;
        use windows::Win32::Graphics::Direct3D11::{
            D3D11_TEX2D_VPIV, D3D11_VIDEO_PROCESSOR_INPUT_VIEW_DESC,
            D3D11_VIDEO_PROCESSOR_INPUT_VIEW_DESC_0, D3D11_VIDEO_PROCESSOR_STREAM,
            D3D11_VPIV_DIMENSION_TEXTURE2D,
        };

        let source_resource: windows::Win32::Graphics::Direct3D11::ID3D11Resource =
            source.cast().map_err(|error| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::GpuConversionFailure,
                    format!("capture surface does not expose ID3D11Resource: {error}"),
                )
            })?;
        let input_description = D3D11_VIDEO_PROCESSOR_INPUT_VIEW_DESC {
            FourCC: 0,
            ViewDimension: D3D11_VPIV_DIMENSION_TEXTURE2D,
            Anonymous: D3D11_VIDEO_PROCESSOR_INPUT_VIEW_DESC_0 {
                Texture2D: D3D11_TEX2D_VPIV {
                    MipSlice: 0,
                    ArraySlice: 0,
                },
            },
        };
        let mut input_view = None;
        unsafe {
            self.video_device.CreateVideoProcessorInputView(
                &source_resource,
                &self.enumerator,
                &input_description,
                Some(&mut input_view),
            )
        }
        .map_err(|error| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::GpuConversionFailure,
                format!("GPU BGRA input view creation failed: {error}"),
            )
        })?;
        let input_view = input_view.ok_or_else(|| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::GpuConversionFailure,
                "GPU BGRA input view was not returned",
            )
        })?;
        let mut stream = D3D11_VIDEO_PROCESSOR_STREAM {
            Enable: true.into(),
            pInputSurface: ManuallyDrop::new(Some(input_view)),
            ..Default::default()
        };
        let result = unsafe {
            self.video_context.VideoProcessorBlt(
                &self.processor,
                &self.output_view,
                0,
                std::slice::from_ref(&stream),
            )
        }
        .map_err(|error| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::GpuConversionFailure,
                format!("GPU BGRA to NV12 conversion failed: {error}"),
            )
        });
        unsafe { ManuallyDrop::drop(&mut stream.pInputSurface) };
        result?;
        Ok(&self.output_texture)
    }
}

#[cfg(windows)]
struct MediaFoundationPlatform;

#[cfg(windows)]
impl Drop for MediaFoundationPlatform {
    fn drop(&mut self) {
        unsafe { windows::Win32::Media::MediaFoundation::MFShutdown() }.ok();
    }
}

#[cfg(windows)]
struct HardwareH264Encoder {
    transform: windows::Win32::Media::MediaFoundation::IMFTransform,
    converter: GpuBgraToNv12Converter,
    output_stream_info: windows::Win32::Media::MediaFoundation::MFT_OUTPUT_STREAM_INFO,
    event_receiver: std::sync::mpsc::Receiver<HardwareMftEvent>,
    callback_handle: HardwareMftCallbackHandle,
    _callback: windows::Win32::Media::MediaFoundation::IMFAsyncCallback,
    _device_manager: windows::Win32::Media::MediaFoundation::IMFDXGIDeviceManager,
    queue: Arc<Mutex<FrameQueue>>,
    replay: EncodedReplayBuffer,
    sequence_header: Vec<u8>,
    accepts_input: bool,
    _mf_platform: MediaFoundationPlatform,
}

#[cfg(windows)]
impl HardwareH264Encoder {
    fn new(
        device: &windows::Win32::Graphics::Direct3D11::ID3D11Device,
        context: &windows::Win32::Graphics::Direct3D11::ID3D11DeviceContext,
        width: u32,
        height: u32,
        queue: Arc<Mutex<FrameQueue>>,
    ) -> Result<Self, HardwareEncoderError> {
        use windows::core::Interface;
        use windows::Win32::Media::MediaFoundation::{
            eAVEncH264VProfile_Base, MFCreateDXGIDeviceManager, MFMediaType_Video, MFStartup,
            MFVideoFormat_H264, MFVideoFormat_NV12, MFSTARTUP_FULL,
            MFT_MESSAGE_NOTIFY_BEGIN_STREAMING, MFT_MESSAGE_NOTIFY_START_OF_STREAM,
            MFT_MESSAGE_SET_D3D_MANAGER, MF_MT_MPEG2_PROFILE, MF_SA_D3D11_AWARE,
            MF_TRANSFORM_ASYNC, MF_TRANSFORM_ASYNC_UNLOCK, MF_VERSION,
        };

        unsafe { MFStartup(MF_VERSION, MFSTARTUP_FULL) }.map_err(|error| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::HardwareUnavailable,
                format!("Media Foundation startup failed: {error}"),
            )
        })?;
        let setup = (|| {
            let (adapter_luid, adapter_identity) = adapter_identity(device)?;
            let global = enumerate_hardware_h264(None)?;
            if global.is_empty() {
                return Err(HardwareEncoderError::new(
                    HardwareEncoderFailure::HardwareUnavailable,
                    "no Media Foundation hardware H.264 encoder is available",
                ));
            }
            let candidates = enumerate_hardware_h264(Some(adapter_luid))?;
            if candidates.is_empty() {
                return Err(HardwareEncoderError::new(
                    HardwareEncoderFailure::AdapterMismatch,
                    "no Media Foundation hardware H.264 encoder matches the capture adapter",
                ));
            }

            let mut reset_token = 0;
            let mut device_manager = None;
            unsafe { MFCreateDXGIDeviceManager(&mut reset_token, &mut device_manager) }.map_err(
                |error| {
                    HardwareEncoderError::new(
                        HardwareEncoderFailure::EncoderSetupFailure,
                        format!("DXGI device manager creation failed: {error}"),
                    )
                },
            )?;
            let device_manager = device_manager.ok_or_else(|| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::EncoderSetupFailure,
                    "DXGI device manager was not returned",
                )
            })?;
            unsafe { device_manager.ResetDevice(device, reset_token) }.map_err(|error| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::EncoderSetupFailure,
                    format!("DXGI device manager reset failed: {error}"),
                )
            })?;
            let converter = GpuBgraToNv12Converter::new(device, context, width, height)?;
            let input_type = create_video_type(
                MFMediaType_Video,
                MFVideoFormat_NV12,
                width,
                height,
                DEFAULT_RECORDING_FPS_NUMERATOR,
                DEFAULT_RECORDING_FPS_DENOMINATOR,
                0,
            )
            .map_err(|message| {
                HardwareEncoderError::new(HardwareEncoderFailure::EncoderSetupFailure, message)
            })?;
            let output_type = create_video_type(
                MFMediaType_Video,
                MFVideoFormat_H264,
                width,
                height,
                DEFAULT_RECORDING_FPS_NUMERATOR,
                DEFAULT_RECORDING_FPS_DENOMINATOR,
                8_000_000,
            )
            .map_err(|message| {
                HardwareEncoderError::new(HardwareEncoderFailure::EncoderSetupFailure, message)
            })?;
            unsafe {
                input_type
                    .SetUINT32(
                        &windows::Win32::Media::MediaFoundation::MF_MT_INTERLACE_MODE,
                        windows::Win32::Media::MediaFoundation::MFVideoInterlace_Progressive.0
                            as u32,
                    )
                    .map_err(|error| {
                        HardwareEncoderError::new(
                            HardwareEncoderFailure::EncoderSetupFailure,
                            format!("MFT NV12 interlace mode setup failed: {error}"),
                        )
                    })?;
                output_type
                    .SetUINT32(
                        &windows::Win32::Media::MediaFoundation::MF_MT_INTERLACE_MODE,
                        windows::Win32::Media::MediaFoundation::MFVideoInterlace_Progressive.0
                            as u32,
                    )
                    .map_err(|error| {
                        HardwareEncoderError::new(
                            HardwareEncoderFailure::EncoderSetupFailure,
                            format!("MFT H.264 interlace mode setup failed: {error}"),
                        )
                    })?;
                output_type
                    .SetUINT32(&MF_MT_MPEG2_PROFILE, eAVEncH264VProfile_Base.0 as u32)
                    .map_err(|error| {
                        HardwareEncoderError::new(
                            HardwareEncoderFailure::EncoderSetupFailure,
                            format!("MFT H.264 Baseline profile setup failed: {error}"),
                        )
                    })?;
            }

            let candidate_count = candidates.len();
            let mut d3d11_aware_count = 0;
            let mut last_rejection = "no candidate was activated".to_string();
            for activation in candidates {
                let friendly_name = mft_friendly_name(&activation);
                let transform: windows::Win32::Media::MediaFoundation::IMFTransform =
                    match unsafe { activation.ActivateObject() } {
                        Ok(transform) => transform,
                        Err(error) => {
                            last_rejection = format!("{friendly_name}: activation failed: {error}");
                            continue;
                        }
                    };
                let attributes = match unsafe { transform.GetAttributes() } {
                    Ok(attributes) => attributes,
                    Err(error) => {
                        last_rejection =
                            format!("{friendly_name}: MFT attributes query failed: {error}");
                        continue;
                    }
                };
                match unsafe { attributes.GetUINT32(&MF_SA_D3D11_AWARE) } {
                    Ok(value) if value != 0 => d3d11_aware_count += 1,
                    Ok(_) => {
                        last_rejection = format!("{friendly_name}: MFT is not MF_SA_D3D11_AWARE");
                        continue;
                    }
                    Err(error) => {
                        last_rejection =
                            format!("{friendly_name}: MFT D3D11 awareness query failed: {error}");
                        continue;
                    }
                }
                if let Err(error) = unsafe { attributes.SetUINT32(&MF_TRANSFORM_ASYNC_UNLOCK, 1) } {
                    last_rejection = format!("{friendly_name}: MFT async unlock failed: {error}");
                    continue;
                }
                if unsafe { attributes.GetUINT32(&MF_TRANSFORM_ASYNC) }.unwrap_or(0) == 0 {
                    last_rejection =
                        format!("{friendly_name}: MFT did not report MF_TRANSFORM_ASYNC");
                    continue;
                }
                if let Err(error) = unsafe {
                    transform.ProcessMessage(
                        MFT_MESSAGE_SET_D3D_MANAGER,
                        device_manager.as_raw() as usize,
                    )
                } {
                    last_rejection =
                        format!("{friendly_name}: MFT D3D manager setup failed: {error}");
                    continue;
                }
                if let Err(error) = unsafe { transform.SetOutputType(0, &output_type, 0) } {
                    last_rejection =
                        format!("{friendly_name}: MFT H.264 output type setup failed: {error}");
                    continue;
                }
                if let Err(error) = unsafe { transform.SetInputType(0, &input_type, 0) } {
                    last_rejection =
                        format!("{friendly_name}: MFT NV12 input type setup failed: {error}");
                    continue;
                }
                let output_stream_info = match unsafe { transform.GetOutputStreamInfo(0) } {
                    Ok(info) => info,
                    Err(error) => {
                        last_rejection =
                            format!("{friendly_name}: MFT output stream info failed: {error}");
                        continue;
                    }
                };
                let generator: windows::Win32::Media::MediaFoundation::IMFMediaEventGenerator =
                    match transform.cast() {
                        Ok(generator) => generator,
                        Err(error) => {
                            last_rejection =
                                format!("MFT async event generator cast failed: {error}");
                            continue;
                        }
                    };
                let (event_sender, event_receiver) =
                    std::sync::mpsc::sync_channel(DEFAULT_HARDWARE_EVENT_QUEUE_CAPACITY);
                let callback_handle = HardwareMftCallbackHandle {
                    callback: Arc::new(AtomicPtr::new(std::ptr::null_mut())),
                };
                let callback: windows::Win32::Media::MediaFoundation::IMFAsyncCallback =
                    HardwareMftEventCallback {
                        generator: generator.clone(),
                        sender: event_sender,
                        handle: callback_handle.clone(),
                    }
                    .into();
                callback_handle
                    .callback
                    .store(callback.as_raw(), Ordering::Release);
                if let Err(error) = unsafe { generator.BeginGetEvent(&callback, None) } {
                    last_rejection = format!("MFT async event registration failed: {error}");
                    callback_handle
                        .callback
                        .store(std::ptr::null_mut(), Ordering::Release);
                    continue;
                }
                if let Err(error) =
                    unsafe { transform.ProcessMessage(MFT_MESSAGE_NOTIFY_BEGIN_STREAMING, 0) }
                {
                    last_rejection = format!("MFT begin streaming notification failed: {error}");
                    callback_handle
                        .callback
                        .store(std::ptr::null_mut(), Ordering::Release);
                    continue;
                }
                if let Err(error) =
                    unsafe { transform.ProcessMessage(MFT_MESSAGE_NOTIFY_START_OF_STREAM, 0) }
                {
                    last_rejection = format!("MFT start streaming notification failed: {error}");
                    callback_handle
                        .callback
                        .store(std::ptr::null_mut(), Ordering::Release);
                    continue;
                }
                let sequence_header = sequence_header(&transform);
                queue
                    .lock()
                    .map_err(|_| {
                        HardwareEncoderError::new(
                            HardwareEncoderFailure::EncoderSetupFailure,
                            "window capture status is unavailable",
                        )
                    })?
                    .configure_hardware_encoder(
                        adapter_identity.clone(),
                        HardwareEncoderPath::MediaFoundationHardwareH264,
                    )
                    .map_err(|failure| {
                        HardwareEncoderError::new(failure, "hardware policy rejected")
                    })?;
                return Ok(Self {
                    transform,
                    converter,
                    output_stream_info,
                    event_receiver,
                    callback_handle,
                    _callback: callback,
                    _device_manager: device_manager,
                    queue,
                    replay: EncodedReplayBuffer::new(),
                    sequence_header,
                    accepts_input: false,
                    _mf_platform: MediaFoundationPlatform,
                });
            }
            Err(HardwareEncoderError::new(
                HardwareEncoderFailure::EncoderSetupFailure,
                format!(
                    "no same-adapter hardware H.264 MFT accepted D3D11 NV12 surfaces; \
                     candidates={candidate_count}; d3d11Aware={d3d11_aware_count}; \
                     lastRejection={last_rejection}"
                ),
            ))
        })();
        if setup.is_err() {
            unsafe { windows::Win32::Media::MediaFoundation::MFShutdown() }.ok();
        }
        setup
    }

    fn submit_texture(
        &mut self,
        source: &windows::Win32::Graphics::Direct3D11::ID3D11Texture2D,
        pts_100ns: i64,
        duration_100ns: i64,
    ) -> Result<(), HardwareEncoderError> {
        use windows::core::Interface;
        use windows::Win32::Media::MediaFoundation::{MFCreateDXGISurfaceBuffer, MFCreateSample};

        self.drain_events()?;
        if !self.accepts_input {
            return Err(HardwareEncoderError::new(
                HardwareEncoderFailure::EncoderRuntimeFailure,
                "hardware H.264 input submitted without an MFT NeedInput permit",
            ));
        }
        let nv12 = self.converter.convert(source)?;
        let buffer = unsafe {
            MFCreateDXGISurfaceBuffer(
                &windows::Win32::Graphics::Direct3D11::ID3D11Texture2D::IID,
                nv12,
                0,
                false,
            )
        }
        .map_err(|error| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::EncoderRuntimeFailure,
                format!("DXGI surface buffer creation failed: {error}"),
            )
        })?;
        let sample = unsafe { MFCreateSample() }.map_err(|error| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::EncoderRuntimeFailure,
                format!("Media Foundation sample creation failed: {error}"),
            )
        })?;
        unsafe {
            sample.AddBuffer(&buffer).map_err(|error| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::EncoderRuntimeFailure,
                    format!("DXGI surface sample setup failed: {error}"),
                )
            })?;
            sample.SetSampleTime(pts_100ns).map_err(|error| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::EncoderRuntimeFailure,
                    format!("hardware input PTS setup failed: {error}"),
                )
            })?;
            sample.SetSampleDuration(duration_100ns).map_err(|error| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::EncoderRuntimeFailure,
                    format!("hardware input duration setup failed: {error}"),
                )
            })?;
            self.transform
                .ProcessInput(0, &sample, 0)
                .map_err(|error| {
                    HardwareEncoderError::new(
                        HardwareEncoderFailure::EncoderRuntimeFailure,
                        format!("hardware H.264 ProcessInput failed: {error}"),
                    )
                })?;
        }
        self.accepts_input = false;
        Ok(())
    }

    fn drain_events(&mut self) -> Result<(), HardwareEncoderError> {
        loop {
            match self.event_receiver.try_recv() {
                Ok(HardwareMftEvent::NeedInput) => self.accepts_input = true,
                Ok(HardwareMftEvent::HaveOutput) => self.read_output()?,
                Ok(HardwareMftEvent::DrainComplete) => {}
                Ok(HardwareMftEvent::Error) => {
                    return Err(HardwareEncoderError::new(
                        HardwareEncoderFailure::EncoderRuntimeFailure,
                        "hardware H.264 MFT event delivery failed",
                    ));
                }
                Err(std::sync::mpsc::TryRecvError::Empty) => return Ok(()),
                Err(std::sync::mpsc::TryRecvError::Disconnected) => {
                    return Err(HardwareEncoderError::new(
                        HardwareEncoderFailure::EncoderRuntimeFailure,
                        "hardware H.264 MFT event channel disconnected",
                    ));
                }
            }
        }
    }

    fn read_output(&mut self) -> Result<(), HardwareEncoderError> {
        use std::mem::ManuallyDrop;
        use windows::Win32::Media::MediaFoundation::{
            MFCreateMemoryBuffer, MFCreateSample, MFSampleExtension_CleanPoint,
            MFT_OUTPUT_DATA_BUFFER, MFT_OUTPUT_STREAM_CAN_PROVIDE_SAMPLES,
            MFT_OUTPUT_STREAM_PROVIDES_SAMPLES,
        };

        let needs_sample = self.output_stream_info.dwFlags
            & (MFT_OUTPUT_STREAM_PROVIDES_SAMPLES.0 | MFT_OUTPUT_STREAM_CAN_PROVIDE_SAMPLES.0)
                as u32
            == 0;
        let provided_sample = if needs_sample {
            let sample = unsafe { MFCreateSample() }.map_err(|error| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::EncoderRuntimeFailure,
                    format!("hardware output sample creation failed: {error}"),
                )
            })?;
            let buffer = unsafe { MFCreateMemoryBuffer(self.output_stream_info.cbSize) }.map_err(
                |error| {
                    HardwareEncoderError::new(
                        HardwareEncoderFailure::EncoderRuntimeFailure,
                        format!("hardware output buffer creation failed: {error}"),
                    )
                },
            )?;
            unsafe { sample.AddBuffer(&buffer) }.map_err(|error| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::EncoderRuntimeFailure,
                    format!("hardware output buffer setup failed: {error}"),
                )
            })?;
            Some(sample)
        } else {
            None
        };
        let mut output = MFT_OUTPUT_DATA_BUFFER {
            dwStreamID: 0,
            pSample: ManuallyDrop::new(provided_sample),
            dwStatus: 0,
            pEvents: ManuallyDrop::new(None),
        };
        let mut status = 0;
        let process_result = unsafe {
            self.transform
                .ProcessOutput(0, std::slice::from_mut(&mut output), &mut status)
        };
        let sample = unsafe { ManuallyDrop::take(&mut output.pSample) };
        unsafe { ManuallyDrop::drop(&mut output.pEvents) };
        process_result.map_err(|error| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::EncoderRuntimeFailure,
                format!("hardware H.264 ProcessOutput failed: {error}"),
            )
        })?;
        let sample = sample.ok_or_else(|| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::InvalidPacket,
                "hardware H.264 MFT returned an output event without a sample",
            )
        })?;
        let buffer = unsafe { sample.ConvertToContiguousBuffer() }.map_err(|error| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::InvalidPacket,
                format!("hardware H.264 access unit buffer conversion failed: {error}"),
            )
        })?;
        let mut data = std::ptr::null_mut();
        let mut current_length = 0;
        unsafe { buffer.Lock(&mut data, None, Some(&mut current_length)) }.map_err(|error| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::InvalidPacket,
                format!("hardware H.264 access unit lock failed: {error}"),
            )
        })?;
        let bytes = if data.is_null() || current_length == 0 {
            Vec::new()
        } else {
            unsafe { std::slice::from_raw_parts(data, current_length as usize).to_vec() }
        };
        unsafe { buffer.Unlock() }.map_err(|error| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::InvalidPacket,
                format!("hardware H.264 access unit unlock failed: {error}"),
            )
        })?;
        let pts_100ns = unsafe { sample.GetSampleTime() }.map_err(|error| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::InvalidPacket,
                format!("hardware H.264 packet PTS read failed: {error}"),
            )
        })?;
        let decode_timestamp = unsafe {
            sample.GetUINT64(
                &windows::Win32::Media::MediaFoundation::MFSampleExtension_DecodeTimestamp,
            )
        }
        .ok()
        .map(i64::try_from)
        .transpose()
        .map_err(|_| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::UnsupportedPacketTiming,
                "hardware H.264 decode timestamp exceeds the supported timeline",
            )
        })?
        .unwrap_or(pts_100ns);
        if decode_timestamp != pts_100ns {
            return Err(HardwareEncoderError::new(
                HardwareEncoderFailure::UnsupportedPacketTiming,
                "reordered H.264 output is unsupported by replay MP4 v1",
            ));
        }
        let packet = EncodedH264Packet {
            bytes: bytes.into(),
            pts_100ns,
            duration_100ns: unsafe { sample.GetSampleDuration() }.map_err(|error| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::InvalidPacket,
                    format!("hardware H.264 packet duration read failed: {error}"),
                )
            })?,
            keyframe: unsafe { sample.GetUINT32(&MFSampleExtension_CleanPoint) }.unwrap_or(0) != 0,
        };
        if self.sequence_header.is_empty() {
            self.sequence_header = sequence_header(&self.transform);
        }
        self.replay.push(packet.clone()).map_err(|error| {
            let failure = match error {
                ReplayBufferError::ByteOverflow => HardwareEncoderFailure::Backpressure,
                ReplayBufferError::TimestampRegression => {
                    HardwareEncoderFailure::UnsupportedPacketTiming
                }
                _ => HardwareEncoderFailure::InvalidPacket,
            };
            self.record_failure(failure);
            HardwareEncoderError::new(
                failure,
                format!("hardware replay buffer rejected a packet: {error:?}"),
            )
        })?;
        self.queue
            .lock()
            .map_err(|_| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::EncoderRuntimeFailure,
                    "window capture status is unavailable",
                )
            })?
            .record_hardware_packet(packet.pts_100ns)
            .map_err(|failure| HardwareEncoderError::new(failure, "invalid hardware packet"))?;
        Ok(())
    }

    fn record_failure(&self, failure: HardwareEncoderFailure) {
        if let Ok(mut queue) = self.queue.lock() {
            queue.record_hardware_failure(failure);
        }
    }

    fn replay_mux_input(
        &self,
        requested_start_100ns: i64,
        requested_end_100ns: i64,
        width: u32,
        height: u32,
        capture_clock: CaptureClockMetadata,
    ) -> Result<ReplayMuxInput, ReplayExportFailure> {
        Ok(ReplayMuxInput {
            snapshot: self
                .replay
                .snapshot(requested_start_100ns, requested_end_100ns)
                .map_err(replay_buffer_export_failure)?,
            sequence_header: Arc::from(self.sequence_header.clone()),
            width,
            height,
            capture_clock,
        })
    }

    #[cfg(test)]
    fn packet_count(&self) -> usize {
        self.replay.status().packet_count
    }

    #[cfg(test)]
    fn has_keyframe(&self) -> bool {
        self.replay.packets.iter().any(|packet| packet.keyframe)
    }

    #[cfg(test)]
    fn full_frame_cpu_readback(&self) -> bool {
        false
    }
}

#[cfg(windows)]
struct HardwareCaptureFrame {
    frame: windows::Graphics::Capture::Direct3D11CaptureFrame,
    sample: FrameSample,
    encoded_pts_100ns: i64,
}

// Direct3D11CaptureFrame is an agile WinRT object. The free-threaded WGC
// callback only forwards ownership; the capture worker remains the sole user
// of the D3D immediate context and Media Foundation transform.
#[cfg(windows)]
unsafe impl Send for HardwareCaptureFrame {}

#[cfg(windows)]
fn submit_hardware_capture_frame(
    encoder: &mut HardwareH264Encoder,
    captured: HardwareCaptureFrame,
) -> Result<(), HardwareEncoderError> {
    use windows::core::Interface;
    use windows::Win32::Graphics::Direct3D11::ID3D11Texture2D;
    use windows::Win32::System::WinRT::Direct3D11::IDirect3DDxgiInterfaceAccess;

    debug_assert!(
        captured.encoded_pts_100ns <= captured.sample.system_relative_time_100ns,
        "derived hardware PTS must not run ahead of its WGC source timestamp"
    );
    let surface = captured.frame.Surface().map_err(|error| {
        HardwareEncoderError::new(
            HardwareEncoderFailure::GpuConversionFailure,
            format!("capture frame surface access failed: {error}"),
        )
    })?;
    let access = surface
        .cast::<IDirect3DDxgiInterfaceAccess>()
        .map_err(|error| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::GpuConversionFailure,
                format!("capture surface DXGI access failed: {error}"),
            )
        })?;
    let source: ID3D11Texture2D = unsafe { access.GetInterface() }.map_err(|error| {
        HardwareEncoderError::new(
            HardwareEncoderFailure::GpuConversionFailure,
            format!("capture surface texture access failed: {error}"),
        )
    })?;
    encoder.submit_texture(
        &source,
        captured.encoded_pts_100ns,
        10_000_000 * DEFAULT_RECORDING_FPS_DENOMINATOR as i64
            / DEFAULT_RECORDING_FPS_NUMERATOR as i64,
    )
}

#[cfg(windows)]
fn dequeue_if_permitted<T>(
    accepts_input: bool,
    receiver: &std::sync::mpsc::Receiver<T>,
) -> Option<T> {
    accepts_input.then(|| receiver.try_recv().ok()).flatten()
}

#[cfg(windows)]
impl Drop for HardwareH264Encoder {
    fn drop(&mut self) {
        use windows::core::Interface;
        use windows::Win32::Media::MediaFoundation::{
            IMFShutdown, MFT_MESSAGE_COMMAND_FLUSH, MFT_MESSAGE_NOTIFY_END_STREAMING,
        };

        self.callback_handle
            .callback
            .store(std::ptr::null_mut(), Ordering::Release);
        unsafe {
            self.transform
                .ProcessMessage(MFT_MESSAGE_COMMAND_FLUSH, 0)
                .ok();
            self.transform
                .ProcessMessage(MFT_MESSAGE_NOTIFY_END_STREAMING, 0)
                .ok();
        }
        if let Ok(shutdown) = self.transform.cast::<IMFShutdown>() {
            unsafe { shutdown.Shutdown() }.ok();
        }
    }
}

#[cfg(windows)]
fn adapter_identity(
    device: &windows::Win32::Graphics::Direct3D11::ID3D11Device,
) -> Result<(windows::Win32::Foundation::LUID, String), HardwareEncoderError> {
    use windows::core::Interface;
    use windows::Win32::Graphics::Dxgi::IDXGIDevice;

    let dxgi_device: IDXGIDevice = device.cast().map_err(|error| {
        HardwareEncoderError::new(
            HardwareEncoderFailure::AdapterMismatch,
            format!("D3D11 device does not expose IDXGIDevice: {error}"),
        )
    })?;
    let adapter = unsafe { dxgi_device.GetAdapter() }.map_err(|error| {
        HardwareEncoderError::new(
            HardwareEncoderFailure::AdapterMismatch,
            format!("D3D11 device adapter lookup failed: {error}"),
        )
    })?;
    let description = unsafe { adapter.GetDesc() }.map_err(|error| {
        HardwareEncoderError::new(
            HardwareEncoderFailure::AdapterMismatch,
            format!("D3D11 adapter description lookup failed: {error}"),
        )
    })?;
    Ok((
        description.AdapterLuid,
        format!(
            "luid:{:08x}{:08x};vendor:{:04x};device:{:04x}",
            description.AdapterLuid.HighPart as u32,
            description.AdapterLuid.LowPart,
            description.VendorId,
            description.DeviceId,
        ),
    ))
}

#[cfg(windows)]
fn enumerate_hardware_h264(
    adapter_luid: Option<windows::Win32::Foundation::LUID>,
) -> Result<Vec<windows::Win32::Media::MediaFoundation::IMFActivate>, HardwareEncoderError> {
    use std::mem::size_of;
    use windows::Win32::Media::MediaFoundation::{
        MFCreateAttributes, MFMediaType_Video, MFTEnum2, MFVideoFormat_H264, MFVideoFormat_NV12,
        MFT_CATEGORY_VIDEO_ENCODER, MFT_ENUM_ADAPTER_LUID, MFT_ENUM_FLAG_HARDWARE,
        MFT_ENUM_FLAG_SORTANDFILTER, MFT_REGISTER_TYPE_INFO,
    };
    use windows::Win32::System::Com::CoTaskMemFree;

    let input_type = MFT_REGISTER_TYPE_INFO {
        guidMajorType: MFMediaType_Video,
        guidSubtype: MFVideoFormat_NV12,
    };
    let output_type = MFT_REGISTER_TYPE_INFO {
        guidMajorType: MFMediaType_Video,
        guidSubtype: MFVideoFormat_H264,
    };
    let attributes = if let Some(luid) = adapter_luid {
        let mut attributes = None;
        unsafe { MFCreateAttributes(&mut attributes, 1) }.map_err(|error| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::EncoderSetupFailure,
                format!("hardware MFT adapter attributes creation failed: {error}"),
            )
        })?;
        let attributes = attributes.ok_or_else(|| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::EncoderSetupFailure,
                "hardware MFT adapter attributes were not returned",
            )
        })?;
        let bytes = unsafe {
            std::slice::from_raw_parts(
                (&luid as *const windows::Win32::Foundation::LUID).cast::<u8>(),
                size_of::<windows::Win32::Foundation::LUID>(),
            )
        };
        unsafe { attributes.SetBlob(&MFT_ENUM_ADAPTER_LUID, bytes) }.map_err(|error| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::EncoderSetupFailure,
                format!("hardware MFT adapter LUID configuration failed: {error}"),
            )
        })?;
        Some(attributes)
    } else {
        None
    };
    let mut raw_activations = std::ptr::null_mut();
    let mut activation_count = 0;
    unsafe {
        MFTEnum2(
            MFT_CATEGORY_VIDEO_ENCODER,
            MFT_ENUM_FLAG_HARDWARE | MFT_ENUM_FLAG_SORTANDFILTER,
            Some(&input_type),
            Some(&output_type),
            attributes.as_ref(),
            &mut raw_activations,
            &mut activation_count,
        )
    }
    .map_err(|error| {
        HardwareEncoderError::new(
            HardwareEncoderFailure::EncoderSetupFailure,
            format!("hardware H.264 MFT enumeration failed: {error}"),
        )
    })?;
    if raw_activations.is_null() || activation_count == 0 {
        return Ok(Vec::new());
    }
    let raw = unsafe { std::slice::from_raw_parts_mut(raw_activations, activation_count as usize) };
    let activations = raw.iter().flatten().cloned().collect();
    for activation in raw {
        unsafe { std::ptr::drop_in_place(activation) };
    }
    unsafe { CoTaskMemFree(Some(raw_activations.cast())) };
    Ok(activations)
}

#[cfg(windows)]
fn mft_friendly_name(activation: &windows::Win32::Media::MediaFoundation::IMFActivate) -> String {
    use windows::Win32::Media::MediaFoundation::MFT_FRIENDLY_NAME_Attribute;

    let Ok(length) = (unsafe { activation.GetStringLength(&MFT_FRIENDLY_NAME_Attribute) }) else {
        return "unnamed hardware H.264 MFT".to_string();
    };
    let mut value = vec![0u16; length as usize + 1];
    if unsafe { activation.GetString(&MFT_FRIENDLY_NAME_Attribute, &mut value, None) }.is_err() {
        return "unnamed hardware H.264 MFT".to_string();
    }
    String::from_utf16_lossy(&value[..length as usize])
}

#[cfg(windows)]
fn sequence_header(transform: &windows::Win32::Media::MediaFoundation::IMFTransform) -> Vec<u8> {
    use windows::Win32::Media::MediaFoundation::MF_MT_MPEG_SEQUENCE_HEADER;

    let Ok(media_type) = (unsafe { transform.GetOutputCurrentType(0) }) else {
        return Vec::new();
    };
    let Ok(size) = (unsafe { media_type.GetBlobSize(&MF_MT_MPEG_SEQUENCE_HEADER) }) else {
        return Vec::new();
    };
    let mut bytes = vec![0; size as usize];
    if unsafe { media_type.GetBlob(&MF_MT_MPEG_SEQUENCE_HEADER, &mut bytes, None) }.is_err() {
        return Vec::new();
    }
    bytes
}

#[cfg(windows)]
fn reserve_recording_timestamp(last: &AtomicI64, timestamp: i64, interval: i64) -> Option<i64> {
    loop {
        let previous = last.load(Ordering::Acquire);
        let reserved = if previous < 0 {
            timestamp
        } else {
            if timestamp < previous || timestamp - previous < interval {
                return None;
            }
            let elapsed_intervals = (timestamp - previous) / interval;
            previous + elapsed_intervals * interval
        };
        if last
            .compare_exchange(previous, reserved, Ordering::AcqRel, Ordering::Acquire)
            .is_ok()
        {
            return Some(reserved);
        }
    }
}

#[cfg(windows)]
fn run_mp4_writer(
    output_path: PathBuf,
    width: u32,
    height: u32,
    receiver: std::sync::mpsc::Receiver<FrameSample>,
    ready: std::sync::mpsc::SyncSender<Result<(), String>>,
    queue: Arc<Mutex<FrameQueue>>,
    failed: Arc<AtomicBool>,
) -> Result<(), String> {
    let mut writer = match Mp4Writer::start(output_path, width, height) {
        Ok(writer) => writer,
        Err(error) => {
            let _ = ready.send(Err(error.clone()));
            return Err(error);
        }
    };
    ready
        .send(Ok(()))
        .map_err(|_| "recording writer startup receiver closed".to_string())?;
    while let Ok(frame) = receiver.recv() {
        if let Err(error) = writer.write_frame(&frame) {
            failed.store(true, Ordering::Release);
            if let Ok(mut guard) = queue.lock() {
                guard.record_encoder_error();
            }
            return Err(error);
        }
        if let Ok(mut guard) = queue.lock() {
            guard.record_writer_submission(frame.system_relative_time_100ns);
        }
    }
    if let Err(error) = writer.finalize() {
        failed.store(true, Ordering::Release);
        if let Ok(mut guard) = queue.lock() {
            guard.record_encoder_error();
        }
        return Err(error);
    }
    Ok(())
}

#[cfg(windows)]
fn run_wgc_window_capture(
    hwnd: usize,
    queue: Arc<Mutex<FrameQueue>>,
    stop: Arc<AtomicBool>,
    ready: std::sync::mpsc::SyncSender<Result<(), String>>,
    recording_path: Option<PathBuf>,
    clock_metadata: CaptureClockMetadata,
    command_receiver: std::sync::mpsc::Receiver<WindowCaptureCommand>,
) -> Result<(), String> {
    use windows::core::{IInspectable, Interface};
    use windows::Foundation::TypedEventHandler;
    use windows::Graphics::Capture::{
        Direct3D11CaptureFramePool, GraphicsCaptureItem, GraphicsCaptureSession,
    };
    use windows::Graphics::DirectX::Direct3D11::IDirect3DDevice;
    use windows::Graphics::DirectX::DirectXPixelFormat;
    use windows::Win32::Foundation::{HMODULE, HWND};
    use windows::Win32::Graphics::Direct3D::{
        D3D_DRIVER_TYPE_HARDWARE, D3D_FEATURE_LEVEL, D3D_FEATURE_LEVEL_10_0,
        D3D_FEATURE_LEVEL_10_1, D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_11_1,
    };
    use windows::Win32::Graphics::Direct3D11::{
        D3D11CreateDevice, ID3D11Device, ID3D11DeviceContext, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
        D3D11_CREATE_DEVICE_VIDEO_SUPPORT, D3D11_SDK_VERSION,
    };
    use windows::Win32::System::WinRT::Graphics::Capture::IGraphicsCaptureItemInterop;
    use windows::Win32::System::WinRT::{RoInitialize, RoUninitialize, RO_INIT_MULTITHREADED};

    if hwnd == 0 {
        return Err("KovaaK window handle is null".to_string());
    }
    unsafe { RoInitialize(RO_INIT_MULTITHREADED) }
        .map_err(|error| format!("RoInitialize failed: {error}"))?;

    let result = (|| {
        if !GraphicsCaptureSession::IsSupported()
            .map_err(|error| format!("GraphicsCaptureSession::IsSupported failed: {error}"))?
        {
            return Err(
                "Windows.Graphics.Capture is not supported on this Windows build".to_string(),
            );
        }

        let feature_levels = [
            D3D_FEATURE_LEVEL_11_1,
            D3D_FEATURE_LEVEL_11_0,
            D3D_FEATURE_LEVEL_10_1,
            D3D_FEATURE_LEVEL_10_0,
        ];
        let mut device: Option<ID3D11Device> = None;
        let mut context: Option<ID3D11DeviceContext> = None;
        let mut feature_level = D3D_FEATURE_LEVEL::default();
        unsafe {
            D3D11CreateDevice(
                None,
                D3D_DRIVER_TYPE_HARDWARE,
                HMODULE::default(),
                D3D11_CREATE_DEVICE_BGRA_SUPPORT | D3D11_CREATE_DEVICE_VIDEO_SUPPORT,
                Some(&feature_levels),
                D3D11_SDK_VERSION,
                Some(&mut device),
                Some(&mut feature_level),
                Some(&mut context),
            )
        }
        .map_err(|error| format!("D3D11CreateDevice failed: {error}"))?;
        let device = device.ok_or_else(|| "D3D11 device was not returned".to_string())?;
        let context = context.ok_or_else(|| "D3D11 context was not returned".to_string())?;
        let dxgi_device = device
            .cast::<windows::Win32::Graphics::Dxgi::IDXGIDevice>()
            .map_err(|error| format!("D3D11 device did not expose IDXGIDevice: {error}"))?;
        let inspectable = unsafe {
            windows::Win32::System::WinRT::Direct3D11::CreateDirect3D11DeviceFromDXGIDevice(
                &dxgi_device,
            )
        }
        .map_err(|error| format!("CreateDirect3D11DeviceFromDXGIDevice failed: {error}"))?;
        let direct3d_device: IDirect3DDevice = inspectable
            .cast()
            .map_err(|error| format!("IDirect3DDevice cast failed: {error}"))?;
        let interop = windows::core::factory::<GraphicsCaptureItem, IGraphicsCaptureItemInterop>()
            .map_err(|error| format!("GraphicsCaptureItem factory failed: {error}"))?;
        let item: GraphicsCaptureItem = unsafe { interop.CreateForWindow(HWND(hwnd as *mut _)) }
            .map_err(|error| format!("CreateForWindow failed: {error}"))?;
        let size = item
            .Size()
            .map_err(|error| format!("capture item size failed: {error}"))?;
        if size.Width <= 0 || size.Height <= 0 {
            return Err("capture window has an invalid size".to_string());
        }

        let frame_pool = Direct3D11CaptureFramePool::CreateFreeThreaded(
            &direct3d_device,
            DirectXPixelFormat::B8G8R8A8UIntNormalized,
            2,
            size,
        )
        .map_err(|error| {
            format!("Direct3D11CaptureFramePool::CreateFreeThreaded failed: {error}")
        })?;
        let session = frame_pool
            .CreateCaptureSession(&item)
            .map_err(|error| format!("CreateCaptureSession failed: {error}"))?;
        let recording_failed = Arc::new(AtomicBool::new(false));
        let mut automatic_hardware_encoder = if recording_path.is_none() {
            let encoder = HardwareH264Encoder::new(
                &device,
                &context,
                size.Width as u32,
                size.Height as u32,
                Arc::clone(&queue),
            )
            .map_err(|error| {
                if let Ok(mut guard) = queue.lock() {
                    guard.record_hardware_failure(error.failure);
                }
                format!(
                    "automatic hardware H.264 initialization failed: {}",
                    error.message
                )
            })?;
            Some(encoder)
        } else {
            None
        };
        let (hardware_frame_sender, hardware_frame_receiver) = if automatic_hardware_encoder
            .is_some()
        {
            let (sender, receiver) = std::sync::mpsc::sync_channel(DEFAULT_FRAME_QUEUE_CAPACITY);
            (Some(sender), Some(receiver))
        } else {
            (None, None)
        };
        let (recording_sender, recording_join) = if let Some(path) = recording_path {
            let (sender, receiver) = std::sync::mpsc::sync_channel(DEFAULT_WRITER_QUEUE_CAPACITY);
            let (writer_ready_tx, writer_ready_rx) = std::sync::mpsc::sync_channel(1);
            let writer_queue = Arc::clone(&queue);
            let writer_failed = Arc::clone(&recording_failed);
            let writer_join = thread::spawn(move || {
                run_mp4_writer(
                    path,
                    size.Width as u32,
                    size.Height as u32,
                    receiver,
                    writer_ready_tx,
                    writer_queue,
                    writer_failed,
                )
            });
            match writer_ready_rx.recv_timeout(std::time::Duration::from_secs(4)) {
                Ok(Ok(())) => (Some(sender), Some(writer_join)),
                Ok(Err(error)) => {
                    let _ = writer_join.join();
                    return Err(error);
                }
                Err(error) => {
                    recording_failed.store(true, Ordering::Release);
                    drop(sender);
                    return Err(format!("recording writer startup timed out: {error}"));
                }
            }
        } else {
            (None, None)
        };
        let recording_readback = recording_sender
            .as_ref()
            .map(|_| {
                D3dFrameReadback::new(&device, &context, size.Width as u32, size.Height as u32)
                    .map(|readback| Arc::new(Mutex::new(readback)))
            })
            .transpose()?;
        let last_recorded_timestamp = Arc::new(AtomicI64::new(-1));
        let sequence = Arc::new(AtomicU64::new(0));
        let queue_for_handler = Arc::clone(&queue);
        let stop_for_handler = Arc::clone(&stop);
        let sender_for_handler = recording_sender.clone();
        let readback_for_handler = recording_readback.clone();
        let hardware_frame_sender_for_handler = hardware_frame_sender.clone();
        let last_recorded_timestamp_for_handler = Arc::clone(&last_recorded_timestamp);
        let recording_failed_for_handler = Arc::clone(&recording_failed);
        let frame_arrived_token = frame_pool
            .FrameArrived(
                &TypedEventHandler::<Direct3D11CaptureFramePool, IInspectable>::new(
                    move |sender, _| {
                        if stop_for_handler.load(Ordering::Acquire) {
                            return Ok(());
                        }
                        let Some(pool) = sender.as_ref() else {
                            return Ok(());
                        };
                        let frame = pool.TryGetNextFrame()?;
                        let content_size = frame.ContentSize()?;
                        if content_size.Width <= 0 || content_size.Height <= 0 {
                            return Ok(());
                        }
                        let timestamp = frame.SystemRelativeTime()?.Duration;
                        let sample = FrameSample {
                            sequence: sequence.fetch_add(1, Ordering::Relaxed) + 1,
                            width: content_size.Width as u32,
                            height: content_size.Height as u32,
                            system_relative_time_100ns: timestamp,
                            clock: clock_metadata,
                            bgra8: Vec::new(),
                        };
                        {
                            let mut guard = queue_for_handler
                                .lock()
                                .map_err(|_| windows::core::Error::from_win32())?;
                            let result = if sender_for_handler.is_some()
                                || hardware_frame_sender_for_handler.is_some()
                            {
                                guard.record_metadata(&sample).map(|_| ())
                            } else {
                                guard.try_push(sample.clone()).map(|_| ())
                            };
                            result.map_err(|error| {
                                windows::core::Error::new(
                                    windows::core::HRESULT(0x80004005u32 as i32),
                                    error.to_string(),
                                )
                            })?;
                        }

                        if recording_failed_for_handler.load(Ordering::Acquire) {
                            return Ok(());
                        }
                        let Some(encoded_pts_100ns) = reserve_recording_timestamp(
                            &last_recorded_timestamp_for_handler,
                            timestamp,
                            10_000_000 * DEFAULT_RECORDING_FPS_DENOMINATOR as i64
                                / DEFAULT_RECORDING_FPS_NUMERATOR as i64,
                        ) else {
                            return Ok(());
                        };
                        if content_size.Width != size.Width || content_size.Height != size.Height {
                            recording_failed_for_handler.store(true, Ordering::Release);
                            if let Ok(mut guard) = queue_for_handler.lock() {
                                if hardware_frame_sender_for_handler.is_some() {
                                    guard.record_hardware_failure(
                                        HardwareEncoderFailure::GpuConversionFailure,
                                    );
                                } else {
                                    guard.record_encoder_error();
                                }
                            }
                            return Ok(());
                        }

                        if let Some(sender) = hardware_frame_sender_for_handler.as_ref() {
                            if frame
                                .cast::<windows::Win32::System::Com::IAgileObject>()
                                .is_err()
                            {
                                recording_failed_for_handler.store(true, Ordering::Release);
                                if let Ok(mut guard) = queue_for_handler.lock() {
                                    guard.record_hardware_failure(
                                        HardwareEncoderFailure::GpuConversionFailure,
                                    );
                                }
                                return Ok(());
                            }
                            match sender.try_send(HardwareCaptureFrame {
                                frame,
                                sample,
                                encoded_pts_100ns,
                            }) {
                                Ok(()) => {}
                                Err(std::sync::mpsc::TrySendError::Full(_)) => {
                                    if let Ok(mut guard) = queue_for_handler.lock() {
                                        guard.record_hardware_failure(
                                            HardwareEncoderFailure::Backpressure,
                                        );
                                    }
                                }
                                Err(std::sync::mpsc::TrySendError::Disconnected(_)) => {
                                    recording_failed_for_handler.store(true, Ordering::Release);
                                    if let Ok(mut guard) = queue_for_handler.lock() {
                                        guard.record_hardware_failure(
                                            HardwareEncoderFailure::EncoderRuntimeFailure,
                                        );
                                    }
                                }
                            }
                            return Ok(());
                        }

                        let (Some(sender), Some(readback)) =
                            (sender_for_handler.as_ref(), readback_for_handler.as_ref())
                        else {
                            return Ok(());
                        };

                        let readback_result = (|| {
                            readback
                                .lock()
                                .map_err(|_| "recording readback is unavailable".to_string())?
                                .submit_frame(&frame, sample)
                        })();
                        match readback_result {
                            Ok(submission) => {
                                if !submission.queued {
                                    if let Ok(mut guard) = queue_for_handler.lock() {
                                        guard.record_writer_drop();
                                    }
                                }
                                if let Some(recording_sample) = submission.completed {
                                    match sender.try_send(recording_sample) {
                                        Ok(()) => {}
                                        Err(std::sync::mpsc::TrySendError::Full(_)) => {
                                            if let Ok(mut guard) = queue_for_handler.lock() {
                                                guard.record_writer_drop();
                                            }
                                        }
                                        Err(std::sync::mpsc::TrySendError::Disconnected(_)) => {
                                            recording_failed_for_handler
                                                .store(true, Ordering::Release);
                                            if let Ok(mut guard) = queue_for_handler.lock() {
                                                guard.record_encoder_error();
                                            }
                                        }
                                    }
                                }
                            }
                            Err(_) => {
                                recording_failed_for_handler.store(true, Ordering::Release);
                                if let Ok(mut guard) = queue_for_handler.lock() {
                                    guard.record_encoder_error();
                                }
                            }
                        }
                        Ok(())
                    },
                ),
            )
            .map_err(|error| format!("FrameArrived registration failed: {error}"))?;
        session
            .StartCapture()
            .map_err(|error| format!("StartCapture failed: {error}"))?;
        ready
            .send(Ok(()))
            .map_err(|_| "capture startup receiver closed".to_string())?;

        let mut export_join = None::<JoinHandle<()>>;
        while !stop.load(Ordering::Acquire) {
            if export_join.as_ref().is_some_and(JoinHandle::is_finished) {
                if let Some(finished) = export_join.take() {
                    let _ = finished.join();
                }
            }
            if let Ok(command) = command_receiver.try_recv() {
                match command {
                    WindowCaptureCommand::ExportReplay {
                        requested_start_100ns,
                        requested_end_100ns,
                        output_path,
                        response,
                    } => {
                        eprintln!(
                            "[capture-export] worker: command received start={requested_start_100ns} end={requested_end_100ns}"
                        );
                        if export_join.is_some() {
                            eprintln!("[capture-export] worker: busy reject");
                            let _ = response.send(Err(replay_export_failure(
                                ReplayExportFailureKind::ExportBusy,
                                "another hardware replay export is still finalizing",
                            )));
                        } else {
                            let input = automatic_hardware_encoder
                                .as_ref()
                                .ok_or_else(|| {
                                    replay_export_failure(
                                        ReplayExportFailureKind::CaptureUnavailable,
                                        "hardware replay encoder is unavailable",
                                    )
                                })
                                .and_then(|encoder| {
                                    encoder.replay_mux_input(
                                        requested_start_100ns,
                                        requested_end_100ns,
                                        size.Width as u32,
                                        size.Height as u32,
                                        clock_metadata,
                                    )
                                });
                            match input {
                                Ok(input) => {
                                    eprintln!(
                                        "[capture-export] worker: mux spawning path={}",
                                        output_path.display()
                                    );
                                    export_join = Some(thread::spawn(move || {
                                        let mux_started = std::time::Instant::now();
                                        eprintln!("[capture-export] mux: begin");
                                        let result =
                                            std::panic::catch_unwind(std::panic::AssertUnwindSafe(
                                                || export_replay_mp4_file(input, output_path),
                                            ));
                                        let outcome = match result {
                                            Ok(Ok(receipt)) => {
                                                eprintln!(
                                                    "[capture-export] mux: ok packets={} elapsed_ms={}",
                                                    receipt.packet_count,
                                                    mux_started.elapsed().as_millis()
                                                );
                                                Ok(receipt)
                                            }
                                            Ok(Err(error)) => {
                                                eprintln!(
                                                    "[capture-export] mux: failed kind={:?} {} elapsed_ms={}",
                                                    error.kind,
                                                    error.message,
                                                    mux_started.elapsed().as_millis()
                                                );
                                                Err(error)
                                            }
                                            Err(panic) => {
                                                eprintln!(
                                                    "[capture-export] mux: PANICKED: {}",
                                                    panic_message(panic)
                                                );
                                                Err(replay_export_failure(
                                                    ReplayExportFailureKind::IoFailure,
                                                    "hardware replay export panicked",
                                                ))
                                            }
                                        };
                                        if response.send(outcome).is_err() {
                                            eprintln!(
                                                "[capture-export] mux: response channel closed before delivery"
                                            );
                                        }
                                    }))
                                }
                                Err(error) => {
                                    eprintln!(
                                        "[capture-export] worker: input build failed kind={:?}",
                                        error.kind
                                    );
                                    let _ = response.send(Err(error));
                                }
                            }
                        }
                    }
                }
            }
            if let (Some(encoder), Some(receiver)) = (
                automatic_hardware_encoder.as_mut(),
                hardware_frame_receiver.as_ref(),
            ) {
                let mut failure = None;
                if let Err(error) = encoder.drain_events() {
                    failure = Some(error.failure);
                }
                if let Some(captured) =
                    dequeue_if_permitted(failure.is_none() && encoder.accepts_input, receiver)
                {
                    if let Err(error) = submit_hardware_capture_frame(encoder, captured) {
                        failure = Some(error.failure);
                    }
                }
                if let Some(failure) = failure {
                    recording_failed.store(true, Ordering::Release);
                    if let Ok(mut guard) = queue.lock() {
                        guard.record_hardware_failure(failure);
                    }
                }
            }
            let poll_interval_ms = if hardware_frame_receiver.is_some() {
                1
            } else {
                20
            };
            thread::sleep(std::time::Duration::from_millis(poll_interval_ms));
        }
        let _ = session.Close();
        let _ = frame_pool.RemoveFrameArrived(frame_arrived_token);
        let _ = frame_pool.Close();
        drop(recording_sender);
        if let Some(join) = recording_join {
            match join.join() {
                Ok(Ok(())) => {}
                Ok(Err(error)) => return Err(error),
                Err(_) => return Err("recording writer thread panicked".to_string()),
            }
        }
        if let Some(export) = export_join {
            let _ = export.join();
        }
        Ok(())
    })();
    if let Err(error) = &result {
        let _ = ready.send(Err(error.clone()));
    }
    unsafe { RoUninitialize() };
    result
}

fn invalid_frame(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn epoch_windows_map_from_the_active_qpc_wgc_capture_clock() {
        let mut state = WindowCaptureState::new(DEFAULT_FRAME_QUEUE_CAPACITY).unwrap();
        state.clock_metadata = Some(CaptureClockMetadata {
            utc_epoch_ms: 1_000,
            qpc_ns: 2_000_000_000,
            clock_source: "utc_epoch_ms+qpc+wgc_system_relative_time",
            timebase_version: "time_alignment.v2",
        });
        state.queue.lock().unwrap().first_system_relative_time_100ns = Some(20_000_000);

        assert_eq!(
            state.epoch_window_to_replay_pts(1_500, 2_000).unwrap(),
            (25_000_000, 30_000_000),
        );
        assert!(state.epoch_window_to_replay_pts(2_000, 1_500).is_err());
        assert!(state
            .epoch_window_to_replay_pts(1_000, 1_000 + 300_001)
            .is_err());
        assert!(state.epoch_window_to_replay_pts(999, 1_000).is_err());
    }

    #[test]
    fn epoch_windows_require_a_current_source_pts_anchor_and_checked_clock() {
        let mut state = WindowCaptureState::new(DEFAULT_FRAME_QUEUE_CAPACITY).unwrap();
        assert!(state.epoch_window_to_replay_pts(1, 2).is_err());
        state.clock_metadata = Some(CaptureClockMetadata {
            utc_epoch_ms: 1,
            qpc_ns: u128::MAX,
            clock_source: "utc_epoch_ms+qpc+wgc_system_relative_time",
            timebase_version: "time_alignment.v2",
        });
        state.queue.lock().unwrap().first_system_relative_time_100ns = Some(1);
        assert!(state.epoch_window_to_replay_pts(1, 2).is_err());
    }

    #[test]
    fn default_frame_queue_capacity_covers_half_a_second_at_recording_fps() {
        // 帧队列丢帧余量契约：60fps 下至少 0.5s（30 帧）缓冲。
        let frames_per_second = DEFAULT_RECORDING_FPS_NUMERATOR / DEFAULT_RECORDING_FPS_DENOMINATOR;
        assert!(DEFAULT_FRAME_QUEUE_CAPACITY >= frames_per_second as usize / 2);
    }

    #[cfg(windows)]
    fn current_process_cpu_100ns() -> u64 {
        use winapi::shared::minwindef::FILETIME;
        use winapi::um::processthreadsapi::{GetCurrentProcess, GetProcessTimes};

        let mut creation: FILETIME = unsafe { std::mem::zeroed() };
        let mut exit: FILETIME = unsafe { std::mem::zeroed() };
        let mut kernel: FILETIME = unsafe { std::mem::zeroed() };
        let mut user: FILETIME = unsafe { std::mem::zeroed() };
        let succeeded = unsafe {
            GetProcessTimes(
                GetCurrentProcess(),
                &mut creation,
                &mut exit,
                &mut kernel,
                &mut user,
            )
        };
        assert_ne!(succeeded, 0, "GetProcessTimes should succeed");
        let as_u64 =
            |value: FILETIME| ((value.dwHighDateTime as u64) << 32) | value.dwLowDateTime as u64;
        as_u64(kernel) + as_u64(user)
    }

    fn frame(sequence: u64, timestamp: i64) -> FrameSample {
        FrameSample {
            sequence,
            width: 2,
            height: 1,
            system_relative_time_100ns: timestamp,
            clock: CaptureClockMetadata {
                utc_epoch_ms: 1_700_000_000_000,
                qpc_ns: 10_000,
                clock_source: "test",
                timebase_version: "time_alignment.v2",
            },
            bgra8: vec![0; 8],
        }
    }

    #[test]
    fn queue_is_bounded_and_drops_new_frames_without_blocking() {
        let mut queue = FrameQueue::new(1).unwrap();
        assert_eq!(
            queue.try_push(frame(1, 100)).unwrap(),
            FrameEnqueueResult::Enqueued
        );
        assert_eq!(
            queue.try_push(frame(2, 200)).unwrap(),
            FrameEnqueueResult::DroppedBackpressure
        );
        assert_eq!(queue.len(), 1);
        assert_eq!(queue.status(false, false).metadata_dropped_frames, 1);
    }

    #[test]
    fn invalid_payload_is_rejected_and_counted() {
        let mut queue = FrameQueue::new(2).unwrap();
        let mut invalid = frame(1, 100);
        invalid.bgra8.pop();
        assert!(queue.try_push(invalid).is_err());
        assert_eq!(queue.status(false, false).invalid_frames, 1);
    }

    #[test]
    fn metadata_only_frame_is_valid_without_gpu_readback() {
        let mut queue = FrameQueue::new(1).unwrap();
        let mut metadata = frame(1, 100);
        metadata.bgra8.clear();
        assert_eq!(
            queue.try_push(metadata).unwrap(),
            FrameEnqueueResult::Enqueued
        );
        let status = queue.status(false, false);
        assert_eq!(status.captured_frames, 1);
        assert_eq!(status.first_system_relative_time_100ns, Some(100));
    }

    #[test]
    fn recording_metadata_observation_does_not_fill_probe_queue() {
        let mut queue = FrameQueue::new(1).unwrap();
        queue.record_metadata(&frame(1, 100)).unwrap();
        queue.record_metadata(&frame(2, 200)).unwrap();
        let status = queue.status(false, true);
        assert_eq!(status.queued_frames, 0);
        assert_eq!(status.metadata_dropped_frames, 0);
        assert_eq!(status.captured_frames, 2);
        assert_eq!(status.first_system_relative_time_100ns, Some(100));
        assert_eq!(status.last_system_relative_time_100ns, Some(200));
    }

    #[test]
    fn queue_reset_starts_each_capture_with_fresh_diagnostics() {
        let mut queue = FrameQueue::new(1).unwrap();
        queue.try_push(frame(1, 100)).unwrap();
        assert_eq!(
            queue.try_push(frame(2, 200)).unwrap(),
            FrameEnqueueResult::DroppedBackpressure
        );
        queue.record_writer_submission(100);
        queue.record_writer_drop();
        queue.record_encoder_error();

        queue.reset();

        let status = queue.status(false, false);
        assert_eq!(status.queued_frames, 0);
        assert_eq!(status.captured_frames, 0);
        assert_eq!(status.metadata_dropped_frames, 0);
        assert_eq!(status.writer_submitted_frames, 0);
        assert_eq!(status.writer_first_system_relative_time_100ns, None);
        assert_eq!(status.writer_last_system_relative_time_100ns, None);
        assert_eq!(status.writer_dropped_frames, 0);
        assert_eq!(status.encoder_errors, 0);
        assert_eq!(status.first_system_relative_time_100ns, None);
        assert_eq!(status.last_system_relative_time_100ns, None);
    }

    #[test]
    fn frame_timestamps_must_not_move_backwards() {
        let mut queue = FrameQueue::new(2).unwrap();
        queue.try_push(frame(1, 200)).unwrap();
        assert!(queue.try_push(frame(2, 100)).is_err());
        assert_eq!(queue.status(false, false).invalid_frames, 1);
    }

    #[test]
    fn writer_submission_status_tracks_encoded_source_range() {
        let mut queue = FrameQueue::new(2).unwrap();
        queue.record_writer_submission(100);
        queue.record_writer_submission(200);
        let status = queue.status(false, true);
        assert_eq!(status.writer_submitted_frames, 2);
        assert_eq!(status.writer_first_system_relative_time_100ns, Some(100));
        assert_eq!(status.writer_last_system_relative_time_100ns, Some(200));
    }

    #[test]
    fn automatic_video_policy_rejects_cpu_readback_sink_writer() {
        assert_eq!(
            HardwareEncoderPath::MediaFoundationHardwareH264.require_automatic_hardware(),
            Ok(HardwareEncoderPath::MediaFoundationHardwareH264)
        );
        assert_eq!(
            HardwareEncoderPath::D3dFrameReadbackSinkWriter.require_automatic_hardware(),
            Err(HardwareEncoderFailure::CpuFallbackDenied)
        );

        let mut queue = FrameQueue::new(1).unwrap();
        assert_eq!(
            queue.configure_hardware_encoder(
                "PCI\\VEN_10DE&DEV_2504",
                HardwareEncoderPath::D3dFrameReadbackSinkWriter,
            ),
            Err(HardwareEncoderFailure::CpuFallbackDenied)
        );
        let status = queue.status(false, false);
        assert_eq!(status.adapter_identity, None);
        assert_eq!(status.encoder_path, None);
    }

    #[test]
    fn hardware_encoder_failures_are_explicit_and_distinct() {
        assert_ne!(
            HardwareEncoderFailure::HardwareUnavailable,
            HardwareEncoderFailure::AdapterMismatch
        );
        assert_ne!(
            HardwareEncoderFailure::GpuConversionFailure,
            HardwareEncoderFailure::EncoderSetupFailure
        );
        assert_ne!(
            HardwareEncoderFailure::EncoderRuntimeFailure,
            HardwareEncoderFailure::Backpressure
        );
        assert_ne!(
            HardwareEncoderFailure::Backpressure,
            HardwareEncoderFailure::InvalidPacket
        );
        assert_ne!(
            HardwareEncoderFailure::InvalidPacket,
            HardwareEncoderFailure::UnsupportedPacketTiming
        );
    }

    #[test]
    fn hardware_packet_status_reports_diagnostics_without_file_paths() {
        let mut queue = FrameQueue::new(2).unwrap();
        queue
            .configure_hardware_encoder(
                "PCI\\VEN_10DE&DEV_2504",
                HardwareEncoderPath::MediaFoundationHardwareH264,
            )
            .unwrap();
        queue.record_hardware_packet(100).unwrap();
        queue.record_hardware_packet(200).unwrap();
        queue.record_hardware_failure(HardwareEncoderFailure::Backpressure);
        queue.record_hardware_failure(HardwareEncoderFailure::EncoderRuntimeFailure);

        let status = queue.status(false, true);
        assert_eq!(
            status.adapter_identity.as_deref(),
            Some("PCI\\VEN_10DE&DEV_2504")
        );
        assert_eq!(
            status.encoder_path,
            Some(HardwareEncoderPath::MediaFoundationHardwareH264)
        );
        assert_eq!(status.first_packet_pts_100ns, Some(100));
        assert_eq!(status.last_packet_pts_100ns, Some(200));
        assert_eq!(status.submitted_packets, 2);
        assert_eq!(status.dropped_packets, 1);
        assert_eq!(status.encoder_errors, 1);
        assert_eq!(
            status.last_encoder_failure,
            Some(HardwareEncoderFailure::EncoderRuntimeFailure)
        );

        let json = serde_json::to_value(status).unwrap();
        let object = json.as_object().unwrap();
        assert!(object.contains_key("adapterIdentity"));
        assert!(object.contains_key("encoderPath"));
        assert!(object.contains_key("firstPacketPts100ns"));
        assert!(object.contains_key("lastPacketPts100ns"));
        assert!(object.contains_key("submittedPackets"));
        assert!(object.contains_key("droppedPackets"));
        assert!(object.keys().all(|key| !key.contains("path")));
    }

    fn replay_packet(
        pts_100ns: i64,
        duration_100ns: i64,
        keyframe: bool,
        bytes: Arc<[u8]>,
    ) -> EncodedH264Packet {
        EncodedH264Packet {
            bytes,
            pts_100ns,
            duration_100ns,
            keyframe,
        }
    }

    fn small_replay_packet(
        pts_100ns: i64,
        duration_100ns: i64,
        keyframe: bool,
    ) -> EncodedH264Packet {
        replay_packet(
            pts_100ns,
            duration_100ns,
            keyframe,
            Arc::from([0, 0, 0, 1, if keyframe { 0x65 } else { 0x41 }]),
        )
    }

    fn replay_mux_input(snapshot: ReplaySnapshot) -> ReplayMuxInput {
        ReplayMuxInput {
            snapshot,
            sequence_header: Arc::from([
                0, 0, 0, 1, 0x67, 0x42, 0xc0, 0x0d, 0xda, 0x05, 0x07, 0xec, 0x04, 0x40, 0, 0, 3, 0,
                0x40, 0, 0, 0x0f, 3, 0xc5, 0x0a, 0xa8, 0, 0, 1, 0x68, 0xce, 0x0f, 0xc8,
            ]),
            width: 320,
            height: 240,
            capture_clock: CaptureClockMetadata {
                utc_epoch_ms: 1_700_000_000_000,
                qpc_ns: 5_000_000_000,
                clock_source: "utc_epoch_ms+qpc+wgc_system_relative_time",
                timebase_version: "time_alignment.v2",
            },
        }
    }

    fn replay_mux_snapshot() -> ReplaySnapshot {
        let packets = vec![
            Arc::new(replay_packet(
                200,
                100,
                true,
                Arc::from([0, 0, 0, 1, 0x65, 0x11]),
            )),
            Arc::new(replay_packet(
                300,
                100,
                false,
                Arc::from([0, 0, 1, 0x41, 0x22]),
            )),
            Arc::new(replay_packet(
                400,
                100,
                false,
                Arc::from([0, 0, 0, 1, 0x41, 0x33]),
            )),
        ];
        ReplaySnapshot {
            packets,
            requested_start_100ns: 250,
            requested_end_100ns: 450,
            decode_start_100ns: 200,
            start_offset_100ns: 50,
            end_offset_100ns: 250,
            total_bytes: 17,
            tolerated_gaps: 0,
        }
    }

    fn read_be_u32(bytes: &[u8], offset: usize) -> u32 {
        u32::from_be_bytes(bytes[offset..offset + 4].try_into().unwrap())
    }

    fn read_be_u64(bytes: &[u8], offset: usize) -> u64 {
        u64::from_be_bytes(bytes[offset..offset + 8].try_into().unwrap())
    }

    fn mp4_child<'a>(bytes: &'a [u8], kind: &[u8; 4]) -> Option<&'a [u8]> {
        let mut offset = 0usize;
        while offset.checked_add(8)? <= bytes.len() {
            let size = read_be_u32(bytes, offset) as usize;
            if size < 8 || offset.checked_add(size)? > bytes.len() {
                return None;
            }
            if &bytes[offset + 4..offset + 8] == kind {
                return Some(&bytes[offset + 8..offset + size]);
            }
            offset += size;
        }
        None
    }

    fn mp4_path<'a>(mut bytes: &'a [u8], path: &[[u8; 4]]) -> &'a [u8] {
        for kind in path {
            bytes = mp4_child(bytes, kind).expect("expected MP4 box path");
        }
        bytes
    }

    #[test]
    fn annex_b_access_unit_converts_three_and_four_byte_start_codes() {
        let converted = annex_b_to_avcc(&[
            0, 0, 0, 1, 0x67, 0x42, 0, 0x1e, 0, 0, 1, 0x68, 0xce, 0x06, 0xe2,
        ])
        .unwrap();
        assert_eq!(
            converted,
            [0, 0, 0, 4, 0x67, 0x42, 0, 0x1e, 0, 0, 0, 4, 0x68, 0xce, 0x06, 0xe2,]
        );
        assert_eq!(
            annex_b_to_avcc(&[0, 0, 0, 0, 1, 0x65, 0xaa, 0, 0]).unwrap(),
            [0, 0, 0, 2, 0x65, 0xaa]
        );
        assert_eq!(
            annex_b_to_avcc(&[0x65, 0x11]).unwrap_err().kind,
            ReplayExportFailureKind::UnsupportedBitstreamFormat
        );
        assert_eq!(
            annex_b_to_avcc(&[0, 0, 0, 1]).unwrap_err().kind,
            ReplayExportFailureKind::UnsupportedBitstreamFormat
        );
    }

    #[test]
    fn replay_mp4_has_keyframe_sample_tables_and_exact_edit_list() {
        let input = replay_mux_input(replay_mux_snapshot());
        let (mp4, receipt) = build_replay_mp4(&input).unwrap();

        assert!(mp4_child(&mp4, b"ftyp").is_some());
        let mdat = mp4_child(&mp4, b"mdat").unwrap();
        assert!(mp4_child(&mp4, b"moov").is_some());
        assert_eq!(
            mdat,
            [0, 0, 0, 2, 0x65, 0x11, 0, 0, 0, 2, 0x41, 0x22, 0, 0, 0, 2, 0x41, 0x33,]
        );

        let elst = mp4_path(&mp4, &[*b"moov", *b"trak", *b"edts", *b"elst"]);
        assert_eq!(elst[0], 1);
        assert_eq!(read_be_u32(elst, 4), 1);
        assert_eq!(read_be_u64(elst, 8), 200);
        assert_eq!(read_be_u64(elst, 16), 50);
        assert_eq!(&elst[24..28], &[0, 1, 0, 0]);

        let stbl = mp4_path(&mp4, &[*b"moov", *b"trak", *b"mdia", *b"minf", *b"stbl"]);
        let stss = mp4_child(stbl, b"stss").unwrap();
        assert_eq!(read_be_u32(stss, 4), 1);
        assert_eq!(read_be_u32(stss, 8), 1);
        let stsz = mp4_child(stbl, b"stsz").unwrap();
        assert_eq!(read_be_u32(stsz, 8), 3);
        assert_eq!(
            (
                read_be_u32(stsz, 12),
                read_be_u32(stsz, 16),
                read_be_u32(stsz, 20)
            ),
            (6, 6, 6)
        );
        let stco = mp4_child(stbl, b"stco").unwrap();
        let chunk_offset = read_be_u32(stco, 8) as usize;
        assert_eq!(&mp4[chunk_offset..chunk_offset + mdat.len()], mdat);

        assert_eq!(receipt.requested_start_100ns, 250);
        assert_eq!(receipt.requested_end_100ns, 450);
        assert_eq!(receipt.decode_start_100ns, 200);
        assert_eq!(receipt.visible_duration_100ns, 200);
        assert_eq!(receipt.decode_preroll_100ns, 50);
        assert_eq!(receipt.reencoded_frames, 0);
    }

    #[test]
    fn replay_edit_list_supports_full_300_second_timeline_contract() {
        let packet = Arc::new(replay_packet(
            0,
            230 * 10_000_000,
            true,
            Arc::from([0, 0, 0, 1, 0x65, 0x11]),
        ));
        let snapshot = ReplaySnapshot {
            packets: vec![packet],
            requested_start_100ns: 220 * 10_000_000,
            requested_end_100ns: 230 * 10_000_000,
            decode_start_100ns: 0,
            start_offset_100ns: 220 * 10_000_000,
            end_offset_100ns: 230 * 10_000_000,
            total_bytes: 6,
            tolerated_gaps: 0,
        };
        let (mp4, _) = build_replay_mp4(&replay_mux_input(snapshot)).unwrap();
        let elst = mp4_path(&mp4, &[*b"moov", *b"trak", *b"edts", *b"elst"]);
        assert_eq!(elst[0], 1);
        assert_eq!(read_be_u64(elst, 8), 10 * 10_000_000);
        assert_eq!(read_be_u64(elst, 16), 220 * 10_000_000);
    }

    #[test]
    fn replay_export_preserves_capture_clock_sidecar_provenance() {
        let input = replay_mux_input(replay_mux_snapshot());
        let (_, receipt) = build_replay_mp4(&input).unwrap();
        let json = serde_json::to_value(receipt).unwrap();
        assert_eq!(json["captureClock"]["utcEpochMs"], 1_700_000_000_000i64);
        assert_eq!(json["captureClock"]["qpcNs"], 5_000_000_000u64);
        assert_eq!(
            json["captureClock"]["clockSource"],
            "utc_epoch_ms+qpc+wgc_system_relative_time"
        );
        assert_eq!(json["captureClock"]["timebaseVersion"], "time_alignment.v2");
    }

    #[test]
    fn replay_mux_rejects_missing_codec_config_and_reordered_packets() {
        let mut missing = replay_mux_input(replay_mux_snapshot());
        missing.sequence_header = Arc::from([]);
        assert_eq!(
            build_replay_mp4(&missing).unwrap_err().kind,
            ReplayExportFailureKind::MissingCodecConfiguration
        );

        let mut reordered = replay_mux_input(replay_mux_snapshot());
        reordered.snapshot.packets[1] = Arc::new(replay_packet(
            150,
            100,
            false,
            Arc::from([0, 0, 0, 1, 0x41, 0x22]),
        ));
        assert_eq!(
            build_replay_mp4(&reordered).unwrap_err().kind,
            ReplayExportFailureKind::UnsupportedPacketTiming
        );
    }

    #[test]
    fn replay_mux_tolerates_small_coverage_gaps_and_reports_them() {
        let gapped = ReplaySnapshot {
            packets: vec![
                Arc::new(replay_packet(
                    200,
                    100,
                    true,
                    Arc::from([0, 0, 0, 1, 0x65, 0x11]),
                )),
                Arc::new(replay_packet(
                    500_000,
                    100,
                    false,
                    Arc::from([0, 0, 0, 1, 0x41, 0x22]),
                )),
            ],
            requested_start_100ns: 250,
            requested_end_100ns: 500_100,
            decode_start_100ns: 200,
            start_offset_100ns: 50,
            end_offset_100ns: 499_900,
            total_bytes: 12,
            tolerated_gaps: 1,
        };
        let (mp4, receipt) = build_replay_mp4(&replay_mux_input(gapped)).unwrap();
        assert_eq!(receipt.packet_count, 2);
        assert_eq!(receipt.tolerated_coverage_gaps, 1);
        // 缺口由前一 sample 的时长吸收，时间线不塌陷。
        let stts = mp4_path(
            &mp4,
            &[*b"moov", *b"trak", *b"mdia", *b"minf", *b"stbl", *b"stts"],
        );
        assert_eq!(read_be_u32(stts, 4), 2);
        assert_eq!(read_be_u32(stts, 8), 1);
        assert_eq!(read_be_u32(stts, 12), 499_800);

        let oversized = ReplaySnapshot {
            packets: vec![
                Arc::new(replay_packet(
                    200,
                    100,
                    true,
                    Arc::from([0, 0, 0, 1, 0x65, 0x11]),
                )),
                Arc::new(replay_packet(
                    2_000_000,
                    100,
                    false,
                    Arc::from([0, 0, 0, 1, 0x41, 0x22]),
                )),
            ],
            requested_start_100ns: 250,
            requested_end_100ns: 2_000_100,
            decode_start_100ns: 200,
            start_offset_100ns: 50,
            end_offset_100ns: 1_999_900,
            total_bytes: 12,
            tolerated_gaps: 0,
        };
        assert_eq!(
            build_replay_mp4(&replay_mux_input(oversized))
                .unwrap_err()
                .kind,
            ReplayExportFailureKind::CoverageGap
        );
    }

    #[test]
    fn replay_mux_snapshot_remains_immutable_while_producer_continues() {
        let mut replay = EncodedReplayBuffer::with_limits(10_000, 1_000).unwrap();
        for pts in [0, 100, 200, 300, 400] {
            replay
                .push(replay_packet(
                    pts,
                    100,
                    pts == 0,
                    Arc::from([0, 0, 0, 1, if pts == 0 { 0x65 } else { 0x41 }, pts as u8]),
                ))
                .unwrap();
        }
        let snapshot = replay.snapshot(50, 250).unwrap();
        let input = replay_mux_input(snapshot);
        let barrier = Arc::new(std::sync::Barrier::new(2));
        let worker_barrier = Arc::clone(&barrier);
        let worker = std::thread::spawn(move || {
            worker_barrier.wait();
            build_replay_mp4(&input)
        });
        barrier.wait();
        replay
            .push(replay_packet(
                500,
                100,
                false,
                Arc::from([0, 0, 0, 1, 0x41, 0x55]),
            ))
            .unwrap();

        let (_, receipt) = worker.join().unwrap().unwrap();
        assert_eq!(receipt.packet_count, 3);
        assert_eq!(replay.status().last_packet_pts_100ns, Some(500));
    }

    #[test]
    fn replay_buffer_retains_300_seconds_at_eight_mbps() {
        let mut replay = EncodedReplayBuffer::new();
        let frame_duration_100ns = 10_000_000 / 60;
        let large_packet = Arc::<[u8]>::from(vec![0; 16_667]);
        let small_packet = Arc::<[u8]>::from(vec![0; 16_666]);
        for index in 0..18_000i64 {
            let pts_100ns = index * frame_duration_100ns;
            let bytes = if index % 3 == 2 {
                Arc::clone(&small_packet)
            } else {
                Arc::clone(&large_packet)
            };
            replay
                .push(replay_packet(
                    pts_100ns,
                    if index == 17_999 {
                        REPLAY_MAX_DURATION_100NS - pts_100ns
                    } else {
                        frame_duration_100ns
                    },
                    index % 60 == 0,
                    bytes,
                ))
                .unwrap();
        }

        let status = replay.status();
        assert_eq!(status.packet_count, 18_000);
        assert_eq!(status.total_bytes, 300_000_000);
        assert!(status.total_bytes < REPLAY_MAX_BYTES);
        assert_eq!(status.evicted_packets, 0);
        assert_eq!(status.coverage_gaps, 0);
        let snapshot = replay.snapshot(0, REPLAY_MAX_DURATION_100NS).unwrap();
        assert_eq!(snapshot.packets.len(), 18_000);
        assert_eq!(snapshot.requested_start_100ns, 0);
        assert_eq!(snapshot.requested_end_100ns, REPLAY_MAX_DURATION_100NS);
        assert_eq!(snapshot.decode_start_100ns, 0);
    }

    #[test]
    fn replay_buffer_evicts_to_the_next_keyframe_within_both_limits() {
        let mut time_limited = EncodedReplayBuffer::with_limits(350, 100).unwrap();
        for (pts, keyframe) in [(0, true), (100, false), (200, false), (300, true)] {
            time_limited
                .push(replay_packet(pts, 100, keyframe, Arc::from([pts as u8])))
                .unwrap();
        }
        let time_status = time_limited.status();
        assert_eq!(time_status.packet_count, 1);
        assert_eq!(time_status.first_packet_pts_100ns, Some(300));
        assert_eq!(time_status.evicted_packets, 3);

        let mut byte_limited = EncodedReplayBuffer::with_limits(1_000, 3).unwrap();
        for (pts, keyframe) in [(0, true), (100, false), (200, false), (300, true)] {
            byte_limited
                .push(replay_packet(pts, 100, keyframe, Arc::from([pts as u8])))
                .unwrap();
        }
        let byte_status = byte_limited.status();
        assert_eq!(byte_status.packet_count, 1);
        assert_eq!(byte_status.total_bytes, 1);
        assert_eq!(byte_status.first_packet_pts_100ns, Some(300));
        assert_eq!(byte_status.keyframes, 1);
        assert_eq!(byte_status.evicted_packets, 3);
    }

    #[test]
    fn replay_buffer_rejects_invalid_packets_and_incomplete_windows() {
        let mut regression = EncodedReplayBuffer::with_limits(1_000, 100).unwrap();
        regression
            .push(small_replay_packet(100, 100, true))
            .unwrap();
        assert_eq!(
            regression.push(small_replay_packet(50, 100, false)),
            Err(ReplayBufferError::TimestampRegression)
        );

        let mut overflow = EncodedReplayBuffer::with_limits(1_000, 4).unwrap();
        assert_eq!(
            overflow.push(small_replay_packet(0, 100, true)),
            Err(ReplayBufferError::ByteOverflow)
        );

        let mut missing_keyframe = EncodedReplayBuffer::with_limits(1_000, 100).unwrap();
        missing_keyframe
            .push(small_replay_packet(0, 100, false))
            .unwrap();
        assert_eq!(
            missing_keyframe.snapshot(0, 100),
            Err(ReplayBufferError::MissingKeyframeCoverage)
        );
        assert_eq!(
            missing_keyframe.snapshot(0, 301 * 10_000_000),
            Err(ReplayBufferError::WindowTooLong)
        );

        let mut incomplete = EncodedReplayBuffer::with_limits(1_000, 100).unwrap();
        incomplete.push(small_replay_packet(0, 100, true)).unwrap();
        assert_eq!(
            incomplete.snapshot(0, 200),
            Err(ReplayBufferError::IncompleteCoverage)
        );

        let mut gap = EncodedReplayBuffer::with_limits(2_000_000, 100).unwrap();
        gap.push(small_replay_packet(0, 100, true)).unwrap();
        gap.push(small_replay_packet(1_000_300, 100, false))
            .unwrap();
        assert_eq!(
            gap.snapshot(0, 1_000_400),
            Err(ReplayBufferError::CoverageGap)
        );
    }

    #[test]
    fn replay_snapshot_tolerates_gaps_within_the_export_tolerance() {
        let mut replay = EncodedReplayBuffer::with_limits(4_000_000, 200).unwrap();
        for pts in [0, 100, 300, 200_000, 2_000_000] {
            replay
                .push(small_replay_packet(pts, 100, pts == 0))
                .unwrap();
        }

        // 无缺口窗口不计数。
        let seamless = replay.snapshot(0, 200).unwrap();
        assert_eq!(seamless.packets.len(), 2);
        assert_eq!(seamless.tolerated_gaps, 0);

        // 100ns 的缺口（丢 1 帧）被容忍并计数，导出继续。
        let dropped = replay.snapshot(0, 400).unwrap();
        assert_eq!(dropped.packets.len(), 3);
        assert_eq!(dropped.tolerated_gaps, 1);

        // 窗口内多个 ≤100ms 缺口累计计数。
        let both = replay.snapshot(0, 200_100).unwrap();
        assert_eq!(both.packets.len(), 4);
        assert_eq!(both.tolerated_gaps, 2);

        // 超过 100ms 容差的缺口仍然失败。
        assert_eq!(
            replay.snapshot(0, 2_000_100),
            Err(ReplayBufferError::CoverageGap)
        );
    }

    #[test]
    fn replay_snapshot_is_immutable_while_the_producer_continues() {
        let mut replay = EncodedReplayBuffer::with_limits(1_000, 15).unwrap();
        for pts in [0, 100, 200] {
            replay
                .push(small_replay_packet(pts, 100, pts == 0))
                .unwrap();
        }
        let snapshot = replay.snapshot(0, 200).unwrap();
        assert_eq!(Arc::strong_count(&snapshot.packets[0]), 2);
        replay.push(small_replay_packet(300, 100, true)).unwrap();

        assert_eq!(snapshot.packets.len(), 2);
        assert_eq!(snapshot.packets[0].pts_100ns, 0);
        assert_eq!(snapshot.packets[1].pts_100ns, 100);
        assert_eq!(Arc::strong_count(&snapshot.packets[0]), 1);
        assert_eq!(replay.status().packet_count, 1);
        assert_eq!(replay.status().first_packet_pts_100ns, Some(300));
    }

    #[test]
    fn replay_snapshot_uses_preceding_keyframe_and_exact_offsets() {
        let mut replay = EncodedReplayBuffer::with_limits(1_000, 100).unwrap();
        for (pts, keyframe) in [(0, true), (100, false), (200, true), (300, false)] {
            replay
                .push(small_replay_packet(pts, 100, keyframe))
                .unwrap();
        }

        let snapshot = replay.snapshot(250, 350).unwrap();
        assert_eq!(snapshot.decode_start_100ns, 200);
        assert_eq!(snapshot.start_offset_100ns, 50);
        assert_eq!(snapshot.end_offset_100ns, 150);
        assert_eq!(snapshot.packets.len(), 2);
        assert!(snapshot.packets[0].keyframe);
    }

    #[test]
    fn replay_requires_separate_explicit_windows_for_consecutive_and_restarted_runs() {
        let mut replay = EncodedReplayBuffer::with_limits(10_000, 1_000).unwrap();
        for pts in (0..6_000).step_by(100) {
            replay
                .push(small_replay_packet(pts, 100, pts % 1_000 == 0))
                .unwrap();
        }

        let first = replay.snapshot(0, 2_000).unwrap();
        let restarted = replay.snapshot(3_000, 5_000).unwrap();
        assert_eq!(
            (first.requested_start_100ns, first.requested_end_100ns),
            (0, 2_000)
        );
        assert_eq!(
            (
                restarted.requested_start_100ns,
                restarted.requested_end_100ns
            ),
            (3_000, 5_000)
        );
        assert!(first.packets.last().unwrap().pts_100ns < restarted.packets[0].pts_100ns);
    }

    #[test]
    fn unsupported_start_is_explicit_and_does_not_enable_capture() {
        let mut state = WindowCaptureState::new(DEFAULT_FRAME_QUEUE_CAPACITY).unwrap();
        assert!(state.start_for_window(123).is_err());
        assert!(!state.status().enabled);
        assert_eq!(state.stop().timebase_version, "time_alignment.v2");
    }

    #[cfg(windows)]
    #[test]
    fn h264_writer_rejects_invalid_dimensions_before_startup() {
        assert!(validate_recording_dimensions(0, 1080).is_err());
        assert!(validate_recording_dimensions(1921, 1080).is_err());
        assert!(validate_recording_dimensions(1920, 1081).is_err());
        assert!(validate_recording_dimensions(1920, 1080).is_ok());
    }

    #[cfg(windows)]
    #[test]
    fn recording_throttle_caps_frames_without_reordering_timestamps() {
        let last = AtomicI64::new(-1);
        assert_eq!(
            reserve_recording_timestamp(&last, 1_000_000, 166_666),
            Some(1_000_000)
        );
        assert_eq!(reserve_recording_timestamp(&last, 1_100_000, 166_666), None);
        assert_eq!(
            reserve_recording_timestamp(&last, 1_181_818, 166_666),
            Some(1_166_666)
        );
        assert_eq!(reserve_recording_timestamp(&last, 1_000_000, 166_666), None);
    }

    #[cfg(windows)]
    #[test]
    fn recording_throttle_preserves_60_fps_phase_for_165_hz_input() {
        let last = AtomicI64::new(-1);
        let source_interval_100ns = 10_000_000 / 165;
        let target_interval_100ns = 10_000_000 / 60;
        let encoded_pts = (0..165i64)
            .filter_map(|index| {
                reserve_recording_timestamp(
                    &last,
                    index * source_interval_100ns,
                    target_interval_100ns,
                )
            })
            .collect::<Vec<_>>();

        assert_eq!(encoded_pts.len(), 60);
        assert!(encoded_pts
            .windows(2)
            .all(|pair| pair[1] - pair[0] == target_interval_100ns));
        assert_eq!(encoded_pts[3], 3 * target_interval_100ns);
        assert_ne!(encoded_pts[3], 9 * source_interval_100ns);
    }

    #[cfg(windows)]
    #[test]
    fn hardware_frames_stay_queued_until_an_input_permit_exists() {
        let (sender, receiver) = std::sync::mpsc::sync_channel(2);
        sender.send(1u8).unwrap();
        sender.send(2u8).unwrap();

        assert_eq!(dequeue_if_permitted(false, &receiver), None);
        assert_eq!(dequeue_if_permitted(true, &receiver), Some(1));
        assert_eq!(dequeue_if_permitted(false, &receiver), None);
        assert_eq!(dequeue_if_permitted(true, &receiver), Some(2));
    }

    #[cfg(windows)]
    #[test]
    fn media_foundation_pairs_use_documented_high_low_packing() {
        assert_eq!(pack_u64_pair(1920, 1080), (1920u64 << 32) | 1080);
        assert_eq!(pack_u64_pair(60, 1), (60u64 << 32) | 1);
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "requires a local hardware H.264 MFT and GPU video processor"]
    fn media_foundation_hardware_h264_surface_smoke() {
        use windows::Win32::Foundation::HMODULE;
        use windows::Win32::Graphics::Direct3D::{
            D3D_DRIVER_TYPE_HARDWARE, D3D_FEATURE_LEVEL_11_0,
        };
        use windows::Win32::Graphics::Direct3D11::{
            D3D11CreateDevice, ID3D11Device, ID3D11DeviceContext,
            D3D11_CREATE_DEVICE_VIDEO_SUPPORT, D3D11_SDK_VERSION,
        };
        use windows::Win32::Graphics::Direct3D11::{
            D3D11_BIND_RENDER_TARGET, D3D11_TEXTURE2D_DESC, D3D11_USAGE_DEFAULT,
        };
        use windows::Win32::Graphics::Dxgi::Common::{
            DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_SAMPLE_DESC,
        };
        use windows::Win32::System::WinRT::{RoInitialize, RoUninitialize, RO_INIT_MULTITHREADED};

        unsafe { RoInitialize(RO_INIT_MULTITHREADED) }
            .expect("synthetic hardware smoke should enter the WinRT MTA");
        let mut device: Option<ID3D11Device> = None;
        let mut context: Option<ID3D11DeviceContext> = None;
        unsafe {
            D3D11CreateDevice(
                None,
                D3D_DRIVER_TYPE_HARDWARE,
                HMODULE::default(),
                D3D11_CREATE_DEVICE_VIDEO_SUPPORT,
                Some(&[D3D_FEATURE_LEVEL_11_0]),
                D3D11_SDK_VERSION,
                Some(&mut device),
                None,
                Some(&mut context),
            )
        }
        .expect("hardware D3D11 device should initialize");
        let device = device.expect("D3D11 device should be returned");
        let context = context.expect("D3D11 context should be returned");
        let description = D3D11_TEXTURE2D_DESC {
            Width: 320,
            Height: 240,
            MipLevels: 1,
            ArraySize: 1,
            Format: DXGI_FORMAT_B8G8R8A8_UNORM,
            SampleDesc: DXGI_SAMPLE_DESC {
                Count: 1,
                Quality: 0,
            },
            Usage: D3D11_USAGE_DEFAULT,
            BindFlags: D3D11_BIND_RENDER_TARGET.0 as u32,
            CPUAccessFlags: 0,
            MiscFlags: 0,
        };
        let mut source = None;
        unsafe { device.CreateTexture2D(&description, None, Some(&mut source)) }
            .expect("GPU BGRA texture should initialize");
        let source = source.expect("GPU BGRA texture should be returned");
        let queue = Arc::new(Mutex::new(
            FrameQueue::new(DEFAULT_FRAME_QUEUE_CAPACITY).unwrap(),
        ));
        let mut encoder = HardwareH264Encoder::new(&device, &context, 320, 240, Arc::clone(&queue))
            .expect("same-adapter hardware H.264 encoder should initialize");
        let frame_duration = 10_000_000 / 60;
        for index in 0..120i64 {
            for _ in 0..1_000 {
                encoder
                    .drain_events()
                    .expect("hardware H.264 event drain should succeed");
                if encoder.accepts_input {
                    break;
                }
                std::thread::sleep(std::time::Duration::from_millis(1));
            }
            assert!(
                encoder.accepts_input,
                "hardware H.264 input permit timed out"
            );
            encoder
                .submit_texture(&source, index * frame_duration, frame_duration)
                .expect("GPU texture should submit without CPU readback");
        }
        for _ in 0..2_000 {
            encoder
                .drain_events()
                .expect("hardware H.264 output drain should succeed");
            if queue.lock().unwrap().status(false, true).submitted_packets == 120 {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(1));
        }
        let status = queue.lock().unwrap().status(false, true);
        eprintln!("hardware H.264 synthetic status: {status:?}");
        assert!(status.adapter_identity.is_some());
        assert_eq!(
            status.encoder_path,
            Some(HardwareEncoderPath::MediaFoundationHardwareH264)
        );
        assert_eq!(status.submitted_packets, 120);
        assert_eq!(status.dropped_packets, 0);
        assert!(status.first_packet_pts_100ns.is_some());
        assert!(status.last_packet_pts_100ns.is_some());
        assert_eq!(encoder.packet_count(), 120);
        assert_eq!(encoder.replay.status().coverage_gaps, 0);
        assert!(
            encoder.has_keyframe(),
            "expected H.264 clean-point metadata"
        );
        assert!(!encoder.full_frame_cpu_readback());
        drop(encoder);
        unsafe { RoUninitialize() };
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "requires a local hardware H.264 MFT plus ffprobe and ffmpeg test tools"]
    fn hardware_replay_snapshot_mp4_ffprobe_smoke() {
        use windows::Win32::Foundation::HMODULE;
        use windows::Win32::Graphics::Direct3D::{
            D3D_DRIVER_TYPE_HARDWARE, D3D_FEATURE_LEVEL_11_0,
        };
        use windows::Win32::Graphics::Direct3D11::{
            D3D11CreateDevice, ID3D11Device, ID3D11DeviceContext,
            D3D11_CREATE_DEVICE_VIDEO_SUPPORT, D3D11_SDK_VERSION,
        };
        use windows::Win32::Graphics::Direct3D11::{
            D3D11_BIND_RENDER_TARGET, D3D11_TEXTURE2D_DESC, D3D11_USAGE_DEFAULT,
        };
        use windows::Win32::Graphics::Dxgi::Common::{
            DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_SAMPLE_DESC,
        };
        use windows::Win32::System::WinRT::{RoInitialize, RoUninitialize, RO_INIT_MULTITHREADED};

        fn extract_rgb(path: &std::path::Path, timestamp: &str) -> [u8; 3] {
            let output = std::process::Command::new("ffmpeg")
                .args(["-v", "error", "-ss", timestamp, "-i"])
                .arg(path)
                .args([
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=1:1",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "rgb24",
                    "-",
                ])
                .output()
                .expect("ffmpeg boundary-frame extraction should start");
            assert!(
                output.status.success(),
                "ffmpeg boundary-frame extraction failed: {}",
                String::from_utf8_lossy(&output.stderr)
            );
            output.stdout[..3].try_into().unwrap()
        }

        let output_path = PathBuf::from(
            std::env::var("AIMING_COOKIE_REPLAY_MP4_SMOKE_OUTPUT")
                .expect("AIMING_COOKIE_REPLAY_MP4_SMOKE_OUTPUT is required"),
        );
        unsafe { RoInitialize(RO_INIT_MULTITHREADED) }
            .expect("replay MP4 smoke should enter the WinRT MTA");
        let mut device: Option<ID3D11Device> = None;
        let mut context: Option<ID3D11DeviceContext> = None;
        unsafe {
            D3D11CreateDevice(
                None,
                D3D_DRIVER_TYPE_HARDWARE,
                HMODULE::default(),
                D3D11_CREATE_DEVICE_VIDEO_SUPPORT,
                Some(&[D3D_FEATURE_LEVEL_11_0]),
                D3D11_SDK_VERSION,
                Some(&mut device),
                None,
                Some(&mut context),
            )
        }
        .expect("hardware D3D11 device should initialize");
        let device = device.expect("D3D11 device should be returned");
        let context = context.expect("D3D11 context should be returned");
        let description = D3D11_TEXTURE2D_DESC {
            Width: 320,
            Height: 240,
            MipLevels: 1,
            ArraySize: 1,
            Format: DXGI_FORMAT_B8G8R8A8_UNORM,
            SampleDesc: DXGI_SAMPLE_DESC {
                Count: 1,
                Quality: 0,
            },
            Usage: D3D11_USAGE_DEFAULT,
            BindFlags: D3D11_BIND_RENDER_TARGET.0 as u32,
            CPUAccessFlags: 0,
            MiscFlags: 0,
        };
        let mut source = None;
        unsafe { device.CreateTexture2D(&description, None, Some(&mut source)) }
            .expect("GPU BGRA texture should initialize");
        let source = source.expect("GPU BGRA texture should be returned");
        let mut render_target = None;
        unsafe { device.CreateRenderTargetView(&source, None, Some(&mut render_target)) }
            .expect("GPU render target should initialize");
        let render_target = render_target.expect("GPU render target should be returned");
        let queue = Arc::new(Mutex::new(
            FrameQueue::new(DEFAULT_FRAME_QUEUE_CAPACITY).unwrap(),
        ));
        let mut encoder = HardwareH264Encoder::new(&device, &context, 320, 240, Arc::clone(&queue))
            .expect("same-adapter hardware H.264 encoder should initialize");
        let frame_duration = 10_000_000 / 60;
        for index in 0..120i64 {
            for _ in 0..1_000 {
                encoder
                    .drain_events()
                    .expect("hardware H.264 event drain should succeed");
                if encoder.accepts_input {
                    break;
                }
                std::thread::sleep(std::time::Duration::from_millis(1));
            }
            assert!(
                encoder.accepts_input,
                "hardware H.264 input permit timed out"
            );
            let color = if index < 30 {
                [1.0, 0.0, 0.0, 1.0]
            } else if index < 90 {
                [0.0, 1.0, 0.0, 1.0]
            } else {
                [0.0, 0.0, 1.0, 1.0]
            };
            unsafe { context.ClearRenderTargetView(&render_target, &color) };
            encoder
                .submit_texture(&source, index * frame_duration, frame_duration)
                .expect("GPU texture should submit without CPU readback");
            encoder
                .drain_events()
                .expect("hardware H.264 event drain should succeed");
        }
        for _ in 0..120 {
            encoder
                .drain_events()
                .expect("hardware H.264 output drain should succeed");
            if encoder.replay.status().last_packet_pts_100ns == Some(119 * frame_duration) {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
        eprintln!("hardware replay status: {:?}", encoder.replay.status());
        for packets in encoder.replay.packets.as_slices().0.windows(2) {
            if packets[1].pts_100ns > packets[0].pts_100ns + packets[0].duration_100ns {
                eprintln!(
                    "hardware replay gap: previous={}+{} next={}",
                    packets[0].pts_100ns, packets[0].duration_100ns, packets[1].pts_100ns
                );
            }
        }
        let input = encoder
            .replay_mux_input(
                30 * frame_duration,
                90 * frame_duration,
                320,
                240,
                CaptureClockMetadata {
                    utc_epoch_ms: 1_700_000_000_000,
                    qpc_ns: 5_000_000_000,
                    clock_source: "utc_epoch_ms+qpc+wgc_system_relative_time",
                    timebase_version: "time_alignment.v2",
                },
            )
            .expect("hardware replay snapshot should cover the requested window");
        let receipt = export_replay_mp4_file(input, output_path.clone())
            .expect("hardware replay MP4 should mux without re-encoding");
        assert_eq!(receipt.visible_duration_100ns, 60 * frame_duration);
        assert_eq!(receipt.reencoded_frames, 0);

        let probe = std::process::Command::new("ffprobe")
            .args([
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_name,profile,avg_frame_rate,width,height,start_time,duration",
                "-of",
                "json",
            ])
            .arg(&output_path)
            .output()
            .expect("ffprobe should start");
        assert!(
            probe.status.success(),
            "ffprobe failed: {}",
            String::from_utf8_lossy(&probe.stderr)
        );
        let probe: serde_json::Value = serde_json::from_slice(&probe.stdout).unwrap();
        let stream = &probe["streams"][0];
        assert_eq!(stream["codec_name"], "h264");
        assert_eq!(stream["profile"], "Constrained Baseline");
        assert_eq!(stream["width"], 320);
        assert_eq!(stream["height"], 240);
        let duration: f64 = probe["format"]["duration"]
            .as_str()
            .unwrap()
            .parse()
            .unwrap();
        assert!(
            (duration - 1.0).abs() < 0.02,
            "unexpected duration: {duration}"
        );
        let frame_rate = stream["avg_frame_rate"].as_str().unwrap();
        let (numerator, denominator) = frame_rate.split_once('/').unwrap();
        let frame_rate = numerator.parse::<f64>().unwrap() / denominator.parse::<f64>().unwrap();
        assert!(
            (frame_rate - 60.0).abs() < 0.1,
            "unexpected FPS: {frame_rate}"
        );

        let first = extract_rgb(&output_path, "0");
        let last = extract_rgb(&output_path, "0.95");
        for pixel in [first, last] {
            assert!(
                pixel[1] > pixel[0].saturating_add(50) && pixel[1] > pixel[2].saturating_add(50),
                "visible boundary frame exposed non-Challenge color: {pixel:?}"
            );
        }
        drop(encoder);
        unsafe { RoUninitialize() };
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "requires a live KovaaK window and RTX hardware field validation"]
    fn live_kovaak_hardware_h264_raw_smoke() {
        let hwnd = std::env::var("AIMING_COOKIE_WGC_SMOKE_HWND")
            .expect("AIMING_COOKIE_WGC_SMOKE_HWND is required");
        let hwnd = if let Some(hex) = hwnd.strip_prefix("0x") {
            usize::from_str_radix(hex, 16).expect("invalid hexadecimal HWND")
        } else {
            hwnd.parse().expect("invalid decimal HWND")
        };
        let raw_output = std::path::PathBuf::from(
            std::env::var("AIMING_COOKIE_HARDWARE_SMOKE_RAW_OUTPUT")
                .expect("AIMING_COOKIE_HARDWARE_SMOKE_RAW_OUTPUT is required"),
        );
        if let Some(parent) = raw_output.parent() {
            std::fs::create_dir_all(parent).expect("Raw smoke output directory should exist");
        }
        let duration_seconds = std::env::var("AIMING_COOKIE_HARDWARE_SMOKE_SECONDS")
            .ok()
            .and_then(|value| value.parse::<u64>().ok())
            .filter(|value| *value > 0)
            .unwrap_or(10);

        let raw = crate::raw_input::RawInputState::new(raw_output);
        raw.set_enabled(true).expect("Raw Input should start");
        let mut window = WindowCaptureState::new(DEFAULT_FRAME_QUEUE_CAPACITY).unwrap();
        let started = window
            .start_for_window(hwnd)
            .expect("hardware WGC capture should start");
        assert_eq!(
            started.encoder_path,
            Some(HardwareEncoderPath::MediaFoundationHardwareH264)
        );

        let wall = std::time::Instant::now();
        let cpu_before = current_process_cpu_100ns();
        std::thread::sleep(std::time::Duration::from_secs(duration_seconds));
        let stopped = window.stop();
        let raw_running_status = raw.status();
        let raw_status = raw
            .set_enabled(false)
            .expect("Raw Input should stop cleanly");
        let elapsed = wall.elapsed().as_secs_f64();
        let cpu_seconds = (current_process_cpu_100ns() - cpu_before) as f64 / 10_000_000.0;
        let cpu_core_equivalents = cpu_seconds / elapsed;
        let packet_attempts = stopped.submitted_packets + stopped.dropped_packets;
        eprintln!("live hardware status: {stopped:?}");
        eprintln!(
            "live hardware process: wall={elapsed:.3}s cpu={cpu_seconds:.3}s cores={cpu_core_equivalents:.3}; rawDropped={}",
            raw_status.dropped_points
        );

        assert!(stopped.captured_frames >= duration_seconds.saturating_mul(45));
        assert!(packet_attempts >= duration_seconds.saturating_mul(40));
        assert!(stopped.submitted_packets > 0);
        assert!(stopped.first_packet_pts_100ns.is_some());
        assert!(stopped.last_packet_pts_100ns.is_some());
        assert_eq!(stopped.encoder_errors, 0);
        assert!(stopped.adapter_identity.is_some());
        assert!(raw_running_status.kovaak_process_present);
        assert!(raw_running_status.capture_healthy);
        assert_eq!(raw_status.dropped_points, 0);
        assert!(
            cpu_core_equivalents < 1.0,
            "hardware path should stay materially below the ~1.44-core CPU baseline"
        );
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "writes a real Media Foundation MP4 to an explicit output path"]
    fn media_foundation_synthetic_mp4_smoke() {
        let output = std::env::var("AIMING_COOKIE_MF_SMOKE_OUTPUT")
            .expect("AIMING_COOKIE_MF_SMOKE_OUTPUT is required");
        let width = 320;
        let height = 240;
        let mut writer = Mp4Writer::start(&output, width, height).expect("writer should start");
        for index in 0..60u64 {
            let mut pixels = vec![0u8; (width * height * FRAME_PIXEL_BYTES as u32) as usize];
            for pixel in pixels.chunks_exact_mut(FRAME_PIXEL_BYTES) {
                pixel[0] = (index * 3) as u8;
                pixel[1] = 96;
                pixel[2] = 192;
                pixel[3] = 255;
            }
            writer
                .write_frame(&FrameSample {
                    sequence: index + 1,
                    width,
                    height,
                    system_relative_time_100ns: index as i64 * 166_666,
                    clock: CaptureClockMetadata {
                        utc_epoch_ms: 1_700_000_000_000,
                        qpc_ns: 10_000,
                        clock_source: "test",
                        timebase_version: "time_alignment.v2",
                    },
                    bgra8: pixels,
                })
                .expect("synthetic frame should encode");
        }
        writer.finalize().expect("writer should finalize");
        let metadata = std::fs::metadata(output).expect("MP4 output should exist");
        assert!(metadata.len() > 0);
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "requires a live KovaaK window and explicit output path"]
    fn live_kovaak_mp4_recording_smoke() {
        let hwnd = std::env::var("AIMING_COOKIE_WGC_SMOKE_HWND")
            .expect("AIMING_COOKIE_WGC_SMOKE_HWND is required");
        let hwnd = if let Some(hex) = hwnd.strip_prefix("0x") {
            usize::from_str_radix(hex, 16).expect("invalid hexadecimal HWND")
        } else {
            hwnd.parse().expect("invalid decimal HWND")
        };
        let output = std::env::var("AIMING_COOKIE_WGC_SMOKE_OUTPUT")
            .expect("AIMING_COOKIE_WGC_SMOKE_OUTPUT is required");
        let mut state = WindowCaptureState::new(DEFAULT_FRAME_QUEUE_CAPACITY).unwrap();
        let started = state
            .start_recording_for_window(hwnd, output.clone().into())
            .expect("recording should start");
        assert!(started.recording);
        let wall = std::time::Instant::now();
        let cpu_before = current_process_cpu_100ns();
        std::thread::sleep(std::time::Duration::from_secs(5));
        let stopped = state.stop();
        let elapsed = wall.elapsed().as_secs_f64();
        let cpu_seconds = (current_process_cpu_100ns() - cpu_before) as f64 / 10_000_000.0;
        eprintln!("live recording status: {stopped:?}");
        eprintln!(
            "recorder process: wall={elapsed:.3}s cpu={cpu_seconds:.3}s approx_cpu={:.1}%",
            cpu_seconds / elapsed * 100.0
        );
        assert!(stopped.captured_frames > 0);
        assert!(stopped.writer_submitted_frames > 0);
        assert_eq!(stopped.encoder_errors, 0);
        let metadata = std::fs::metadata(output).expect("MP4 output should exist");
        assert!(metadata.len() > 0);
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "requires a live KovaaK window and explicit paired bundle output"]
    fn live_kovaak_raw_wgc_mp4_paired_bundle_smoke() {
        let hwnd = std::env::var("AIMING_COOKIE_WGC_SMOKE_HWND")
            .expect("AIMING_COOKIE_WGC_SMOKE_HWND is required");
        let hwnd = if let Some(hex) = hwnd.strip_prefix("0x") {
            usize::from_str_radix(hex, 16).expect("invalid hexadecimal HWND")
        } else {
            hwnd.parse().expect("invalid decimal HWND")
        };
        let output = std::env::var("AIMING_COOKIE_PAIRED_SMOKE_OUTPUT")
            .expect("AIMING_COOKIE_PAIRED_SMOKE_OUTPUT is required");
        let output_path = std::path::PathBuf::from(&output);
        let parent = output_path
            .parent()
            .expect("paired bundle output must have a parent directory");
        std::fs::create_dir_all(parent).expect("paired bundle output directory should exist");
        let raw_path = output_path.with_extension("acri-v1.bin");
        let mp4_path = output_path.with_extension("mp4");

        let raw = crate::raw_input::RawInputState::new(raw_path.clone());
        raw.set_enabled(true).expect("Raw Input should start");
        let mut window = WindowCaptureState::new(DEFAULT_FRAME_QUEUE_CAPACITY).unwrap();
        window
            .start_recording_for_window(hwnd, mp4_path.clone())
            .expect("WGC/MP4 should start");
        let duration_seconds = std::env::var("AIMING_COOKIE_PAIRED_SMOKE_SECONDS")
            .ok()
            .and_then(|value| value.parse::<u64>().ok())
            .filter(|value| *value > 0)
            .unwrap_or(5);
        std::thread::sleep(std::time::Duration::from_secs(duration_seconds));
        let window_status = window.stop();
        let raw_status = raw
            .set_enabled(false)
            .expect("Raw Input should stop cleanly");
        let bundle = serde_json::json!({
            "schemaVersion": "capture_validation_bundle.v1",
            "timebaseVersion": "time_alignment.v2",
            "raw": raw_status,
            "window": window_status,
            "rawSnapshot": raw_path.to_string_lossy(),
            "mp4": mp4_path.to_string_lossy(),
        });
        std::fs::write(
            &output_path,
            serde_json::to_vec_pretty(&bundle).expect("paired bundle should serialize"),
        )
        .expect("paired bundle should be written");
        assert!(window_status.captured_frames > 0);
        assert!(window_status.writer_submitted_frames > 0);
        assert_eq!(window_status.encoder_errors, 0);
        assert!(mp4_path.is_file());
    }
}
