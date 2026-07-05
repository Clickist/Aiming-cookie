import type { Metadata } from "next";
import { Inter, JetBrains_Mono, Outfit } from "next/font/google";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

const outfit = Outfit({
  subsets: ["latin"],
  variable: "--font-outfit",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Aiming Cookie — Flicking 张力分析与 AI 教练",
  description:
    "上传 KovaaK's 录像,AI 教练解析减速段张力、目标获取速度与微校正模式,给个性化诊断。",
  openGraph: {
    title: "Aiming Cookie",
    description: "Flicking 张力分析与 AI 教练",
    type: "website",
  },
  twitter: {
    card: "summary",
    title: "Aiming Cookie",
    description: "Flicking 张力分析与 AI 教练",
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="zh-CN"
      className={`dark ${inter.variable} ${jetbrainsMono.variable} ${outfit.variable}`}
    >
      <head>
        {/* Material Symbols Outlined — coach 页图标(play_arrow/pause/send/
            psychology 等)依赖此字体。与 stitch 设计稿 CDN 一致。 */}
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap"
        />
      </head>
      <body className="font-sans antialiased">
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-[200] focus:px-md focus:py-sm focus:bg-primary focus:text-on-primary focus:rounded-md"
        >
          跳到主内容
        </a>
        {children}
      </body>
    </html>
  );
}
