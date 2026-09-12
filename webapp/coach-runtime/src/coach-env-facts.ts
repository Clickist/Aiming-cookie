/**
 * Coach 运行环境实测（0911 点点纠偏：应用要分发到任意用户的 Windows 机器，
 * 不能把开发机的能力当成普遍情况）。sidecar 启动时探测一次、进程内缓存，
 * 结果注入系统提示词末尾——教练据此决定用 bash 还是退回内建工具。
 *
 * 关键事实：pi 的 bash 工具在 Windows 只找 Git for Windows / PATH 里的
 * bash，找不到就整体报错不降级；python/node/jq 在用户机器上同样不保证
 * 存在。一切以探测结果为准，提示词正文不再写死任何机器能力。
 */
import { execFile } from "node:child_process";
import { existsSync } from "node:fs";

export type CoachEnvFacts = {
  /** bash 可执行文件路径；null＝这台机器 bash 工具不可用。 */
  bashPath: string | null;
  pythonCommand: string | null;
  nodeCommand: string | null;
  jqAvailable: boolean;
};

function run(command: string, args: string[], timeoutMs = 2_500): Promise<boolean> {
  return new Promise((resolve) => {
    try {
      execFile(
        command,
        args,
        { timeout: timeoutMs, windowsHide: true },
        (error, _stdout, _stderr) => resolve(!error),
      );
    } catch {
      resolve(false);
    }
  });
}

/**
 * 与 pi 同源的三步查找：Git for Windows 固定位置 → /bin/bash（非 Windows）
 * → PATH。找不到返回 null＝这台机器 bash 工具不可用（pi 会整体报错）。
 */
export async function findBash(): Promise<string | null> {
  const candidates: string[] = [];
  if (process.env.ProgramFiles) candidates.push(`${process.env.ProgramFiles}\\Git\\bin\\bash.exe`);
  if (process.env["ProgramFiles(x86)"]) candidates.push(`${process.env["ProgramFiles(x86)"]}\\Git\\bin\\bash.exe`);
  for (const candidate of candidates) {
    if (existsSync(candidate)) return candidate;
  }
  if (process.platform === "win32") {
    const found = await new Promise<string | null>((resolve) => {
      execFile("where", ["bash.exe"], { timeout: 2_500, windowsHide: true }, (error, stdout) => {
        if (error || !stdout) return resolve(null);
        const first = stdout.trim().split(/\r?\n/)[0] ?? "";
        resolve(first && existsSync(first) ? first : null);
      });
    });
    if (found) return found;
  } else if (existsSync("/bin/bash")) {
    return "/bin/bash";
  }
  return null;
}

async function probeOnce(): Promise<CoachEnvFacts> {
  const bashPath = await findBash();
  const [python, python3, node, jq] = await Promise.all([
    run("python", ["--version"]),
    run("python3", ["--version"]),
    run("node", ["--version"]),
    run("jq", ["--version"]),
  ]);
  return {
    bashPath,
    pythonCommand: python ? "python" : python3 ? "python3" : null,
    nodeCommand: node ? "node" : null,
    jqAvailable: jq,
  };
}

let factsPromise: Promise<CoachEnvFacts> | null = null;

/** 启动即热身：首回合就能拿到结果，探测不阻塞回合（结果在缓存里等）。 */
factsPromise ??= probeOnce();

export function coachEnvFacts(): Promise<CoachEnvFacts> {
  factsPromise ??= probeOnce();
  return factsPromise;
}

export function describeCoachEnvFacts(facts: CoachEnvFacts): string {
  const lines = [
    "## 你的运行环境（本机实测，随用户机器不同；以下事实优先于任何假设）",
    "",
    facts.bashPath
      ? `- bash 工具可用（shell: ${facts.bashPath}）。`
      : "- bash 工具在这台机器上不可用（未找到 bash）：不要调用 bash，也不要重试；同类需求一律改用 read/ls/grep/find 与产品命令完成，用户问起时如实说明。",
    facts.pythonCommand
      ? `- Python 可用，命令是 \`${facts.pythonCommand}\`。`
      : "- Python 不可用：不要尝试 python/python3；解析/统计需求用 read 与 grep 内建工具完成。",
    facts.nodeCommand
      ? `- Node.js 可用，命令是 \`node\`。`
      : "- Node.js 不可用。",
    facts.jqAvailable
      ? "- jq 可用。"
      : "- jq 不可用：不要用 jq；需要看 JSON 就用 read/grep，Python 可用时可用 `python -c`。",
    "",
  ];
  return lines.join("\n");
}
