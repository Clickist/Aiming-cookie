; Aiming Cookie 自定义安装钩子 + 界面文案
; 通过 tauri.conf.json 的 bundle > windows > nsis > installerHooks 注入官方模板。
; 模板版本对应 @tauri-apps/cli 2.11.4；升级 CLI 时需核对注入点仍在。

; ---------- 界面文案（中英双语，跟随系统语言自动选择） ----------
; LangString 用数字语言 ID（2052=简体中文，1033=English）：本文件在官方模板的
; MUI_LANGUAGE 插入之前被 include，${LANG_*} 常量此刻尚未定义。tauri.conf.json
; 的 languages 首位（SimpChinese）是系统语言不匹配时的回落，与既往行为一致。
LangString AC_WELCOME_TITLE 2052 "欢迎安装 Aiming Cookie"
LangString AC_WELCOME_TITLE 1033 "Welcome to Aiming Cookie"
LangString AC_WELCOME_TEXT 2052 "Aiming Cookie 是你的 AI 瞄准教练：自动采集训练与对局，逐帧复盘，给出马上能用的改进建议。$\r$\n$\r$\n点击「下一步」继续。"
LangString AC_WELCOME_TEXT 1033 "Aiming Cookie is your AI aiming coach: it captures your training and matches automatically, reviews them frame by frame, and turns them into tips you can use right away.$\r$\n$\r$\nClick Next to continue."
LangString AC_FINISH_TITLE 2052 "安装完成"
LangString AC_FINISH_TITLE 1033 "Installation Complete"
LangString AC_FINISH_TEXT 2052 "Aiming Cookie 已就绪。你只管打枪，复盘交给它。$\r$\n$\r$\n点击「完成」退出安装向导。"
LangString AC_FINISH_TEXT 1033 "Aiming Cookie is ready. You just focus on shooting — leave the review to it.$\r$\n$\r$\nClick Finish to close the setup wizard."
LangString AC_FINISH_RUN 2052 "运行 Aiming Cookie"
LangString AC_FINISH_RUN 1033 "Run Aiming Cookie"
LangString AC_UNCONFIRM_TOP 2052 "即将从电脑移除 Aiming Cookie。你的训练数据与分析记录默认保留。"
LangString AC_UNCONFIRM_TOP 1033 "Aiming Cookie will be removed from your computer. Your training data and analysis records are kept by default."
LangString AC_KILLING 2052 "正在关闭 Aiming Cookie 后台进程…"
LangString AC_KILLING 1033 "Closing Aiming Cookie background processes..."
LangString AC_CLOSE_FAILED 2052 "无法关闭 Aiming Cookie，本次安装或卸载已停止。请同意管理员授权，或完全退出 Aiming Cookie 后重试。"
LangString AC_CLOSE_FAILED 1033 "Could not close Aiming Cookie. Installation or uninstallation stopped. Approve the administrator prompt, or fully exit Aiming Cookie and retry."
LangString AC_RUNTIME_CLEAN_FAILED 2052 "无法清理旧版运行环境，本次安装已停止。请完全退出 Aiming Cookie 后重试；若反复失败，请暂时关闭杀毒软件的实时防护。"
LangString AC_RUNTIME_CLEAN_FAILED 1033 "Could not clean up the previous runtime, so installation stopped. Fully exit Aiming Cookie and retry; if it keeps failing, temporarily disable your antivirus real-time protection."

!define MUI_WELCOMEPAGE_TITLE "$(AC_WELCOME_TITLE)"
!define MUI_WELCOMEPAGE_TEXT "$(AC_WELCOME_TEXT)"
!define MUI_FINISHPAGE_TITLE "$(AC_FINISH_TITLE)"
!define MUI_FINISHPAGE_TEXT "$(AC_FINISH_TEXT)"
!define MUI_FINISHPAGE_RUN_TEXT "$(AC_FINISH_RUN)"
!define MUI_UNCONFIRMPAGE_TEXT_TOP "$(AC_UNCONFIRM_TOP)"

; ---------- 进程清理 ----------
; 主程序退出后，两个后台子进程（Python 运行时与 Coach sidecar）还有短暂
; 存活窗口（stdin EOF 自杀协议），期间锁着 runtime\ 下的 DLL，安装器覆盖
; 时会报 "Error opening file for writing"。顺序：先杀主程序（断掉重启监
; 督者），再循环杀子进程，直到进程枚举确认对应镜像已全部退出。
; 是否退出由进程枚举确认；关闭被拒绝时仅对 taskkill 申请管理员权限。

!macro AC_KILL_WAIT IMAGE ID
  !define UniqueID ${ID}
  Push $R0
  Push $R1
  Push $R2
  StrCpy $R1 0
  StrCpy $R2 0
ac_kill_retry_${UniqueID}:
  nsExec::Exec '"$SYSDIR\taskkill.exe" /F /T /IM "${IMAGE}"'
  Pop $R0
  ; taskkill /T can return 128 for a still-running elevated process tree.
  nsis_tauri_utils::FindProcess "${IMAGE}"
  Pop $R0
  StrCmp $R0 1 ac_kill_done_${UniqueID}
  StrCmp $R0 0 0 ac_kill_failed_${UniqueID}
  StrCmp $R2 1 ac_kill_wait_${UniqueID}
  StrCpy $R2 1
  ClearErrors
  ExecShellWait "runas" "$SYSDIR\taskkill.exe" '/F /T /IM "${IMAGE}"' SW_HIDE
  IfErrors ac_kill_failed_${UniqueID}
ac_kill_wait_${UniqueID}:
  Sleep 250
  IntOp $R1 $R1 + 1
  IntCmp $R1 12 ac_kill_failed_${UniqueID} ac_kill_retry_${UniqueID} ac_kill_failed_${UniqueID}
ac_kill_failed_${UniqueID}:
  Pop $R2
  Pop $R1
  Pop $R0
  SetErrorLevel 1
  Abort "$(AC_CLOSE_FAILED)"
ac_kill_done_${UniqueID}:
  Pop $R2
  Pop $R1
  Pop $R0
  !undef UniqueID
!macroend

!macro NSIS_HOOK_PREINSTALL
  DetailPrint "$(AC_KILLING)"
  !insertmacro AC_KILL_WAIT "${MAINBINARYNAME}.exe" PREINSTALL_MAIN
  !insertmacro AC_KILL_WAIT "aiming-cookie-runtime.exe" PREINSTALL_RUNTIME
  !insertmacro AC_KILL_WAIT "coach-sidecar.exe" PREINSTALL_COACH
  Sleep 500
  ; 升级必须整删旧 runtime 目录（1102 报障：1.4.20 起打包排除 pyarrow/PIL/cv2，
  ; 但 NSIS 覆盖安装从不删旧文件，旧版残留的 _internal\pyarrow 目录让
  ; "import pyarrow" 命中空壳 namespace 包，pandas 读 pa.__version__ 崩，
  ; 后端启动即死 → 用户卡在「本地服务未能启动」且自更新通道同时失效）。
  ; 目录是构建产物，不承载用户数据：训练数据在 %APPDATA%\com.aimingcookie.desktop，
  ; 自定义知识库在 DATA_ROOT\knowledge-packs，都不在 $INSTDIR 下。
  ; 删不干净必须中止安装——静默继续会装出一个仍然坏掉的 runtime，正是本次事故形态。
  ; 仅在目录存在时删：RMDir 对不存在的路径也置错误标志，会误杀全新安装；
  ; 目录探测用通配符形式（NSIS 文档口径，裸目录名不可靠）。
  IfFileExists "$INSTDIR\runtime\*.*" 0 ac_runtime_clean_preinstall
  ClearErrors
  RMDir /r "$INSTDIR\runtime"
  IfErrors ac_runtime_stale_preinstall
  Goto ac_runtime_clean_preinstall
ac_runtime_stale_preinstall:
  SetErrorLevel 1
  Abort "$(AC_RUNTIME_CLEAN_FAILED)"
ac_runtime_clean_preinstall:
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  DetailPrint "$(AC_KILLING)"
  !insertmacro AC_KILL_WAIT "${MAINBINARYNAME}.exe" UNINSTALL_MAIN
  !insertmacro AC_KILL_WAIT "aiming-cookie-runtime.exe" UNINSTALL_RUNTIME
  !insertmacro AC_KILL_WAIT "coach-sidecar.exe" UNINSTALL_COACH
  Sleep 500
!macroend
