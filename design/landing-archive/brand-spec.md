# Aiming Cookie 落地页 · Brand Spec

来源：`Aiming-cookie/DESIGN-cursor.md`（品牌气质权威）+ `docs/design-system.md`（token 治理）。
以下为 approved hex palette 的 OKLch 等价值，供落地页 `:root` 绑定。

## 主方向（Light · 暖纸面编辑室）

| Token | OKLch | 对应品牌 hex | 角色 |
| --- | --- | --- | --- |
| `--bg` | `oklch(96.6% 0.006 85)` | `#f7f5f0` background | 页画布 |
| `--surface` | `oklch(99.2% 0.004 90)` | `#fffdf8` surface | 卡片 / 抬起面 |
| `--fg` | `oklch(23.5% 0.008 75)` | `#24211d` on-background | 主文本 |
| `--muted` | `oklch(47.5% 0.013 80)` | `#625c54` on-surface-variant | 次级文本 |
| `--border` | `oklch(82.5% 0.014 85)` | `#cec6bc` outline-variant | 细发线 |
| `--accent` | `oklch(53% 0.175 43)` | `#c83d00` primary | 唯一高电压动作色 |

## 备选方向（Dark · 暗色测控台）

| Token | OKLch | 对应品牌 hex |
| --- | --- | --- |
| `--bg` | `oklch(21% 0.004 80)` | `#141413` |
| `--surface` | `oklch(24.5% 0.005 80)` | `#1c1c1a` |
| `--fg` | `oklch(92.5% 0.005 90)` | `#eae8e3` |
| `--muted` | `oklch(68% 0.008 85)` | `#9e9a92` |
| `--border` | `oklch(33% 0.007 75)` | `#3a3833` |
| `--accent` | `oklch(72% 0.15 45)` | `#ff8a5c` |

## 字体

- Display：`Outfit, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Noto Sans SC', sans-serif`
- Body：`Inter, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Noto Sans SC', sans-serif`
- Mono / 数据：`JetBrains Mono, ui-monospace, 'SF Mono', Menlo, monospace`

（品牌源指定 Outfit/Inter/JetBrains Mono，优先于技能种子的衬线默认。）

## 视觉语言规则（从 DESIGN-cursor.md 观察提炼）

1. 暖中性纸面 + 细发线分层；深度来自 surface 阶梯与 hairline，不用装饰性投影。
2. 橙色是唯一高电压动作 / 活跃分析信号，必须稀缺——每屏至多两处（eyebrow + 主 CTA 为默认预算）。
3. 编辑感排印：展示体大尺度、字距收紧；数据、编号、标注一律 mono + tabular-nums。
4. 紧凑机械感圆角（10–16px），避免消费级大胶囊。
5. 营销面版式尺度可放大、更有表现力，但信息密度与克制感延续桌面产品，不做通用 SaaS 模板。

**一句话**：暖纸面上的编辑级精密工具——大标题说人话，mono 标注说真话，橙色只在该出手时出手。
