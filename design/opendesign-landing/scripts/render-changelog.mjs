#!/usr/bin/env node
/**
 * 从仓库级单一事实源 design/opendesign-landing/changelog.json 预渲染公开 changelog 静态页：
 *   changelog/index.html      （zh-CN）
 *   en/changelog/index.html   （en）
 *
 * 内容直接写进 HTML 源码（搜索引擎 / AI 爬虫可读，不依赖 JS 运行时渲染），
 * JS 仅保留与落地页同款的主题切换（localStorage 'ac-landing-theme'）。
 * 文案约定：变更条目原样取自 changelog.json（zh 页取 .zh、en 页取 .en），
 * 分组标签与弹窗一致（新增/改进/修复、最新 ↔ New/Improved/Fixed、Latest），
 * 页面骨架文案复用首页弹窗与页脚既有文案，不新造文案。
 *
 * 用法：node scripts/render-changelog.mjs
 * 不修改 changelog.json，不影响 webapp/frontend/scripts/sync-changelog.mjs。
 */
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = path.join(root, "changelog.json");

const data = JSON.parse(readFileSync(source, "utf8"));
if (!Array.isArray(data.versions) || data.versions.length === 0) {
  throw new Error(`[render-changelog] changelog.json 缺少非空 versions 数组：${source}`);
}
for (const entry of data.versions) {
  if (typeof entry.version !== "string" || !Array.isArray(entry.items)) {
    throw new Error(`[render-changelog] changelog.json 版本块结构非法（缺 version 或 items）：${source}`);
  }
}

const SITE = "https://aimingcookie.com";
const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );

const PAGES = {
  zh: {
    out: path.join(root, "changelog", "index.html"),
    htmlLang: "zh-CN",
    url: `${SITE}/changelog/`,
    otherUrl: `${SITE}/en/changelog/`,
    xDefaultUrl: `${SITE}/changelog/`,
    ogLocale: "zh_CN",
    title: "更新日志 · Aiming Cookie（KovaaK's 瞄准诊断 + AI 教练）",
    ogTitle: "更新日志 · Aiming Cookie",
    description:
      "Aiming Cookie 每个版本更新了什么，都在这里：新增、改进与修复的完整更新日志。KovaaK's 瞄准诊断 + AI 教练，免费 Windows 桌面应用。",
    h1: "更新日志",
    // 复用首页弹窗 cl-sub 文案
    lead: "Aiming Cookie 每个版本更新了什么，都在这里。",
    backHome: "← 返回首页",
    backUrl: "/",
    nav: [
      ["对照", "/#contrast"],
      ["采集", "/#capture"],
      ["指标", "/#metrics"],
      ["Coach", "/#coach"],
      ["套餐", "/#pricing"],
      ["Bilibili 主页", "https://space.bilibili.com/14425468?spm_id_from=333.337.0.0"],
    ],
    navAria: "主导航",
    menuAria: "打开导航菜单",
    langSwitch: "EN",
    themeAria: "切换深色模式",
    themeTitle: "切换深色 / 浅色",
    footerLeft: "© 2026 Aiming Cookie · KovaaK's 瞄准诊断 + AI 教练",
    footerRightPrefix: "简体中文 · Windows 桌面应用 · 免费 · ",
    footerGithub: "GitHub 开源",
    TAG: { new: "新增", imp: "改进", fix: "修复", latest: "最新" },
  },
  en: {
    out: path.join(root, "en", "changelog", "index.html"),
    htmlLang: "en",
    url: `${SITE}/en/changelog/`,
    otherUrl: `${SITE}/changelog/`,
    xDefaultUrl: `${SITE}/changelog/`,
    ogLocale: "en_US",
    title: "Release Notes · Aiming Cookie (KovaaK's aim analysis + AI coach)",
    ogTitle: "Release Notes · Aiming Cookie",
    description:
      "Every change to Aiming Cookie, in one place: the full changelog of new features, improvements and fixes. Free Windows desktop app for KovaaK's aim analysis with an AI coach.",
    h1: "Release notes",
    // 复用英文首页弹窗 cl-sub 文案
    lead: "Every change to Aiming Cookie, in one place.",
    backHome: "← Back to home",
    backUrl: "/en/",
    nav: [
      ["Compare", "/en/#contrast"],
      ["Capture", "/en/#capture"],
      ["Metrics", "/en/#metrics"],
      ["Coach", "/en/#coach"],
      ["Pricing", "/en/#pricing"],
      ["Bilibili Channel", "https://space.bilibili.com/14425468?spm_id_from=333.337.0.0"],
    ],
    navAria: "Main navigation",
    menuAria: "Open navigation menu",
    langSwitch: "中文",
    themeAria: "Toggle dark mode",
    themeTitle: "Toggle dark / light",
    footerLeft: "© 2026 Aiming Cookie · KovaaK's aim analysis + AI coach",
    footerRightPrefix: "Simplified Chinese UI · Windows desktop app · Free · ",
    footerGithub: "GitHub (open source)",
    TAG: { new: "New", imp: "Improved", fix: "Fixed", latest: "Latest" },
  },
};

function renderItem(item, lang, t) {
  const text = item && typeof item[lang] === "string" ? item[lang] : "";
  if (!text) throw new Error(`[render-changelog] 条目缺少 ${lang} 文案：${JSON.stringify(item)}`);
  const cls = { new: "cl-new", imp: "cl-imp", fix: "cl-fix" }[item.type] || "cl-imp";
  const label = t.TAG[item.type] || t.TAG.imp;
  return `            <li class="${cls}"><span class="cl-tag">${esc(label)}</span><span>${esc(text)}</span></li>`;
}

function renderVersion(v, i, lang, t) {
  const items = v.items.map((it) => renderItem(it, lang, t)).join("\n");
  const badge = i === 0 ? `<span class="cl-badge">${esc(t.TAG.latest)}</span>` : "";
  return `        <div class="cl-rel">
          <div class="cl-rel-head"><h2 class="cl-rel-ver">v${esc(v.version)}</h2>${badge ? " " + badge : ""}<span class="cl-date">${esc(v.date)}</span></div>
          <ul>
${items}
          </ul>
        </div>`;
}

function renderVersions(lang, t) {
  return data.versions.map((v, i) => renderVersion(v, i, lang, t)).join("\n");
}

// 与落地页 index.html 公共部分一致：token / base / topnav / type / pagefoot /
// theme-toggle / lang-switch，加上弹窗同款 cl-* 条目样式（原样搬运）。
const CSS = `    /* ─── tokens（绑定 brand-spec.md · Light）───────────────────────── */
    :root {
      --bg:      oklch(96.6% 0.006 85);   /* #f7f5f0 background */
      --surface: oklch(99.2% 0.004 90);   /* #fffdf8 surface */
      --fg:      oklch(23.5% 0.008 75);   /* #24211d on-background */
      --muted:   oklch(47.5% 0.013 80);   /* #625c54 on-surface-variant */
      --border:  oklch(82.5% 0.014 85);   /* #cec6bc outline-variant */
      --accent:  #c83d00;                 /* primary */

      /* derived — do not change */
      --accent-soft: color-mix(in oklch, var(--accent) 14%, transparent);
      --fg-soft:     color-mix(in oklch, var(--fg) 6%, transparent);

      /* type — 品牌指定：Outfit 展示 / Inter 正文 / JetBrains Mono 数据 */
      --font-display: 'Outfit', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Noto Sans SC', sans-serif;
      --font-body:    'Inter', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Noto Sans SC', sans-serif;
      --font-mono:    'JetBrains Mono', ui-monospace, 'SF Mono', Menlo, monospace;

      /* scale */
      --fs-h1: clamp(44px, 6vw, 76px);
      --fs-h2: clamp(32px, 4vw, 48px);
      --fs-h3: 22px;
      --fs-lead: 19px;
      --fs-body: 16px;
      --fs-meta: 13px;

      /* spacing — 8-point grid */
      --gap-xs: 8px;
      --gap-sm: 12px;
      --gap-md: 20px;
      --gap-lg: 32px;
      --gap-xl: 56px;
      --gap-2xl: 96px;
      --container: 1120px;
      --gutter: 32px;

      --radius: 10px;
      --radius-lg: 16px;

      color-scheme: light;
    }
    /* ─── 深色模式（绑定 app v1.1.0 深色 token）────────────────────── */
    html.dark {
      --bg:      #141413;
      --surface: #1c1c1a;
      --fg:      #eae8e3;
      --muted:   #9e9a92;
      --border:  #3a3833;
      --accent:  #ff8a5c;
      color-scheme: dark;
    }
    *, *::before, *::after { box-sizing: border-box; }
    html { -webkit-text-size-adjust: 100%; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--fg);
      font-family: var(--font-body);
      font-size: var(--fs-body);
      line-height: 1.55;
      text-rendering: optimizeLegibility;
      -webkit-font-smoothing: antialiased;
    }
    img, svg { display: block; max-width: 100%; }
    a { color: inherit; text-decoration: none; }
    .inline-link { text-decoration: underline; text-underline-offset: 3px; text-decoration-color: var(--border); transition: color 150ms ease, text-decoration-color 150ms ease; }
    .inline-link:hover { color: var(--accent); text-decoration-color: var(--accent); }
    button { font: inherit; cursor: pointer; }
    p { text-wrap: pretty; }
    h1, h2, h3, h4 { text-wrap: balance; }
    :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 4px; }
    .container { max-width: var(--container); margin-inline: auto; padding-inline: var(--gutter); }
    .section { padding-block: clamp(40px, 5vw, 72px); }
    .row-between { display: flex; align-items: center; justify-content: space-between; gap: var(--gap-md); }
    /* ─── type ─────────────────────────────────────────────────────── */
    .h1, h1 { font-family: var(--font-display); font-size: var(--fs-h1); line-height: 1.08; letter-spacing: -0.02em; font-weight: 600; margin: 0; }
    .h2, h2 { font-family: var(--font-display); font-size: var(--fs-h2); line-height: 1.12; letter-spacing: -0.015em; font-weight: 600; margin: 0; }
    .lead   { font-size: var(--fs-lead); line-height: 1.6; color: var(--muted); max-width: 64ch; margin: 0; }
    .eyebrow {
      font-family: var(--font-mono);
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--accent);
      margin: 0 0 var(--gap-md);
    }
    .meta { font-family: var(--font-mono); font-size: var(--fs-meta); color: var(--muted); }
    .topnav {
      position: sticky; top: 0; z-index: 10;
      background: color-mix(in oklch, var(--bg) 92%, transparent);
      backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--border);
    }
    .topnav-inner { display: flex; align-items: center; justify-content: space-between; padding-block: 14px; }
    .topnav .logo { display: inline-flex; align-items: center; font-family: var(--font-display); font-size: 19px; font-weight: 600; letter-spacing: -0.01em; }
    .topnav .logo-mark { width: 26px; height: 26px; margin-right: 10px; filter: invert(1); display: inline-block; }
    html.dark .topnav .logo-mark { filter: none; }
    .topnav-right { display: flex; align-items: center; gap: var(--gap-sm); }
    .topnav nav { display: flex; gap: var(--gap-lg); }
    .topnav nav a { display: inline-flex; align-items: center; min-height: 44px; font-size: 14px; color: var(--muted); }
    .topnav nav a:hover { color: var(--fg); text-decoration: underline; text-underline-offset: 5px; }
    .menu-toggle { display: none; width: 44px; height: 44px; padding: 0; place-items: center; background: transparent; color: var(--fg); border: 1px solid var(--border); border-radius: var(--radius); }
    .menu-toggle svg { width: 20px; height: 20px; }
    @media (max-width: 920px) {
      .topnav-inner { flex-wrap: wrap; }
      .menu-toggle { display: grid; }
      .topnav nav { display: none; order: 3; width: 100%; padding: 10px 0 4px; border-top: 1px solid var(--border); margin-top: 10px; }
      .topnav nav[data-open="true"] { display: grid; grid-template-columns: repeat(2, 1fr); gap: 0 16px; }
      .topnav nav a { border-bottom: 1px solid var(--border); }
    }
    .theme-toggle {
      display: inline-flex; align-items: center; justify-content: center;
      width: 40px; height: 44px; padding: 0;
      background: transparent; color: var(--muted);
      border: 0; border-radius: var(--radius);
      cursor: pointer; transition: color .15s ease;
    }
    .theme-toggle:hover { color: var(--fg); }
    .lang-switch {
      display: inline-flex; align-items: center;
      height: 28px; padding: 0 12px;
      border: 1px solid var(--border); border-radius: 999px;
      font-size: 13px; font-weight: 600; color: var(--muted);
      transition: color .15s ease, border-color .15s ease;
    }
    .lang-switch:hover { color: var(--fg); border-color: var(--fg); }
    html.dark .icon-moon { display: none; }
    html:not(.dark) .icon-sun { display: none; }
    .pagefoot { padding-block: var(--gap-xl); color: var(--muted); font-size: 13px; border-top: 1px solid var(--border); }
    .pagefoot .row-between { flex-wrap: wrap; gap: var(--gap-md); }
    .pagefoot a { text-underline-offset: 2px; }
    .pagefoot a:hover { color: var(--fg); text-decoration: underline; }
    /* ─── 更新日志条目（与首页弹窗 cl-* 同款，原样搬运）───────────────── */
    .cl-rel { border: 1px solid var(--border); border-radius: 14px; padding: 16px 20px; margin-bottom: 12px; }
    .cl-rel:last-child { margin-bottom: 0; }
    .cl-rel-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }
    .cl-rel-ver { font-family: var(--font-display); font-size: 18px; font-weight: 700; margin: 0; }
    .cl-badge {
      font-size: 11px; font-weight: 600; color: var(--accent);
      background: var(--accent-soft); padding: 2px 9px; border-radius: 999px;
    }
    .cl-date { font-size: 12.5px; color: var(--muted); margin-left: auto; }
    .cl-rel ul { list-style: none; margin: 0; padding: 0; }
    .cl-item { display: flex; gap: 10px; align-items: flex-start; font-size: 14.5px; padding: 3px 0; }
    .cl-tag { flex: none; font-size: 11px; font-weight: 600; padding: 1px 8px; border-radius: 999px; margin-top: 3px; }
    .cl-new .cl-tag { background: var(--accent-soft); color: var(--accent); }
    .cl-imp .cl-tag { background: color-mix(in srgb, var(--muted) 16%, transparent); color: var(--muted); }
    .cl-fix .cl-tag { border: 1px solid var(--border); color: var(--muted); }
    /* ─── 本页布局 ─────────────────────────────────────────────────── */
    .cl-wrap { max-width: 760px; }
    .cl-head-block { margin-bottom: var(--gap-lg); }
    .cl-back {
      display: inline-flex; margin-bottom: 20px;
      font-size: 15px; font-weight: 500; letter-spacing: -0.005em;
      color: var(--fg);
    }
    .cl-back:hover { color: var(--accent); }
    /* 子页面压缩区块顶部留白，返回链接与标题的间隙单独给足 */
    main > .section { padding-top: clamp(28px, 4vw, 48px); }
    .cl-head-block .lead { margin-top: var(--gap-sm); }
    @media (max-width: 560px) {
      :root { --gutter: 20px; }
    }
    @media (max-width: 640px) {
      .cl-rel { padding: 14px 16px; }
      .cl-item { font-size: 14px; }
    }`;

const THEME_SVG = `
          <svg class="icon-moon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"></path></svg>
          <svg class="icon-sun" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><circle cx="12" cy="12" r="4.2"></circle><path d="M12 2.5v2.4M12 19.1v2.4M2.5 12h2.4M19.1 12h2.4M4.9 4.9l1.7 1.7M17.4 17.4l1.7 1.7M19.1 4.9l-1.7 1.7M6.6 17.4l-1.7 1.7"></path></svg>`;

function renderPage(lang) {
  const t = PAGES[lang];
  const versions = renderVersions(lang, t);
  return `<!doctype html>
<html lang="${t.htmlLang}"><head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="baidu-site-verification" content="codeva-zhLqXj6A6t" />
  <title>${esc(t.title)}</title>
  <meta name="description" content="${esc(t.description)}">
  <meta name="robots" content="index, follow">
  <meta name="theme-color" content="#f7f5f0" id="metaThemeColor">
  <script>
    // 首屏前应用持久化主题，避免深色用户闪白（与落地页同款）
    try { if (localStorage.getItem('ac-landing-theme') === 'dark') document.documentElement.classList.add('dark'); } catch (e) {}
  </script>
  <link rel="canonical" href="${t.url}">
  <link rel="alternate" hreflang="zh-CN" href="${SITE}/changelog/">
  <link rel="alternate" hreflang="en" href="${SITE}/en/changelog/">
  <link rel="alternate" hreflang="x-default" href="${t.xDefaultUrl}">
  <link rel="icon" type="image/x-icon" href="/favicon.ico">
  <link rel="icon" type="image/png" sizes="64x64" href="/favicon-64.png">
  <link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
  <!-- Open Graph -->
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="Aiming Cookie">
  <meta property="og:locale" content="${t.ogLocale}">
  <meta property="og:title" content="${esc(t.ogTitle)}">
  <meta property="og:description" content="${esc(t.description)}">
  <meta property="og:url" content="${t.url}">
  <meta property="og:image" content="${SITE}/logo.png">
  <meta property="og:image:width" content="1024">
  <meta property="og:image:height" content="1024">
  <meta property="og:image:alt" content="Aiming Cookie logo">
  <!-- Twitter Card -->
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="${esc(t.ogTitle)}">
  <meta name="twitter:description" content="${esc(t.description)}">
  <meta name="twitter:image" content="${SITE}/logo.png">
  <link href="/fonts.css" rel="stylesheet">
  <style>
${CSS}
  </style>
  <script type="application/ld+json">
${JSON.stringify({
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "WebPage",
      "@id": `${t.url}#webpage`,
      url: t.url,
      name: t.ogTitle,
      description: t.description,
      inLanguage: t.htmlLang,
      isPartOf: { "@id": `${SITE}/#website` },
      about: { "@id": `${SITE}/#org` },
    },
    {
      "@type": "BreadcrumbList",
      "@id": `${t.url}#breadcrumb`,
      itemListElement: [
        { "@type": "ListItem", position: 1, name: "Aiming Cookie", item: `${SITE}/` },
        { "@type": "ListItem", position: 2, name: t.h1, item: t.url },
      ],
    },
  ],
})}
  </script>
</head>
<body>
  <header class="topnav">
    <div class="container topnav-inner">
      <span class="logo"><img class="logo-mark" src="/logo.png" alt="" aria-hidden="true">Aiming Cookie</span>
      <nav id="site-nav" aria-label="${esc(t.navAria)}" data-open="false">
${t.nav.map(([label, href]) => href.startsWith("http")
  ? `        <a href="${href}" target="_blank" rel="noopener">${esc(label)}</a>`
  : `        <a href="${href}">${esc(label)}</a>`).join("\n")}
      </nav>
      <button class="menu-toggle" type="button" aria-label="${esc(t.menuAria)}" aria-controls="site-nav" aria-expanded="false">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"></path></svg>
      </button>
      <div class="topnav-right">
        <a class="lang-switch" href="${t.otherUrl}">${esc(t.langSwitch)}</a>
        <button class="theme-toggle" id="themeToggle" type="button" aria-label="${esc(t.themeAria)}" title="${esc(t.themeTitle)}">${THEME_SVG}
        </button>
      </div>
    </div>
  </header>

  <main id="content">
    <section class="section">
      <div class="container cl-wrap">
        <div class="cl-head-block">
          <a class="cl-back" href="${t.backUrl}">${esc(t.backHome)}</a>
          <h1 class="h2">${esc(t.h1)}</h1>
          <p class="lead">${esc(t.lead)}</p>
        </div>
        <div class="cl-list">
${versions}
        </div>
      </div>
    </section>
  </main>

  <footer class="pagefoot">
    <div class="container row-between">
      <span>${esc(t.footerLeft)}</span>
      <span class="meta">${esc(t.footerRightPrefix)}<a href="https://github.com/Clickist/Aiming-cookie" target="_blank" rel="noopener">${esc(t.footerGithub)}</a></span>
    </div>
  </footer>
  <script>
    // ─── 深色模式切换（持久化，与落地页同款）─────────────────────────────
    (function () {
      var toggle = document.getElementById('themeToggle');
      var meta = document.getElementById('metaThemeColor');
      var apply = function (dark) {
        document.documentElement.classList.toggle('dark', dark);
        if (meta) meta.content = dark ? '#141413' : '#f7f5f0';
      };
      if (!toggle) return;
      apply(document.documentElement.classList.contains('dark'));
      toggle.addEventListener('click', function () {
        var dark = !document.documentElement.classList.contains('dark');
        apply(dark);
        try { localStorage.setItem('ac-landing-theme', dark ? 'dark' : 'light'); } catch (e) {}
      });
    })();
    // ─── 移动端导航折叠（与落地页同款交互）───────────────────────────────
    (function () {
      var mt = document.querySelector('.menu-toggle');
      var nav = document.getElementById('site-nav');
      if (!mt || !nav) return;
      mt.addEventListener('click', function () {
        var open = nav.dataset.open === 'true';
        nav.dataset.open = String(!open);
        mt.setAttribute('aria-expanded', String(!open));
      });
    })();
  </script>
</body></html>
`;
}

for (const lang of ["zh", "en"]) {
  const t = PAGES[lang];
  mkdirSync(path.dirname(t.out), { recursive: true });
  writeFileSync(t.out, renderPage(lang), "utf8");
  console.log(`[render-changelog] ${data.versions.length} 个版本 / ${data.versions.reduce((n, v) => n + v.items.length, 0)} 条条目 → ${path.relative(root, t.out)}`);
}

// llms.txt 的版本号与安装包直链随最新版本同步，避免 AI 端读到滞后的版本信息
const latest = data.versions[0].version;
const llmsPath = path.join(root, "llms.txt");
const llmsBefore = readFileSync(llmsPath, "utf8");
const llmsAfter = llmsBefore
  .replace(/Aiming_Cookie_[\w.]+-setup\.exe/g, `Aiming_Cookie_${latest}_x64-setup.exe`)
  .replace(/latest version v[\d.]+/g, `latest version v${latest}`);
if (llmsAfter !== llmsBefore) {
  writeFileSync(llmsPath, llmsAfter, "utf8");
  console.log(`[render-changelog] llms.txt 版本信息已同步到 v${latest}`);
}
