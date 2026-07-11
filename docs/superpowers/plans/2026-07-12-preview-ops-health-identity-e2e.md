# 预览可运营：Health + 身份边界 + Browser E2E 骨架 — 可执行施工图

> **状态：completed（2026-07-12，Task 1–4）。**
> **白话目标**：服务活着能查；预览环境不靠浏览器伪造用户；有一条浏览器自动点通主路径。

## Frozen

1. `GET /healthz`：进程活着 → 200。
2. `GET /readyz`：DB 可连 +（若配置了 sidecar）sidecar health 可达 → 200，否则 503。
3. **身份（预览）**：
   - 默认 dev：仍可 `X-User-Id`（本地开发）。
   - `TRUST_PROXY_USER=1` 时：只信任 `X-Forwarded-User`（或 `Remote-User`），**忽略**客户端乱填的 `X-User-Id`。
   - 文档写清：生产/预览前面必须有 VPN/SSO 反代。
4. Browser E2E：**Playwright** 最小一条（或项目已有工具则复用）：打开首页不崩；有 fixture 时再加上传（可 mock API）。不阻塞无浏览器 CI 的开发机——测试标 skip if no browser。
5. 不做完整 Clerk/OAuth。

## Tasks

### Task 1 — healthz / readyz

**Allowed:** `routes.py` or `health.py`, `main` app include, tests

### Task 2 — 可信用户头模式

**Allowed:** config, auth helper, routes 取 user 的唯一入口, tests（伪造 X-User-Id 在 trust 模式下无效）

### Task 3 — Playwright 骨架 + 文档

**Allowed:** `webapp/frontend` e2e 或 `webapp/tests/e2e_browser`, package devDep（需点点接受加依赖时再装）, PROGRESS, deployment-guide 一小段

### Task 4 — 回归

**Verify:** pytest + tsc；browser 测有则跑

---

完成定义：readyz 可给部署探活；trust 模式测过；至少 1 条 browser 骨架测试文件存在。
