# 发版 SOP（RELEASE）

本文件是**打全量安装包并发布**的唯一权威流程。每步一条可直接复制的命令；坑收进文末速查表，一行一条。
单机开发、启动、测试命令见 [`DEVELOPMENT.md`](DEVELOPMENT.md)，本文不重复。

历史坑的完整考古在 ops-hub 与 agent 记忆里；**本文只维护当前正确的做法**——发现新坑时更新速查表，而不是追加段落。

## 前置条件（一次性，坏了才需要动）

| 依赖 | 位置 / 命令 | 说明 |
|---|---|---|
| Updater 签名密钥 | `~/.tauri-keys/aiming-cookie.key` | minisign 私钥，永不入仓库；丢失=换钥+全量重装 |
| wrangler | `npx wrangler whoami` | R2 上传用，OAuth 登录点点账号 |
| gh CLI | `gh auth status` | GH Release 用 |
| push 代理 | `http://127.0.0.1:7897` | 直连 push 报 Empty reply 时加 `-c http.proxy=… -c https.proxy=…` |

## 流程总览

```
① bump+changelog → ② release-build.ps1 → ③ release-install.ps1
→ ④ 本机逐项验证 → ⑤ 发布（外发，手动） → ⑥ 线上三验
```

①–④ 全部通过后才允许执行 ⑤⑤–⑥ 是外发动作，逐条手动执行，保留人肉闸门。

## ① 版本与 changelog bump

```powershell
# 四文件版本号：webapp/frontend/package.json、src-tauri/tauri.conf.json、
# src-tauri/Cargo.toml、src-tauri/Cargo.lock（lock 里 aiming-cookie-desktop 的 version 行）
# design/opendesign-landing/changelog.json 顶部插新版块：
#   - 首条 type 必须 "new"（release-notice.test.ts 断言 items[0].type=="new" 且 items>=3）
cd webapp/frontend; npm run sync-changelog; cd ../..
```

提交为 `chore(release): X.Y.Z bump + changelog（<条目摘要>）`。

## ② 打包（一键）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\release-build.ps1
# 纯前端改动想省 3 分钟：加 -SkipRuntimeRebuild（改过 webapp/backend/** 或 telemetry_capture/** 时禁用）
```

脚本内部分两段：**A 段**（读版本 → 钉 MSVC 工具链 → 清签名环境变量 → 杀全家进程 → 重建 PyInstaller runtime → tauri NSIS，`-Unsigned` 的签名步 exit 1 是**设计内行为**，exe 完整产出）→ **B 段**（下划线名副本 → bash 补签 `scripts/resign.sh` → 重算 sha256 → **产物自检（sig 新鲜度/尺寸、版本一致，缺件即 throw）** → 生成 latest.json）。签名必须经 bash：空字符串环境变量只有 bash 能设置，PowerShell/cmd 赋空串等于删变量，tauri 会在无终端的后台运行里挂死等密码。产物不齐不会走到最后，结尾 `== BUILD OK ==` 才算成功。

## ③ 本机安装（一键）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\release-install.ps1
# 默认装 nsis 目录最新下划线名包到 %LOCALAPPDATA%\Aiming Cookie，装完自动验：
# 主 exe 版本一致 / runtime exe 时间戳已更新 / 注册表 InstallLocation 未被带偏
```

## ④ 本机逐项验证

按本次改动清单逐项真机走查。常用配方：

- **触发分析**：读 `E:\ACData\desktop-runtime.json` 拿 `python_base_url`+`python_token`（每次启动刷新），`POST {base}/api/kovaak-runs/{id}/analyze`，body `{"force":true}`，头 `X-Aiming-Cookie-Desktop-Token: <token>`+`X-User-Id: desktop-local`；轮询 `GET /api/sessions` 看状态（别用 `/sessions/{id}`，按 owner 鉴权易 forbidden）。
- **真机 UI 走查**：启动前设 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS="--remote-debugging-port=9223"`，playwright-core `connectOverCDP("http://127.0.0.1:9223")`。
- **Coach sidecar API**：端口随机（扫 LISTENING 或前端网络面板），调用必须带 `X-User-Id: desktop-local` **和** `X-Aiming-Cookie-Desktop-Token`。
- **诊断包字段**（采集/附着问题先看这里）：`desktop_collect_capture_diagnostics`（Tauri invoke）→ `schemaVersion` / `windowCapture.resizeEvents` / `telemetryCaptureSnapshot.attach_causes`。

## ⑤ 发布（外发，逐条手动）

```powershell
# 1) R2 三件套（桶 aiming-cookie-downloads，域名 dl.aimingcookie.com）
cd webapp\frontend\src-tauri\target\release\bundle\nsis
npx wrangler r2 object put "aiming-cookie-downloads/Aiming_Cookie_X.Y.Z_x64-setup.exe" --file "Aiming_Cookie_X.Y.Z_x64-setup.exe" --remote --content-type application/octet-stream
npx wrangler r2 object put "aiming-cookie-downloads/Aiming_Cookie_X.Y.Z_x64-setup.exe.sig" --file "Aiming_Cookie_X.Y.Z_x64-setup.exe.sig" --remote --content-type application/octet-stream
# 2) —— latest.json 必须最后传：它一上线老客户端就弹更新 ——
npx wrangler r2 object put "aiming-cookie-downloads/latest.json" --file "latest.json" --remote --content-type application/json

# 3) 落地页：index.html 与 en/index.html 各 2 处下载链接 sed 到新版本
#    （链接不在 changelog.json 里），然后渲染更新动态页：
node design/opendesign-landing/scripts/render-changelog.mjs
git add design/opendesign-landing/ && git commit -m "docs(landing): 下载链接 X.Y.Z + changelog 页渲染"
git -c http.proxy=http://127.0.0.1:7897 -c https.proxy=http://127.0.0.1:7897 push origin main   # CF Pages ~2 分钟自动部署

# 4) tag + GH Release（走代理）
git tag vX.Y.Z && git -c http.proxy=http://127.0.0.1:7897 -c https.proxy=http://127.0.0.1:7897 push origin vX.Y.Z
gh release create vX.Y.Z --title "Aiming Cookie X.Y.Z" --notes-file <notes.md> "webapp/frontend/src-tauri/target/release/bundle/nsis/Aiming_Cookie_X.Y.Z_x64-setup.exe"
```

## ⑥ 线上三验

```powershell
curl -s https://dl.aimingcookie.com/latest.json | grep version          # = X.Y.Z
curl -sI https://dl.aimingcookie.com/Aiming_Cookie_X.Y.Z_x64-setup.exe | grep -i "^HTTP"   # 200
curl -sL https://www.aimingcookie.com/ | grep -o "Aiming_Cookie_X.Y.Z_x64-setup.exe"       # 落地页已部署
```

## 坑速查表

| 坑 | 一句话解法 |
|---|---|
| 打包 os error 32 / exe 锁 | 杀 `aiming-cookie-desktop` / `-runtime` / `-coach-sidecar` 全家（脚本已内置） |
| 改了 backend/telemetry 但包里没生效 | runtime 是构建产物，必须重建重嵌（脚本默认做，别乱 -Skip） |
| 签名挂在 "Decrypting updater signing key" | 空字符串环境变量只有 bash 能设（PS/cmd 赋空串=删变量）；**永远走两段式**：`-Unsigned` 打包（签名步 exit 1 属预期）+ `scripts/resign.sh` 补签（release-build 已内置，sig 绑文件名——签下划线名副本） |
| cargo 报 GNU 工具链 ld/dlltool 错 | `RUSTUP_TOOLCHAIN=stable-x86_64-pc-windows-msvc`（脚本已内置） |
| 从 bash 调 ps1 传中文路径 | 不传 `-RepoRoot`，脚本从自身位置推导仓库根（release-build 已内置） |
| NSIS 弹交互向导不静默 | 别在 Git Bash 里裸跑 exe；用 release-install.ps1（PowerShell Start-Process） |
| 覆盖安装装进了 temp/旧目录 | `/S` + `/D=<目标>` 且 `/D` 放最后、值不带引号；装后核对注册表 InstallLocation（脚本已内置） |
| 同版本覆盖装验不出来 | 看主 exe `aiming-cookie-desktop.exe` ProductVersion + runtime exe LastWriteTime（脚本已内置） |
| ps1 中文注释解析炸 | 脚本必须 UTF-8 with BOM（PS 5.1 按 GBK 读无 BOM 文件） |
| 后台跑构建/全量测试并行 | 抢 CPU 假挂死 40+ 分钟；构建前等测试跑完 |
| 本机 `~/.curlrc` 报 `"http2" is unknown` | 已知噪音，请求照常成功，勿当失败 |
| 冒烟测试残留注册表 | `test-packaged-runtime.ps1` 曾把 InstallLocation 带偏到 temp；显式 `/D=` 可免疫，异常时核对并纠正注册表 |
