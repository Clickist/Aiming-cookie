# Aiming Cookie 桌面打包调研（Tauri 2.0 推荐）

> 2026-07-10 · 桌面 hybrid 架构（PRD §5.1 / IA redesign spec §2.3）的**壳选型 + 打包工程**方案。独立于 IA redesign，是 desktop-hybrid 的工程层。方向锚以 PRD 为准。
>
> 数据通过 gemini-grounding-search 于 2026-07-10 获取，标注 ✅（有 sources）/ ⚠️（通用知识，待核实）。

## 1. 选型结论

**推荐 Tauri 2.0**（待点点拍板）。对标 Aiming Cookie 场景的理由：

- **CV + agent 是 CPU 密集，跑在 Python sidecar**，壳只是轻载体 → 不需要 Electron 的重 Node 生态
- **体积 2-10MB**（Electron 80-200MB+）：GitHub Releases 分发友好，用户下载快
- **内存 30-60MB**（Electron 150-380MB+）：后台跑不挤占游戏/系统资源（点点用户边打 KovaaK's 边用）
- Tauri 2.0 已成熟，且支持未来移动端扩展
- **顾虑**：壳层是 Rust，但代码量极少（配置 + sidecar spawn + 更新），主逻辑在 Python sidecar 和前端，可接受

**Electron 的适用场景**（若点点倾向）：前端重度依赖 Node/SSR、要求跨平台渲染绝对一致、团队完全不想碰 Rust。Aiming Cookie 不在这些场景，故不推荐。

---

## 2. Tauri 2.0 vs Electron 对比 ✅

| 指标 | Tauri 2.0 | Electron |
|---|---|---|
| 安装包 | **2-10MB** | 80-200MB+ |
| 空闲内存 | **30-60MB** | 150-380MB+ |
| 冷启动 | **0.3-1s** | 1.5-3s+ |
| 架构 | Rust + OS 原生 WebView | Node.js + 内置 Chromium |
| 跨平台渲染一致性 | 依赖系统 WebView（有差异） | **绝对一致**（同 Chromium） |
| 生态成熟度 | 成长中（2.0 稳） | **极成熟** |
| 移动端 | **2.0 支持 iOS/Android** | 无 |

sources: openreplay.com / pkgpulse.com / reddit.com 等

---

## 3. 架构

```
Tauri 壳（Rust，极薄：配置 + sidecar 管理 + 更新）
├── 前端：Next.js 静态导出（output: 'export'）→ Tauri 加载本地 build/out
├── Python sidecar（PyInstaller 打包的独立可执行）
│   └── pan_tracker / flicking / track 指标计算 + coach agent 框架（tool-use loop）
└── 云端（HTTPS）：调后端 API（鉴权 / LLM 代理 / 数据同步）
```

---

## 4. Python sidecar 打包 ✅（sources: tauri.app 官方）

- **打包**：`pyinstaller --onefile`（或 `--onedir`）把 `kovaak_tracker` 入口打成独立可执行
- **平台三元组命名**（必需）：如 `aiming-cookie-sidecar-x86_64-pc-windows-msvc.exe`；用 `rustc -vV` 查当前平台三元组
- **配置**：`src-tauri/tauri.conf.json` → `bundle.externalBin`（路径相对 `src-tauri`）
- **权限**：`src-tauri/capabilities/default.json` → `shell:allow-execute`
- **IPC 方案**（二选一）：
  - **STDIN/STDOUT 流式**（推荐）：`tauri-plugin-shell` 的 `app.shell().sidecar(name).spawn()`，Rust 侧 `rx` 监听 stdout、`child.write()` 写 stdin；前端用 `@tauri-apps/api/event` 收 Rust emit 的事件。无需端口、更安全
  - **本地 HTTP/WS**：sidecar 监听 `localhost:PORT`，适合复杂双向 API（分析进度、视频帧）

> ⚠️ **体积风险**：`opencv-contrib-python` + `numpy` + `scipy` + `pandas` + `plotly` 用 `--onefile` 打包可能 **200-300MB**。实测建议：先 `--onedir` 看真实体积，必要时拆分（plotly 图表可改前端渲染，sidecar 只算指标输出 JSON）。

---

## 5. Next.js 进壳

- `next.config.js` 设 `output: 'export'` 静态导出 → Tauri `frontendDist` 指向 `out/`
- 视频流（`GET /api/sessions/{id}/video`）、coach chat 等都走 **HTTPS 调云端 API**，前端纯客户端请求
- ⚠️ **实现期风险**：现 `webapp/frontend/` 用了动态路由 `app/sessions/[id]/...`，静态导出需改客户端路由或 `generateStaticParams`（空 + 运行时解析）。IA 实现计划里要处理

---

## 6. 自动更新 + 跨平台签名 ✅

| 平台 | 证书 | 成本 | 工具 |
|---|---|---|---|
| macOS | Apple Developer Program + Developer ID | **$99/年** | `codesign` + `xcrun notarytool` 公证（必需，否则 Gatekeeper 拦） |
| Windows | OV 代码签名证书 | **$100-150/年**（EV $300-600） | `signtool.exe` Authenticode；可选 Azure Key Vault 云签名 ~$10/月 |

**Tauri Updater**：Ed25519 密钥对（`minisign` 格式）签名更新包生成 `.sig`，`tauri.conf.json` 配 updater 插件，发布 `latest.json` 清单（版本号 + 下载 URL + 签名），应用内公钥验签。

> ⚠️ **Windows SmartScreen 声誉**：新 OV 证书需靠用户下载量积累声誉，前期仍弹警告；EV 证书即时声誉但贵。**v1 策略可选**：先无签名分发（接受 SmartScreen 警告 + 用户手动信任），用户量起来后再买证书。

---

## 7. 成本（额外于 deployment-guide）

- 签名证书：**~$200-250/年**（macOS $99 + Windows OV $100-150）；v1 可推迟
- GitHub Actions 构建矩阵：免费额度内

---

## 8. 待点点决策

1. **Tauri vs Electron**（推荐 Tauri 2.0）
2. **首版平台**：建议先只打 **Windows**（点点主力 + KovaaK's 生态），macOS 后续
3. **签名**：v1 就买证书，还是先无签名分发积累声誉后再签
4. **sidecar 打包**：`--onefile`（单文件，体积大）vs `--onedir`（目录，体积小但分发多文件）——实测体积后定

---

## 关联

- 架构前提：`docs/PRD.md` §5.1 / §9；IA redesign spec §2.3
- 上线部署：`docs/deployment-guide.md`
