// 桌面令牌闸门的测试口径：sidecar 对 healthz 之外的路由 fail-closed（env 未
// 配置一律 401），直接起 server 的测试文件须 import 本模块注入 env，并让请求
// helper 附带同一 token 头（X-Aiming-Cookie-Desktop-Token）。生产 token 由
// Tauri 每次启动生成，绝不使用这里的常量。
export const DESKTOP_TEST_TOKEN = "coach-sidecar-desktop-test-token";

process.env.AIMING_COOKIE_DESKTOP_TOKEN ??= DESKTOP_TEST_TOKEN;
