import "../ui/theme.css";
import "../components/task3/task3.css";
import "../components/task4/task4.css";
import "../components/task6/task6-settings.css";
import "../components/task6/task6.css";
import "../components/task7/session-rail.css";

import type { Metadata } from "next";
import { Suspense, type ReactNode } from "react";

import { AppShell } from "@/components/task3/AppShell";
import { RuntimeGate } from "@/components/task3/RuntimeGate";
// layout 是 Server Component：meta description 只用纯翻译函数，直接引 core——
// 经 "@/lib/i18n" barrel 会连带把含 hooks 的 react.ts 拉进 server 编译图（build 红）。
import { t } from "@/lib/i18n/core";
import { ThemeProvider, ThemeScript } from "@/ui/theme";

export const metadata: Metadata = {
  title: "Aiming Cookie",
  description: t("app.meta.description"),
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        <ThemeScript />
      </head>
      <body>
        <ThemeProvider>
          <Suspense fallback={children}>
            <RuntimeGate>
              <AppShell>{children}</AppShell>
            </RuntimeGate>
          </Suspense>
        </ThemeProvider>
      </body>
    </html>
  );
}
