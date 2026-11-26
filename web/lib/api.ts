function resolveApiBase(): string {
  if (process.env.NEXT_PUBLIC_API_BASE) {
    return process.env.NEXT_PUBLIC_API_BASE.replace(/\/$/, "");
  }
  if (typeof window !== "undefined" && window.location.protocol.startsWith("http")) {
    const { protocol, hostname, port } = window.location;
    // `next dev` runs on :3000 while the API stays on :8000
    if (port === "3000") return `${protocol}//${hostname}:8000`;
    // served by FastAPI (browser or packaged desktop): always same origin,
    // whatever port the launcher picked
    return `${protocol}//${hostname}${port ? `:${port}` : ""}`;
  }
  return "http://127.0.0.1:8000";
}

export const API_BASE = resolveApiBase();

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit, timeoutMs = 45000): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      ...init,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      cache: "no-store",
    });
    const text = await response.text();
    let body: unknown = null;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = { detail: text };
    }
    if (!response.ok) {
      const detail =
        (body as { detail?: unknown })?.detail ?? `HTTP ${response.status}`;
      throw new ApiError(
        typeof detail === "string" ? detail : JSON.stringify(detail),
        response.status,
      );
    }
    return body as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError("请求超时：后端可能未启动或响应过慢", 0);
    }
    throw new ApiError(
      `未连接到 ExamOS 后端（${API_BASE}）。请运行 scripts\\start.ps1 或启动 ExamOS.exe，页面会自动重试。`,
      0,
    );
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
  get: <T>(path: string, timeoutMs?: number) => request<T>(path, undefined, timeoutMs),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body ?? {}) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body ?? {}) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  health: () => request<{ status: string }>("/health", undefined, 4000),
};

// ------------------------------------------------------------------------- //
// shared entity shapes (only fields the UI consumes)
// ------------------------------------------------------------------------- //
export interface Subject {
  id: number;
  subject_type: string;
  name: string;
  total_score: number;
}

export interface Dashboard {
  countdown_days: number | null;
  exam_date: string | null;
  university: string;
  major: string;
  exam_year: number | null;
  subjects: Subject[];
  mastery: { average: number; by_state: Record<string, number> };
  coverage: { knowledge_total: number; studied: number; stable: number };
  top_roi: {
    knowledge_id: number;
    code: string;
    name: string;
    subject: string;
    roi: number;
    expected_gain: number;
    frequency: number;
  }[];
  due: { cards: number; tasks: number };
  today: {
    budget_minutes: number;
    used_minutes: number;
    task_count: number;
    done: number;
  };
  accuracy: { last7d: number; attempts: number };
  agent: {
    open_tickets: number;
    running: number;
    recent_reports: {
      id: number;
      summary: string;
      status: string;
      confidence: number;
      created_at: string;
    }[];
  };
  last_test: {
    id: number;
    test_type: string;
    score: number;
    max_score: number;
    status: string;
    created_at: string;
  } | null;
  recent_attempts: {
    id: number;
    question_id: number;
    question_key: string;
    question_type: string;
    correct: boolean;
    error_type: string;
    duration_sec: number;
    created_at: string;
    subject: string;
  }[];
}

export interface RoiRow {
  knowledge_id: number;
  code: string;
  name: string;
  subject_id: number;
  subject_name: string;
  chapter: string;
  frequency: number;
  score_share: number;
  expected_gain: number;
  hours: number;
  roi: number;
  confidence: number;
  rank: number;
  blocked: boolean;
  reasons: {
    frequency: number;
    score_share: number;
    gap: number;
    expected_gain: number;
    hours: number;
    confidence: number;
    prerequisite: string;
    data_basis: string;
    formula: string[];
  };
  computed_at: string;
}

export interface KnowledgeItem {
  id: number;
  code: string;
  name: string;
  category: string;
  chapter: string;
  importance: number;
  est_minutes: number;
  syllabus_requirement: string;
  subject_id: number;
  subject_name: string;
  frequency: number;
  score_share: number;
  years_present: number[];
  mastery: number;
  state: string;
  roi: number;
  blocked: boolean;
  prerequisites: string[];
}

export interface GraphNode {
  id: number;
  code: string;
  name: string;
  chapter: string;
  category: string;
  importance: number;
  mastery: number;
  state: string;
  frequency: number;
  roi: number;
  blocked: boolean;
}

export interface GraphEdge {
  source: number;
  target: number;
  relation_type: string;
  weight: number;
}

export interface GraphData {
  subject_id: number;
  subject_name: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  dag: { valid: boolean; node_count: number; edge_count: number; cycle_nodes: string[] };
}

export interface Task {
  id: number;
  task_type: string;
  title: string;
  detail: string;
  est_minutes: number;
  priority: number;
  status: string;
  plan_date: string;
  subject_id: number;
  subject_name: string;
  knowledge_id: number | null;
  source: string;
  payload: Record<string, unknown>;
}

export interface Plan {
  date: string;
  budget_minutes: number;
  used_minutes: number;
  tasks: Task[];
  due_reviews: {
    id: number;
    front: string;
    card_type: string;
    state: string;
    due_at: string | null;
  }[];
  due_tests: { test_type: string; reason: string }[];
  generated_at: string;
}

export interface MemoryCard {
  id: number;
  subject_id: number;
  knowledge_id: number | null;
  card_type: string;
  front: string;
  back: string;
  source_ref: string;
  state: string;
  ease: number;
  interval_days: number;
  reps: number;
  lapses: number;
  due_at: string | null;
}

export interface QuestionItem {
  id: number;
  key: string;
  subject_id: number;
  paper_id: number;
  number: string;
  question_type: string;
  content: string;
  score: number;
  difficulty: number;
  source_type: string;
  is_synthetic: boolean;
  parent_question_id: number | null;
  transform_level: string | null;
  verification: Record<string, unknown>;
  answer?: string;
  explanation?: string;
  knowledge: {
    knowledge_id: number;
    code: string;
    name: string;
    weight: number;
    confidence: number;
    is_core: boolean;
    role: string;
  }[];
}

export interface TestSummary {
  id: number;
  subject_id: number;
  test_type: string;
  status: string;
  score: number;
  max_score: number;
  accuracy: number | null;
  blueprint: Record<string, unknown>;
  created_at: string;
  submitted_at: string | null;
}

export interface Ticket {
  id: number;
  thread_id: number | null;
  thread_topic: string | null;
  subject_id: number | null;
  ticket_type: string;
  title: string;
  status: string;
  priority: number;
  payload: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  latest_report: {
    id: number;
    summary: string;
    status: string;
    confidence: number;
    evidence: string[];
    next_actions: string[];
    requires_human_review: boolean;
  } | null;
  assignment_count: number;
}

export interface TraceStep {
  step: string;
  [key: string]: unknown;
}

export interface AgentResult {
  reply: string;
  trace: TraceStep[];
  ticket: Ticket | null;
  report: Record<string, unknown> | null;
  requires_human_review: boolean;
  ticket_id: number | null;
  thread_id: number | null;
  intent: { ticket_type: string; worker_type: string };
}

export interface SourceItem {
  id: number;
  key: string;
  source_type: string;
  title: string;
  url: string;
  publisher: string;
  license: string;
  trust_score: number;
  document_count: number;
  paper_count: number;
  created_at: string;
}

export interface PaperItem {
  id: number;
  key: string;
  subject_id: number;
  year: number;
  paper_type: string;
  title: string;
  total_score: number;
  question_count: number;
  created_at: string;
}

export interface AttemptResult {
  attempt: {
    id: number;
    question_id: number;
    question_key: string;
    correct: boolean;
    score_awarded: number;
    max_score: number;
    method: string;
    answer: string;
    explanation: string;
  };
  mastery: { knowledge_id: number; code: string; name: string; mastery: number; state: string }[];
  diagnosis: {
    error_type: string | null;
    label: string;
    severity: string;
    evidence: string[];
    explanation: string;
  };
  rollback: {
    needed: boolean;
    strategy: string;
    nodes: {
      knowledge_id: number;
      code: string;
      name: string;
      mastery: number;
      est_minutes: number;
      reason: string;
    }[];
    total_minutes: number;
  };
}

export interface TestReport {
  score: number;
  max_score: number;
  accuracy: number;
  correct: number;
  question_count: number;
  results: {
    question_id: number;
    key: string;
    question_type: string;
    correct: boolean;
    score: number;
    max_score: number;
    method: string;
    answer: string;
    explanation: string;
    knowledge: string[];
    diagnosis: { label: string; error_type: string | null; explanation: string };
  }[];
  diagnosis: {
    question_id: number;
    question_key: string;
    knowledge: string[];
    label: string;
    error_type: string | null;
    severity: string;
    explanation: string;
  }[];
  rollback: {
    needed: boolean;
    strategy: string;
    nodes: {
      knowledge_id: number;
      code: string;
      name: string;
      mastery: number;
      est_minutes: number;
      reason: string;
    }[];
  };
  mastery_delta: {
    knowledge_id: number;
    before: number;
    after: number;
    delta: number;
  }[];
  next_actions: string[];
}

// ------------------------------------------------------------------------- //
// studio: subjects / books / workflows / autonomy
// ------------------------------------------------------------------------- //
export interface StudioSubject {
  id: number;
  name: string;
  subject_type: string;
  total_score: number;
  code: string;
}

export interface BookItem {
  id: number;
  key: string;
  title: string;
  author: string;
  publisher: string;
  subject_type: string;
  major: string;
  level: string;
  tags: string[];
  description: string;
  status: string;
  created_by: string;
  score: number | null;
  reason: string;
}

export interface WorkflowStep {
  name: string;
  focus: string;
  minutes: number;
  task_type: string;
  enabled?: boolean;
}

export interface StudyWorkflowItem {
  id: number;
  name: string;
  description: string;
  subject_id: number | null;
  subject_name: string | null;
  steps: WorkflowStep[];
  status: string;
  source: string;
  meta: Record<string, unknown>;
  created_at: string;
}

export interface AutonomySettings {
  allow_agent_add_subjects: boolean;
  allow_agent_search_books: boolean;
  allow_agent_edit_workflow: boolean;
  allow_agent_remove_subjects: boolean;
  require_confirm_for_delete: boolean;
}

export interface StudioOverview {
  subjects: StudioSubject[];
  workflows: StudyWorkflowItem[];
  books: { total: number; active: number };
  autonomy: AutonomySettings;
}
