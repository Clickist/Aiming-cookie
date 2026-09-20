// 批 5（task7 + app + ui）en 分片：与 dict/task7.zh.ts 键集保持一致，聚合处
// en-US.ts 的 satisfies 强制校验。翻译口径见 .zcode/i18n-conventions.md。
export const task7En = {
  // components/task7/SessionRail.tsx —— 会话侧栏
  "coach.rail.regionLabel": "Sessions",
  "coach.rail.newSessionButton": "New chat",
  "coach.rail.search": "Search sessions",
  "coach.rail.searchClear": "Clear search",
  "coach.rail.listLabel": "Session list",
  "coach.rail.group.today": "Today",
  "coach.rail.group.yesterday": "Yesterday",
  "coach.rail.group.week": "Last 7 days",
  "coach.rail.group.older": "Earlier",
  "coach.rail.untitledSession": "Untitled chat",
  "coach.rail.newSessionDisplay": "New chat",
  "coach.rail.archiveTitle": "Archive",
  "coach.rail.archiveAria": "Archive {title}",
  "coach.rail.deleteTitle": "Delete",
  "coach.rail.deleteAria": "Delete {title}",
  "coach.rail.deleteConfirmAria": "Confirm delete {title}",
  "coach.rail.deleteConfirmTitle": "Click again to confirm delete",
  "coach.rail.groupShowAll": "Show all {count}",
  "coach.rail.groupShowPreview": "Show first {count}",
  "coach.rail.emptyFiltered": "No matching sessions",
  "coach.rail.empty": "No sessions yet",
  "coach.rail.history": "Training history",
  "coach.rail.settings": "Settings",
  "coach.rail.loginEntry": "Sign in / Sign up ›",
  "coach.rail.resubscribe": "Re-subscribe ›",

  // components/task7/CoachVideoPane.tsx —— Coach 视频讲解面板
  "coach.video.paneLabel": "Coach video walkthrough",
  "coach.video.eyebrow": "Video walkthrough",
  "coach.video.fallbackTitle": "Training video",
  "coach.video.close": "Close video walkthrough",
  "coach.video.loading": "Loading local video and evidence",
  "coach.video.unavailableTitle": "Video walkthrough unavailable",

  // ui/primitives.tsx —— Toast 关闭钮 aria
  "ui.toast.closeLabel": "Close notification",

  // app/error.tsx / app/global-error.tsx —— Next 错误页
  "app.error.title": "This page hit a problem",
  "app.error.body": "Something went wrong on this page. Retrying usually fixes it; if it keeps happening, restart the app.",
  "app.globalError.title": "The app hit a problem",
  "app.globalError.bodyPrimary": "The app ran into an unexpected problem. You can try again.",
  "app.globalError.bodySecondary": "If retrying doesn't help, restart the app.",
  "common.retry": "Retry",

  // app/layout.tsx —— meta description
  "app.meta.description": "A local-first workbench for aim training analysis.",
} as const;
