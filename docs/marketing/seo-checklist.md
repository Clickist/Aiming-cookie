# SEO 官方实践 Checklist（Google Search Central 口径）

- **来源**：Google 官方文档，全部页面于 2026-09-07 通读（新手指南 + 基础 7 页 + 抓取索引 8 页 + 搜索呈现与结构化数据 10 页，约 25 页官方文档）。
- **用途**：我们名下各站做 SEO 自查的唯一清单；第三方工具/文章的建议一律先对照本清单核实（这本身也是官方要求）。
- **性质**：操作口径快照。官方文档后续更新时以官方为准，本文件记录的是 2026-09-07 的官方口径。
- 三站逐项体检结果与优化建议见文末第 7 节。

---

## 1. 技术底线（硬要求，不达标可能完全无法出现在搜索结果）

- [ ] 页面无需登录即可访问；Googlebot 能抓到最终 HTML。
- [ ] 全站 HTTPS，证书有效；无 HTTP 资源混入、无 HTTPS→HTTP 跳转。
- [ ] `<head>` 内三件套：`<meta charset="utf-8">`、`<meta name="viewport">`、`<meta name="description">`（viewport 的存在本身即向 Google 表明适合移动端）。
- [ ] 每页唯一 `<title>`：含产品名 + 核心价值；不堆砌关键词；重要信息放前面（过长会被按设备宽度截断）；禁全站共用样板标题。
- [ ] 每页唯一 meta description：一两个完整句子（官方示例约 150+ 字符；中文建议 70–90 字、关键信息前置）；与页面内容一致；禁纯关键词罗列。
- [ ] 自适应设计（官方最推荐配置）；主要内容不得依赖点击/滑动/输入才加载。
- [ ] 关键信息必须是 DOM 中的真实文字：不放视频里、不放图片内嵌文字里、不用 canvas/插件、不用 CSS `content` 属性。
- [ ] 链接只用带 `href` 的真 `<a>`；禁 `javascript:` 伪链接、`<span onclick>`；锚文本描述明确，禁"点击此处"。
- [ ] 图片用 `<img>`（CSS background-image 不被索引），有准确 alt；装饰图可用空 alt；语义化文件名。
- [ ] 未知路径返回真实 404（禁止全站 fallback 200 = 软 404 工厂）。
- [ ] 接入 Search Console 并持续监控；用 PageSpeed Insights 盯核心网页指标（LCP/INP/CLS）。

## 2. 抓取、索引与规范化

**规范化信号强度：服务器 301 > rel="canonical" > sitemap 收录。多信号同向叠加最稳。**

- [ ] 规范页自身放**自引用 canonical**，绝对 URL，放 `<head>`。
- [ ] 副本/重复地址：优先 301；不能 301 时（如第三方托管副本）用 canonical 指向规范页。
- [ ] www / 裸域 / http→https：用 301 统一到唯一规范主机。
- [ ] 站内所有链接、sitemap 一律只写规范 URL（canonical 指裸域而内链写 www = 信号打架）。
- [ ] robots.txt 放根目录；`Sitemap:` 行必须**绝对 URL**。
- [ ] sitemap：XML、UTF-8、绝对地址、只列规范 URL；Google **忽略** priority/changefreq；lastmod 只在"可验证的重大更新"时写。≤500 页且内链完整可不提交（提交也无害）。
- [ ] robots.txt 与 noindex 职责不分家：**robots.txt 只管抓取，noindex 才管索引**；两者叠加会让 noindex 永远不被看见。

**禁止**：用 robots.txt 屏蔽想被收录的页面或其资源（CSS/JS/图片/视频/缩略图/favicon）；对"允许抓取的副本"叠 noindex（与 canonical 目标冲突）；用 robots.txt 或 GSC 移除工具做规范化；canonical 用相对路径或放 `<body>`；用 URL `#` 片段承载不同内容。

## 3. 内容质量（官方"实用可靠内容"自测）

- [ ] 原创信息/第一手经验（真实使用截图、实测数据）；对主题有实质、完整描述。
- [ ] 网页标题与主标题是实用、描述性的总结，不夸大、不标题党。
- [ ] **把目标用户会搜的词放在显眼位置：网页标题、主标题（h1/h2）、图片 alt、链接文本**（官方核心最佳实践）。
- [ ] 出品方/作者信息清晰可信；展示来源与专业依据。
- [ ] 用 AI 辅助生成内容：目的必须是服务用户而非操纵排名（后者违反垃圾政策，可整站移除）；适度披露。
- [ ] 积极在相关社区推广（官方最佳实践之一；社区链接本身就是发现渠道）。

**官方明确无效的做法**：凑"首选字数"（不存在首选字数）、篡改日期装新、无实质改动时批量增删内容、把关键词塞进 `meta keywords`（Google 完全忽略）、HTML `lang` 属性（Google 靠页面文字自动检测语言）。

## 4. 搜索结果呈现

- [ ] favicon：首页 `<link rel="icon">`，正方形 ≥48×48px，URL 稳定，不被 robots.txt 屏蔽 → 搜索结果中显示图标。
- [ ] 网站名称：首页 JSON-LD `WebSite`（`name` + `url` 必填，`alternateName` 可选）→ 标题上方显示站点名；失败回退显示裸域名。
- [ ] 截图/产品图：`<img>` + 中文 alt + 语义文件名 + WebP/AVIF 压缩；关键文字不嵌图内；产品图放相关文字附近。
- [ ] 视频：页面 HTML 内有 `<video>`/`<iframe>` 标记、文件与缩略图可抓取、页面不默认隐藏 → 见第 5 节 VideoObject。

## 5. 结构化数据（JSON-LD，官方推荐格式）

| 类型 | 现状与结论 |
|---|---|
| `WebSite`（name+url） | ✅ 有效，站点名显示的基础，放首页一次 |
| `Organization`（logo+url+name，sameAs 可选） | ✅ 有效；logo ≥112×112px、可抓取、白底正常 |
| `VideoObject` | ✅ **无评分无价格场景下最高收益富结果**；必填仅 3 项：`name`、`thumbnailUrl`（GIF/JPG/PNG，≥112×112，建议 16:9）、`uploadDate`（ISO 8601 含时区）；推荐 `duration`/`contentUrl`；不设 `expiresDate` |
| `SoftwareApplication` | ⚠️ **2023 下半年起富结果仅对 Google Play 页面展示**，自建官网写了不违规但出不了卡片；若保留：免费产品 `offers.price` 必须为 `0` 并配 `priceCurrency`，`applicationCategory`/`operatingSystem` 至少其一 |
| `aggregateRating` | ❌ 无真实评分系统时**严禁虚构**（可致失去富结果资格甚至手动处罚） |

通用红线：结构化数据只能描述页面上真实存在、用户可见的内容；同一页面可叠加多类型；上线用 Rich Results Test 验证。

## 6. 垃圾政策红线（违规后果：降权、移出索引、整站处理）

标题党/夸大；隐藏文字或链接；cloaking；虚构评分/评论/获奖；自动生成内容以操纵排名为主要目的；大规模复制改写他人内容；付费买卖排名链接（外链广告须 `rel="sponsored"`，用户可插链接区用 `rel="ugc"`）；相信任何"保证排名第一""Google 认可的 SEO 服务"（Google 不收费、广告不影响自然排名、不评估任何第三方工具）。

---

## 7. 三站体检快照（2026-09-07 实测）

### 7.1 aimingcookie.com — 基础分高，两项可升级

已达标：HTTPS + 三重 301（www→裸域、http→https、/index.html→/）；自引用 canonical（绝对 URL）；pages.dev 副本 canonical 指裸域且未叠 noindex；robots.txt 含绝对 Sitemap 行；sitemap 只列规范 URL；title/description 前置关键信息；OG/Twitter 卡完整（含图片尺寸与 alt）；favicon 全套（方形、稳定）；JSON-LD 三件套（Organization+WebSite+SoftwareApplication，价格正确填 0）；真实 404；正文为真实 DOM 文字（UI 演示是 CSS 模拟而非截图，可被索引）；字体 woff2 unicode-range + font-display swap。

| 优先级 | 建议 | 依据 | 改动量 |
|---|---|---|---|
| ~~P0~~ | ~~补 VideoObject~~ **已评估砍掉（2026-09-07 抽帧复核）**：hero 视频实为点点 KovaaK 训练实录（1wall 6targets small，白墙靶点画面）而非产品演示——缩略图无信息量、标记与内容不符，VideoObject 与 poster 均不做。**未来选项**：若剪出真产品演示视频（AC 采集→诊断报告→AI 讲解 30-60s 录屏），再启用本方案（VideoObject 三必填 + poster），该视频可同时替代 hero 素材 | 第 5 节 | 已终止 |
| P1 | 补 1–3 张真实产品截图 `<img>`：中文 alt + 语义文件名（如 `kovaaks-aim-analysis.webp`），进入 Google 图片池 | 第 1/4 节图片条目 | 小 |
| P1 | h1 现为营销钩子"练一局，向AI教练提问"，不含任何产品/场景词；官方要求把用户会搜的词放主标题。可改为含"Aiming Cookie / KovaaK's 瞄准"的描述性主标题（视觉钩子降为副标题），属设计权衡，需拍板 | 第 3 节 | 小但涉及视觉 |
| P2 | `/hero-demo.mp4` 缓存仅 1 天：改版本化文件名 + `_headers` 加 `immutable` 一年（对齐 fonts 现行策略） | 性能实践 | 极小 |
| P2 | Search Console：确认 sitemap 已提交、富结果状态报告盯 VideoObject 生效；可选开启 CF HSTS（官方认可的额外规范化信号） | 第 1/2 节 | 用户操作 |

明确不需要做：不删 `meta keywords`（百度侧可能有用，Google 自动忽略）；不给 pages.dev 叠 noindex；不删 SoftwareApplication（无风险，留作语义信息）。

### 7.2 replace-logitech.pages.dev — 未上线站点

体检与优化建议已移至内部运营文档，不随开源仓库分发。

### 7.3 play.gearclickist.com（《鼠一把》猜鼠标游戏，自托管） — 基础尚可，四项可补

> 勘误（2026-09-07）：初版误把 mousedle.com 当作我们的游戏站——那是别人的 GoDaddy 停放域名。我们的游戏站是 **play.gearclickist.com**，以下为实测。

已达标：HTTPS 200；`lang="zh-CN"` + viewport；title/description 描述准确且关键信息前置（"鼠一把 - 猜鼠标游戏，支持单人多难度与多人实时对战"）；OG 全套（og:image 为绝对 URL 且可访问）+ Twitter card；favicon svg/png/apple-touch-icon/site.webmanifest 齐全；robots.txt Allow 全开且 **Sitemap 行为绝对 URL（写法正确）**；sitemap.xml 存在。

| 优先级 | 建议 | 依据 | 改动量 |
|---|---|---|---|
| P0 | 补**自引用 canonical**：`<link rel="canonical" href="https://play.gearclickist.com/">`（一行） | 第 2 节：规范页必须自带 canonical | 极小 |
| P1 | 未知路径返回真 404：实测 `/no-such-page` 返回 200 壳页（SPA fallback），属软 404 模式；自托管服务器对未匹配路由返回 404 状态码即可 | 第 1 节 404 条目 | 小 |
| P1 | 正文仍是 JS 空壳（首屏 HTML 无任何内容，Googlebot 需二次渲染才见正文）；游戏本体 JS 化可接受，建议把游戏介绍/玩法说明等文字内容预渲染或 SSR 进首屏 HTML | 第 1 节"真实文字在 DOM 中" | 中 |
| P2 | 补 JSON-LD：`WebSite`（name="鼠一把"+url）锁定站点名显示；将来若有宣传视频再叠 `VideoObject` | 第 5 节 | 小 |
| P2 | 裸域 gearclickist.com 与 www 目前无解析/无响应：如需品牌入口，可在裸域放简单落地页链向 play.（可选；单一子域名本身无 SEO 问题） | 第 2 节链接发现 | 可选 |

### 7.4 已排除：mousedle.com（非我方站点）

mousedle.com 为他人 GoDaddy 停放域名（根路径 114 字节 JS 跳转到 parking 模板），与我们无关，勿再纳入体检范围。

---

*依据文档（均 2026-09-07 读取，zh-cn）：`/search/docs/fundamentals/seo-starter-guide`、`how-search-works`、`creating-helpful-content`、`get-started`、`do-i-need-seo`、`get-started-developers`、`third-party-seo`、`/search/docs/essentials`、`/search/docs/crawling-indexing`（consolidate-duplicate-urls、sitemaps/build-sitemap、links-crawlable、qualify-outbound-links、url-structure、block-indexing、special-tags、mobile）、`/search/docs/appearance`（title-link、snippet、site-names、favicon-in-search、google-images、video、structured-data/intro、sd-policies、software-app、organization）。*
