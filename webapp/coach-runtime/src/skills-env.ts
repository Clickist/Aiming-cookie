// Pi 的 NodeExecutionEnv 在 Windows 上把 FileInfo.name 填成完整路径，且 ignore v7
// 拒绝盘符绝对路径；产品与测试统一经此包装把路径转成正斜杠后再交给 Pi。
// （skills-loading.test.ts 钉住此行为：9 个打包 skill 必须都能加载。）
export function skillsExecutionEnv(base: Record<string, unknown>): Record<string, unknown> {
  const forwardSlash = (value: string) => value.replace(/\\/g, "/");
  const slashPath = (value: unknown) => (typeof value === "string" ? forwardSlash(value) : value);
  // Pi 在 Windows 上让 listDir 的 name 带完整路径（POSIX 上是纯 basename），
  // loadSkills 按 name 匹配 SKILL.md，所以 name 必须归一回路径末端。
  const baseName = (slashyPath: string) => slashyPath.split("/").pop() ?? slashyPath;
  const normalizeResultPath = (result: unknown): unknown => {
    if (result && typeof result === "object" && (result as { ok?: boolean }).ok === true) {
      const value = (result as { value?: unknown }).value;
      if (Array.isArray(value)) {
        return {
          ...(result as object),
          value: value.map((entry) =>
            entry && typeof entry === "object" && typeof (entry as { path?: unknown }).path === "string"
              ? normalizeEntry(entry as Record<string, unknown>)
              : entry,
          ),
        };
      }
      if (value && typeof value === "object") {
        const path = (value as { path?: unknown }).path;
        if (typeof path === "string") {
          return { ...(result as object), value: normalizeEntry(value as Record<string, unknown>) };
        }
      }
    }
    return result;
  };
  const normalizeEntry = (entry: Record<string, unknown>): Record<string, unknown> => {
    const path = forwardSlash(entry.path as string);
    return {
      ...entry,
      path,
      ...(typeof entry.name === "string" ? { name: baseName(path) } : {}),
    };
  };
  const call = (name: string, args: unknown[]): Promise<unknown> =>
    (base[name] as (...args: unknown[]) => Promise<unknown>)(...args);
  return {
    cwd: forwardSlash(String(base.cwd ?? "")),
    fileInfo: async (path: unknown, signal?: unknown) =>
      normalizeResultPath(await call("fileInfo", [slashPath(path), signal])),
    listDir: async (path: unknown, signal?: unknown) =>
      normalizeResultPath(await call("listDir", [slashPath(path), signal])),
    canonicalPath: async (path: unknown, signal?: unknown) =>
      normalizeResultPath(await call("canonicalPath", [slashPath(path), signal])),
    readTextFile: (path: unknown, signal?: unknown) => call("readTextFile", [slashPath(path), signal]),
    readTextLines: (path: unknown, options?: unknown) => call("readTextLines", [slashPath(path), options]),
    exists: (path: unknown, signal?: unknown) => call("exists", [slashPath(path), signal]),
  };
}
