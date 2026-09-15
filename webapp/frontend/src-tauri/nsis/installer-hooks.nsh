; Aiming Cookie 自定义安装钩子 + 界面文案
; 通过 tauri.conf.json 的 bundle > windows > nsis > installerHooks 注入官方模板。
; 模板版本对应 @tauri-apps/cli 2.11.4；升级 CLI 时需核对注入点仍在。

; ---------- 界面文案 ----------
!define MUI_WELCOMEPAGE_TITLE "欢迎安装 Aiming Cookie"
!define MUI_WELCOMEPAGE_TEXT "Aiming Cookie 是你的 AI 瞄准教练：自动采集训练与对局，逐帧复盘，给出马上能用的改进建议。\r\n\r\n点击「下一步」继续。"
!define MUI_FINISHPAGE_TITLE "安装完成"
!define MUI_FINISHPAGE_TEXT "Aiming Cookie 已就绪。你只管打枪，复盘交给它。\r\n\r\n点击「完成」退出安装向导。"
!define MUI_FINISHPAGE_RUN_TEXT "运行 Aiming Cookie"
!define MUI_UNCONFIRMPAGE_TEXT_TOP "即将从电脑移除 Aiming Cookie。你的训练数据与分析记录默认保留。"

; ---------- 进程清理 ----------
; 主程序退出后，两个后台子进程（Python 运行时与 Coach sidecar）还有短暂
; 存活窗口（stdin EOF 自杀协议），期间锁着 runtime\ 下的 DLL，安装器覆盖
; 时会报 "Error opening file for writing"。顺序：先杀主程序（断掉重启监
; 督者），再循环杀子进程，直到 taskkill 报无匹配进程（退出码 128）。
; taskkill 退出码：0=已终止，128=无匹配进程，其余=失败继续重试。

!macro AC_KILL_WAIT IMAGE ID
  !define UniqueID ${ID}
  Push $R0
  Push $R1
  StrCpy $R1 0
ac_kill_retry_${UniqueID}:
  nsExec::Exec 'taskkill /F /T /IM "${IMAGE}"'
  Pop $R0
  IntCmp $R0 128 ac_kill_done_${UniqueID} ac_kill_wait_${UniqueID} ac_kill_wait_${UniqueID}
ac_kill_wait_${UniqueID}:
  Sleep 250
  IntOp $R1 $R1 + 1
  IntCmp $R1 12 ac_kill_done_${UniqueID} ac_kill_retry_${UniqueID} ac_kill_done_${UniqueID}
ac_kill_done_${UniqueID}:
  Pop $R1
  Pop $R0
  !undef UniqueID
!macroend

!macro NSIS_HOOK_PREINSTALL
  DetailPrint "正在关闭 Aiming Cookie 后台进程…"
  !insertmacro AC_KILL_WAIT "${MAINBINARYNAME}.exe" PREINSTALL_MAIN
  !insertmacro AC_KILL_WAIT "aiming-cookie-runtime.exe" PREINSTALL_RUNTIME
  !insertmacro AC_KILL_WAIT "coach-sidecar.exe" PREINSTALL_COACH
  Sleep 500
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  DetailPrint "正在关闭 Aiming Cookie 后台进程…"
  !insertmacro AC_KILL_WAIT "${MAINBINARYNAME}.exe" UNINSTALL_MAIN
  !insertmacro AC_KILL_WAIT "aiming-cookie-runtime.exe" UNINSTALL_RUNTIME
  !insertmacro AC_KILL_WAIT "coach-sidecar.exe" UNINSTALL_COACH
  Sleep 500
!macroend
