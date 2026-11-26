export function fmtPct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function fmtNum(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

export function fmtMinutes(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return "—";
  const total = Math.round(minutes);
  if (total < 60) return `${total} 分钟`;
  const hours = Math.floor(total / 60);
  const rest = total % 60;
  return rest ? `${hours} 小时 ${rest} 分` : `${hours} 小时`;
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function daysUntil(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const target = new Date(iso);
  if (Number.isNaN(target.getTime())) return null;
  return Math.max(
    0,
    Math.ceil((target.getTime() - Date.now()) / (1000 * 60 * 60 * 24)),
  );
}

export function statusColor(status: string): string {
  const key = status.toUpperCase();
  if (["READY", "DONE", "STABLE", "SUBMITTED", "OK"].includes(key)) return "var(--good)";
  if (["FAILED", "REJECTED", "BLOCKED"].includes(key)) return "var(--bad)";
  if (["RUNNING", "PROGRESS", "DOING", "WAITING", "PENDING", "OPEN"].includes(key))
    return "var(--warn)";
  if (["ACTIVE_RECALL", "APPLICATION", "RECOGNITION", "LEARNING"].includes(key))
    return "var(--accent-2)";
  return "var(--muted)";
}

export function stateOrder(state: string): number {
  const order = [
    "UNSEEN",
    "LEARNING",
    "RECOGNITION",
    "ACTIVE_RECALL",
    "APPLICATION",
    "STABLE",
  ];
  const index = order.indexOf(state);
  return index === -1 ? 0 : index;
}

export function roiTier(roi: number): "high" | "mid" | "low" {
  if (roi >= 8) return "high";
  if (roi >= 3) return "mid";
  return "low";
}

export function shortText(text: string, max = 90): string {
  if (!text) return "";
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max)}…` : flat;
}

export function pctWidth(value: number, total: number): string {
  if (!total) return "0%";
  return `${Math.max(0, Math.min(100, (value / total) * 100)).toFixed(1)}%`;
}

export const TASK_LABELS: Record<string, string> = {
  learn: "学习",
  practice: "练习",
  review: "复习",
  memory: "背诵",
  test: "测试",
  transform: "改造",
  diagnose: "诊断",
  coverage: "覆盖",
};

export const TEST_LABELS: Record<string, string> = {
  micro: "每日微测",
  mixed: "混合小测",
  stage: "阶段测试",
  mock: "综合模拟",
  diagnostic: "初始诊断",
};

export const SUBJECT_LABELS: Record<string, string> = {
  math_1: "数学一",
  english_1: "英语一",
  professional: "专业课",
};

export const WORKER_LABELS: Record<string, string> = {
  collector: "资料搜集",
  parser: "试卷解析",
  tagger: "知识点标注",
  exam_analyst: "考情分析",
  roi_planner: "ROI 规划",
  question_generator: "题目改造",
  solver_verifier: "解题验证",
  memory_builder: "背诵构建",
  test_designer: "测试设计",
  diagnostician: "错误诊断",
  math_specialist: "数学一",
  english_specialist: "英语一",
  professional_specialist: "专业课",
  watchdog: "Watchdog",
};
