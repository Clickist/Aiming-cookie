"use client";

import { useEffect } from "react";

import { Button, ErrorState } from "@/ui/primitives";

export default function PageError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    // Next 约定：记录错误与栈，方便用户截图报障。
    console.error(error);
  }, [error]);

  return (
    <div
      style={{
        alignItems: "center",
        display: "grid",
        minHeight: "60vh",
        padding: "var(--space-6)",
        placeItems: "center",
      }}
    >
      <ErrorState title="页面出错了">
        <p style={{ margin: "0 0 var(--space-4)" }}>这个页面出了点问题，重试即可恢复；若反复出现请重启应用。</p>
        <Button onClick={reset} variant="secondary">
          重试
        </Button>
      </ErrorState>
    </div>
  );
}
