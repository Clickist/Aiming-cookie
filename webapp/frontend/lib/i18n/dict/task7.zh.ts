// 批 5（task7 + app + ui）zh 分片：本批新键只加在这里，与 dict/task7.en.ts 同步填
// （只填一侧聚合编译直接红）。不改 shared 冻结区与其他分片。
// 值＝产品现行中文逐字搬运；口径见 .zcode/i18n-conventions.md。
export const task7Zh = {
  // components/task7/SessionRail.tsx —— 会话侧栏（时间桶/搜索/条目操作/页脚/账号卡）。
  // 「新对话」哨兵常量 NEW_SESSION_TITLE 是跨层标识符不进字典；展示走 newSessionDisplay。
  // 复用批 1 冻结键：member.noProvider.short（「未连接模型服务」）。
  "coach.rail.regionLabel": "会话",
  "coach.rail.newSessionButton": "新建对话",
  "coach.rail.search": "搜索会话",
  "coach.rail.searchClear": "清除搜索",
  "coach.rail.listLabel": "会话列表",
  "coach.rail.group.today": "今天",
  "coach.rail.group.yesterday": "昨天",
  "coach.rail.group.week": "近 7 天",
  "coach.rail.group.older": "更早",
  "coach.rail.untitledSession": "未命名对话",
  "coach.rail.newSessionDisplay": "新对话",
  "coach.rail.archiveTitle": "归档",
  "coach.rail.archiveAria": "归档 {title}",
  "coach.rail.deleteTitle": "删除",
  "coach.rail.deleteAria": "删除 {title}",
  "coach.rail.deleteConfirmAria": "确认删除 {title}",
  "coach.rail.deleteConfirmTitle": "再次点击确认删除",
  "coach.rail.groupShowAll": "显示全部 {count} 条",
  "coach.rail.groupShowPreview": "显示前 {count} 条",
  "coach.rail.emptyFiltered": "没有匹配的会话",
  "coach.rail.empty": "还没有会话",
  "coach.rail.history": "训练历史",
  "coach.rail.settings": "系统设置",
  "coach.rail.loginEntry": "登录 / 注册 ›",
  "coach.rail.resubscribe": "重新订阅 ›",

  // components/task7/CoachVideoPane.tsx —— Coach 视频讲解面板。
  // 复用批 1 冻结键：coach.discussion.runSuffix（「 · run {runId}」）。
  "coach.video.paneLabel": "Coach 视频讲解",
  "coach.video.eyebrow": "视频讲解",
  "coach.video.fallbackTitle": "训练视频",
  "coach.video.close": "关闭视频讲解",
  "coach.video.loading": "正在读取本地视频与证据",
  "coach.video.unavailableTitle": "视频讲解暂时不可用",

  // ui/primitives.tsx —— Toast 关闭钮 aria
  "ui.toast.closeLabel": "关闭通知",

  // app/error.tsx / app/global-error.tsx —— Next 错误页；common.retry 供三处「重试」共用
  "app.error.title": "页面出错了",
  "app.error.body": "这个页面出了点问题，重试即可恢复；若反复出现请重启应用。",
  "app.globalError.title": "应用遇到问题",
  "app.globalError.bodyPrimary": "应用遇到了意外问题，可以重试一下。",
  "app.globalError.bodySecondary": "若重试无效请重启应用。",
  "common.retry": "重试",

  // app/layout.tsx —— meta description（静态导出 build 期恒 zh 属预期）
  "app.meta.description": "本地优先的瞄准训练分析工作台。",
} as const;
