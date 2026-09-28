/**
 * 基础设施域名（构建期内联，运行期只读）：真实值由仓库根 infra-urls.local.txt
 * （gitignore 的本地文件，KEY=VALUE 格式）经 next.config.ts 的 env 键在构建时
 * 写死进 NEXT_PUBLIC_*；公开源码构建没有该文件，回落 example.invalid 占位——
 * 会员外链打不开，其余功能不受影响。真实域名不入库
 * （见 docs/DEVELOPMENT.md「基础设施域名注入」）。
 */
export const ACCOUNTS_BASE_URL = (
  process.env.NEXT_PUBLIC_ACCOUNTS_BASE_URL ?? "https://accounts.example.invalid"
).replace(/\/+$/, "");
