import "../ui/theme.css";
import "../components/task3/task3.css";
import "../components/task4/task4.css";
import "../components/task6/task6.css";

import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AppShell } from "@/components/task3/AppShell";
import { ThemeProvider, ThemeScript } from "@/ui/theme";

export const metadata: Metadata = {
  title: "Aiming Cookie",
  description: "本地优先的瞄准训练分析工作台。",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        <ThemeScript />
      </head>
      <body>
        <ThemeProvider><AppShell>{children}</AppShell></ThemeProvider>
      </body>
    </html>
  );
}
