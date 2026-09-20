"use client";

import { useEffect } from "react";

import { useT } from "@/lib/i18n";
import { Button, ErrorState } from "@/ui/primitives";

export default function PageError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  const t = useT();
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
      <ErrorState title={t("app.error.title")}>
        <p style={{ margin: "0 0 var(--space-4)" }}>{t("app.error.body")}</p>
        <Button onClick={reset} variant="secondary">
          {t("common.retry")}
        </Button>
      </ErrorState>
    </div>
  );
}
