//! Bounded Windows window-capture boundary.
//!
//! Windows.Graphics.Capture boundary for a bounded, non-blocking frame probe.
//! The current task records frame metadata only. Pixel readback and MP4
//! encoding are separate later steps so the Raw Input path stays priority.

use serde::{Deserialize, Serialize};
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
// 录制 H.264 的目标码率（8Mbps）。媒体类型上的 MF_MT_AVG_BITRATE 只是
// 名义值，硬件 MFT 普遍忽略它并回落到厂商默认（实测 ~16Mbps）；真正
// 生效的约束要在 SetOutputType 之前经 ICodecAPI 设置，见
// rate_control_plans / configure_rate_control。
pub const DEFAULT_RECORDING_TARGET_BITRATE_BPS: u32 = 8_000_000;
pub const REPLAY_MAX_DURATION_100NS: i64 = 300 * 10_000_000;
pub const REPLAY_MAX_BYTES: usize = 384 * 1024 * 1024;
// 导出窗口内容忍的时间线缺口：丢 1 帧（60fps 下 16.7ms）不应废掉整个
// 导出，缺口由前一 sample 的时长自然吸收；超过该阈值仍按 CoverageGap 失败。
// 软件编码层实测存在 100-250ms 级的编码抖动空洞，100ms 会把整局录制
// 判死为 capture_coverage_gap（2026-08-21 软编实测），放宽到 250ms。
pub const REPLAY_TOLERATED_GAP_100NS: i64 = 2_500_000;
// 软件编码层的输入下采样：跳帧到 30fps，使 CPU 回读+转换+编码只承担
// 一半负载；均匀跳帧让包间隔保持 ~33ms，远离 CoverageGap 阈值。
pub const SOFTWARE_INPUT_INTERVAL_100NS: i64 = 10_000_000 / 30;
pub const SOFTWARE_FRAME_DURATION_100NS: i64 = 10_000_000 / 30;

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
        let dims_note = capture_dims_note(width, height);
        validate_recording_dimensions(width, height)
            .map_err(|error| format!("{dims_note}{error}"))?;
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
            .map_err(|error| format!("{dims_note}MFStartup failed: {error}"))?;
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
                DEFAULT_RECORDING_TARGET_BITRATE_BPS,
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
            return Err(format!("{dims_note}{error}"));
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

// 编码链可接受的尺寸上限：软编兜底层（Microsoft H.264 Encoder MFT）的
// 实际上限量级在 4096 一档。超限会话显式终态而非静默缩放——缩放需要
// 软编路径不具备的 scaler，等真实报障数据证明需要时再引入。
#[cfg(windows)]
const ENCODE_DIMENSION_LIMIT: u32 = 4096;

/// 会话尺寸 → 编码尺寸：H.264/NV12 的 4:2:0 色度子采样要求偶数维，
/// 向下取偶（裁掉 ≤1px，视觉无感）。WGC item size 含非客户区，虚拟
/// 显示器与分数 DPI 缩放下常为奇数；不取偶会让硬编 NV12 纹理与软编
/// output type 双双拒绝（0930 线上报障）。<2 或超上限返回 Err，调用方
/// 显式终态，不做会话中途重建。
#[cfg(windows)]
fn normalize_encode_dimensions(width: u32, height: u32) -> Result<(u32, u32), String> {
    if width < 2 || height < 2 {
        return Err(format!(
            "capture window is too small to encode: {width}x{height}"
        ));
    }
    if width > ENCODE_DIMENSION_LIMIT || height > ENCODE_DIMENSION_LIMIT {
        return Err(format!(
            "capture window exceeds the encoder size limit {ENCODE_DIMENSION_LIMIT}: \
             {width}x{height}"
        ));
    }
    Ok((width & !1, height & !1))
}

/// 终态编码错误消息的尺寸前缀：fps/码率/profile 是全机器常量，唯一随
/// 机器变化的输入就是尺寸，必须让它第一时间出现在日志与诊断包里。
#[cfg(windows)]
fn capture_dims_note(width: u32, height: u32) -> String {
    format!(
        "capture {width}x{height}@{}/{}: ",
        DEFAULT_RECORDING_FPS_NUMERATOR, DEFAULT_RECORDING_FPS_DENOMINATOR
    )
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

// H.264 码率约束计划。硬件/软件 MFT 的默认码率控制不受媒体类型上的
// MF_MT_AVG_BITRATE 约束（实测 ~16Mbps），必须在 SetOutputType 之前经
// ICodecAPI 显式设置。先试 CBR（均值锁定目标码率），失败再试峰值受限
// VBR（均值与峰值上限都钉在目标码率，等效不超调）。
#[cfg(windows)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum RateControlPlan {
    Cbr { mean_bps: u32 },
    PeakConstrainedVbr { mean_bps: u32, max_bps: u32 },
}

#[cfg(windows)]
fn rate_control_plans(target_bps: u32) -> Vec<RateControlPlan> {
    vec![
        RateControlPlan::Cbr {
            mean_bps: target_bps,
        },
        RateControlPlan::PeakConstrainedVbr {
            mean_bps: target_bps,
            max_bps: target_bps,
        },
    ]
}

#[cfg(windows)]
fn ui4_variant(value: u32) -> windows::Win32::System::Variant::VARIANT {
    let mut variant = windows::Win32::System::Variant::VARIANT::default();
    unsafe {
        let anonymous = &mut variant.Anonymous.Anonymous;
        anonymous.vt = windows::Win32::System::Variant::VT_UI4;
        anonymous.Anonymous.ulVal = value;
    }
    variant
}

#[cfg(windows)]
fn configure_rate_control(
    codec_api: &windows::Win32::Media::MediaFoundation::ICodecAPI,
    target_bps: u32,
) -> Result<(), String> {
    use windows::Win32::Media::MediaFoundation::{
        eAVEncCommonRateControlMode_CBR, eAVEncCommonRateControlMode_PeakConstrainedVBR,
        CODECAPI_AVEncCommonMaxBitRate, CODECAPI_AVEncCommonMeanBitRate,
        CODECAPI_AVEncCommonRateControlMode,
    };
    let mut last_error = "no rate control plan was attempted".to_string();
    for plan in rate_control_plans(target_bps) {
        let (mode, mean_bps, max_bps) = match plan {
            RateControlPlan::Cbr { mean_bps } => (eAVEncCommonRateControlMode_CBR, mean_bps, None),
            RateControlPlan::PeakConstrainedVbr { mean_bps, max_bps } => (
                eAVEncCommonRateControlMode_PeakConstrainedVBR,
                mean_bps,
                Some(max_bps),
            ),
        };
        let attempt = (|| -> Result<(), String> {
            unsafe {
                codec_api
                    .SetValue(
                        &CODECAPI_AVEncCommonRateControlMode,
                        &ui4_variant(mode.0 as u32),
                    )
                    .map_err(|error| format!("rate control mode setup failed: {error}"))?;
                codec_api
                    .SetValue(&CODECAPI_AVEncCommonMeanBitRate, &ui4_variant(mean_bps))
                    .map_err(|error| format!("mean bitrate setup failed: {error}"))?;
                if let Some(max_bps) = max_bps {
                    codec_api
                        .SetValue(&CODECAPI_AVEncCommonMaxBitRate, &ui4_variant(max_bps))
                        .map_err(|error| format!("max bitrate setup failed: {error}"))?;
                }
            }
            Ok(())
        })();
        match attempt {
            Ok(()) => return Ok(()),
            Err(error) => last_error = format!("{plan:?}: {error}"),
        }
    }
    Err(last_error)
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
    // 全局硬件枚举为空但按采集适配器 LUID 定点枚举仍命中的硬件路径。
    MediaFoundationHardwareAdapterLuidH264,
    // 两层硬件枚举都为空时回退的 CPU 软件编码路径（Microsoft H264 Encoder MFT）。
    MediaFoundationSoftwareH264,
    #[allow(dead_code)] // Explicit CPU-only baseline; automatic capture rejects it.
    D3dFrameReadbackSinkWriter,
}

impl HardwareEncoderPath {
    pub fn require_automatic_hardware(self) -> Result<Self, HardwareEncoderFailure> {
        match self {
            Self::MediaFoundationHardwareH264
            | Self::MediaFoundationHardwareAdapterLuidH264
            | Self::MediaFoundationSoftwareH264 => Ok(self),
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
    // 窗口分辨率偏离采集会话启动尺寸：编码管线按启动尺寸固化，无法在
    // 会话中途重建（F6）。录制在此诚实终态化并落显式错误码，下一局
    // start 重置队列后按新尺寸自动恢复。
    CaptureResizedUnsupported,
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

#[derive(Clone, Debug, PartialEq, Serialize)]
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
    // 病灶 A：会话内的窗口尺寸漂移跟随事件（等比 letterbox）。旧 receipt
    // 无此字段 → 消费端按无变换处理，不做字段缺省迁移。
    pub geometry_events: Vec<GeometryEvent>,
}

#[derive(Clone, Debug)]
struct ReplayMuxInput {
    snapshot: ReplaySnapshot,
    sequence_header: Arc<[u8]>,
    width: u32,
    height: u32,
    capture_clock: CaptureClockMetadata,
    geometry_events: Vec<GeometryEvent>,
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
    // 与 EncodedReplayBuffer::snapshot 的尾部判定对称：末帧持续显示到
    // 窗口终点，shortfall ≤ 容忍阈值即覆盖（软编低帧率时尾包距终点
    // 数十至一百余毫秒属正常）。
    if snapshot.requested_end_100ns - covered_until > REPLAY_TOLERATED_GAP_100NS {
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
        geometry_events: input.geometry_events.clone(),
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
        // 尾部覆盖与内部缺口同一语义：视频帧持续显示到下一帧，最后一个
        // 包距窗口终点 ≤ 容忍阈值即视为覆盖。软编层帧率可能低于 30fps
        // （2026-08-21 实测 ~6fps/171ms 间隔），固定采样时长 33ms 会让
        // 尾包距终点几十至一百余毫秒就被整局判死，与内部缺口判定不对称。
        if requested_end_100ns - covered_until > REPLAY_TOLERATED_GAP_100NS {
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

#[derive(Clone, Debug, PartialEq, Serialize)]
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
    pub capture_width: Option<u32>,
    pub capture_height: Option<u32>,
    pub encode_width: Option<u32>,
    pub encode_height: Option<u32>,
    pub first_packet_pts_100ns: Option<i64>,
    pub last_packet_pts_100ns: Option<i64>,
    pub submitted_packets: u64,
    pub dropped_packets: u64,
    pub last_encoder_failure: Option<HardwareEncoderFailure>,
    // 硬编层失败被软编回退顶替时的拒绝原因（类别+消息）：末级软编聚合
    // 错误会完全遮蔽硬编层死在哪一步（0930 报障的盲区），此处单独留痕。
    pub last_hardware_rejection: Option<String>,
    pub first_system_relative_time_100ns: Option<i64>,
    pub last_system_relative_time_100ns: Option<i64>,
    pub replay_keyframes: u64,
    pub replay_evicted_packets: u64,
    pub replay_coverage_gaps: u64,
    pub replay_bytes: usize,
    // 病灶 A：窗口尺寸漂移事件史（跟随/终态化各记一条，capped 16），
    // 随诊断包 windowCapture.resizeEvents 落盘（camelCase）。
    pub resize_events: Vec<CaptureResizeEvent>,
    pub clock_source: &'static str,
    pub timebase_version: &'static str,
    pub clock_anchor_utc_ms: Option<i64>,
    pub clock_anchor_qpc_ns: Option<u128>,
    // v7 启动失败快照投影（28000 预览版 WGC 排障）：上次 start 失败的
    // 错误全文/时刻/逐适配器尝试/retry mode。快照挂在 FrameQueue 之外，
    // 随 start 入口的队列 reset 幸存，成功 start 才清除。
    pub last_start_error: Option<String>,
    pub last_start_error_at_utc_ms: Option<i64>,
    pub last_adapter_attempts: Vec<WgcAdapterAttempt>,
    pub gpu_driver_suspect: bool,
    pub retry_mode: &'static str,
}

/// 逐适配器重试的单条尝试记录：结构化进诊断包，替代纯日志拼接。
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WgcAdapterAttempt {
    pub adapter: String,
    pub luid: Option<String>,
    pub step: String,
    pub message: String,
}

/// 枚举环顺手拿到的 DXGI 适配器描述（GetDesc1）：诊断包 dxgiAdapters
/// 的数据源，成功路径零额外 DXGI 调用。
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WgcAdapterDescriptor {
    pub name: String,
    pub luid: Option<String>,
    pub vendor_id: Option<u32>,
    pub dedicated_video_memory: Option<u64>,
    pub software: bool,
}

/// 一次 WGC start 失败的现场快照：错误全文 + 时刻 + 当时 retry mode +
/// 注入开关自标注 + 逐适配器尝试与 DXGI 适配器清单。
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StartFailureSnapshot {
    pub error: String,
    pub at_utc_ms: i64,
    pub retry_mode: &'static str,
    pub force_adapter_retry: bool,
    pub simulate_default_device_failure: bool,
    pub adapter_attempts: Vec<WgcAdapterAttempt>,
    pub dxgi_adapters: Vec<WgcAdapterDescriptor>,
}

/// WGC 触发面治理模式：wide（缺省）放宽为「会话创建两步任意 Win 类错误
/// 与默认设备创建失败均可换卡重试」；strict 完整恢复 1.3.3 行为（仅
/// E_INVALIDARG 族白名单，默认设备失败即 Fatal），是旧行为的逃生开关。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum WgcRetryMode {
    Wide,
    Strict,
}

impl WgcRetryMode {
    fn as_str(self) -> &'static str {
        match self {
            WgcRetryMode::Wide => "wide",
            WgcRetryMode::Strict => "strict",
        }
    }
}

/// `AIMING_COOKIE_WGC_RETRY_MODE=strict` 恢复 1.3.3 行为；其余取值（含
/// 未设置）均为 wide。
fn wgc_retry_mode_from_env() -> WgcRetryMode {
    match std::env::var("AIMING_COOKIE_WGC_RETRY_MODE").as_deref() {
        Ok("strict") => WgcRetryMode::Strict,
        _ => WgcRetryMode::Wide,
    }
}

/// `AIMING_COOKIE_FORCE_ADAPTER_RETRY=1`：跳过默认设备直接进枚举环。
/// 仅测试/支持用，普通路径零影响。
fn wgc_force_adapter_retry_enabled() -> bool {
    std::env::var("AIMING_COOKIE_FORCE_ADAPTER_RETRY").as_deref() == Ok("1")
}

/// `AIMING_COOKIE_SIMULATE_DEFAULT_DEVICE_FAILURE=1`：create_default_device
/// 注入 Err。仅测试/支持用，普通路径零影响。
fn wgc_simulate_default_device_failure_enabled() -> bool {
    std::env::var("AIMING_COOKIE_SIMULATE_DEFAULT_DEVICE_FAILURE").as_deref() == Ok("1")
}

fn utc_now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as i64)
        .unwrap_or(0)
}

impl StartFailureSnapshot {
    fn new(
        error: String,
        adapter_attempts: Vec<WgcAdapterAttempt>,
        dxgi_adapters: Vec<WgcAdapterDescriptor>,
    ) -> Self {
        Self {
            error,
            at_utc_ms: utc_now_ms(),
            retry_mode: wgc_retry_mode_from_env().as_str(),
            force_adapter_retry: wgc_force_adapter_retry_enabled(),
            simulate_default_device_failure: wgc_simulate_default_device_failure_enabled(),
            adapter_attempts,
            dxgi_adapters,
        }
    }
}

/// gpuDriverSuspect 派生（中英两形态）：WMI 适配器名含「基本显示适配器 /
/// Basic Display Adapter」（无驱动），或启动失败快照显示枚举环里全部
/// 适配器被 software/no-driver 跳过。gpu_names 为空（WMI 不可用）时只看
/// 快照侧证据。
pub(crate) fn gpu_driver_suspect(
    gpu_names: &[String],
    last_failure: Option<&StartFailureSnapshot>,
) -> bool {
    let name_suspect = gpu_names.iter().any(|name| {
        name.contains("基本显示适配器")
            || name.to_ascii_lowercase().contains("basic display adapter")
    });
    name_suspect
        || last_failure.is_some_and(|snapshot| {
            !snapshot.adapter_attempts.is_empty()
                && snapshot.adapter_attempts.iter().all(|attempt| {
                    attempt.step == "skip" && attempt.message.contains("software/no-driver")
                })
        })
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
    // WGC item size（capture*）与取偶后进入编码链的尺寸（encode*）：
    // 奇数窗口两者差 1px，诊断时需要同时看到。
    capture_width: Option<u32>,
    capture_height: Option<u32>,
    encode_width: Option<u32>,
    encode_height: Option<u32>,
    first_packet_pts_100ns: Option<i64>,
    last_packet_pts_100ns: Option<i64>,
    submitted_packets: u64,
    dropped_packets: u64,
    last_encoder_failure: Option<HardwareEncoderFailure>,
    last_hardware_rejection: Option<String>,
    first_system_relative_time_100ns: Option<i64>,
    last_system_relative_time_100ns: Option<i64>,
    replay_keyframes: u64,
    replay_evicted_packets: u64,
    replay_coverage_gaps: u64,
    replay_bytes: usize,
    // 窗口尺寸漂移事件史（capped 16，丢最旧）与「跟随生效」标志：
    // 跟随与可观测同 PR（项目铁律）——跟随路径没有诊断字段=没做完。
    resize_events: Vec<ResizeEventRecord>,
    resize_following: bool,
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
            capture_width: None,
            capture_height: None,
            encode_width: None,
            encode_height: None,
            first_packet_pts_100ns: None,
            last_packet_pts_100ns: None,
            submitted_packets: 0,
            dropped_packets: 0,
            last_encoder_failure: None,
            last_hardware_rejection: None,
            first_system_relative_time_100ns: None,
            last_system_relative_time_100ns: None,
            replay_keyframes: 0,
            replay_evicted_packets: 0,
            replay_coverage_gaps: 0,
            replay_bytes: 0,
            resize_events: Vec::new(),
            resize_following: false,
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

    /// 会话启动时记录 WGC item size（capture*）与取偶后的编码尺寸
    /// （encode*）：尺寸不进诊断包时，奇数窗口故障只能靠猜（0930 报障）。
    pub fn record_session_dimensions(
        &mut self,
        capture_width: u32,
        capture_height: u32,
        encode_width: u32,
        encode_height: u32,
    ) {
        self.capture_width = Some(capture_width);
        self.capture_height = Some(capture_height);
        self.encode_width = Some(encode_width);
        self.encode_height = Some(encode_height);
    }

    /// 记录一次窗口尺寸漂移事件（跟随或终态化），超限丢最旧。
    pub fn record_resize_event(&mut self, event: CaptureResizeEvent, frame_pts_100ns: i64) {
        if self.resize_events.len() >= RESIZE_EVENT_HISTORY_LIMIT {
            self.resize_events.remove(0);
        }
        self.resize_events.push(ResizeEventRecord {
            event,
            frame_pts_100ns,
        });
    }

    /// 「跟随生效」标志：跟随事件生效时置位，内容回到会话尺寸或队列
    /// reset 时清除。协调器据此把 video 子状态标注为 capture_resized_following。
    pub fn set_resize_following(&mut self, active: bool) {
        self.resize_following = active;
    }

    /// 硬编层失败被软编回退顶替时留痕（类别+消息），不被末级软编聚合
    /// 错误遮蔽。
    pub fn record_hardware_rejection(&mut self, rejection: String) {
        self.last_hardware_rejection = Some(rejection);
    }

    /// 编码器在每包入重放缓冲后同步累计的重放侧统计，随诊断导出：
    /// keyframes/字节数用于判断码率与缓冲占用，evicted/coverage_gaps
    /// 用于定位导出 CoverageGap（缓冲被淘汰或时间线断档）的根因。
    pub fn record_replay_stats(
        &mut self,
        keyframe: bool,
        byte_len: usize,
        evicted_packets: u64,
        coverage_gaps: u64,
    ) {
        if keyframe {
            self.replay_keyframes += 1;
        }
        self.replay_bytes = self.replay_bytes.saturating_add(byte_len);
        self.replay_evicted_packets = evicted_packets;
        self.replay_coverage_gaps = coverage_gaps;
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
        self.capture_width = None;
        self.capture_height = None;
        self.encode_width = None;
        self.encode_height = None;
        self.first_packet_pts_100ns = None;
        self.last_packet_pts_100ns = None;
        self.submitted_packets = 0;
        self.dropped_packets = 0;
        self.last_encoder_failure = None;
        self.last_hardware_rejection = None;
        self.first_system_relative_time_100ns = None;
        self.last_system_relative_time_100ns = None;
        self.replay_keyframes = 0;
        self.replay_evicted_packets = 0;
        self.replay_coverage_gaps = 0;
        self.replay_bytes = 0;
        self.resize_events.clear();
        self.resize_following = false;
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
            capture_width: self.capture_width,
            capture_height: self.capture_height,
            encode_width: self.encode_width,
            encode_height: self.encode_height,
            first_packet_pts_100ns: self.first_packet_pts_100ns,
            last_packet_pts_100ns: self.last_packet_pts_100ns,
            submitted_packets: self.submitted_packets,
            dropped_packets: self.dropped_packets,
            last_encoder_failure: self.last_encoder_failure,
            last_hardware_rejection: self.last_hardware_rejection.clone(),
            first_system_relative_time_100ns: self.first_system_relative_time_100ns,
            last_system_relative_time_100ns: self.last_system_relative_time_100ns,
            replay_keyframes: self.replay_keyframes,
            replay_evicted_packets: self.replay_evicted_packets,
            replay_coverage_gaps: self.replay_coverage_gaps,
            replay_bytes: self.replay_bytes,
            resize_events: self
                .resize_events
                .iter()
                .map(|record| record.event.clone())
                .collect(),
            clock_source: "utc_epoch_ms+qpc+wgc_system_relative_time",
            timebase_version: "time_alignment.v2",
            clock_anchor_utc_ms: None,
            clock_anchor_qpc_ns: None,
            // 启动失败快照投影的占位值：FrameQueue 不持有快照，真实值由
            // WindowCaptureState::status() 覆盖。
            last_start_error: None,
            last_start_error_at_utc_ms: None,
            last_adapter_attempts: Vec::new(),
            gpu_driver_suspect: false,
            retry_mode: "wide",
        }
    }
}

pub struct WindowCaptureState {
    enabled: bool,
    recording: bool,
    queue: Arc<Mutex<FrameQueue>>,
    // 启动失败快照：刻意放在 FrameQueue 之外——start 入口会 reset 队列，
    // 快照必须幸存到诊断包与下一局；成功 start 才清除。Arc 共享给采集
    // 线程，失败现场（含逐适配器尝试）由线程侧写入。
    last_start_failure: Arc<Mutex<Option<StartFailureSnapshot>>>,
    // 在途 replay 导出计数（尺寸重建安全门 b）：>0 表示 mux worker 正在
    // 为导出服务，此时停采集会毁掉在途证据。锁保护；poison 按保守方向
    // （视为在途）处理，宁可推迟重建也不冒险毁证据。
    replay_exports_in_flight: Mutex<usize>,
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
            last_start_failure: Arc::new(Mutex::new(None)),
            replay_exports_in_flight: Mutex::new(0),
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
        let last_failure = self.last_start_failure_snapshot();
        status.last_start_error = last_failure.as_ref().map(|failure| failure.error.clone());
        status.last_start_error_at_utc_ms = last_failure.as_ref().map(|failure| failure.at_utc_ms);
        status.last_adapter_attempts = last_failure
            .as_ref()
            .map(|failure| failure.adapter_attempts.clone())
            .unwrap_or_default();
        // 只投快照侧证据；gpu_names（WMI 查询）侧由诊断包组装时补齐，
        // status 是高频查询，不能每次跑 PowerShell。
        status.gpu_driver_suspect = gpu_driver_suspect(&[], last_failure.as_ref());
        status.retry_mode = last_failure
            .as_ref()
            .map(|failure| failure.retry_mode)
            .unwrap_or_else(|| wgc_retry_mode_from_env().as_str());
        status
    }

    /// 上次 start 失败快照的只读克隆（诊断包 dxgiAdapters/gpuDriverSuspect
    /// 组装用）；无失败或槽位不可用时为 None。
    pub fn last_start_failure_snapshot(&self) -> Option<StartFailureSnapshot> {
        self.last_start_failure
            .lock()
            .ok()
            .and_then(|slot| slot.clone())
    }

    /// 成功 start 清除失败快照；失败快照由采集线程写入共享槽位。
    fn clear_start_failure(&self) {
        if let Ok(mut slot) = self.last_start_failure.lock() {
            *slot = None;
        }
    }

    /// 兜底写入：采集线程未及写入（如 start 超时）时由 start 侧补一份
    /// 最小快照，不覆盖线程已写入的完整现场。
    fn ensure_start_failure_snapshot(&self, error: String) {
        if let Ok(mut slot) = self.last_start_failure.lock() {
            if slot.is_none() {
                *slot = Some(StartFailureSnapshot::new(error, Vec::new(), Vec::new()));
            }
        }
    }

    #[cfg(test)]
    fn set_last_start_failure_for_test(&self, snapshot: Option<StartFailureSnapshot>) {
        if let Ok(mut slot) = self.last_start_failure.lock() {
            *slot = snapshot;
        }
    }

    /// 当前会话录制是否因窗口尺寸漂移被诚实终态化（F6）。供采集协调器在
    /// Capturing 相位轮询，把 video 子状态降级为显式原因；失败码随队列
    /// reset 清除，因此下一局 start 自动恢复，不会跨会话粘连。
    pub fn recording_terminated_by_resize(&self) -> bool {
        self.queue
            .lock()
            .map(|queue| {
                queue.last_encoder_failure
                    == Some(HardwareEncoderFailure::CaptureResizedUnsupported)
            })
            .unwrap_or(false)
    }

    /// 硬编 letterbox 跟随是否正在生效（病灶 A）。终态化在场时一律返回
    /// false：degraded/rebuild 路径拥有 video 状态，跟随标注不得覆盖它。
    pub fn recording_following_resize(&self) -> bool {
        if self.recording_terminated_by_resize() {
            return false;
        }
        self.queue
            .lock()
            .map(|queue| queue.resize_following)
            .unwrap_or(false)
    }

    #[cfg(test)]
    pub(crate) fn set_resize_following_for_test(&self, active: bool) {
        if let Ok(mut queue) = self.queue.lock() {
            queue.set_resize_following(active);
        }
    }

    /// 导出在途记账 +1：handle_export 在把导出排入 mux worker 前调用，
    /// 与 stop/重建互斥的证据保护窗由此开始。
    pub fn replay_export_begin(&self) {
        if let Ok(mut count) = self.replay_exports_in_flight.lock() {
            *count = count.saturating_add(1);
        }
    }

    /// 导出在途记账 -1：导出收尾（成功/失败/提前返回）由 RAII guard 保证。
    pub fn replay_export_end(&self) {
        if let Ok(mut count) = self.replay_exports_in_flight.lock() {
            *count = count.saturating_sub(1);
        }
    }

    /// 是否有在途 replay 导出。槽位 poison 按保守方向（视为在途）处理：
    /// 重建安全门宁可长期推迟，也不在证据可能存在的时刻停采集。
    pub fn replay_export_in_flight(&self) -> bool {
        self.replay_exports_in_flight
            .lock()
            .map(|count| *count > 0)
            .unwrap_or(true)
    }

    #[cfg(test)]
    pub(crate) fn set_resize_rebuild_probe_for_test(
        &self,
        failure: Option<HardwareEncoderFailure>,
        last_packet_pts_100ns: Option<i64>,
    ) {
        let mut queue = self.queue.lock().unwrap();
        queue.last_encoder_failure = failure;
        queue.last_packet_pts_100ns = last_packet_pts_100ns;
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
            let failure_snapshot = Arc::clone(&self.last_start_failure);
            let thread_stop = Arc::clone(&stop);
            let (ready_tx, ready_rx) = std::sync::mpsc::sync_channel(1);
            let (command_sender, command_receiver) = std::sync::mpsc::sync_channel(1);
            let join = thread::spawn(move || {
                crate::thread_priority::apply_capture_thread_priority();
                run_wgc_window_capture(
                    hwnd,
                    queue,
                    thread_stop,
                    ready_tx,
                    recording_path,
                    clock_metadata,
                    command_receiver,
                    failure_snapshot,
                )
            });
            match ready_rx.recv_timeout(std::time::Duration::from_secs(5)) {
                Ok(Ok(())) => {
                    self.clear_start_failure();
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
                    // 快照本体由采集线程写入共享槽位；此处只兜底防空。
                    self.ensure_start_failure_snapshot(error.clone());
                    Err(error)
                }
                Err(error) => {
                    stop.store(true, Ordering::Release);
                    let _ = join.join();
                    let message = format!("window capture startup timed out: {error}");
                    self.ensure_start_failure_snapshot(message.clone());
                    Err(message)
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
        // 编码尺寸取偶后 staging 按编码尺寸分配；源表面（item size，可为
        // 奇数）不能整拷（CopyResource 要求两侧同尺寸），用 box 裁剪复制
        // 左上 encode* 区域。
        let crop_box = windows::Win32::Graphics::Direct3D11::D3D11_BOX {
            left: 0,
            top: 0,
            front: 0,
            right: self.width,
            bottom: self.height,
            back: 1,
        };
        unsafe {
            self.context.CopySubresourceRegion(
                &self.staging[staging_index],
                0,
                0,
                0,
                0,
                &source,
                0,
                Some(&crop_box as *const _),
            );
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
        // 像素已按编码尺寸（取偶）裁剪，sample 尺寸同步改写，与
        // Mp4Writer 的启动尺寸保持一致（write_frame 会做全等校验）。
        completed.sample.width = self.width;
        completed.sample.height = self.height;
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
    // UpdateSubresource（NV12 清黑）挂在基类 ID3D11DeviceContext 上，绑定
    // 未把基类方法投到 ID3D11VideoContext，留一份引用清黑时用。
    device_context: windows::Win32::Graphics::Direct3D11::ID3D11DeviceContext,
    enumerator: windows::Win32::Graphics::Direct3D11::ID3D11VideoProcessorEnumerator,
    processor: windows::Win32::Graphics::Direct3D11::ID3D11VideoProcessor,
    output_texture: windows::Win32::Graphics::Direct3D11::ID3D11Texture2D,
    output_view: windows::Win32::Graphics::Direct3D11::ID3D11VideoProcessorOutputView,
    output_width: u32,
    output_height: u32,
    // 当前已应用于 video processor 的 stream 矩形；None = 默认矩形
    // （全源→全目标，既有行为）。矩形变化时先清黑 NV12 目标。
    applied_rect: Option<LetterboxRect>,
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
            device_context: context.clone(),
            enumerator,
            processor,
            output_texture,
            output_view: output_view.ok_or_else(|| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::GpuConversionFailure,
                    "GPU NV12 output view was not returned",
                )
            })?,
            output_width: width,
            output_height: height,
            applied_rect: None,
        })
    }

    /// 带等比 letterbox 矩形的转换路径（病灶 A）：Blt 前设置 stream
    /// source/dest rect（stream index=0），目标矩形由调用方按等比 fit
    /// 居中算好；黑边靠对 NV12 目标清黑（Y=0、UV=128）实现。
    /// 无矩形调用（None）恢复默认矩形，保持既有全源→全目标行为。
    fn convert_with_letterbox(
        &mut self,
        source: &windows::Win32::Graphics::Direct3D11::ID3D11Texture2D,
        letterbox: Option<LetterboxRect>,
    ) -> Result<&windows::Win32::Graphics::Direct3D11::ID3D11Texture2D, HardwareEncoderError> {
        use windows::Win32::Foundation::RECT;

        if letterbox != self.applied_rect {
            match letterbox {
                Some(rect) => {
                    // 矩形首次生效或变化（含缩小后旧画面残留在新黑边里）：
                    // 先清黑再 Blt。稳态跟随矩形不变时零额外成本。
                    self.clear_nv12_output();
                    let source_rect = RECT {
                        left: 0,
                        top: 0,
                        right: rect.src_width,
                        bottom: rect.src_height,
                    };
                    let destination_rect = RECT {
                        left: rect.dst_x,
                        top: rect.dst_y,
                        right: rect.dst_x + rect.dst_width,
                        bottom: rect.dst_y + rect.dst_height,
                    };
                    unsafe {
                        self.video_context.VideoProcessorSetStreamSourceRect(
                            &self.processor,
                            0,
                            true,
                            Some(&source_rect),
                        );
                        self.video_context.VideoProcessorSetStreamDestRect(
                            &self.processor,
                            0,
                            true,
                            Some(&destination_rect),
                        );
                    }
                }
                None => {
                    // 回到无矩形路径：显式禁用矩形，恢复默认全源→全目标。
                    unsafe {
                        self.video_context.VideoProcessorSetStreamSourceRect(
                            &self.processor,
                            0,
                            false,
                            None,
                        );
                        self.video_context.VideoProcessorSetStreamDestRect(
                            &self.processor,
                            0,
                            false,
                            None,
                        );
                    }
                }
            }
            self.applied_rect = letterbox;
        }
        self.blt(source)
    }

    /// NV12 清黑：Y 平面 0、UV 平面 128（中灰，避免色度偏色）。只在
    /// converter 创建后首个矩形生效与矩形变化时执行——每帧清零需要每帧
    /// 上传 ~1.5×分辨率字节（2560x1600 下约 6MB/帧），而 Blt 只写 dst
    /// 矩形，两次矩形变化之间黑边内容不变。UpdateSubresource 无返回值，
    /// 失败只在调试层可见，不影响录制继续（诚实性由事件与日志承担）。
    fn clear_nv12_output(&mut self) {
        let width = self.output_width;
        let height = self.output_height;
        let luma_bytes = width as usize * height as usize;
        let mut data = vec![0u8; luma_bytes + luma_bytes / 2];
        for byte in &mut data[luma_bytes..] {
            *byte = 128;
        }
        unsafe {
            self.device_context.UpdateSubresource(
                &self.output_texture,
                0,
                None,
                data.as_ptr().cast(),
                width,
                width * height * 3 / 2,
            );
        }
    }

    fn blt(
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
            let global = enumerate_h264_mfts(None, true)?;
            let (candidates, path) = if global.is_empty() {
                // 第二层：全局硬件枚举为空时不再立即失败，先按采集适配器
                // LUID 定点枚举；命中后走与第一层相同的装配路径。
                let luid = enumerate_h264_mfts(Some(adapter_luid), true)?;
                if luid.is_empty() {
                    return Err(HardwareEncoderError::new(
                        HardwareEncoderFailure::HardwareUnavailable,
                        "no Media Foundation hardware H.264 encoder is available",
                    ));
                }
                (
                    luid,
                    HardwareEncoderPath::MediaFoundationHardwareAdapterLuidH264,
                )
            } else {
                let luid = enumerate_h264_mfts(Some(adapter_luid), true)?;
                if luid.is_empty() {
                    return Err(HardwareEncoderError::new(
                        HardwareEncoderFailure::AdapterMismatch,
                        "no Media Foundation hardware H.264 encoder matches the capture adapter",
                    ));
                }
                (luid, HardwareEncoderPath::MediaFoundationHardwareH264)
            };
            // 层级选定即写入诊断，后续装配失败时 adapter_identity 与
            // encoderPath 仍保留，不因编码器初始化失败而丢失。
            queue
                .lock()
                .map_err(|_| {
                    HardwareEncoderError::new(
                        HardwareEncoderFailure::EncoderSetupFailure,
                        "window capture status is unavailable",
                    )
                })?
                .configure_hardware_encoder(adapter_identity, path)
                .map_err(|failure| {
                    HardwareEncoderError::new(failure, "hardware policy rejected")
                })?;

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
                DEFAULT_RECORDING_TARGET_BITRATE_BPS,
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
                // ICodecAPI 码率约束必须在 SetOutputType 之前生效，否则硬件
                // MFT 按厂商默认码率编码（实测 ~16Mbps，超标一倍）。
                let codec_api: windows::Win32::Media::MediaFoundation::ICodecAPI =
                    match transform.cast() {
                        Ok(codec_api) => codec_api,
                        Err(error) => {
                            last_rejection =
                                format!("{friendly_name}: MFT ICodecAPI cast failed: {error}");
                            continue;
                        }
                    };
                if let Err(error) =
                    configure_rate_control(&codec_api, DEFAULT_RECORDING_TARGET_BITRATE_BPS)
                {
                    last_rejection =
                        format!("{friendly_name}: MFT rate control setup failed: {error}");
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
                    "{}no same-adapter hardware H.264 MFT accepted D3D11 NV12 surfaces; \
                     candidates={candidate_count}; d3d11Aware={d3d11_aware_count}; \
                     lastRejection={last_rejection}",
                    capture_dims_note(width, height)
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
        letterbox: Option<LetterboxRect>,
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
        let nv12 = self.converter.convert_with_letterbox(source, letterbox)?;
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
        if let Ok(mut queue) = self.queue.lock() {
            queue.record_replay_stats(
                packet.keyframe,
                packet.bytes.len(),
                self.replay.evicted_packets,
                self.replay.coverage_gaps,
            );
        }
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
            geometry_events: queue_geometry_events(&self.queue),
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

// BT.601 有限范围的 CPU BGRA→NV12 转换。WGC 帧为 B8G8R8A8 交错布局，
// NV12 为全尺寸 Y 平面 + 2x2 平均的 UV 交错平面；行内计算避免逐行拷贝。
#[cfg(windows)]
fn bgra8_to_nv12(bgra: &[u8], width: u32, height: u32) -> Vec<u8> {
    let width = width as usize;
    let height = height as usize;
    // 奇数尺寸按 ceil 计算色度平面（MFCalculateImageSize 的 NV12 合同）；
    // 3/2 的截断公式只对偶数尺寸成立。
    let chroma_width = width.div_ceil(2);
    let chroma_height = height.div_ceil(2);
    let mut nv12 = vec![0u8; width * height + chroma_width * chroma_height * 2];
    for row in 0..height {
        for col in 0..width {
            let offset = (row * width + col) * FRAME_PIXEL_BYTES;
            let blue = bgra[offset] as u32;
            let green = bgra[offset + 1] as u32;
            let red = bgra[offset + 2] as u32;
            let luma = (66 * red + 129 * green + 25 * blue + 128) / 256 + 16;
            nv12[row * width + col] = luma.clamp(16, 235) as u8;
        }
    }
    let uv_plane = width * height;
    for row in (0..height).step_by(2) {
        for col in (0..width).step_by(2) {
            let mut red = 0u32;
            let mut green = 0u32;
            let mut blue = 0u32;
            // 奇数宽/高时 2x2 色度块越界：边缘像素复制补齐（窗口尺寸可奇，
            // 视觉上 1px 的色度边缘复制无感知差异）。
            for (dy, dx) in [(0, 0), (0, 1), (1, 0), (1, 1)] {
                let sample_row = (row + dy).min(height - 1);
                let sample_col = (col + dx).min(width - 1);
                let offset = (sample_row * width + sample_col) * FRAME_PIXEL_BYTES;
                blue += bgra[offset] as u32;
                green += bgra[offset + 1] as u32;
                red += bgra[offset + 2] as u32;
            }
            red /= 4;
            green /= 4;
            blue /= 4;
            let chroma_u =
                (-38 * red as i32 - 74 * green as i32 + 112 * blue as i32 + 128) / 256 + 128;
            let chroma_v =
                (112 * red as i32 - 94 * green as i32 - 18 * blue as i32 + 128) / 256 + 128;
            let index = uv_plane + (row / 2) * chroma_width * 2 + (col / 2) * 2;
            nv12[index] = chroma_u.clamp(16, 240) as u8;
            nv12[index + 1] = chroma_v.clamp(16, 240) as u8;
        }
    }
    nv12
}

// 软件 H.264 编码器（Microsoft H264 Encoder MFT）第三层回退路径。
// 与硬件路径的差异：同步 MFT（无 async 事件回调）、输入为 CPU 读回的
// BGRA→NV12 系统内存帧（MFCreateMemoryBuffer，不设 D3D 设备管理器），
// ProcessInput 后同步 ProcessOutput 排空。输出合同与硬件路径完全一致：
// EncodedH264Packet 进 replay、sequence_header（SPS/PPS）进 mux、
// queue 记录包与层级，下游 replay 导出无感知。
#[cfg(windows)]
struct SoftwareH264Encoder {
    transform: windows::Win32::Media::MediaFoundation::IMFTransform,
    output_stream_info: windows::Win32::Media::MediaFoundation::MFT_OUTPUT_STREAM_INFO,
    queue: Arc<Mutex<FrameQueue>>,
    replay: EncodedReplayBuffer,
    sequence_header: Vec<u8>,
    // 30fps 下采样：距离上一提交帧不足 SOFTWARE_INPUT_INTERVAL_100NS 的
    // 输入帧在回读/转换前被丢弃，避免 CPU 编码积压引发帧通道丢弃断档。
    last_submitted_pts_100ns: Option<i64>,
    width: u32,
    height: u32,
    context: windows::Win32::Graphics::Direct3D11::ID3D11DeviceContext,
    staging: windows::Win32::Graphics::Direct3D11::ID3D11Texture2D,
    _mf_platform: MediaFoundationPlatform,
}

#[cfg(windows)]
impl SoftwareH264Encoder {
    // source* 是 WGC 帧表面的原始尺寸（可为奇数，BGRA 合法）；encode* 是
    // 取偶后的编码尺寸（媒体类型/NV12 转换/MFT 输入都用它）。staging 按
    // source* 分配保 CopyResource 同尺寸，回读行拷贝按 encode* 裁掉 ≤1px。
    fn new(
        device: &windows::Win32::Graphics::Direct3D11::ID3D11Device,
        context: &windows::Win32::Graphics::Direct3D11::ID3D11DeviceContext,
        source_width: u32,
        source_height: u32,
        encode_width: u32,
        encode_height: u32,
        queue: Arc<Mutex<FrameQueue>>,
    ) -> Result<Self, HardwareEncoderError> {
        use windows::core::Interface;
        use windows::Win32::Graphics::Direct3D11::{
            D3D11_CPU_ACCESS_READ, D3D11_TEXTURE2D_DESC, D3D11_USAGE_STAGING,
        };
        use windows::Win32::Graphics::Dxgi::Common::{
            DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_SAMPLE_DESC,
        };
        use windows::Win32::Media::MediaFoundation::{
            eAVEncH264VProfile_Base, MFMediaType_Video, MFStartup, MFVideoFormat_H264,
            MFVideoFormat_NV12, MFSTARTUP_FULL, MFT_MESSAGE_NOTIFY_BEGIN_STREAMING,
            MFT_MESSAGE_NOTIFY_START_OF_STREAM, MF_MT_MPEG2_PROFILE, MF_VERSION,
        };

        unsafe { MFStartup(MF_VERSION, MFSTARTUP_FULL) }.map_err(|error| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::HardwareUnavailable,
                format!("Media Foundation startup failed: {error}"),
            )
        })?;
        let setup = (|| {
            let (_, adapter_identity) = adapter_identity(device)?;
            let candidates = enumerate_h264_mfts(None, false)?;
            if candidates.is_empty() {
                return Err(HardwareEncoderError::new(
                    HardwareEncoderFailure::HardwareUnavailable,
                    "no Media Foundation software H.264 encoder is available",
                ));
            }
            let input_type = create_video_type(
                MFMediaType_Video,
                MFVideoFormat_NV12,
                encode_width,
                encode_height,
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
                encode_width,
                encode_height,
                DEFAULT_RECORDING_FPS_NUMERATOR,
                DEFAULT_RECORDING_FPS_DENOMINATOR,
                DEFAULT_RECORDING_TARGET_BITRATE_BPS,
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
                            format!("software MFT NV12 interlace mode setup failed: {error}"),
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
                            format!("software MFT H.264 interlace mode setup failed: {error}"),
                        )
                    })?;
                output_type
                    .SetUINT32(&MF_MT_MPEG2_PROFILE, eAVEncH264VProfile_Base.0 as u32)
                    .map_err(|error| {
                        HardwareEncoderError::new(
                            HardwareEncoderFailure::EncoderSetupFailure,
                            format!("software MFT H.264 Baseline profile setup failed: {error}"),
                        )
                    })?;
            }
            let staging_description = D3D11_TEXTURE2D_DESC {
                Width: source_width,
                Height: source_height,
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
            let mut staging = None;
            unsafe { device.CreateTexture2D(&staging_description, None, Some(&mut staging)) }
                .map_err(|error| {
                    HardwareEncoderError::new(
                        HardwareEncoderFailure::EncoderSetupFailure,
                        format!("software input staging texture creation failed: {error}"),
                    )
                })?;
            let staging = staging.ok_or_else(|| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::EncoderSetupFailure,
                    "software input staging texture was not returned",
                )
            })?;
            let candidate_count = candidates.len();
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
                // 与硬件路径同一配置点：ICodecAPI 码率约束先于 SetOutputType，
                // 防止软编 MFT 回落到自身默认码率（历史实测约超标 10 倍）。
                let codec_api: windows::Win32::Media::MediaFoundation::ICodecAPI =
                    match transform.cast() {
                        Ok(codec_api) => codec_api,
                        Err(error) => {
                            last_rejection =
                                format!("{friendly_name}: MFT ICodecAPI cast failed: {error}");
                            continue;
                        }
                    };
                if let Err(error) =
                    configure_rate_control(&codec_api, DEFAULT_RECORDING_TARGET_BITRATE_BPS)
                {
                    last_rejection =
                        format!("{friendly_name}: MFT rate control setup failed: {error}");
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
                if let Err(error) =
                    unsafe { transform.ProcessMessage(MFT_MESSAGE_NOTIFY_BEGIN_STREAMING, 0) }
                {
                    last_rejection = format!(
                        "{friendly_name}: MFT begin streaming notification failed: {error}"
                    );
                    continue;
                }
                if let Err(error) =
                    unsafe { transform.ProcessMessage(MFT_MESSAGE_NOTIFY_START_OF_STREAM, 0) }
                {
                    last_rejection = format!(
                        "{friendly_name}: MFT start streaming notification failed: {error}"
                    );
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
                        HardwareEncoderPath::MediaFoundationSoftwareH264,
                    )
                    .map_err(|failure| {
                        HardwareEncoderError::new(failure, "software policy rejected")
                    })?;
                return Ok(Self {
                    transform,
                    output_stream_info,
                    queue,
                    replay: EncodedReplayBuffer::new(),
                    sequence_header,
                    last_submitted_pts_100ns: None,
                    width: encode_width,
                    height: encode_height,
                    context: context.clone(),
                    staging,
                    _mf_platform: MediaFoundationPlatform,
                });
            }
            Err(HardwareEncoderError::new(
                HardwareEncoderFailure::EncoderSetupFailure,
                format!(
                    "{}no software H.264 MFT accepted NV12 input; \
                     candidates={candidate_count}; lastRejection={last_rejection}",
                    capture_dims_note(encode_width, encode_height)
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
        use windows::Win32::Graphics::Direct3D11::{D3D11_MAPPED_SUBRESOURCE, D3D11_MAP_READ};
        use windows::Win32::Media::MediaFoundation::{MFCreateMemoryBuffer, MFCreateSample};

        // 软件路径逐帧同步排空输出，不需要 async 事件泵；阻塞 Map 等待
        // CopyResource 完成，fallback 路径不追求 GPU 流水线并行。
        unsafe { self.context.CopyResource(&self.staging, source) };
        let mut mapped = D3D11_MAPPED_SUBRESOURCE::default();
        unsafe {
            self.context
                .Map(&self.staging, 0, D3D11_MAP_READ, 0, Some(&mut mapped))
        }
        .map_err(|error| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::EncoderRuntimeFailure,
                format!("software input staging map failed: {error}"),
            )
        })?;
        let row_bytes = (self.width as usize)
            .checked_mul(FRAME_PIXEL_BYTES)
            .ok_or_else(|| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::InvalidPacket,
                    "software input row byte size overflow",
                )
            })?;
        let total_bytes = row_bytes.checked_mul(self.height as usize).ok_or_else(|| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::InvalidPacket,
                "software input frame byte size overflow",
            )
        })?;
        let pixels = if mapped.pData.is_null() || mapped.RowPitch < row_bytes as u32 {
            Err(HardwareEncoderError::new(
                HardwareEncoderFailure::InvalidPacket,
                "software input staging returned an invalid mapping",
            ))
        } else if mapped.RowPitch == row_bytes as u32 {
            let mut pixels = vec![0u8; total_bytes];
            unsafe {
                std::ptr::copy_nonoverlapping(
                    mapped.pData.cast::<u8>(),
                    pixels.as_mut_ptr(),
                    total_bytes,
                );
            }
            Ok(pixels)
        } else {
            let mut pixels = vec![0u8; total_bytes];
            for row in 0..self.height as usize {
                unsafe {
                    std::ptr::copy_nonoverlapping(
                        (mapped.pData as *const u8).add(row * mapped.RowPitch as usize),
                        pixels.as_mut_ptr().add(row * row_bytes),
                        row_bytes,
                    );
                }
            }
            Ok(pixels)
        };
        unsafe { self.context.Unmap(&self.staging, 0) };
        let pixels = pixels?;

        let nv12 = bgra8_to_nv12(&pixels, self.width, self.height);
        let buffer = unsafe { MFCreateMemoryBuffer(nv12.len() as u32) }.map_err(|error| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::EncoderRuntimeFailure,
                format!("software input buffer creation failed: {error}"),
            )
        })?;
        let mut destination = std::ptr::null_mut();
        unsafe {
            buffer.Lock(&mut destination, None, None).map_err(|error| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::EncoderRuntimeFailure,
                    format!("software input buffer lock failed: {error}"),
                )
            })?;
            std::ptr::copy_nonoverlapping(nv12.as_ptr(), destination, nv12.len());
            buffer.Unlock().map_err(|error| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::EncoderRuntimeFailure,
                    format!("software input buffer unlock failed: {error}"),
                )
            })?;
            buffer
                .SetCurrentLength(nv12.len() as u32)
                .map_err(|error| {
                    HardwareEncoderError::new(
                        HardwareEncoderFailure::EncoderRuntimeFailure,
                        format!("software input buffer length setup failed: {error}"),
                    )
                })?;
        }
        let sample = unsafe { MFCreateSample() }.map_err(|error| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::EncoderRuntimeFailure,
                format!("software input sample creation failed: {error}"),
            )
        })?;
        unsafe {
            sample.AddBuffer(&buffer).map_err(|error| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::EncoderRuntimeFailure,
                    format!("software input sample buffer setup failed: {error}"),
                )
            })?;
            sample.SetSampleTime(pts_100ns).map_err(|error| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::EncoderRuntimeFailure,
                    format!("software input PTS setup failed: {error}"),
                )
            })?;
            sample.SetSampleDuration(duration_100ns).map_err(|error| {
                HardwareEncoderError::new(
                    HardwareEncoderFailure::EncoderRuntimeFailure,
                    format!("software input duration setup failed: {error}"),
                )
            })?;
            self.transform
                .ProcessInput(0, &sample, 0)
                .map_err(|error| {
                    HardwareEncoderError::new(
                        HardwareEncoderFailure::EncoderRuntimeFailure,
                        format!("software H.264 ProcessInput failed: {error}"),
                    )
                })?;
        }
        self.pump_output()
    }

    fn pump_output(&mut self) -> Result<(), HardwareEncoderError> {
        use std::mem::ManuallyDrop;
        use windows::Win32::Media::MediaFoundation::{
            MFCreateMemoryBuffer, MFCreateSample, MFT_OUTPUT_DATA_BUFFER,
            MFT_OUTPUT_STREAM_CAN_PROVIDE_SAMPLES, MFT_OUTPUT_STREAM_PROVIDES_SAMPLES,
            MF_E_TRANSFORM_NEED_MORE_INPUT, MF_E_TRANSFORM_STREAM_CHANGE,
        };

        // 同步 MFT 驱动循环：ProcessOutput 直到 NEED_MORE_INPUT。成功但
        // 无 sample（NO_SAMPLE 标志）时继续泵取；上限防止病态 MFT 死循环。
        for _ in 0..1024 {
            let needs_sample = self.output_stream_info.dwFlags
                & (MFT_OUTPUT_STREAM_PROVIDES_SAMPLES.0 | MFT_OUTPUT_STREAM_CAN_PROVIDE_SAMPLES.0)
                    as u32
                == 0;
            let provided_sample = if needs_sample {
                let sample = unsafe { MFCreateSample() }.map_err(|error| {
                    HardwareEncoderError::new(
                        HardwareEncoderFailure::EncoderRuntimeFailure,
                        format!("software output sample creation failed: {error}"),
                    )
                })?;
                let buffer = unsafe { MFCreateMemoryBuffer(self.output_stream_info.cbSize) }
                    .map_err(|error| {
                        HardwareEncoderError::new(
                            HardwareEncoderFailure::EncoderRuntimeFailure,
                            format!("software output buffer creation failed: {error}"),
                        )
                    })?;
                unsafe { sample.AddBuffer(&buffer) }.map_err(|error| {
                    HardwareEncoderError::new(
                        HardwareEncoderFailure::EncoderRuntimeFailure,
                        format!("software output buffer setup failed: {error}"),
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
            match process_result {
                Ok(()) => {
                    let Some(sample) = sample else {
                        // 成功但未产出 sample 不是终止条件，继续泵取直到
                        // NEED_MORE_INPUT 或达到上限。
                        continue;
                    };
                    let packet = self.sample_to_packet(&sample)?;
                    self.accept_packet(packet)?;
                }
                Err(error)
                    if error.code() == MF_E_TRANSFORM_STREAM_CHANGE
                        || error.code() == windows::Win32::Media::MediaFoundation::MF_E_TRANSFORM_TYPE_NOT_SET =>
                {
                    // SPS/PPS 或输出类型刷新：重读 sequence header 后继续。
                    self.sequence_header = sequence_header(&self.transform);
                }
                Err(error) if error.code() == MF_E_TRANSFORM_NEED_MORE_INPUT => {
                    return Ok(());
                }
                Err(error) => {
                    return Err(HardwareEncoderError::new(
                        HardwareEncoderFailure::EncoderRuntimeFailure,
                        format!("software H.264 ProcessOutput failed: {error}"),
                    ));
                }
            }
        }
        Err(HardwareEncoderError::new(
            HardwareEncoderFailure::EncoderRuntimeFailure,
            "software H.264 output pump exceeded the iteration limit",
        ))
    }

    fn sample_to_packet(
        &self,
        sample: &windows::Win32::Media::MediaFoundation::IMFSample,
    ) -> Result<EncodedH264Packet, HardwareEncoderError> {
        use windows::Win32::Media::MediaFoundation::MFSampleExtension_CleanPoint;

        let buffer = unsafe { sample.ConvertToContiguousBuffer() }.map_err(|error| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::InvalidPacket,
                format!("software H.264 access unit buffer conversion failed: {error}"),
            )
        })?;
        let mut data = std::ptr::null_mut();
        let mut current_length = 0;
        unsafe { buffer.Lock(&mut data, None, Some(&mut current_length)) }.map_err(|error| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::InvalidPacket,
                format!("software H.264 access unit lock failed: {error}"),
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
                format!("software H.264 access unit unlock failed: {error}"),
            )
        })?;
        let pts_100ns = unsafe { sample.GetSampleTime() }.map_err(|error| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::InvalidPacket,
                format!("software H.264 packet PTS read failed: {error}"),
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
                "software H.264 decode timestamp exceeds the supported timeline",
            )
        })?
        .unwrap_or(pts_100ns);
        if decode_timestamp != pts_100ns {
            return Err(HardwareEncoderError::new(
                HardwareEncoderFailure::UnsupportedPacketTiming,
                "reordered H.264 output is unsupported by replay MP4 v1",
            ));
        }
        let duration_100ns = unsafe { sample.GetSampleDuration() }.map_err(|error| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::InvalidPacket,
                format!("software H.264 packet duration read failed: {error}"),
            )
        })?;
        Ok(EncodedH264Packet {
            bytes: bytes.into(),
            pts_100ns,
            duration_100ns,
            keyframe: unsafe { sample.GetUINT32(&MFSampleExtension_CleanPoint) }.unwrap_or(0) != 0,
        })
    }

    fn accept_packet(&mut self, packet: EncodedH264Packet) -> Result<(), HardwareEncoderError> {
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
                format!("software replay buffer rejected a packet: {error:?}"),
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
            .map_err(|failure| HardwareEncoderError::new(failure, "invalid software packet"))?;
        if let Ok(mut queue) = self.queue.lock() {
            queue.record_replay_stats(
                packet.keyframe,
                packet.bytes.len(),
                self.replay.evicted_packets,
                self.replay.coverage_gaps,
            );
        }
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
            geometry_events: queue_geometry_events(&self.queue),
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
        true
    }
}

#[cfg(windows)]
impl Drop for SoftwareH264Encoder {
    fn drop(&mut self) {
        use windows::core::Interface;
        use windows::Win32::Media::MediaFoundation::{
            IMFShutdown, MFT_MESSAGE_COMMAND_FLUSH, MFT_MESSAGE_NOTIFY_END_STREAMING,
        };

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

// 自动采集编码器封装：硬件路径（第一/二层）或软件路径（第三层）共用
// 同一装配点与帧驱动协议，下游 replay 导出与诊断无感知具体层级。
// 软件编码层的输入下采样判定：与上一提交帧间隔达到 interval_100ns 才
// 接受，否则在回读前丢弃该帧。interval 由调用方按帧通道积压深度放大
// （见 submit_capture_frame），使包间隔稳定贴住可持续编码节奏。
#[cfg(windows)]
fn software_input_accepted(
    last_submitted_pts_100ns: Option<i64>,
    pts_100ns: i64,
    interval_100ns: i64,
) -> bool {
    last_submitted_pts_100ns.is_none_or(|last| pts_100ns.saturating_sub(last) >= interval_100ns)
}

#[cfg(windows)]
enum AutomaticH264Encoder {
    Hardware(HardwareH264Encoder),
    Software(SoftwareH264Encoder),
}

/// 硬编装配失败中允许降级到软件编码的类别。GPU 转换装配失败（部分 AMD 老
/// 驱动对 NV12+RENDER_TARGET+VIDEO_ENCODER 纹理组合返回 E_INVALIDARG）自
/// 0929 起同列：软编走 staging BGRA 读回，不依赖该转换（线上报障原路径
/// 整体失败导致录制永远不可用）。
#[cfg(windows)]
fn encoder_failure_allows_software_fallback(failure: HardwareEncoderFailure) -> bool {
    matches!(
        failure,
        HardwareEncoderFailure::HardwareUnavailable
            | HardwareEncoderFailure::AdapterMismatch
            | HardwareEncoderFailure::GpuConversionFailure
    )
}

#[cfg(windows)]
impl AutomaticH264Encoder {
    // source*/encode* 语义同 SoftwareH264Encoder：硬编层的转换与媒体类型
    // 全部使用取偶后的 encode*（converter 输入视图建在奇数源表面上，
    // VideoProcessorBlt 默认矩形把全源缩放到偶数 NV12 目标，≤1px 无感）。
    fn new(
        device: &windows::Win32::Graphics::Direct3D11::ID3D11Device,
        context: &windows::Win32::Graphics::Direct3D11::ID3D11DeviceContext,
        source_width: u32,
        source_height: u32,
        encode_width: u32,
        encode_height: u32,
        queue: Arc<Mutex<FrameQueue>>,
    ) -> Result<Self, HardwareEncoderError> {
        // 只读环境覆盖：强制走第三层软件编码，用于无硬件 MFT 机器的
        // 实机验证与故障排查（正常采集永远先尝试硬件层）。
        let forced_software =
            std::env::var("AIMING_COOKIE_FORCE_SOFTWARE_ENCODER").is_ok_and(|value| value == "1");
        if forced_software {
            return Ok(Self::Software(SoftwareH264Encoder::new(
                device,
                context,
                source_width,
                source_height,
                encode_width,
                encode_height,
                queue,
            )?));
        }
        match HardwareH264Encoder::new(
            device,
            context,
            encode_width,
            encode_height,
            Arc::clone(&queue),
        ) {
            Ok(encoder) => Ok(Self::Hardware(encoder)),
            // 硬件编码器不可用（两级硬件枚举都为空）、全局枚举有编码器但
            // 都不匹配采集适配器（hybrid 机器）、或 GPU 侧转换装配失败时
            // 回退软件编码；其余装配错误保持原样上报。回退不丢证据：硬件
            // 层失败原因先落日志与诊断（lastHardwareRejection），软编也
            // 失败时合成双因消息，不再只看得到末级软编聚合错误（0930
            // 报障的定位盲区）。
            Err(error) if encoder_failure_allows_software_fallback(error.failure) => {
                crate::dlog!(
                    "[capture-encoder] hardware layer failed, falling back to software: \
                     {:?}: {}",
                    error.failure,
                    error.message
                );
                if let Ok(mut guard) = queue.lock() {
                    guard.record_hardware_rejection(format!(
                        "{:?}: {}",
                        error.failure, error.message
                    ));
                }
                match SoftwareH264Encoder::new(
                    device,
                    context,
                    source_width,
                    source_height,
                    encode_width,
                    encode_height,
                    queue,
                ) {
                    Ok(encoder) => Ok(Self::Software(encoder)),
                    Err(software) => Err(HardwareEncoderError::new(
                        software.failure,
                        format!(
                            "hardware layer failed: {}; software layer failed: {}",
                            error.message, software.message
                        ),
                    )),
                }
            }
            Err(error) => Err(error),
        }
    }

    fn drain_events(&mut self) -> Result<(), HardwareEncoderError> {
        match self {
            Self::Hardware(encoder) => encoder.drain_events(),
            Self::Software(_) => Ok(()),
        }
    }

    fn accepts_input(&self) -> bool {
        match self {
            Self::Hardware(encoder) => encoder.accepts_input,
            Self::Software(_) => true,
        }
    }

    fn submit_capture_frame(
        &mut self,
        captured: HardwareCaptureFrame,
        frame_backlog: usize,
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
        let duration = 10_000_000 * DEFAULT_RECORDING_FPS_DENOMINATOR as i64
            / DEFAULT_RECORDING_FPS_NUMERATOR as i64;
        match self {
            Self::Hardware(encoder) => encoder.submit_texture(
                &source,
                captured.encoded_pts_100ns,
                duration,
                captured.letterbox,
            ),
            Self::Software(encoder) => {
                // 软编不跟随尺寸漂移（无 scaler，漂移即终态化）；矩形字段
                // 在软编变体下恒为 None。
                debug_assert!(captured.letterbox.is_none());
                // 自适应下采样：基准 30fps；帧通道有积压说明编码吞吐跟不上，
                // 按积压深度放大接受间隔，让包间隔稳定贴住可持续节奏（积压
                // n 帧就约每 n+1 帧取 1），避免包间隔随编码完成时刻抖动、
                // 偶发 >250ms 空洞把导出判死为 capture_coverage_gap
                // （2026-08-21 实测：固定 33ms 间隔在慢编码下仍按编码节奏
                // 逐帧全收，间隔抖动无改善）。采样时长保持 1/30s 上限语义，
                // MP4 stts 由 mux 层按真实相邻间隔吸收。
                let interval =
                    SOFTWARE_INPUT_INTERVAL_100NS.saturating_mul(1 + frame_backlog as i64);
                if !software_input_accepted(
                    encoder.last_submitted_pts_100ns,
                    captured.encoded_pts_100ns,
                    interval,
                ) {
                    return Ok(());
                }
                encoder.last_submitted_pts_100ns = Some(captured.encoded_pts_100ns);
                encoder.submit_texture(
                    &source,
                    captured.encoded_pts_100ns,
                    SOFTWARE_FRAME_DURATION_100NS,
                )
            }
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
        match self {
            Self::Hardware(encoder) => encoder.replay_mux_input(
                requested_start_100ns,
                requested_end_100ns,
                width,
                height,
                capture_clock,
            ),
            Self::Software(encoder) => encoder.replay_mux_input(
                requested_start_100ns,
                requested_end_100ns,
                width,
                height,
                capture_clock,
            ),
        }
    }

    // 导出失败时把缓冲实况打进控制台：首尾 PTS 对比请求窗口，一眼区分
    // 头部缺 keyframe、尾部滞后（IncompleteCoverage）与中途断档。
    fn log_replay_status(&self, requested_start_100ns: i64, requested_end_100ns: i64) {
        let status = match self {
            Self::Hardware(encoder) => encoder.replay.status(),
            Self::Software(encoder) => encoder.replay.status(),
        };
        let (dropped_packets, encoder_errors) = match self {
            Self::Hardware(encoder) => encoder
                .queue
                .lock()
                .map(|queue| (queue.dropped_packets, queue.encoder_errors))
                .ok(),
            Self::Software(encoder) => encoder
                .queue
                .lock()
                .map(|queue| (queue.dropped_packets, queue.encoder_errors))
                .ok(),
        }
        .unwrap_or((0, 0));
        crate::dlog!(
            "[capture-export] replay buffer: packets={} keyframes={} bytes={} \
             evicted={} gaps={} dropped={} encoder_errors={} \
             first_pts={:?} last_pts={:?} window={requested_start_100ns}..{requested_end_100ns}",
            status.packet_count,
            status.keyframes,
            status.total_bytes,
            status.evicted_packets,
            status.coverage_gaps,
            dropped_packets,
            encoder_errors,
            status.first_packet_pts_100ns,
            status.last_packet_pts_100ns,
        );
    }
}

#[cfg(windows)]
struct HardwareCaptureFrame {
    frame: windows::Graphics::Capture::Direct3D11CaptureFrame,
    sample: FrameSample,
    encoded_pts_100ns: i64,
    // 病灶 A：尺寸漂移跟随的 letterbox 矩形（等比 fit 居中）；None =
    // 默认全源→全目标转换。仅硬编变体会携带（软编漂移即终态化）。
    letterbox: Option<LetterboxRect>,
}

// Direct3D11CaptureFrame is an agile WinRT object. The free-threaded WGC
// callback only forwards ownership; the capture worker remains the sole user
// of the D3D immediate context and Media Foundation transform.
#[cfg(windows)]
unsafe impl Send for HardwareCaptureFrame {}

#[cfg(windows)]
fn dequeue_if_permitted<T>(
    accepts_input: bool,
    receiver: &std::sync::mpsc::Receiver<T>,
) -> Option<T> {
    accepts_input.then(|| receiver.try_recv().ok()).flatten()
}

// D1：worker 循环尾部的等待策略。gate 开时条件等待——帧由 FrameArrived
// 回调 try_send 进通道，到达即醒，替代固定 1ms 轮询的后台 CPU 空转；
// gate 关或通道已断开时退回 20ms 轮询。
#[cfg(windows)]
const WORKER_FRAME_WAIT_MS: u64 = 100;
#[cfg(windows)]
const WORKER_IDLE_POLL_MS: u64 = 20;

#[cfg(windows)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum FrameWaitDecision {
    /// gate 开：recv_timeout 条件等待，帧到达即醒。
    WaitForFrame,
    /// gate 关 / 通道已断：固定 20ms 轮询。
    PollSleep,
}

/// 等待决策。Disconnected 必须粘住 PollSleep：通道断开后 recv 立即返回，
/// 继续条件等待会退化成忙等。
#[cfg(windows)]
fn frame_wait_decision(accepts_input: bool, channel_disconnected: bool) -> FrameWaitDecision {
    if accepts_input && !channel_disconnected {
        FrameWaitDecision::WaitForFrame
    } else {
        FrameWaitDecision::PollSleep
    }
}

/// recv_timeout 结果 →（出队的帧，是否发生 Disconnected）。
/// 供 gate 已确认开启的调用方消费；gate 关时绝不能调用——
/// dequeue_if_permitted 的既有语义是帧不被消费就留在通道里。
#[cfg(windows)]
fn apply_frame_wait_result<T>(
    result: std::result::Result<T, std::sync::mpsc::RecvTimeoutError>,
) -> (Option<T>, bool) {
    match result {
        Ok(frame) => (Some(frame), false),
        Err(std::sync::mpsc::RecvTimeoutError::Timeout) => (None, false),
        Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => (None, true),
    }
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
fn enumerate_h264_mfts(
    adapter_luid: Option<windows::Win32::Foundation::LUID>,
    hardware_only: bool,
) -> Result<Vec<windows::Win32::Media::MediaFoundation::IMFActivate>, HardwareEncoderError> {
    use std::mem::size_of;
    use windows::Win32::Media::MediaFoundation::{
        MFCreateAttributes, MFMediaType_Video, MFTEnum2, MFVideoFormat_H264, MFVideoFormat_NV12,
        MFT_CATEGORY_VIDEO_ENCODER, MFT_ENUM_ADAPTER_LUID, MFT_ENUM_FLAG_HARDWARE,
        MFT_ENUM_FLAG_SORTANDFILTER, MFT_ENUM_FLAG_SYNCMFT, MFT_REGISTER_TYPE_INFO,
    };
    use windows::Win32::System::Com::CoTaskMemFree;

    let kind = if hardware_only {
        "hardware"
    } else {
        "software"
    };
    let flags = if hardware_only {
        // 硬件层沿用现状：HARDWARE 枚举（async MFT 事件模型驱动）。
        MFT_ENUM_FLAG_HARDWARE | MFT_ENUM_FLAG_SORTANDFILTER
    } else {
        // 软件层只接受同步 MFT（Microsoft H264 Encoder MFT 即 sync），
        // 由 ProcessInput/ProcessOutput 同步驱动，不参与 async 事件模型。
        MFT_ENUM_FLAG_SYNCMFT | MFT_ENUM_FLAG_SORTANDFILTER
    };
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
                format!("{kind} MFT adapter attributes creation failed: {error}"),
            )
        })?;
        let attributes = attributes.ok_or_else(|| {
            HardwareEncoderError::new(
                HardwareEncoderFailure::EncoderSetupFailure,
                format!("{kind} MFT adapter attributes were not returned"),
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
                format!("{kind} MFT adapter LUID configuration failed: {error}"),
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
            flags,
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
            format!("{kind} H.264 MFT enumeration failed: {error}"),
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

/// 帧内容尺寸是否偏离采集会话启动尺寸；任一维度变化即视为分辨率漂移，
/// 当前会话的录制管线按启动尺寸固化，漂移即触发诚实终态化（F6）。
#[cfg(windows)]
fn frame_size_drifts_from_session(session: (i32, i32), content: (i32, i32)) -> bool {
    content != session
}

/// 一次窗口尺寸漂移事件的诊断记录（诊断包 windowCapture.resizeEvents，
/// camelCase 落盘）。followed=false 表示该事件触发了终态化而非跟随；
/// 终态化路径同样记一条再走原逻辑。frame_pts_100ns 是该漂移生效帧的
/// 会话时间基（WGC system relative time），仅供 receipt canonical_ms
/// 换算使用，不进诊断包。
#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CaptureResizeEvent {
    pub at_utc_ms: i64,
    pub src_width: u32,
    pub src_height: u32,
    pub dst_x: i64,
    pub dst_y: i64,
    pub dst_width: u32,
    pub dst_height: u32,
    pub scale: f64,
    pub followed: bool,
}

/// 跟随事件的内部记录：诊断形态 + canonical_ms 换算所需的帧 PTS。
#[derive(Clone, Debug, PartialEq)]
struct ResizeEventRecord {
    event: CaptureResizeEvent,
    frame_pts_100ns: i64,
}

/// 漂移跟随事件的落盘上限：超过后丢最旧（FIFO）。足够回放一次会话内
/// 的完整尺寸变化史，又不会在 resize 风暴里撑爆诊断包。
const RESIZE_EVENT_HISTORY_LIMIT: usize = 16;

/// 等比 letterbox 的目标几何：src 为漂移帧内容区域（源表面左上原点），
/// dst 为等比 fit 后在 NV12 编码目标里的居中矩形（黑边 = fit 剩余区域）。
#[derive(Clone, Copy, Debug, PartialEq)]
#[cfg(windows)]
struct LetterboxFit {
    src_width: u32,
    src_height: u32,
    dst_x: i64,
    dst_y: i64,
    dst_width: u32,
    dst_height: u32,
    scale: f64,
}

/// D3D RECT 用的 i32 形态（VideoProcessorSetStreamSourceRect/DestRect）。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[cfg(windows)]
struct LetterboxRect {
    src_width: i32,
    src_height: i32,
    dst_x: i32,
    dst_y: i32,
    dst_width: i32,
    dst_height: i32,
}

#[cfg(windows)]
impl LetterboxFit {
    fn to_rect(self) -> LetterboxRect {
        LetterboxRect {
            src_width: self.src_width as i32,
            src_height: self.src_height as i32,
            dst_x: self.dst_x as i32,
            dst_y: self.dst_y as i32,
            dst_width: self.dst_width as i32,
            dst_height: self.dst_height as i32,
        }
    }
}

/// 等比缩放下限：s ≥ 0.5 跟随（含放大：只糊不变形，坐标变换精确）；
/// s < 0.5 画面过小失去训练价值，退回终态化。
const RESIZE_FOLLOW_MIN_SCALE: f64 = 0.5;
/// 单会话跟随事件预算：超过后防抖退回终态化（resize 抖动风暴保护）。
const RESIZE_FOLLOW_MAX_EVENTS: usize = 8;
/// 2s 窗口内 ≥3 次尺寸来回振荡 → 退回终态化。
const RESIZE_FOLLOW_OSCILLATION_LIMIT: usize = 3;
/// 振荡检测窗口：2s（WGC system relative time 100ns 单位）。
const RESIZE_FOLLOW_OSCILLATION_WINDOW_100NS: i64 = 20_000_000;

/// 跟随决策拒绝理由（稳定码，只进日志与事件追踪，不进控制面 reason）。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[cfg(windows)]
enum ResizeFollowDenial {
    ScaleBelowFloor,
    FollowBudgetExhausted,
    ResizeOscillation,
}

#[cfg(windows)]
impl ResizeFollowDenial {
    fn as_str(self) -> &'static str {
        match self {
            Self::ScaleBelowFloor => "resize_follow_scale_below_floor",
            Self::FollowBudgetExhausted => "resize_follow_budget_exhausted",
            Self::ResizeOscillation => "resize_follow_oscillation",
        }
    }
}

/// 漂移帧的跟随决策：Follow 产出 letterbox 几何；Deny 附带原几何
/// （事件里可见 ratio）与拒绝理由，调用方走既有终态化逻辑。
#[derive(Clone, Copy, Debug, PartialEq)]
#[cfg(windows)]
enum ResizeFollowDecision {
    Follow(LetterboxFit),
    Deny {
        fit: LetterboxFit,
        reason: ResizeFollowDenial,
    },
}

/// 等比 fit 居中：s = min(dstW/srcW, dstH/srcH)，四舍五入后 clamp 进
/// 编码目标，剩余维度居中。encode* 是会话启动时算好的取偶编码尺寸
/// （单一计算点），这里只消费、不重算。
#[cfg(windows)]
fn letterbox_fit(encode: (u32, u32), content: (u32, u32)) -> LetterboxFit {
    let (encode_width, encode_height) = encode;
    let (src_width, src_height) = content;
    let scale_x = f64::from(encode_width) / f64::from(src_width);
    let scale_y = f64::from(encode_height) / f64::from(src_height);
    let scale = scale_x.min(scale_y);
    let dst_width = ((f64::from(src_width) * scale).round() as u32).min(encode_width);
    let dst_height = ((f64::from(src_height) * scale).round() as u32).min(encode_height);
    let dst_x = i64::from((encode_width - dst_width) / 2);
    let dst_y = i64::from((encode_height - dst_height) / 2);
    let applied_scale = (f64::from(dst_width) / f64::from(src_width))
        .min(f64::from(dst_height) / f64::from(src_height));
    LetterboxFit {
        src_width,
        src_height,
        dst_x,
        dst_y,
        dst_width,
        dst_height,
        scale: applied_scale,
    }
}

/// 一次已生效的跟随事件（防抖历史）：会话时基戳 + 漂移源尺寸。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[cfg(windows)]
struct ResizeFollowRecord {
    at_100ns: i64,
    src_width: u32,
    src_height: u32,
}

/// 阈值与防抖的纯决策（A1c）。判定顺序：scale 下限 → 会话预算 → 振荡。
/// history 只含已生效的跟随事件；at_100ns 是当前漂移帧的会话时基戳。
#[cfg(windows)]
fn resize_follow_decision(
    encode: (u32, u32),
    content: (u32, u32),
    history: &[ResizeFollowRecord],
    at_100ns: i64,
) -> ResizeFollowDecision {
    let fit = letterbox_fit(encode, content);
    if fit.scale < RESIZE_FOLLOW_MIN_SCALE {
        return ResizeFollowDecision::Deny {
            fit,
            reason: ResizeFollowDenial::ScaleBelowFloor,
        };
    }
    // 预算：本会话已生效的跟随事件达到上限后，新的漂移尺寸退回终态化。
    if history.len() >= RESIZE_FOLLOW_MAX_EVENTS {
        return ResizeFollowDecision::Deny {
            fit,
            reason: ResizeFollowDenial::FollowBudgetExhausted,
        };
    }
    // 振荡防抖：2s 窗口内 A→B→A 反复 ≥3 次（含当前事件）→ 退回终态化。
    let window_start = at_100ns.saturating_sub(RESIZE_FOLLOW_OSCILLATION_WINDOW_100NS);
    let mut recent: Vec<u64> = history
        .iter()
        .filter(|record| record.at_100ns >= window_start)
        .map(resize_oscillation_key)
        .collect();
    recent.push(u64::from(content.0) * 0x1_0000_0000 + u64::from(content.1));
    let oscillations = recent
        .iter()
        .enumerate()
        .skip(2)
        .filter(|(index, size)| *size == &recent[index - 2] && *size != &recent[index - 1])
        .count();
    if oscillations >= RESIZE_FOLLOW_OSCILLATION_LIMIT {
        return ResizeFollowDecision::Deny {
            fit,
            reason: ResizeFollowDenial::ResizeOscillation,
        };
    }
    ResizeFollowDecision::Follow(fit)
}

/// 振荡比较键：把源尺寸折成一个可比较整数。
#[cfg(windows)]
fn resize_oscillation_key(record: &ResizeFollowRecord) -> u64 {
    u64::from(record.src_width) * 0x1_0000_0000 + u64::from(record.src_height)
}

/// handler 侧一帧的跟随判定输入。drifted 由调用方按会话 item size 判定
/// 后显式传入（encode* 是取偶值，不能作为漂移判定基准）；encode 只作
/// letterbox fit 的目标矩形基准。
#[derive(Clone, Copy, Debug)]
#[cfg(windows)]
struct ResizeFollowFrameInput {
    encode: (u32, u32),
    content: (u32, u32),
    drifted: bool,
    at_100ns: i64,
    at_utc_ms: i64,
    hardware_encoder: bool,
    has_encoded_output: bool,
}

/// 单帧判定结果。Follow.event 只在尺寸变化沿（新漂移尺寸首次生效）时
/// 携带事件；稳态漂移帧复用既定矩形、不重复记事件。Terminate.denial
/// 是决策性拒绝理由（超阈值/防抖）；结构性拒绝（软编/首帧）为 None。
#[derive(Clone, Debug, PartialEq)]
#[cfg(windows)]
enum ResizeFollowOutcome {
    PassThrough,
    Follow {
        fit: LetterboxFit,
        event: Option<CaptureResizeEvent>,
    },
    Terminate {
        event: CaptureResizeEvent,
        denial: Option<ResizeFollowDenial>,
    },
}

/// 会话内跟随状态机（handler 持有；WGC FrameArrived 串行派发，无需加锁）。
/// 硬编 + 已有编码产出 + 决策放行 → 跟随；其余 → 记 followed=false 事件
/// 后由调用方走既有终态化逻辑（软编/首帧/超阈值/防抖）。
#[cfg(windows)]
struct ResizeFollowController {
    following: bool,
    last_content: Option<(u32, u32)>,
    history: Vec<ResizeFollowRecord>,
}

#[cfg(windows)]
impl ResizeFollowController {
    fn new() -> Self {
        Self {
            following: false,
            last_content: None,
            history: Vec::new(),
        }
    }

    /// 跟随是否正在生效（上一帧判定为 Follow 且未回到会话尺寸）。
    fn is_following(&self) -> bool {
        self.following
    }

    fn on_frame(&mut self, input: ResizeFollowFrameInput) -> ResizeFollowOutcome {
        let (src_width, src_height) = input.content;
        if !input.drifted {
            // 内容回到会话尺寸：跟随解除，矩形不再随帧传递。
            self.following = false;
            self.last_content = Some(input.content);
            return ResizeFollowOutcome::PassThrough;
        }
        // 硬编 + 已有编码产出是跟随的两个硬前提；否则记 followed=false
        // 事件后走既有终态化（软编无 scaler、首帧漂移维持诚实终态化）。
        if !input.hardware_encoder || !input.has_encoded_output {
            let event = self.structural_deny_event(input);
            return ResizeFollowOutcome::Terminate {
                event,
                denial: None,
            };
        }
        let transition = self.last_content != Some(input.content);
        if !transition {
            if !self.following {
                // 防御：非沿帧且未在跟随（正常流不可达，终态化后 handler
                // 不再到达此处）——按终态化处理，不静默放行。
                let event = self.structural_deny_event(input);
                return ResizeFollowOutcome::Terminate {
                    event,
                    denial: None,
                };
            }
            // 稳态漂移帧：复用既定矩形，不重复记事件。
            return ResizeFollowOutcome::Follow {
                fit: letterbox_fit(input.encode, input.content),
                event: None,
            };
        }
        match resize_follow_decision(input.encode, input.content, &self.history, input.at_100ns) {
            ResizeFollowDecision::Follow(fit) => {
                self.following = true;
                self.last_content = Some(input.content);
                self.history.push(ResizeFollowRecord {
                    at_100ns: input.at_100ns,
                    src_width,
                    src_height,
                });
                ResizeFollowOutcome::Follow {
                    fit,
                    event: Some(CaptureResizeEvent {
                        at_utc_ms: input.at_utc_ms,
                        src_width,
                        src_height,
                        dst_x: fit.dst_x,
                        dst_y: fit.dst_y,
                        dst_width: fit.dst_width,
                        dst_height: fit.dst_height,
                        scale: fit.scale,
                        followed: true,
                    }),
                }
            }
            ResizeFollowDecision::Deny { fit, reason } => {
                let event = CaptureResizeEvent {
                    at_utc_ms: input.at_utc_ms,
                    src_width,
                    src_height,
                    dst_x: fit.dst_x,
                    dst_y: fit.dst_y,
                    dst_width: fit.dst_width,
                    dst_height: fit.dst_height,
                    scale: fit.scale,
                    followed: false,
                };
                ResizeFollowOutcome::Terminate {
                    event,
                    denial: Some(reason),
                }
            }
        }
    }

    /// 结构性终态化事件（followed=false，无决策性拒绝理由）：几何按等比
    /// fit 计算，ratio 随事件可见。
    fn structural_deny_event(&self, input: ResizeFollowFrameInput) -> CaptureResizeEvent {
        let fit = letterbox_fit(input.encode, input.content);
        CaptureResizeEvent {
            at_utc_ms: input.at_utc_ms,
            src_width: input.content.0,
            src_height: input.content.1,
            dst_x: fit.dst_x,
            dst_y: fit.dst_y,
            dst_width: fit.dst_width,
            dst_height: fit.dst_height,
            scale: fit.scale,
            followed: false,
        }
    }
}

/// 帧会话时基戳 → UTC epoch ms（与 CaptureClockMetadata 锚点同源换算）。
#[cfg(windows)]
fn frame_timestamp_to_utc_ms(
    clock: CaptureClockMetadata,
    system_relative_time_100ns: i64,
) -> Option<i64> {
    let anchor_100ns = i64::try_from(clock.qpc_ns / 100).ok()?;
    let delta_100ns = system_relative_time_100ns.checked_sub(anchor_100ns)?;
    Some(clock.utc_epoch_ms + delta_100ns.div_euclid(10_000))
}

/// 队列中的跟随事件 → receipt geometryEvents：canonical_ms = epoch 毫秒，
/// 与诊断包 resizeEvents.atUtcMs 同源同轴（消费端 letterbox_segment_at
/// 按 run window 的 start_epoch_ms epoch 轴选段）。
#[cfg(windows)]
fn queue_geometry_events(queue: &Arc<Mutex<FrameQueue>>) -> Vec<GeometryEvent> {
    queue
        .lock()
        .map(|queue| {
            queue
                .resize_events
                .iter()
                .map(|record| GeometryEvent {
                    canonical_ms: record.event.at_utc_ms,
                    src_width: record.event.src_width,
                    src_height: record.event.src_height,
                    dst_x: record.event.dst_x,
                    dst_y: record.event.dst_y,
                    dst_width: record.event.dst_width,
                    dst_height: record.event.dst_height,
                    scale: record.event.scale,
                })
                .collect()
        })
        .unwrap_or_default()
}

/// receipt 导出的几何变换事件（camelCase 落盘）。canonical_ms 为该漂移
/// 生效帧的 **epoch 毫秒**（与诊断包 resizeEvents.atUtcMs 同源同轴，由
/// frame_timestamp_to_utc_ms 从会话 PTS 换算）；消费端按 run window 的
/// start_epoch_ms epoch 轴选段。
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GeometryEvent {
    pub canonical_ms: i64,
    pub src_width: u32,
    pub src_height: u32,
    pub dst_x: i64,
    pub dst_y: i64,
    pub dst_width: u32,
    pub dst_height: u32,
    pub scale: f64,
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

/// 一次成功的 WGC 会话启动产物：默认适配器路径与逐适配器重试路径共用，
/// 解构后 device/context/size/frame_pool/session 交给既有录制管线。
#[cfg(windows)]
struct WgcSessionStart {
    device: windows::Win32::Graphics::Direct3D11::ID3D11Device,
    context: windows::Win32::Graphics::Direct3D11::ID3D11DeviceContext,
    direct3d_device: windows::Graphics::DirectX::Direct3D11::IDirect3DDevice,
    item: windows::Graphics::Capture::GraphicsCaptureItem,
    size: windows::Graphics::SizeInt32,
    frame_pool: windows::Graphics::Capture::Direct3D11CaptureFramePool,
    session: windows::Graphics::Capture::GraphicsCaptureSession,
}

/// 链上单步错误：WinRT/D3D 调用携带原始 error（供 HRESULT 判定），纯校验类
/// 失败（如窗口尺寸非法）携带文案。
#[cfg(windows)]
enum WgcStepError {
    Win(windows::core::Error),
    Msg(String),
}

#[cfg(windows)]
impl std::fmt::Display for WgcStepError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            WgcStepError::Win(error) => write!(formatter, "{error}"),
            WgcStepError::Msg(message) => write!(formatter, "{message}"),
        }
    }
}

/// 仅 E_INVALIDARG 族触发逐适配器重试：0x80070057 = E_INVALIDARG，
/// 0xC000000D = STATUS_INVALID_PARAMETER（28000 Insider 上
/// CreateCaptureSession 观察到的原始 NTSTATUS 形态）。
#[cfg(windows)]
fn wgc_step_error_is_invalid_parameter(error: &WgcStepError) -> bool {
    match error {
        WgcStepError::Win(error) => {
            let code = error.code().0 as u32;
            code == 0x8007_0057 || code == 0xC000_000D
        }
        WgcStepError::Msg(_) => false,
    }
}

/// 启动链上「换适配器可解」的两步（wide 模式的放宽范围）；步骤名必须与
/// try_start_wgc_with_device 里的 step() 命名一致。
#[cfg(windows)]
const WGC_STEP_CREATE_FREE_THREADED: &str = "Direct3D11CaptureFramePool::CreateFreeThreaded";
#[cfg(windows)]
const WGC_STEP_CREATE_CAPTURE_SESSION: &str = "CreateCaptureSession";

/// 换卡重试判据（按治理模式分流）：
/// - strict（1.3.3 行为完整恢复）：仅 E_INVALIDARG 族白名单，任意步骤。
/// - wide（缺省）：CreateFreeThreaded / CreateCaptureSession 两步的任意
///   Win 类错误均可换卡重试（28000 预览版上同一根因会以多种 HRESULT/
///   NTSTATUS 形态出现）；CreateForWindow 失败与窗口尺寸校验恒 Fatal
///   （换卡无解）；Msg 类错误不重试。
#[cfg(windows)]
fn wgc_step_error_is_session_start_retryable(
    step_name: &str,
    error: &WgcStepError,
    mode: WgcRetryMode,
) -> bool {
    match mode {
        WgcRetryMode::Strict => wgc_step_error_is_invalid_parameter(error),
        WgcRetryMode::Wide => match error {
            WgcStepError::Win(_) => matches!(
                step_name,
                WGC_STEP_CREATE_FREE_THREADED | WGC_STEP_CREATE_CAPTURE_SESSION
            ),
            WgcStepError::Msg(_) => false,
        },
    }
}

/// 在给定 D3D 设备上走完 item → frame pool → session 启动链。任一步失败
/// 返回（步骤名, 错误），中间产物靠 drop 释放，调用方可换设备整链重试。
#[cfg(windows)]
fn try_start_wgc_with_device(
    hwnd: usize,
    device: windows::Win32::Graphics::Direct3D11::ID3D11Device,
    context: windows::Win32::Graphics::Direct3D11::ID3D11DeviceContext,
) -> Result<WgcSessionStart, (String, WgcStepError)> {
    use windows::core::Interface;
    use windows::Graphics::Capture::{Direct3D11CaptureFramePool, GraphicsCaptureItem};
    use windows::Graphics::DirectX::Direct3D11::IDirect3DDevice;
    use windows::Graphics::DirectX::DirectXPixelFormat;
    use windows::Win32::Foundation::HWND;
    use windows::Win32::System::WinRT::Graphics::Capture::IGraphicsCaptureItemInterop;

    let step = |name: &str, error: WgcStepError| (name.to_string(), error);

    let dxgi_device = device
        .cast::<windows::Win32::Graphics::Dxgi::IDXGIDevice>()
        .map_err(|error| step("IDXGIDevice cast", WgcStepError::Win(error)))?;
    let inspectable = unsafe {
        windows::Win32::System::WinRT::Direct3D11::CreateDirect3D11DeviceFromDXGIDevice(
            &dxgi_device,
        )
    }
    .map_err(|error| {
        step(
            "CreateDirect3D11DeviceFromDXGIDevice",
            WgcStepError::Win(error),
        )
    })?;
    let direct3d_device: IDirect3DDevice = inspectable
        .cast()
        .map_err(|error| step("IDirect3DDevice cast", WgcStepError::Win(error)))?;
    let interop = windows::core::factory::<GraphicsCaptureItem, IGraphicsCaptureItemInterop>()
        .map_err(|error| step("GraphicsCaptureItem factory", WgcStepError::Win(error)))?;
    let item: GraphicsCaptureItem = unsafe { interop.CreateForWindow(HWND(hwnd as *mut _)) }
        .map_err(|error| step("CreateForWindow", WgcStepError::Win(error)))?;
    let size = item
        .Size()
        .map_err(|error| step("capture item size", WgcStepError::Win(error)))?;
    if size.Width <= 0 || size.Height <= 0 {
        return Err(step(
            "capture window size check",
            WgcStepError::Msg("capture window has an invalid size".to_string()),
        ));
    }
    let frame_pool = Direct3D11CaptureFramePool::CreateFreeThreaded(
        &direct3d_device,
        DirectXPixelFormat::B8G8R8A8UIntNormalized,
        // 4 个 buffer：软编路径编码期间持有帧表面，2 buffer 会让 WGC
        // 帧池枯竭、实际送达率被编码时长封顶（2026-08-21 实测 ~6fps）。
        4,
        size,
    )
    .map_err(|error| step(WGC_STEP_CREATE_FREE_THREADED, WgcStepError::Win(error)))?;
    let session = frame_pool
        .CreateCaptureSession(&item)
        .map_err(|error| step(WGC_STEP_CREATE_CAPTURE_SESSION, WgcStepError::Win(error)))?;
    Ok(WgcSessionStart {
        device,
        context,
        direct3d_device,
        item,
        size,
        frame_pool,
        session,
    })
}

/// 默认设备路径失败后的兜底：枚举 DXGI 适配器，跳过 WARP/无驱动适配器，
/// 逐个以 D3D_DRIVER_TYPE_UNKNOWN + 显式 adapter 重建设备整链重试
/// （ScreenRecorderLib #141 / WebRTC wgc_capturer_win.cc 同款）。
/// 换卡判据按治理模式分流（见 wgc_step_error_is_session_start_retryable）；
/// 不可重试错误终止枚举。返回的错误携带结构化的逐适配器尝试与 DXGI
/// 适配器清单，直接进启动失败快照与诊断包。
#[cfg(windows)]
struct WgcEnumeratedAdaptersFailure {
    message: String,
    attempts: Vec<WgcAdapterAttempt>,
    adapters: Vec<WgcAdapterDescriptor>,
}

#[cfg(windows)]
fn try_start_wgc_with_enumerated_adapters(
    hwnd: usize,
    feature_levels: &[windows::Win32::Graphics::Direct3D::D3D_FEATURE_LEVEL],
    retry_mode: WgcRetryMode,
) -> Result<WgcSessionStart, WgcEnumeratedAdaptersFailure> {
    use windows::core::Interface;
    use windows::Win32::Foundation::{HMODULE, LUID};
    use windows::Win32::Graphics::Direct3D::{D3D_DRIVER_TYPE_UNKNOWN, D3D_FEATURE_LEVEL};
    use windows::Win32::Graphics::Direct3D11::{
        D3D11CreateDevice, D3D11_CREATE_DEVICE_BGRA_SUPPORT, D3D11_CREATE_DEVICE_VIDEO_SUPPORT,
        D3D11_SDK_VERSION,
    };
    use windows::Win32::Graphics::Dxgi::{
        CreateDXGIFactory1, IDXGIAdapter, IDXGIFactory1, DXGI_ADAPTER_FLAG_SOFTWARE,
    };

    let factory: IDXGIFactory1 =
        unsafe { CreateDXGIFactory1() }.map_err(|error| WgcEnumeratedAdaptersFailure {
            message: format!("CreateDXGIFactory1 failed: {error}"),
            attempts: Vec::new(),
            adapters: Vec::new(),
        })?;
    let format_luid = |luid: LUID| format!("luid:{:08x}{:08x}", luid.HighPart as u32, luid.LowPart);
    let mut attempts: Vec<WgcAdapterAttempt> = Vec::new();
    let mut adapters: Vec<WgcAdapterDescriptor> = Vec::new();
    let mut index = 0u32;
    loop {
        let adapter = match unsafe { factory.EnumAdapters1(index) } {
            Ok(adapter) => adapter,
            Err(_) => break, // DXGI_ERROR_NOT_FOUND：枚举结束
        };
        index += 1;
        let desc = match unsafe { adapter.GetDesc1() } {
            Ok(desc) => desc,
            Err(error) => {
                attempts.push(WgcAdapterAttempt {
                    adapter: format!("adapter[{index}]"),
                    luid: None,
                    step: "GetDesc1".to_string(),
                    message: format!("GetDesc1 failed: {error}"),
                });
                continue;
            }
        };
        let name = String::from_utf16_lossy(&desc.Description)
            .trim_end_matches('\0')
            .to_string();
        let luid = format_luid(desc.AdapterLuid);
        let software = desc.Flags & (DXGI_ADAPTER_FLAG_SOFTWARE.0 as u32) != 0;
        adapters.push(WgcAdapterDescriptor {
            name: name.clone(),
            luid: Some(luid.clone()),
            vendor_id: Some(desc.VendorId),
            dedicated_video_memory: Some(desc.DedicatedVideoMemory as u64),
            software,
        });
        // 无驱动适配器（"Microsoft 基本显示适配器"）与 WARP 没有可用的硬件
        // 设备，对它们整链重试只会复现同一错误。
        if software || desc.DedicatedVideoMemory == 0 {
            attempts.push(WgcAdapterAttempt {
                adapter: name,
                luid: Some(luid),
                step: "skip".to_string(),
                message: "skipped (software/no-driver adapter)".to_string(),
            });
            continue;
        }
        // D3D11CreateDevice 的参数绑定要基类接口 IDXGIAdapter。
        let adapter_base: IDXGIAdapter = match adapter.cast() {
            Ok(adapter_base) => adapter_base,
            Err(error) => {
                attempts.push(WgcAdapterAttempt {
                    adapter: name,
                    luid: Some(luid),
                    step: "IDXGIAdapter cast".to_string(),
                    message: format!("IDXGIAdapter cast failed: {error}"),
                });
                continue;
            }
        };
        let mut device = None;
        let mut context = None;
        let mut feature_level = D3D_FEATURE_LEVEL::default();
        let created = unsafe {
            D3D11CreateDevice(
                Some(&adapter_base),
                D3D_DRIVER_TYPE_UNKNOWN,
                HMODULE::default(),
                D3D11_CREATE_DEVICE_BGRA_SUPPORT | D3D11_CREATE_DEVICE_VIDEO_SUPPORT,
                Some(feature_levels),
                D3D11_SDK_VERSION,
                Some(&mut device),
                Some(&mut feature_level),
                Some(&mut context),
            )
        };
        let (device, context) = match created {
            Ok(()) => match (device, context) {
                (Some(device), Some(context)) => (device, context),
                _ => {
                    attempts.push(WgcAdapterAttempt {
                        adapter: name,
                        luid: Some(luid),
                        step: "D3D11CreateDevice".to_string(),
                        message: "device creation returned no device".to_string(),
                    });
                    continue;
                }
            },
            Err(error) => {
                attempts.push(WgcAdapterAttempt {
                    adapter: name,
                    luid: Some(luid),
                    step: "D3D11CreateDevice".to_string(),
                    message: format!("device creation failed ({error})"),
                });
                continue;
            }
        };
        match try_start_wgc_with_device(hwnd, device, context) {
            Ok(started) => return Ok(started),
            Err((step_name, error)) => {
                let retryable =
                    wgc_step_error_is_session_start_retryable(&step_name, &error, retry_mode);
                attempts.push(WgcAdapterAttempt {
                    adapter: name,
                    luid: Some(luid),
                    step: step_name.clone(),
                    message: format!("{step_name} failed ({error})"),
                });
                if !retryable {
                    // 设备/适配器身份之外的错误（窗口消失、帧池枯竭等）不是
                    // 换适配器能解决的，终止并交出已尝试清单。
                    break;
                }
            }
        }
    }
    Err(WgcEnumeratedAdaptersFailure {
        message: if attempts.is_empty() {
            "no hardware adapter available to retry".to_string()
        } else {
            format!(
                "tried: {}",
                attempts
                    .iter()
                    .map(|attempt| format!(
                        "{}: {} ({})",
                        attempt.adapter, attempt.step, attempt.message
                    ))
                    .collect::<Vec<_>>()
                    .join("; ")
            )
        },
        attempts,
        adapters,
    })
}

/// B 计划占位：WARP 软件适配器兜底启动。尚未接线（28000 加固先落诊断与
/// 换卡重试，WARP 保底挂账后续）；保留签名占位，接线时移除 allow。
#[cfg(windows)]
#[allow(dead_code)]
fn try_start_wgc_with_warp(_hwnd: usize) -> Result<WgcSessionStart, String> {
    Err("WARP fallback is not wired yet".to_string())
}

#[cfg(windows)]
#[allow(clippy::too_many_arguments)] // 线程入口参数表：现有管线合同的延续
fn run_wgc_window_capture(
    hwnd: usize,
    queue: Arc<Mutex<FrameQueue>>,
    stop: Arc<AtomicBool>,
    ready: std::sync::mpsc::SyncSender<Result<(), String>>,
    recording_path: Option<PathBuf>,
    clock_metadata: CaptureClockMetadata,
    command_receiver: std::sync::mpsc::Receiver<WindowCaptureCommand>,
    failure_snapshot: Arc<Mutex<Option<StartFailureSnapshot>>>,
) -> Result<(), String> {
    use windows::core::{IInspectable, Interface};
    use windows::Foundation::TypedEventHandler;
    use windows::Graphics::Capture::{Direct3D11CaptureFramePool, GraphicsCaptureSession};
    use windows::Win32::Foundation::HMODULE;
    use windows::Win32::Graphics::Direct3D::{
        D3D_DRIVER_TYPE_HARDWARE, D3D_FEATURE_LEVEL, D3D_FEATURE_LEVEL_10_0,
        D3D_FEATURE_LEVEL_10_1, D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_11_1,
    };
    use windows::Win32::Graphics::Direct3D11::{
        D3D11CreateDevice, ID3D11Device, ID3D11DeviceContext, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
        D3D11_CREATE_DEVICE_VIDEO_SUPPORT, D3D11_SDK_VERSION,
    };
    use windows::Win32::System::WinRT::{RoInitialize, RoUninitialize, RO_INIT_MULTITHREADED};

    if hwnd == 0 {
        return Err("KovaaK window handle is null".to_string());
    }
    unsafe { RoInitialize(RO_INIT_MULTITHREADED) }
        .map_err(|error| format!("RoInitialize failed: {error}"))?;

    // 触发面治理模式与注入开关在 start 时刻定格，并自标注进失败快照
    //（开关仅测试/支持用，普通路径零影响）。
    let retry_mode = wgc_retry_mode_from_env();
    let force_adapter_retry = wgc_force_adapter_retry_enabled();
    let simulate_default_device_failure = wgc_simulate_default_device_failure_enabled();
    // 逐适配器尝试与 DXGI 适配器清单：枚举环失败时收集，终态失败处写进
    // 共享快照槽位（FrameQueue 之外，start 的队列 reset 清不掉）。
    let mut adapter_attempts: Vec<WgcAdapterAttempt> = Vec::new();
    let mut dxgi_adapters_seen: Vec<WgcAdapterDescriptor> = Vec::new();

    let result = (|| {
        if !GraphicsCaptureSession::IsSupported()
            .map_err(|error| format!("GraphicsCaptureSession::IsSupported failed: {error}"))?
        {
            return Err(
                "Windows.Graphics.Capture is not supported on this Windows build".to_string(),
            );
        }

        // 28000 Insider 上 CreateCaptureSession 报 E_INVALIDARG 的经典机理
        // = D3D 设备所在适配器与采集目标所属适配器不一致（ScreenRecorderLib
        // #141、Chromium wgc_capturer_win.cc 同款结论）。默认路径保持第一
        // 优先（正式版行为零变化）；默认设备失败时按治理模式分流：wide
        // （缺省）下设备创建失败与会话创建两步的 Win 类错误都换卡重试，
        // strict（=1.3.3）仅 E_INVALIDARG 族换卡、设备创建失败即终态。
        let feature_levels = [
            D3D_FEATURE_LEVEL_11_1,
            D3D_FEATURE_LEVEL_11_0,
            D3D_FEATURE_LEVEL_10_1,
            D3D_FEATURE_LEVEL_10_0,
        ];
        let create_default_device = || {
            if simulate_default_device_failure {
                return Err(
                    "simulated default device failure (AIMING_COOKIE_SIMULATE_DEFAULT_DEVICE_FAILURE=1)"
                        .to_string(),
                );
            }
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
            Ok((
                device.ok_or_else(|| "D3D11 device was not returned".to_string())?,
                context.ok_or_else(|| "D3D11 context was not returned".to_string())?,
            ))
        };

        enum DefaultDeviceOutcome {
            Started(WgcSessionStart),
            // 值得逐适配器显式重试；携带默认路径错误文案。
            RetryEnumerated(String),
            // 不可重试失败（窗口无效、WGC 不支持等）：直接终态。
            Fatal(String),
        }
        let default_outcome = if force_adapter_retry {
            DefaultDeviceOutcome::RetryEnumerated(
                "forced via AIMING_COOKIE_FORCE_ADAPTER_RETRY=1 (default device skipped)"
                    .to_string(),
            )
        } else {
            match create_default_device() {
                Ok((device, context)) => match try_start_wgc_with_device(hwnd, device, context) {
                    Ok(started) => DefaultDeviceOutcome::Started(started),
                    Err((step_name, error)) => {
                        let text = format!("{step_name} failed: {error}");
                        if wgc_step_error_is_session_start_retryable(&step_name, &error, retry_mode)
                        {
                            DefaultDeviceOutcome::RetryEnumerated(text)
                        } else {
                            DefaultDeviceOutcome::Fatal(text)
                        }
                    }
                },
                Err(error) => match retry_mode {
                    // wide：默认设备建不出来也可能是适配器身份问题，交给
                    // 枚举环收集逐适配器证据（含无驱动适配器跳过记录）。
                    WgcRetryMode::Wide => DefaultDeviceOutcome::RetryEnumerated(error),
                    WgcRetryMode::Strict => DefaultDeviceOutcome::Fatal(error),
                },
            }
        };

        let started = match default_outcome {
            DefaultDeviceOutcome::Started(started) => started,
            DefaultDeviceOutcome::Fatal(text) => return Err(text),
            DefaultDeviceOutcome::RetryEnumerated(default_error) => {
                match try_start_wgc_with_enumerated_adapters(hwnd, &feature_levels, retry_mode) {
                    Ok(started) => {
                        crate::dlog!(
                            "[wgc-capture] default adapter path failed, \
                             enumerated adapter retry succeeded (default path: {default_error})"
                        );
                        started
                    }
                    Err(fallback) => {
                        adapter_attempts = fallback.attempts;
                        dxgi_adapters_seen = fallback.adapters;
                        return Err(format!(
                            "default adapter path: {default_error}; enumerated adapters: {}",
                            fallback.message
                        ));
                    }
                }
            }
        };
        let WgcSessionStart {
            device,
            context,
            direct3d_device: _direct3d_device,
            item: _item,
            size,
            frame_pool,
            session,
        } = started;
        // 编码尺寸防线（单一计算点）：编码器构造、MP4 writer 与 replay
        // 导出必须消费同一对取偶值；帧池、drift 判定与会话尺寸保持
        // item size 不变。病态尺寸（<2 或超上限）在此显式终态。
        let (encode_width, encode_height) =
            normalize_encode_dimensions(size.Width as u32, size.Height as u32).map_err(
                |message| {
                    crate::dlog!("[capture-encoder] session size rejected: {message}");
                    message
                },
            )?;
        if let Ok(mut guard) = queue.lock() {
            guard.record_session_dimensions(
                size.Width as u32,
                size.Height as u32,
                encode_width,
                encode_height,
            );
        }
        let recording_failed = Arc::new(AtomicBool::new(false));
        let mut automatic_encoder = if recording_path.is_none() {
            // 三级回退：全局硬件枚举 → LUID 定点枚举 → 软件 H.264 MFT，
            // 全部失败才报错；选中的层级与适配器身份已写入队列诊断。
            let encoder = AutomaticH264Encoder::new(
                &device,
                &context,
                size.Width as u32,
                size.Height as u32,
                encode_width,
                encode_height,
                Arc::clone(&queue),
            )
            .map_err(|error| {
                if let Ok(mut guard) = queue.lock() {
                    guard.record_hardware_failure(error.failure);
                }
                format!("automatic H.264 initialization failed: {}", error.message)
            })?;
            Some(encoder)
        } else {
            None
        };
        let (encoder_frame_sender, encoder_frame_receiver) = if automatic_encoder.is_some() {
            let (sender, receiver) = std::sync::mpsc::sync_channel(DEFAULT_FRAME_QUEUE_CAPACITY);
            (Some(sender), Some(receiver))
        } else {
            (None, None)
        };
        // 编码帧通道的实时积压深度：发送 +1、出队 -1。std mpsc 的 Receiver
        // 没有 len()，软编层用它自适应放大下采样间隔。
        let encoder_frame_backlog = Arc::new(AtomicU64::new(0));
        let encoder_frame_backlog_for_handler = Arc::clone(&encoder_frame_backlog);
        let (recording_sender, recording_join) = if let Some(path) = recording_path {
            let (sender, receiver) = std::sync::mpsc::sync_channel(DEFAULT_WRITER_QUEUE_CAPACITY);
            let (writer_ready_tx, writer_ready_rx) = std::sync::mpsc::sync_channel(1);
            let writer_queue = Arc::clone(&queue);
            let writer_failed = Arc::clone(&recording_failed);
            let writer_join = thread::spawn(move || {
                crate::thread_priority::apply_capture_thread_priority();
                run_mp4_writer(
                    path,
                    encode_width,
                    encode_height,
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
                D3dFrameReadback::new(&device, &context, encode_width, encode_height)
                    .map(|readback| Arc::new(Mutex::new(readback)))
            })
            .transpose()?;
        let last_recorded_timestamp = Arc::new(AtomicI64::new(-1));
        let sequence = Arc::new(AtomicU64::new(0));
        let queue_for_handler = Arc::clone(&queue);
        let stop_for_handler = Arc::clone(&stop);
        let sender_for_handler = recording_sender.clone();
        let readback_for_handler = recording_readback.clone();
        let encoder_frame_sender_for_handler = encoder_frame_sender.clone();
        let last_recorded_timestamp_for_handler = Arc::clone(&last_recorded_timestamp);
        let recording_failed_for_handler = Arc::clone(&recording_failed);
        // 病灶 A：硬编 letterbox 跟随。编码器变体在装配点即已确定且会话
        // 内不变；跟随状态机由本回调独占（WGC FrameArrived 串行派发）。
        let hardware_encoder_active =
            matches!(automatic_encoder, Some(AutomaticH264Encoder::Hardware(_)));
        let mut resize_follow = ResizeFollowController::new();
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
                        // 本会话是否已有编码帧产出（record_hardware_packet
                        // 计数）：首帧漂移（无产出）不跟随，维持诚实终态化。
                        let has_encoded_output = {
                            let mut guard = queue_for_handler
                                .lock()
                                .map_err(|_| windows::core::Error::from_win32())?;
                            let result = if sender_for_handler.is_some()
                                || encoder_frame_sender_for_handler.is_some()
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
                            guard.submitted_packets > 0
                        };

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
                        // 病灶 A：尺寸漂移分支。硬编 + 已有编码产出 + 决策
                        // 放行 → 等比 letterbox 跟随（本局视频保住）；软编
                        // 变体、首帧即漂移、s<0.5、防抖触发 → 记 followed=
                        // false 事件后走既有诚实终态化逻辑（语义不变）。
                        // 非漂移帧也过一遍状态机：跟随解除（内容回到会话
                        // 尺寸）要能清除队列里的跟随标注。
                        let drifted = frame_size_drifts_from_session(
                            (size.Width, size.Height),
                            (content_size.Width, content_size.Height),
                        );
                        let mut letterbox = None;
                        let was_following = resize_follow.is_following();
                        match resize_follow.on_frame(ResizeFollowFrameInput {
                            encode: (encode_width, encode_height),
                            content: (content_size.Width as u32, content_size.Height as u32),
                            drifted,
                            at_100ns: timestamp,
                            at_utc_ms: frame_timestamp_to_utc_ms(clock_metadata, timestamp)
                                .unwrap_or_default(),
                            hardware_encoder: hardware_encoder_active,
                            has_encoded_output,
                        }) {
                            ResizeFollowOutcome::PassThrough => {
                                if was_following {
                                    crate::dlog!(
                                        "[capture-resize] following ended: frame back to \
                                         session size sequence={}",
                                        sample.sequence
                                    );
                                    if let Ok(mut guard) = queue_for_handler.lock() {
                                        guard.set_resize_following(false);
                                    }
                                }
                            }
                            ResizeFollowOutcome::Follow { fit, event } => {
                                letterbox = Some(fit.to_rect());
                                if let Some(event) = event {
                                    crate::dlog!(
                                        "[capture-resize] following: session={}x{} \
                                         frame={}x{} dst={}x{}+{},+{} scale={:.3} \
                                         sequence={}",
                                        size.Width,
                                        size.Height,
                                        content_size.Width,
                                        content_size.Height,
                                        fit.dst_width,
                                        fit.dst_height,
                                        fit.dst_x,
                                        fit.dst_y,
                                        fit.scale,
                                        sample.sequence
                                    );
                                    if let Ok(mut guard) = queue_for_handler.lock() {
                                        guard.record_resize_event(event, timestamp);
                                        guard.set_resize_following(true);
                                    }
                                }
                            }
                            ResizeFollowOutcome::Terminate { event, denial } => {
                                // F6：旧实现在此静默丢弃后续所有帧（含硬件
                                // replay 路径），UI 仍显示采集中，局末导出
                                // 才发现 coverage gap。编码管线按启动尺寸
                                // 固化、无法会话中途重建，跟随不可用（软
                                // 编/首帧/超阈值/防抖）时诚实终态化：先落
                                // resize 事件再记显式错误码（诊断包与协调
                                // 器 video 状态可见）；下一局 start 重置队
                                // 列后按新尺寸自动恢复。
                                crate::dlog!(
                                    "[capture-resize] recording terminated: \
                                     session={}x{} frame={}x{} sequence={} denial={}",
                                    size.Width,
                                    size.Height,
                                    content_size.Width,
                                    content_size.Height,
                                    sample.sequence,
                                    denial.map(|reason| reason.as_str()).unwrap_or("structural")
                                );
                                recording_failed_for_handler.store(true, Ordering::Release);
                                if let Ok(mut guard) = queue_for_handler.lock() {
                                    guard.record_resize_event(event, timestamp);
                                    guard.record_hardware_failure(
                                        HardwareEncoderFailure::CaptureResizedUnsupported,
                                    );
                                }
                                return Ok(());
                            }
                        }

                        if let Some(sender) = encoder_frame_sender_for_handler.as_ref() {
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
                                letterbox,
                            }) {
                                Ok(()) => {
                                    encoder_frame_backlog_for_handler
                                        .fetch_add(1, Ordering::Release);
                                }
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
        // D1：encoder 帧通道断开粘滞标志。Disconnected 后 recv 会立即返回，
        // 继续条件等待就退化成忙等，必须退回 20ms 轮询。
        let mut encoder_channel_disconnected = false;
        while !stop.load(Ordering::Acquire) {
            // D1：本轮已由 recv_timeout 条件等待过 → 不再追加 sleep。
            let mut waited_for_frame = false;
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
                        crate::dlog!(
                            "[capture-export] worker: command received start={requested_start_100ns} end={requested_end_100ns}"
                        );
                        if export_join.is_some() {
                            crate::dlog!("[capture-export] worker: busy reject");
                            let _ = response.send(Err(replay_export_failure(
                                ReplayExportFailureKind::ExportBusy,
                                "another hardware replay export is still finalizing",
                            )));
                        } else {
                            let input = automatic_encoder
                                .as_ref()
                                .ok_or_else(|| {
                                    replay_export_failure(
                                        ReplayExportFailureKind::CaptureUnavailable,
                                        "automatic replay encoder is unavailable",
                                    )
                                })
                                .and_then(|encoder| {
                                    encoder.replay_mux_input(
                                        requested_start_100ns,
                                        requested_end_100ns,
                                        encode_width,
                                        encode_height,
                                        clock_metadata,
                                    )
                                });
                            match input {
                                Ok(input) => {
                                    crate::dlog!(
                                        "[capture-export] worker: mux spawning path={}",
                                        output_path.display()
                                    );
                                    export_join = Some(thread::spawn(move || {
                                        crate::thread_priority::apply_capture_thread_priority();
                                        let mux_started = std::time::Instant::now();
                                        crate::dlog!("[capture-export] mux: begin");
                                        let result =
                                            std::panic::catch_unwind(std::panic::AssertUnwindSafe(
                                                || export_replay_mp4_file(input, output_path),
                                            ));
                                        let outcome = match result {
                                            Ok(Ok(receipt)) => {
                                                crate::dlog!(
                                                    "[capture-export] mux: ok packets={} elapsed_ms={}",
                                                    receipt.packet_count,
                                                    mux_started.elapsed().as_millis()
                                                );
                                                Ok(receipt)
                                            }
                                            Ok(Err(error)) => {
                                                crate::dlog!(
                                                    "[capture-export] mux: failed kind={:?} {} elapsed_ms={}",
                                                    error.kind,
                                                    error.message,
                                                    mux_started.elapsed().as_millis()
                                                );
                                                Err(error)
                                            }
                                            Err(panic) => {
                                                crate::dlog!(
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
                                            crate::dlog!(
                                                "[capture-export] mux: response channel closed before delivery"
                                            );
                                        }
                                    }))
                                }
                                Err(error) => {
                                    if let Some(encoder) = automatic_encoder.as_ref() {
                                        encoder.log_replay_status(
                                            requested_start_100ns,
                                            requested_end_100ns,
                                        );
                                    }
                                    crate::dlog!(
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
            if let (Some(encoder), Some(receiver)) =
                (automatic_encoder.as_mut(), encoder_frame_receiver.as_ref())
            {
                let mut failure = None;
                if let Err(error) = encoder.drain_events() {
                    failure = Some(error.failure);
                }
                // 先查 gate 再等帧：dequeue_if_permitted 的既有语义是 gate 关
                // 时帧留在通道里不被消费，先 recv 再查 gate 会偷走一帧。
                let gate_open = failure.is_none() && encoder.accepts_input();
                let captured = if frame_wait_decision(gate_open, encoder_channel_disconnected)
                    == FrameWaitDecision::WaitForFrame
                {
                    waited_for_frame = true;
                    let (captured, disconnected) = apply_frame_wait_result(
                        receiver
                            .recv_timeout(std::time::Duration::from_millis(WORKER_FRAME_WAIT_MS)),
                    );
                    encoder_channel_disconnected |= disconnected;
                    captured
                } else {
                    dequeue_if_permitted(gate_open, receiver)
                };
                if let Some(captured) = captured {
                    let backlog = encoder_frame_backlog.fetch_sub(1, Ordering::AcqRel) as usize;
                    if let Err(error) = encoder.submit_capture_frame(captured, backlog) {
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
            if !waited_for_frame {
                thread::sleep(std::time::Duration::from_millis(WORKER_IDLE_POLL_MS));
            }
        }
        let _ = session.Close();
        let _ = frame_pool.RemoveFrameArrived(frame_arrived_token);
        let _ = frame_pool.Close();
        // 停止前排空已积压的编码输入帧：软编 worker 最多积压一个通道深度
        // （60fps 下 ~0.5s）。若随 stop 直接丢弃，重放缓冲尾部缺失，导出
        // 以 IncompleteCoverage 判死（2026-08-21 实测：局尾立刻退出游戏）。
        if let (Some(encoder), Some(receiver)) =
            (automatic_encoder.as_mut(), encoder_frame_receiver.as_ref())
        {
            while let Some(captured) = dequeue_if_permitted(encoder.accepts_input(), receiver) {
                let backlog = encoder_frame_backlog.fetch_sub(1, Ordering::AcqRel) as usize;
                if encoder.submit_capture_frame(captured, backlog).is_err() {
                    break;
                }
            }
        }
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
        // 启动失败快照：写入共享槽位（FrameQueue 之外），start 入口的
        // 队列 reset 与下一局 start 都清不掉；成功 start 才清除。
        if let Ok(mut slot) = failure_snapshot.lock() {
            *slot = Some(StartFailureSnapshot::new(
                error.clone(),
                std::mem::take(&mut adapter_attempts),
                std::mem::take(&mut dxgi_adapters_seen),
            ));
        }
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

    #[test]
    #[cfg(windows)]
    fn frame_size_drift_matches_only_dimension_changes() {
        // F6 决策契约：任一维度偏离会话启动尺寸即终态化，完全一致则继续。
        assert!(!frame_size_drifts_from_session((1920, 1080), (1920, 1080)));
        assert!(frame_size_drifts_from_session((1920, 1080), (1280, 1080)));
        assert!(frame_size_drifts_from_session((1920, 1080), (1920, 720)));
        assert!(frame_size_drifts_from_session((1920, 1080), (2560, 1440)));
    }

    #[test]
    #[cfg(windows)]
    fn frame_wait_decision_gates_on_accepts_input_and_disconnection() {
        // gate 关 → 轮询：帧必须留在通道里，先等帧会偷走一帧。
        assert_eq!(
            frame_wait_decision(false, false),
            FrameWaitDecision::PollSleep
        );
        assert_eq!(
            frame_wait_decision(false, true),
            FrameWaitDecision::PollSleep
        );
        // gate 开 + 通道健在 → 条件等待（帧到达即醒）。
        assert_eq!(
            frame_wait_decision(true, false),
            FrameWaitDecision::WaitForFrame
        );
        // gate 开但通道已断 → 粘住轮询：Disconnected 后 recv 立即返回会忙等。
        assert_eq!(
            frame_wait_decision(true, true),
            FrameWaitDecision::PollSleep
        );
    }

    #[test]
    #[cfg(windows)]
    fn apply_frame_wait_result_maps_frame_timeout_and_disconnection() {
        let (sender, receiver) = std::sync::mpsc::sync_channel::<usize>(1);
        sender.send(7).expect("seed frame");
        let (frame, disconnected) =
            apply_frame_wait_result(receiver.recv_timeout(std::time::Duration::from_millis(1)));
        assert_eq!(frame, Some(7));
        assert!(!disconnected);
        // 超时：无帧、不断开。
        let (frame, disconnected) =
            apply_frame_wait_result(receiver.recv_timeout(std::time::Duration::from_millis(1)));
        assert_eq!(frame, None);
        assert!(!disconnected, "timeout must not latch disconnection");
        // Disconnected：置粘滞标志，调用方必须退回轮询。
        drop(sender);
        let (frame, disconnected) =
            apply_frame_wait_result(receiver.recv_timeout(std::time::Duration::from_millis(100)));
        assert_eq!(frame, None);
        assert!(disconnected, "Disconnected must latch the poll fallback");
    }

    #[test]
    fn resize_termination_surfaces_explicit_code_until_queue_reset() {
        let state = WindowCaptureState::new(DEFAULT_FRAME_QUEUE_CAPACITY).unwrap();
        assert!(!state.recording_terminated_by_resize());
        assert_eq!(state.status().last_encoder_failure, None);

        state
            .queue
            .lock()
            .unwrap()
            .record_hardware_failure(HardwareEncoderFailure::CaptureResizedUnsupported);

        let status = state.status();
        assert_eq!(
            status.last_encoder_failure,
            Some(HardwareEncoderFailure::CaptureResizedUnsupported)
        );
        assert_eq!(status.encoder_errors, 1);
        assert!(state.recording_terminated_by_resize());

        // 下一局 start 重置队列后不得跨会话粘连。
        state.queue.lock().unwrap().reset();
        assert!(!state.recording_terminated_by_resize());
        assert_eq!(state.status().last_encoder_failure, None);
    }

    #[test]
    fn resize_failure_serializes_as_an_explicit_camel_case_code() {
        assert_eq!(
            serde_json::to_string(&HardwareEncoderFailure::CaptureResizedUnsupported).unwrap(),
            "\"captureResizedUnsupported\""
        );
    }

    // ---- 病灶 A：硬编 letterbox 跟随（窗口尺寸漂移不再整场终态化）----

    #[test]
    #[cfg(windows)]
    fn letterbox_fit_centers_equal_ratio_fit_inside_encode_rect() {
        // 等比 fit 合同：s = min(dstW/srcW, dstH/srcH)，居中、零变形；
        // 放大（s>1）同样合法：只糊不变形，坐标变换精确。
        let fit = letterbox_fit((2560, 1600), (1280, 800));
        assert_eq!((fit.dst_width, fit.dst_height), (2560, 1600));
        assert_eq!((fit.dst_x, fit.dst_y), (0, 0));
        assert!((fit.scale - 2.0).abs() < 1e-9);

        // 纵向黑边：s = 2560/1920，dst 2560x1440 上下各留 80。
        let fit = letterbox_fit((2560, 1600), (1920, 1080));
        assert_eq!((fit.dst_width, fit.dst_height), (2560, 1440));
        assert_eq!((fit.dst_x, fit.dst_y), (0, 80));
        assert!((fit.scale - 2560.0 / 1920.0).abs() < 1e-9);

        // 奇数内容（item size 可奇）：四舍五入后仍不超 encode 并居中。
        let fit = letterbox_fit((2560, 1600), (2559, 1599));
        assert!(fit.dst_width <= 2560 && fit.dst_height <= 1600);
        assert_eq!(fit.dst_x, i64::from((2560 - fit.dst_width) / 2));
        assert_eq!(fit.dst_y, i64::from((1600 - fit.dst_height) / 2));
    }

    #[test]
    #[cfg(windows)]
    fn resize_follow_decision_follows_when_scale_meets_floor() {
        // 缩小到恰好一半（s=0.5）与放大（s>1）都在跟随阈值上。
        assert!(matches!(
            resize_follow_decision((1280, 800), (2560, 1600), &[], 1_000),
            ResizeFollowDecision::Follow(_)
        ));
        assert!(matches!(
            resize_follow_decision((2560, 1600), (1280, 800), &[], 1_000),
            ResizeFollowDecision::Follow(_)
        ));
    }

    #[test]
    #[cfg(windows)]
    fn resize_follow_decision_denies_when_scale_below_floor() {
        // s < 0.5（内容远大于编码目标）：退回终态化。
        match resize_follow_decision((2560, 1600), (6400, 4000), &[], 1_000) {
            ResizeFollowDecision::Deny { reason, .. } => {
                assert_eq!(reason.as_str(), "resize_follow_scale_below_floor");
            }
            ResizeFollowDecision::Follow(_) => panic!("s<0.5 must deny the follow"),
        }
    }

    #[test]
    #[cfg(windows)]
    fn resize_follow_decision_budget_exhausts_after_eight_follows() {
        let record = |index: usize| ResizeFollowRecord {
            at_100ns: 1_000_000 * index as i64,
            src_width: 1280,
            src_height: 800,
        };
        // 历史含 7 条时第 8 次跟随仍放行。
        let history: Vec<_> = (0..7).map(record).collect();
        assert!(matches!(
            resize_follow_decision((2560, 1600), (1280, 800), &history, 8_000_000),
            ResizeFollowDecision::Follow(_)
        ));
        // 第 9 次触发预算防抖 → 终态化。
        let history: Vec<_> = (0..8).map(record).collect();
        match resize_follow_decision((2560, 1600), (1920, 1200), &history, 9_000_000) {
            ResizeFollowDecision::Deny { reason, .. } => {
                assert_eq!(reason.as_str(), "resize_follow_budget_exhausted");
            }
            ResizeFollowDecision::Follow(_) => panic!("budget must exhaust after 8 follows"),
        }
    }

    #[test]
    #[cfg(windows)]
    fn resize_follow_decision_denies_three_oscillations_within_two_seconds() {
        // A→B→A 记一次振荡；2s 窗口内累计 3 次 → 退回终态化。
        let oscillating = |at_100ns: i64| ResizeFollowRecord {
            at_100ns,
            src_width: 1280,
            src_height: 800,
        };
        let other = |at_100ns: i64| ResizeFollowRecord {
            at_100ns,
            src_width: 1920,
            src_height: 1200,
        };
        // 历史 4 条 + 当前事件构成 A,B,A,B,A（间隔 3ms，全部落在 2s 窗口）。
        let history = vec![
            oscillating(0),
            other(3_000_000),
            oscillating(6_000_000),
            other(9_000_000),
        ];
        match resize_follow_decision((2560, 1600), (1280, 800), &history, 12_000_000) {
            ResizeFollowDecision::Deny { reason, .. } => {
                assert_eq!(reason.as_str(), "resize_follow_oscillation");
            }
            ResizeFollowDecision::Follow(_) => panic!("3 oscillations in 2s must deny"),
        }
        // 同样 4 条历史摊开到 2s 窗口之外：只看得到 1 条，不触发防抖。
        let history = vec![
            oscillating(0),
            other(30_000_000),
            oscillating(60_000_000),
            other(90_000_000),
        ];
        assert!(matches!(
            resize_follow_decision((2560, 1600), (1280, 800), &history, 120_000_000),
            ResizeFollowDecision::Follow(_)
        ));
    }

    #[test]
    #[cfg(windows)]
    fn resize_follow_controller_follows_hardware_drift_after_encoded_output() {
        // 红测 (1)：硬编 + 已有编码产出 + 漂移 → 跟随：不终态化，
        // 产出预期 letterbox 矩形并记录 followed=true 事件。
        let input = ResizeFollowFrameInput {
            encode: (2560, 1600),
            content: (1280, 800),
            drifted: true,
            at_100ns: 10_000_000,
            at_utc_ms: 1_791_280_000_000,
            hardware_encoder: true,
            has_encoded_output: true,
        };
        let mut controller = ResizeFollowController::new();
        match controller.on_frame(input) {
            ResizeFollowOutcome::Follow { fit, event } => {
                assert_eq!((fit.dst_width, fit.dst_height), (2560, 1600));
                let event = event.expect("first follow must record a resize event");
                assert!(event.followed);
                assert_eq!(event.at_utc_ms, 1_791_280_000_000);
                assert_eq!((event.src_width, event.src_height), (1280, 800));
                assert!((event.scale - 2.0).abs() < 1e-9);
            }
            ResizeFollowOutcome::Terminate { .. } => {
                panic!("hardware drift with prior output must follow, not terminate")
            }
            ResizeFollowOutcome::PassThrough => panic!("drifted frame must not pass through"),
        }

        // 稳态漂移帧（同尺寸）：继续跟随，不重复记事件。
        let steady = ResizeFollowFrameInput {
            at_100ns: 10_333_333,
            at_utc_ms: 1_791_280_000_033,
            ..input
        };
        match controller.on_frame(steady) {
            ResizeFollowOutcome::Follow { event, .. } => assert!(event.is_none()),
            _ => panic!("steady drifted frame must keep following"),
        }
    }

    #[test]
    #[cfg(windows)]
    fn resize_follow_controller_terminates_on_first_frame_drift_without_output() {
        // 红测 (4)：首帧即漂移（无编码帧产出）→ 仍终态化，事件 followed=false。
        let mut controller = ResizeFollowController::new();
        match controller.on_frame(ResizeFollowFrameInput {
            encode: (2560, 1600),
            content: (1280, 800),
            drifted: true,
            at_100ns: 10_000,
            at_utc_ms: 1_791_280_000_000,
            hardware_encoder: true,
            has_encoded_output: false,
        }) {
            ResizeFollowOutcome::Terminate { event, .. } => {
                assert!(!event.followed);
                assert_eq!((event.src_width, event.src_height), (1280, 800));
            }
            _ => panic!("first-frame drift must terminate"),
        }
    }

    #[test]
    #[cfg(windows)]
    fn resize_follow_controller_terminates_software_variant_drift() {
        // 红测 (3)：软编变体漂移 → 仍终态化。
        let mut controller = ResizeFollowController::new();
        match controller.on_frame(ResizeFollowFrameInput {
            encode: (2560, 1600),
            content: (1280, 800),
            drifted: true,
            at_100ns: 10_000,
            at_utc_ms: 1_791_280_000_000,
            hardware_encoder: false,
            has_encoded_output: true,
        }) {
            ResizeFollowOutcome::Terminate { event, .. } => assert!(!event.followed),
            _ => panic!("software variant drift must terminate"),
        }
    }

    #[test]
    #[cfg(windows)]
    fn resize_follow_controller_terminates_when_scale_below_floor() {
        // 红测 (2)：s<0.5 → 仍终态化，事件带 ratio。
        let mut controller = ResizeFollowController::new();
        match controller.on_frame(ResizeFollowFrameInput {
            encode: (2560, 1600),
            content: (6400, 4000),
            drifted: true,
            at_100ns: 10_000,
            at_utc_ms: 1_791_280_000_000,
            hardware_encoder: true,
            has_encoded_output: true,
        }) {
            ResizeFollowOutcome::Terminate { event, .. } => {
                assert!(!event.followed);
                assert!(event.scale < 0.5);
            }
            _ => panic!("below-floor scale must terminate"),
        }
    }

    #[test]
    #[cfg(windows)]
    fn resize_follow_controller_debounce_falls_back_to_termination() {
        // 红测 (5)：防抖触发（预算耗尽）→ 终态化。
        let mut controller = ResizeFollowController::new();
        let frame = |at_100ns: i64, width: u32, height: u32| ResizeFollowFrameInput {
            encode: (2560, 1600),
            content: (width, height),
            drifted: true,
            at_100ns,
            at_utc_ms: 1_791_280_000_000 + at_100ns / 10_000,
            hardware_encoder: true,
            has_encoded_output: true,
        };
        // 8 个互不相同的漂移尺寸全部跟随。
        let sizes = [
            (1280u32, 800u32),
            (1281, 800),
            (1280, 801),
            (1282, 800),
            (1280, 802),
            (1283, 800),
            (1280, 803),
            (1284, 800),
        ];
        for (index, (width, height)) in sizes.iter().enumerate() {
            let outcome = controller.on_frame(frame(10_000_000 * index as i64, *width, *height));
            assert!(
                matches!(outcome, ResizeFollowOutcome::Follow { .. }),
                "follow #{index} must succeed"
            );
        }
        // 第 9 个尺寸：预算防抖 → 退回终态化。
        let outcome = controller.on_frame(frame(80_000_000, 1285, 800));
        assert!(
            matches!(outcome, ResizeFollowOutcome::Terminate { .. }),
            "9th resize must trip the debounce"
        );
    }

    #[test]
    #[cfg(windows)]
    fn resize_follow_controller_passthrough_resumes_without_letterbox() {
        // 漂移解除（内容回到会话尺寸）→ PassThrough；再次漂移（预算内）仍可跟随。
        let input = ResizeFollowFrameInput {
            encode: (2560, 1600),
            content: (1280, 800),
            drifted: true,
            at_100ns: 10_000_000,
            at_utc_ms: 1_791_280_000_000,
            hardware_encoder: true,
            has_encoded_output: true,
        };
        let mut controller = ResizeFollowController::new();
        assert!(matches!(
            controller.on_frame(input),
            ResizeFollowOutcome::Follow { .. }
        ));
        assert!(matches!(
            controller.on_frame(ResizeFollowFrameInput {
                content: (2560, 1600),
                drifted: false,
                at_100ns: 20_000_000,
                at_utc_ms: 1_791_280_000_001,
                ..input
            }),
            ResizeFollowOutcome::PassThrough
        ));
        assert!(matches!(
            controller.on_frame(ResizeFollowFrameInput {
                at_100ns: 30_000_000,
                at_utc_ms: 1_791_280_000_002,
                ..input
            }),
            ResizeFollowOutcome::Follow { event: Some(_), .. }
        ));
    }

    #[test]
    #[cfg(windows)]
    fn frame_timestamp_maps_to_utc_ms_via_capture_clock() {
        let clock = CaptureClockMetadata {
            utc_epoch_ms: 1_791_280_000_000,
            qpc_ns: 5_000_000_000, // 锚点 = QPC 500ms
            clock_source: "utc_epoch_ms+qpc+wgc_system_relative_time",
            timebase_version: "time_alignment.v2",
        };
        // 帧时间恰为锚点 → 原样；+250ms → +250。
        assert_eq!(
            frame_timestamp_to_utc_ms(clock, 50_000_000),
            Some(1_791_280_000_000)
        );
        assert_eq!(
            frame_timestamp_to_utc_ms(clock, 52_500_000),
            Some(1_791_280_000_250)
        );
    }

    #[test]
    fn resize_events_cap_at_sixteen_drop_oldest_and_clear_on_reset() {
        let state = WindowCaptureState::new(DEFAULT_FRAME_QUEUE_CAPACITY).unwrap();
        let event = |sequence: u32| CaptureResizeEvent {
            at_utc_ms: 1_000 + i64::from(sequence),
            src_width: 1280,
            src_height: 800,
            dst_x: 0,
            dst_y: 0,
            dst_width: 2560,
            dst_height: 1600,
            scale: 2.0,
            followed: true,
        };
        {
            let mut queue = state.queue.lock().unwrap();
            for sequence in 0..18u32 {
                queue.record_resize_event(event(sequence), 100 * i64::from(sequence));
            }
        }
        let status = state.status();
        assert_eq!(status.resize_events.len(), 16);
        // 丢最旧：首条是 sequence=2。
        assert_eq!(status.resize_events[0].at_utc_ms, 1_002);
        assert_eq!(status.resize_events[15].at_utc_ms, 1_017);

        // reset 后清空（随 last_encoder_failure 一并清）。
        state.queue.lock().unwrap().reset();
        assert!(state.status().resize_events.is_empty());
    }

    #[test]
    fn resize_following_predicate_reflects_queue_flag_and_terminated_override() {
        let state = WindowCaptureState::new(DEFAULT_FRAME_QUEUE_CAPACITY).unwrap();
        assert!(!state.recording_following_resize());
        state.queue.lock().unwrap().set_resize_following(true);
        assert!(state.recording_following_resize());
        // 终态化优先：跟随标志在场也不得报「跟随中」。
        state
            .queue
            .lock()
            .unwrap()
            .record_hardware_failure(HardwareEncoderFailure::CaptureResizedUnsupported);
        assert!(!state.recording_following_resize());
        assert!(state.recording_terminated_by_resize());
    }

    #[test]
    #[cfg(windows)]
    fn queue_geometry_events_map_resize_events_to_epoch_canonical_ms() {
        let state = WindowCaptureState::new(DEFAULT_FRAME_QUEUE_CAPACITY).unwrap();
        state.queue.lock().unwrap().record_resize_event(
            CaptureResizeEvent {
                at_utc_ms: 1_791_280_000_000,
                src_width: 1280,
                src_height: 800,
                dst_x: 0,
                dst_y: 0,
                dst_width: 2560,
                dst_height: 1600,
                scale: 2.0,
                followed: true,
            },
            12_345_678, // 会话 PTS（100ns），仅内部换算留痕
        );
        let events = queue_geometry_events(&state.queue);
        assert_eq!(events.len(), 1);
        // canonical_ms = epoch 毫秒（与诊断包 resizeEvents.atUtcMs 同源同轴），
        // 不是会话相对轴——消费端 letterbox_segment_at 按 run window 的
        // start_epoch_ms epoch 轴选段，两轴错位会让段永远选错/选不到。
        assert_eq!(events[0].canonical_ms, 1_791_280_000_000);
        assert_eq!((events[0].src_width, events[0].src_height), (1280, 800));
        assert_eq!((events[0].dst_width, events[0].dst_height), (2560, 1600));
        assert!((events[0].scale - 2.0).abs() < 1e-9);
    }

    #[test]
    #[cfg(windows)]
    fn geometry_events_and_resize_events_share_the_same_frame_clock() {
        // 对齐性合同：同一漂移帧产出的 resizeEvents.atUtcMs 与落盘 receipt
        // 的 geometryEvents.canonicalMs 必须同值同轴（epoch 毫秒），消费端
        // 才能用 run window 的 start_epoch_ms 把段选对。
        let state = WindowCaptureState::new(DEFAULT_FRAME_QUEUE_CAPACITY).unwrap();
        state.queue.lock().unwrap().record_resize_event(
            CaptureResizeEvent {
                at_utc_ms: 1_791_280_001_234,
                src_width: 1280,
                src_height: 800,
                dst_x: 0,
                dst_y: 0,
                dst_width: 2560,
                dst_height: 1600,
                scale: 2.0,
                followed: true,
            },
            12_345_678,
        );
        let at_utc_ms = state.status().resize_events[0].at_utc_ms;
        let canonical_ms = queue_geometry_events(&state.queue)[0].canonical_ms;
        assert_eq!(at_utc_ms, 1_791_280_001_234);
        assert_eq!(canonical_ms, at_utc_ms);
    }

    #[test]
    fn replay_receipt_serializes_geometry_events_as_camel_case() {
        let mut input = replay_mux_input(replay_mux_snapshot());
        input.geometry_events = vec![GeometryEvent {
            canonical_ms: 17_912_800_000_000,
            src_width: 1280,
            src_height: 800,
            dst_x: 320,
            dst_y: 0,
            dst_width: 1280,
            dst_height: 1600,
            scale: 0.667,
        }];
        let (_, receipt) = build_replay_mp4(&input).unwrap();
        assert_eq!(receipt.geometry_events.len(), 1);
        assert_eq!(receipt.geometry_events[0].canonical_ms, 17_912_800_000_000);

        let json = serde_json::to_string(&receipt).unwrap();
        let value: serde_json::Value = serde_json::from_str(&json).unwrap();
        let geometry = &value["geometryEvents"];
        assert_eq!(geometry[0]["canonicalMs"].as_f64(), Some(1.79128e13));
        assert_eq!(geometry[0]["srcWidth"], 1280);
        assert_eq!(geometry[0]["dstX"], 320);
        assert_eq!(geometry[0]["dstHeight"], 1600);
        assert_eq!(geometry[0]["scale"].as_f64(), Some(0.667));
        // 旧 receipt 无 geometryEvents 字段：消费端按无变换处理，Rust 侧
        // 不做字段缺省迁移；此处只锁定新 receipt 的落盘形态。
    }

    #[test]
    fn replay_export_in_flight_counter_tracks_begin_end_pairs() {
        // 重建安全门 (b) 的计数契约：begin/end 成对记账，多余的 end 不得把
        // 计数打穿；poison 槽位按保守方向（视为在途）阻塞重建。
        let state = WindowCaptureState::new(DEFAULT_FRAME_QUEUE_CAPACITY).unwrap();
        assert!(!state.replay_export_in_flight());
        state.replay_export_begin();
        state.replay_export_begin();
        assert!(state.replay_export_in_flight());
        state.replay_export_end();
        assert!(state.replay_export_in_flight());
        state.replay_export_end();
        assert!(!state.replay_export_in_flight());
        state.replay_export_end();
        assert!(!state.replay_export_in_flight());
    }

    #[test]
    #[cfg(windows)]
    fn rate_control_plans_lock_bitrate_to_the_recording_target() {
        // 码率约束契约：无论走 CBR 还是峰值受限 VBR，均值与峰值上限都
        // 不得超过录制目标码率，且 CBR 计划优先尝试。
        let plans = rate_control_plans(DEFAULT_RECORDING_TARGET_BITRATE_BPS);
        assert_eq!(plans.len(), 2);
        assert_eq!(
            plans[0],
            RateControlPlan::Cbr {
                mean_bps: DEFAULT_RECORDING_TARGET_BITRATE_BPS
            }
        );
        for plan in &plans {
            match plan {
                RateControlPlan::Cbr { mean_bps } => {
                    assert_eq!(*mean_bps, DEFAULT_RECORDING_TARGET_BITRATE_BPS);
                }
                RateControlPlan::PeakConstrainedVbr { mean_bps, max_bps } => {
                    assert_eq!(*mean_bps, DEFAULT_RECORDING_TARGET_BITRATE_BPS);
                    assert!(max_bps <= mean_bps);
                }
            }
        }
    }

    #[test]
    #[cfg(windows)]
    fn ui4_variant_carries_the_value_as_vt_ui4() {
        let variant = ui4_variant(8_000_000);
        unsafe {
            assert_eq!(
                variant.Anonymous.Anonymous.vt,
                windows::Win32::System::Variant::VT_UI4
            );
            assert_eq!(variant.Anonymous.Anonymous.Anonymous.ulVal, 8_000_000);
        }
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
            geometry_events: Vec::new(),
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
                    3_000_000,
                    100,
                    false,
                    Arc::from([0, 0, 0, 1, 0x41, 0x22]),
                )),
            ],
            requested_start_100ns: 250,
            requested_end_100ns: 3_000_100,
            decode_start_100ns: 200,
            start_offset_100ns: 50,
            end_offset_100ns: 2_999_900,
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

        let mut incomplete = EncodedReplayBuffer::with_limits(4_000_000, 100).unwrap();
        incomplete.push(small_replay_packet(0, 100, true)).unwrap();
        // 尾部 shortfall ≤ 容忍阈值（视频末帧持续显示）视为覆盖。
        assert!(incomplete.snapshot(0, 2_000_000).is_ok());
        // 超过容忍阈值（250ms）的尾部缺失仍失败。
        assert_eq!(
            incomplete.snapshot(0, 3_000_000),
            Err(ReplayBufferError::IncompleteCoverage)
        );

        let mut gap = EncodedReplayBuffer::with_limits(4_000_000, 100).unwrap();
        gap.push(small_replay_packet(0, 100, true)).unwrap();
        gap.push(small_replay_packet(2_600_000, 100, false))
            .unwrap();
        assert_eq!(
            gap.snapshot(0, 2_600_100),
            Err(ReplayBufferError::CoverageGap)
        );
    }

    #[test]
    fn replay_snapshot_tolerates_gaps_within_the_export_tolerance() {
        let mut replay = EncodedReplayBuffer::with_limits(4_000_000, 200).unwrap();
        for pts in [0, 100, 300, 200_000, 3_000_000] {
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

        // 窗口内多个 ≤250ms 缺口累计计数。
        let both = replay.snapshot(0, 200_100).unwrap();
        assert_eq!(both.packets.len(), 4);
        assert_eq!(both.tolerated_gaps, 2);

        // 超过 250ms 容差的缺口仍然失败。
        assert_eq!(
            replay.snapshot(0, 3_000_100),
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
                .submit_texture(&source, index * frame_duration, frame_duration, None)
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
                .submit_texture(&source, index * frame_duration, frame_duration, None)
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

    // WARP 设备在无 GPU 的 CI/虚拟机上也存在，软件编码路径不依赖 GPU
    // 视频处理能力，用 WARP 让测试在任何 Win10+ 环境可复现。
    #[cfg(windows)]
    fn warp_bgra_source(
        width: u32,
        height: u32,
    ) -> (
        windows::Win32::Graphics::Direct3D11::ID3D11Device,
        windows::Win32::Graphics::Direct3D11::ID3D11DeviceContext,
        windows::Win32::Graphics::Direct3D11::ID3D11Texture2D,
    ) {
        use windows::Win32::Foundation::HMODULE;
        use windows::Win32::Graphics::Direct3D::{D3D_DRIVER_TYPE_WARP, D3D_FEATURE_LEVEL_11_0};
        use windows::Win32::Graphics::Direct3D11::{
            D3D11CreateDevice, ID3D11Device, ID3D11DeviceContext, D3D11_BIND_RENDER_TARGET,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT, D3D11_SDK_VERSION, D3D11_TEXTURE2D_DESC,
            D3D11_USAGE_DEFAULT,
        };
        use windows::Win32::Graphics::Dxgi::Common::{
            DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_SAMPLE_DESC,
        };

        let mut device: Option<ID3D11Device> = None;
        let mut context: Option<ID3D11DeviceContext> = None;
        unsafe {
            D3D11CreateDevice(
                None,
                D3D_DRIVER_TYPE_WARP,
                HMODULE::default(),
                D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                Some(&[D3D_FEATURE_LEVEL_11_0]),
                D3D11_SDK_VERSION,
                Some(&mut device),
                None,
                Some(&mut context),
            )
        }
        .expect("WARP D3D11 device should initialize");
        let device = device.expect("D3D11 device should be returned");
        let context = context.expect("D3D11 context should be returned");
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
            Usage: D3D11_USAGE_DEFAULT,
            BindFlags: D3D11_BIND_RENDER_TARGET.0 as u32,
            CPUAccessFlags: 0,
            MiscFlags: 0,
        };
        let mut source = None;
        unsafe { device.CreateTexture2D(&description, None, Some(&mut source)) }
            .expect("GPU BGRA texture should initialize");
        (
            device,
            context,
            source.expect("GPU BGRA texture should be returned"),
        )
    }

    #[cfg(windows)]
    fn fill_bgra_texture(
        context: &windows::Win32::Graphics::Direct3D11::ID3D11DeviceContext,
        texture: &windows::Win32::Graphics::Direct3D11::ID3D11Texture2D,
        width: u32,
        height: u32,
        frame_index: u32,
    ) {
        let mut pixels = vec![0u8; (width * height * FRAME_PIXEL_BYTES as u32) as usize];
        for (index, pixel) in pixels.chunks_exact_mut(FRAME_PIXEL_BYTES).enumerate() {
            let x = (index as u32) % width;
            pixel[0] = (x * 255 / width).wrapping_add(frame_index) as u8;
            pixel[1] = 96;
            pixel[2] = 192;
            pixel[3] = 255;
        }
        unsafe {
            context.UpdateSubresource(
                texture,
                0,
                None,
                pixels.as_ptr().cast(),
                width * FRAME_PIXEL_BYTES as u32,
                0,
            );
        }
    }

    #[cfg(windows)]
    fn drive_software_encoder(
        frames: u32,
        source_width: u32,
        source_height: u32,
        encode_width: u32,
        encode_height: u32,
    ) -> (SoftwareH264Encoder, Arc<Mutex<FrameQueue>>) {
        let (device, context, source) = warp_bgra_source(source_width, source_height);
        let queue = Arc::new(Mutex::new(
            FrameQueue::new(DEFAULT_FRAME_QUEUE_CAPACITY).unwrap(),
        ));
        let mut encoder = SoftwareH264Encoder::new(
            &device,
            &context,
            source_width,
            source_height,
            encode_width,
            encode_height,
            Arc::clone(&queue),
        )
        .expect("software H.264 encoder should initialize");
        let frame_duration = 10_000_000 / 60;
        for index in 0..frames {
            fill_bgra_texture(&context, &source, source_width, source_height, index);
            encoder
                .submit_texture(&source, index as i64 * frame_duration, frame_duration)
                .expect("software encoder should accept a CPU frame");
        }
        (encoder, queue)
    }

    #[cfg(windows)]
    #[test]
    fn software_h264_mft_enumeration_finds_microsoft_encoder() {
        let candidates = enumerate_h264_mfts(None, false)
            .expect("software H.264 MFT enumeration should succeed");
        assert!(
            !candidates.is_empty(),
            "Win10 always ships the in-box Microsoft H.264 Encoder MFT"
        );
        let names: Vec<String> = candidates.iter().map(mft_friendly_name).collect();
        eprintln!("software H.264 MFT candidates: {names:?}");
        assert!(
            names
                .iter()
                .any(|name| name.to_ascii_uppercase().contains("H264")),
            "expected an in-box software H.264 encoder MFT, got: {names:?}"
        );
    }

    #[cfg(windows)]
    #[test]
    fn bgra8_to_nv12_uses_bt601_limited_range() {
        let width = 4u32;
        let height = 2u32;
        let mut bgra = vec![0u8; (width * height * FRAME_PIXEL_BYTES as u32) as usize];
        for (row, blue, green, red) in [(0u32, 0u8, 0u8, 255u8), (1, 255, 0, 0)] {
            for col in 0..width {
                let offset = ((row * width + col) * FRAME_PIXEL_BYTES as u32) as usize;
                bgra[offset] = blue;
                bgra[offset + 1] = green;
                bgra[offset + 2] = red;
                bgra[offset + 3] = 255;
            }
        }
        let nv12 = bgra8_to_nv12(&bgra, width, height);
        // BT.601 有限范围：Y(红) = (66*255 + 128)/256 + 16 = 82，
        // Y(蓝) = (25*255 + 128)/256 + 16 = 41。
        assert_eq!(nv12[0], 82);
        assert_eq!(nv12[1], 82);
        assert_eq!(nv12[width as usize], 41);
        assert_eq!(nv12[width as usize + 1], 41);
        // 上排红 + 下排蓝的 2x2 均值在 RGB 域 → (127, 0, 127)：
        // U = (-38*127 + 112*127 + 128)/256 + 128 = 165，
        // V = (112*127 - 18*127 + 128)/256 + 128 = 175。
        let uv = (width * height) as usize;
        assert_eq!(nv12[uv], 165);
        assert_eq!(nv12[uv + 1], 175);
    }

    #[cfg(windows)]
    #[test]
    fn bgra8_to_nv12_handles_odd_dimensions_without_oob() {
        // 窗口尺寸可奇（WGC 帧尺寸不保证偶数）；2x2 色度块在右/下边缘
        // 复制补齐，不得越界读取或写出。
        let width = 5u32;
        let height = 3u32;
        let bgra = vec![128u8; (width * height * FRAME_PIXEL_BYTES as u32) as usize];
        let nv12 = bgra8_to_nv12(&bgra, width, height);
        assert_eq!(nv12.len(), 27);
        // 中灰 (128,128,128)：Y = (66+129+25)*128/256 + 16 = 126，
        // U = V = (-38-74+112)*128/256 + 128 = 128。
        assert_eq!(nv12[0], 126);
        let uv = (width * height) as usize;
        assert_eq!(nv12[uv], 128);
        assert_eq!(nv12[uv + 1], 128);
    }

    #[cfg(windows)]
    #[test]
    fn software_h264_encoder_encodes_synthetic_frames() {
        let width = 320u32;
        let height = 240u32;
        // MFT 有约 16 帧固有管线延迟（feed=N 时输出到 N-16，稳态 1:1），
        // 120 帧输入保证覆盖窗口内全部输出且尾部仍在缓冲中。
        let fed = 120u32;
        let (encoder, queue) = drive_software_encoder(fed, width, height, width, height);
        let status = queue.lock().unwrap().status(false, true);
        eprintln!("software H.264 synthetic status: {status:?}");
        assert_eq!(
            status.encoder_path,
            Some(HardwareEncoderPath::MediaFoundationSoftwareH264)
        );
        assert!(status.adapter_identity.is_some());
        assert_eq!(status.dropped_packets, 0);
        assert_eq!(status.encoder_errors, 0);
        assert_eq!(encoder.packet_count(), status.submitted_packets as usize);
        assert!(
            status.submitted_packets >= (fed - 20) as u64,
            "steady-state output should trail input by the MFT pipeline delay"
        );
        assert_eq!(status.first_packet_pts_100ns, Some(0));
        assert_eq!(encoder.replay.status().coverage_gaps, 0);
        assert!(
            encoder.has_keyframe(),
            "expected H.264 clean-point metadata"
        );
        assert!(encoder.full_frame_cpu_readback());
        assert!(
            !encoder.sequence_header.is_empty(),
            "software MFT should expose the SPS/PPS sequence header"
        );
        let first = &encoder.replay.packets[0];
        assert!(
            first.bytes.starts_with(&[0, 0, 0, 1]) || first.bytes.starts_with(&[0, 0, 1]),
            "software H.264 access unit is not Annex B: {:02x?}",
            &first.bytes[..first.bytes.len().min(8)]
        );
    }

    #[cfg(windows)]
    #[test]
    fn software_encoder_encodes_odd_source_at_even_encode_dims() {
        // 0930 报障回归锁：奇数源（虚拟显示器/分数 DPI 下的整窗尺寸）
        // 直接进编码链会让软编 MFT output type 被拒；staging 保源尺寸、
        // 回读按偶数编码尺寸裁剪后，装配与编码都必须成功。
        let (encoder, queue) = drive_software_encoder(120, 321, 241, 320, 240);
        let status = queue.lock().unwrap().status(false, true);
        assert_eq!(
            status.encoder_path,
            Some(HardwareEncoderPath::MediaFoundationSoftwareH264)
        );
        assert_eq!(status.encoder_errors, 0);
        assert!(
            status.submitted_packets > 0,
            "odd-source cropping should still produce encoded packets"
        );
        assert!(
            encoder.has_keyframe(),
            "expected H.264 clean-point metadata"
        );
    }

    #[cfg(windows)]
    #[test]
    fn software_replay_mux_writes_valid_mp4() {
        let width = 320u32;
        let height = 240u32;
        let frame_duration = 10_000_000 / 60;
        // 120 帧输入 + ~16 帧管线延迟 → 输出覆盖 PTS 0..100+，窗口 0..60 安全覆盖。
        let (encoder, _queue) = drive_software_encoder(120, width, height, width, height);
        let output = std::env::temp_dir().join(format!(
            "aiming-cookie-software-encoder-{}.mp4",
            std::process::id()
        ));
        let _ = std::fs::remove_file(&output);
        let input = encoder
            .replay_mux_input(
                0,
                60 * frame_duration,
                width,
                height,
                CaptureClockMetadata {
                    utc_epoch_ms: 1_700_000_000_000,
                    qpc_ns: 5_000_000_000,
                    clock_source: "utc_epoch_ms+qpc+wgc_system_relative_time",
                    timebase_version: "time_alignment.v2",
                },
            )
            .expect("software replay snapshot should cover the requested window");
        let receipt = export_replay_mp4_file(input, output.clone())
            .expect("software replay MP4 should mux without re-encoding");
        assert_eq!(receipt.visible_duration_100ns, 60 * frame_duration);
        assert_eq!(receipt.reencoded_frames, 0);
        let bytes = std::fs::read(&output).expect("software replay MP4 should exist");
        assert!(!bytes.is_empty());
        assert_eq!(
            &bytes[4..8],
            b"ftyp",
            "MP4 file should start with an ftyp box"
        );
        let _ = std::fs::remove_file(&output);
    }

    #[cfg(windows)]
    #[test]
    fn gpu_conversion_failure_falls_back_to_software_encoder() {
        // 0929 线上报障回归锁：GPU 侧转换装配失败（AMD 老驱动 NV12 纹理
        // E_INVALIDARG）必须降级软件编码，不得整体失败。
        assert!(encoder_failure_allows_software_fallback(
            HardwareEncoderFailure::GpuConversionFailure
        ));
        assert!(encoder_failure_allows_software_fallback(
            HardwareEncoderFailure::HardwareUnavailable
        ));
        assert!(encoder_failure_allows_software_fallback(
            HardwareEncoderFailure::AdapterMismatch
        ));
    }

    #[cfg(windows)]
    #[test]
    fn normalize_encode_dimensions_rounds_odd_down_to_even() {
        // 0930 报障回归锁：奇数窗口必须向下取偶（裁 ≤1px），偶数原样。
        assert_eq!(
            normalize_encode_dimensions(1921, 1081).unwrap(),
            (1920, 1080)
        );
        assert_eq!(normalize_encode_dimensions(2, 3).unwrap(), (2, 2));
        assert_eq!(
            normalize_encode_dimensions(1920, 1080).unwrap(),
            (1920, 1080)
        );
        assert_eq!(
            normalize_encode_dimensions(4096, 4095).unwrap(),
            (4096, 4094)
        );
    }

    #[cfg(windows)]
    #[test]
    fn normalize_encode_dimensions_rejects_degenerate_and_oversized() {
        // 病态尺寸显式终态且消息携带实际尺寸，便于日志一锤定音。
        for (width, height) in [(0, 0), (1, 1080), (1920, 1)] {
            let error = normalize_encode_dimensions(width, height).unwrap_err();
            assert!(
                error.contains(&format!("{width}x{height}")),
                "rejection should carry actual dims: {error}"
            );
        }
        assert!(normalize_encode_dimensions(4097, 1080).is_err());
        assert!(normalize_encode_dimensions(1920, 4097).is_err());
    }

    #[test]
    fn session_dimensions_and_hardware_rejection_surface_in_status() {
        // 诊断包可见性：会话/编码尺寸与硬编层被回退顶替的拒绝原因都要
        // 进 status，且随 reset 清空（下一局不残留旧值）。
        let mut queue = FrameQueue::new(DEFAULT_FRAME_QUEUE_CAPACITY).unwrap();
        queue.record_session_dimensions(1921, 1081, 1920, 1080);
        let rejection = "GpuConversionFailure: GPU NV12 texture creation failed".to_string();
        queue.record_hardware_rejection(rejection.clone());
        let status = queue.status(false, false);
        assert_eq!(status.capture_width, Some(1921));
        assert_eq!(status.capture_height, Some(1081));
        assert_eq!(status.encode_width, Some(1920));
        assert_eq!(status.encode_height, Some(1080));
        assert_eq!(
            status.last_hardware_rejection.as_deref(),
            Some(rejection.as_str())
        );
        queue.reset();
        let status = queue.status(false, false);
        assert_eq!(status.capture_width, None);
        assert_eq!(status.encode_height, None);
        assert!(status.last_hardware_rejection.is_none());
    }

    #[test]
    fn force_software_encoder_env_selects_software_path() {
        use windows::Win32::Foundation::HMODULE;
        use windows::Win32::Graphics::Direct3D::{D3D_DRIVER_TYPE_WARP, D3D_FEATURE_LEVEL_11_0};
        use windows::Win32::Graphics::Direct3D11::{
            D3D11CreateDevice, ID3D11Device, ID3D11DeviceContext, D3D11_SDK_VERSION,
        };

        // 只读覆盖环境变量是本测试的私有开关，没有其他测试读取它；
        // 正常路径（无该变量）仍由真实硬件枚举驱动，见其余 smoke 测试。
        std::env::set_var("AIMING_COOKIE_FORCE_SOFTWARE_ENCODER", "1");
        let result = {
            let mut device: Option<ID3D11Device> = None;
            let mut context: Option<ID3D11DeviceContext> = None;
            unsafe {
                D3D11CreateDevice(
                    None,
                    D3D_DRIVER_TYPE_WARP,
                    HMODULE::default(),
                    windows::Win32::Graphics::Direct3D11::D3D11_CREATE_DEVICE_FLAG(0),
                    Some(&[D3D_FEATURE_LEVEL_11_0]),
                    D3D11_SDK_VERSION,
                    Some(&mut device),
                    None,
                    Some(&mut context),
                )
            }
            .expect("WARP D3D11 device should initialize");
            let device = device.expect("D3D11 device should be returned");
            let context = context.expect("D3D11 context should be returned");
            let queue = Arc::new(Mutex::new(
                FrameQueue::new(DEFAULT_FRAME_QUEUE_CAPACITY).unwrap(),
            ));
            AutomaticH264Encoder::new(&device, &context, 320, 240, 320, 240, queue)
        };
        std::env::remove_var("AIMING_COOKIE_FORCE_SOFTWARE_ENCODER");
        let encoder = result.expect("forced software encoder should initialize");
        assert!(matches!(encoder, AutomaticH264Encoder::Software(_)));
    }

    #[cfg(windows)]
    #[test]
    fn software_input_downsampling_scales_with_frame_backlog() {
        // 60fps 保留时间戳（~166_667ns 步进）：零积压时下采样到 ~30fps；
        // 积压 3 帧时接受间隔放大 4 倍，包间隔贴住可持续节奏。
        let mut last: Option<i64> = None;
        let mut accepted = 0;
        for step in 0..60 {
            let pts = (step as i64) * 10_000_000 / 60;
            if software_input_accepted(last, pts, SOFTWARE_INPUT_INTERVAL_100NS) {
                accepted += 1;
                last = Some(pts);
            }
        }
        assert_eq!(accepted, 30);

        let mut last: Option<i64> = None;
        let mut accepted = 0;
        let scaled = SOFTWARE_INPUT_INTERVAL_100NS * 4;
        for step in 0..60 {
            let pts = (step as i64) * 10_000_000 / 60;
            if software_input_accepted(last, pts, scaled) {
                accepted += 1;
                last = Some(pts);
            }
        }
        // 4× 间隔 ≈ 133ms ≈ 60fps 输入的 8 帧步长：60 帧接受 8 个。
        assert_eq!(accepted, 8);
        // 采样时长与基准采样间隔一致，MP4 stts 由 mux 按真实间隔吸收；
        // 基准下采样间隔必须远低于导出 CoverageGap 容差。
        assert_eq!(SOFTWARE_FRAME_DURATION_100NS, SOFTWARE_INPUT_INTERVAL_100NS);
        const { assert!(SOFTWARE_INPUT_INTERVAL_100NS < REPLAY_TOLERATED_GAP_100NS) };
    }

    fn start_failure_snapshot_fixture(
        adapter_attempts: Vec<WgcAdapterAttempt>,
    ) -> StartFailureSnapshot {
        StartFailureSnapshot {
            error:
                "default adapter path: CreateCaptureSession failed; enumerated adapters: tried: ..."
                    .to_string(),
            at_utc_ms: 1_760_000_000_000,
            retry_mode: "wide",
            force_adapter_retry: false,
            simulate_default_device_failure: false,
            adapter_attempts,
            dxgi_adapters: Vec::new(),
        }
    }

    #[cfg(windows)]
    #[test]
    fn wgc_step_retry_predicate_splits_by_mode() {
        // 28000 加固触发面：同一根因在预览版会以多种 HRESULT/NTSTATUS 形态
        // 出现。strict=1.3.3 白名单行为；wide 只放宽会话创建两步，窗口身份
        // 与尺寸校验两模式一致恒 Fatal。
        let make_win_error = |code: u32| {
            WgcStepError::Win(windows::core::Error::new(
                windows::core::HRESULT(code as i32),
                "synthetic start-chain error",
            ))
        };
        let invalid_arg = make_win_error(0x8007_0057);
        let nt_invalid_parameter = make_win_error(0xC000_000D);
        let unexpected = make_win_error(0x8007_0005); // 非白名单 HRESULT
        let msg_error = WgcStepError::Msg("capture window has an invalid size".to_string());

        let session_step = WGC_STEP_CREATE_CAPTURE_SESSION;
        let pool_step = WGC_STEP_CREATE_FREE_THREADED;
        let window_step = "CreateForWindow";

        // 两码两模式同过（CreateCaptureSession 步骤）。
        for error in [&invalid_arg, &nt_invalid_parameter] {
            assert!(wgc_step_error_is_session_start_retryable(
                session_step,
                error,
                WgcRetryMode::Strict
            ));
            assert!(wgc_step_error_is_session_start_retryable(
                session_step,
                error,
                WgcRetryMode::Wide
            ));
        }
        // 同步骤的未知 HRESULT：strict 终态（1.3.3 行为）/ wide 换卡重试。
        assert!(!wgc_step_error_is_session_start_retryable(
            session_step,
            &unexpected,
            WgcRetryMode::Strict
        ));
        assert!(wgc_step_error_is_session_start_retryable(
            session_step,
            &unexpected,
            WgcRetryMode::Wide
        ));
        // wide 同样覆盖 CreateFreeThreaded 步骤。
        assert!(wgc_step_error_is_session_start_retryable(
            pool_step,
            &unexpected,
            WgcRetryMode::Wide
        ));
        // CreateForWindow（换卡无解）：wide 恒 Fatal；strict 沿用 1.3.3
        // 白名单语义——E_INVALIDARG 族在任意步骤都重试，历史行为原样保留。
        assert!(wgc_step_error_is_session_start_retryable(
            window_step,
            &invalid_arg,
            WgcRetryMode::Strict
        ));
        assert!(!wgc_step_error_is_session_start_retryable(
            window_step,
            &invalid_arg,
            WgcRetryMode::Wide
        ));
        // 窗口尺寸校验等 Msg 类错误两模式都不重试。
        for mode in [WgcRetryMode::Strict, WgcRetryMode::Wide] {
            assert!(!wgc_step_error_is_session_start_retryable(
                session_step,
                &msg_error,
                mode
            ));
        }
    }

    #[test]
    fn start_failure_snapshot_projects_into_status_and_survives_queue_reset() {
        let state = WindowCaptureState::new(DEFAULT_FRAME_QUEUE_CAPACITY).unwrap();
        // 无失败：错误/时刻/尝试均为空，嫌疑为否（retryMode 回落当前治理
        // 模式，值依赖环境变量，不在空态断言）。
        let status = state.status();
        assert!(status.last_start_error.is_none());
        assert!(status.last_start_error_at_utc_ms.is_none());
        assert!(status.last_adapter_attempts.is_empty());
        assert!(!status.gpu_driver_suspect);

        // 失败写入：结构化尝试进 status。
        state.set_last_start_failure_for_test(Some(start_failure_snapshot_fixture(vec![
            WgcAdapterAttempt {
                adapter: "NVIDIA GeForce RTX 4070".to_string(),
                luid: Some("luid:0000000000000c8a".to_string()),
                step: "CreateCaptureSession".to_string(),
                message: "CreateCaptureSession failed (The parameter is incorrect)".to_string(),
            },
        ])));
        // start 入口会 reset 队列；快照挂在 FrameQueue 之外必须幸存。
        state.queue.lock().expect("queue mutex").reset();
        let status = state.status();
        assert_eq!(
            status.last_start_error.as_deref(),
            Some(
                "default adapter path: CreateCaptureSession failed; enumerated adapters: tried: ..."
            )
        );
        assert_eq!(status.last_start_error_at_utc_ms, Some(1_760_000_000_000));
        assert_eq!(status.last_adapter_attempts.len(), 1);
        assert_eq!(
            status.last_adapter_attempts[0].adapter,
            "NVIDIA GeForce RTX 4070"
        );
        assert_eq!(status.retry_mode, "wide");
        // 有硬件尝试（而非全部跳过）不算无驱动嫌疑。
        assert!(!status.gpu_driver_suspect);

        // serde 投影字段为 camelCase（前端与诊断包同一契约）。
        let json = serde_json::to_string(&status).unwrap();
        assert!(json.contains("\"lastStartError\""), "{json}");
        assert!(json.contains("\"lastStartErrorAtUtcMs\""), "{json}");
        assert!(json.contains("\"lastAdapterAttempts\""), "{json}");
        assert!(json.contains("\"gpuDriverSuspect\""), "{json}");
        assert!(json.contains("\"retryMode\""), "{json}");
        let snapshot_json =
            serde_json::to_string(&state.last_start_failure_snapshot().unwrap()).unwrap();
        assert!(snapshot_json.contains("\"atUtcMs\""), "{snapshot_json}");
        assert!(
            snapshot_json.contains("\"forceAdapterRetry\""),
            "{snapshot_json}"
        );
        assert!(
            snapshot_json.contains("\"simulateDefaultDeviceFailure\""),
            "{snapshot_json}"
        );

        // 成功 start 清除快照。
        state.clear_start_failure();
        let status = state.status();
        assert!(status.last_start_error.is_none());
        assert!(status.last_adapter_attempts.is_empty());
    }

    #[test]
    fn gpu_driver_suspect_matches_basic_display_adapter_names_and_all_skipped_snapshot() {
        // WMI 名单侧：中英两形态都算无驱动嫌疑（英文不区分大小写）。
        assert!(gpu_driver_suspect(
            &["Microsoft 基本显示适配器".to_string()],
            None
        ));
        assert!(gpu_driver_suspect(
            &["Microsoft Basic Display Adapter".to_string()],
            None
        ));
        assert!(gpu_driver_suspect(
            &["Microsoft basic display adapter".to_string()],
            None
        ));
        assert!(gpu_driver_suspect(
            &[
                "NVIDIA GeForce RTX 4070".to_string(),
                "Microsoft Basic Display Adapter".to_string(),
            ],
            None
        ));
        assert!(!gpu_driver_suspect(
            &["NVIDIA GeForce RTX 4070".to_string()],
            None
        ));
        assert!(!gpu_driver_suspect(&[], None));

        // 快照侧：全部适配器被 software/no-driver 跳过才嫌疑；取或语义。
        let skip = |name: &str| WgcAdapterAttempt {
            adapter: name.to_string(),
            luid: Some("luid:0000000000000001".to_string()),
            step: "skip".to_string(),
            message: "skipped (software/no-driver adapter)".to_string(),
        };
        let all_skipped = start_failure_snapshot_fixture(vec![
            skip("Microsoft 基本显示适配器"),
            skip("Microsoft Basic Display Adapter"),
        ]);
        assert!(gpu_driver_suspect(&[], Some(&all_skipped)));
        assert!(gpu_driver_suspect(
            &["NVIDIA GeForce RTX 4070".to_string()],
            Some(&all_skipped)
        ));
        // 有硬件尝试（即使失败）或空尝试清单都不算。
        let attempted = start_failure_snapshot_fixture(vec![WgcAdapterAttempt {
            adapter: "NVIDIA GeForce RTX 4070".to_string(),
            luid: Some("luid:0000000000000002".to_string()),
            step: "CreateCaptureSession".to_string(),
            message: "CreateCaptureSession failed (The parameter is incorrect)".to_string(),
        }]);
        assert!(!gpu_driver_suspect(&[], Some(&attempted)));
        let empty = start_failure_snapshot_fixture(Vec::new());
        assert!(!gpu_driver_suspect(&[], Some(&empty)));
    }

    #[test]
    fn wgc_governance_env_switches_parse() {
        // 治理模式：缺省 wide；显式 strict 才收紧；未知取值回落 wide。
        std::env::remove_var("AIMING_COOKIE_WGC_RETRY_MODE");
        assert_eq!(wgc_retry_mode_from_env(), WgcRetryMode::Wide);
        std::env::set_var("AIMING_COOKIE_WGC_RETRY_MODE", "strict");
        assert_eq!(wgc_retry_mode_from_env(), WgcRetryMode::Strict);
        std::env::set_var("AIMING_COOKIE_WGC_RETRY_MODE", "bogus");
        assert_eq!(wgc_retry_mode_from_env(), WgcRetryMode::Wide);
        std::env::remove_var("AIMING_COOKIE_WGC_RETRY_MODE");

        // 注入开关：仅 "1" 激活，缺省关闭；用完即清不泄漏给并行测试。
        assert!(!wgc_force_adapter_retry_enabled());
        assert!(!wgc_simulate_default_device_failure_enabled());
        std::env::set_var("AIMING_COOKIE_FORCE_ADAPTER_RETRY", "1");
        std::env::set_var("AIMING_COOKIE_SIMULATE_DEFAULT_DEVICE_FAILURE", "1");
        assert!(wgc_force_adapter_retry_enabled());
        assert!(wgc_simulate_default_device_failure_enabled());
        std::env::remove_var("AIMING_COOKIE_FORCE_ADAPTER_RETRY");
        std::env::remove_var("AIMING_COOKIE_SIMULATE_DEFAULT_DEVICE_FAILURE");
    }
}
