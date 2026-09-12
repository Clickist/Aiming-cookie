"use client";

import "../ui/theme.css";

import { useEffect } from "react";

import { Button, ErrorState } from "@/ui/primitives";

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    // Next 约定：记录错误与栈，方便用户截图报障。
    console.error(error);
  }, [error]);

  return (
    // 最后防线：root layout（含 ThemeProvider/ThemeScript）本身崩溃时由本文件接管，
    // 必须自带 <html>/<body>。颜色 token 无 JS 注入，用浅色回退值（取自 ui/tokens.ts 浅色表）。
    <html lang="zh-CN">
      <body style={{ margin: 0 }}>
        <div
          style={{
            alignItems: "center",
            background: "var(--background, #f7f5f0)",
            color: "var(--on-surface-variant, #625c54)",
            display: "grid",
            minHeight: "100vh",
            padding: "var(--space-6)",
            placeItems: "center",
          }}
        >
          <ErrorState title="应用遇到问题">
            <p style={{ margin: "0 0 var(--space-2)" }}>应用遇到了意外问题，可以重试一下。</p>
            <p style={{ margin: "0 0 var(--space-4)" }}>若重试无效请重启应用。</p>
            <Button onClick={reset} variant="secondary">
              重试
            </Button>
          </ErrorState>
        </div>
      </body>
    </html>
  );
}
