//! 采集链路线程让路：AC 在后台打 KovaaK's 时，采集线程（WGC worker /
//! MP4 mux / replay 导出）此前与前台游戏以同等优先级抢 CPU。这里把线程
//! 降到 BELOW_NORMAL 并挂 MMCSS "Capture" 任务画像，让调度器在游戏与
//! 采集之间明确偏向游戏。任何一步失败只落 dlog，绝不阻断线程启动——
//! 让路是优化，拿不到就退回默认调度，录像行为不变。

/// WGC worker / MP4 writer / replay 导出线程：降优先级 + 挂 MMCSS。
pub fn apply_capture_thread_priority() {
    #[cfg(windows)]
    {
        use windows::Win32::System::Threading::{
            GetCurrentThread, SetThreadPriority, THREAD_PRIORITY_BELOW_NORMAL,
        };
        unsafe {
            if SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL).is_err() {
                crate::dlog!("[thread-priority] SetThreadPriority(BELOW_NORMAL) failed");
            }
        }
        apply_mmcss_capture_characteristics();
    }
}

/// Raw input 线程：只挂 MMCSS、不降优先级——它的 SyncSender 满即丢，
/// 数据完整性依赖泵速，降优先级会直接放大丢包（已拍板收窄）。
pub fn apply_mmcss_capture_characteristics() {
    #[cfg(windows)]
    {
        use windows::Win32::System::Threading::AvSetMmThreadCharacteristicsW;
        let mut task_index: u32 = 0;
        unsafe {
            if AvSetMmThreadCharacteristicsW(windows::core::w!("Capture"), &mut task_index).is_err()
            {
                crate::dlog!(
                    "[thread-priority] MMCSS AvSetMmThreadCharacteristicsW(Capture) failed"
                );
            }
        }
    }
}

#[cfg(test)]
mod tests {
    // 测试环境无游戏/无 MMCSS 也必须不 panic：失败路径只落 dlog。
    #[test]
    fn apply_capture_thread_priority_does_not_panic() {
        super::apply_capture_thread_priority();
    }

    #[test]
    fn apply_mmcss_capture_characteristics_does_not_panic() {
        super::apply_mmcss_capture_characteristics();
    }
}
