"use client";

import { useState } from "react";
import {
  Badge,
  EmptyState,
  ErrorBox,
  Loading,
  Panel,
  StatCard,
  useApi,
} from "@/components/ui";
import {
  api,
  type AgentResult,
  type AutonomySettings,
  type BookItem,
  type StudioOverview,
  type StudyWorkflowItem,
} from "@/lib/api";
import { shortText } from "@/lib/format";

const SUBJECT_TYPE_LABELS: Record<string, string> = {
  math_1: "数学一",
  english_1: "英语一",
  professional: "专业课",
  custom: "自定义",
};

const LEVEL_TONES: Record<string, "good" | "warn" | "accent"> = {
  基础: "good",
  强化: "warn",
  冲刺: "accent",
  进阶: "accent",
};

const AUTONOMY_LABELS: { key: keyof AutonomySettings; label: string; hint: string }[] = [
  {
    key: "allow_agent_search_books",
    label: "允许智能体检索书籍",
    hint: "用自然语言找其他专业/科目的教材与参考书",
  },
  {
    key: "allow_agent_edit_workflow",
    label: "允许智能体编排工作流",
    hint: "根据你的描述自动生成、修改学习流程",
  },
  {
    key: "allow_agent_add_subjects",
    label: "允许智能体新增科目",
    hint: "例如“帮我加一门数据结构”",
  },
  {
    key: "allow_agent_remove_subjects",
    label: "允许智能体移除科目",
    hint: "危险操作，默认关闭；建议只在自主编排页手动删除",
  },
  {
    key: "require_confirm_for_delete",
    label: "删除操作必须二次确认",
    hint: "删除科目/工作流时要求 confirm=true",
  },
];

export default function StudioPage() {
  const overview = useApi<StudioOverview>(() => api.get<StudioOverview>("/api/studio/overview"), []);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [command, setCommand] = useState("帮我找计算机考研的数据结构教材");
  const [agentReply, setAgentReply] = useState<{ reply: string; intent: string } | null>(null);

  const [subjectForm, setSubjectForm] = useState({ name: "", subject_type: "custom", total_score: 150, major: "" });

  const [bookQuery, setBookQuery] = useState("");
  const [bookResults, setBookResults] = useState<BookItem[] | null>(null);
  const [bookBusy, setBookBusy] = useState(false);
  const [bookForm, setBookForm] = useState({ title: "", author: "", major: "", level: "基础" });

  const [workflowText, setWorkflowText] = useState("给数学一编排 基础-强化-真题-冲刺 工作流");
  const [workflowBusy, setWorkflowBusy] = useState(false);

  const refresh = () => {
    overview.reload();
    if (bookResults) void runBookSearch();
  };

  const notify = (text: string) => {
    setMessage(text);
    setError(null);
  };

  const runAgent = async () => {
    if (!command.trim()) return;
    setBusy(true);
    setAgentReply(null);
    try {
      const result = await api.post<AgentResult>("/api/agent/run", { message: command });
      setAgentReply({ reply: result.reply, intent: result.intent.ticket_type });
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "执行失败");
    } finally {
      setBusy(false);
    }
  };

  const toggleAutonomy = async (key: keyof AutonomySettings) => {
    const current = overview.data?.autonomy;
    if (!current) return;
    try {
      await api.put<AutonomySettings>("/api/autonomy", { [key]: !current[key] });
      notify("权限已更新");
      overview.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新失败");
    }
  };

  const addSubject = async () => {
    if (!subjectForm.name.trim()) return;
    setBusy(true);
    try {
      await api.post("/api/subjects", {
        name: subjectForm.name.trim(),
        subject_type: subjectForm.subject_type,
        total_score: Number(subjectForm.total_score) || 150,
        major: subjectForm.major,
      });
      notify(`已新增科目：${subjectForm.name}`);
      setSubjectForm({ name: "", subject_type: "custom", total_score: 150, major: "" });
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "新增失败");
    } finally {
      setBusy(false);
    }
  };

  const removeSubject = async (id: number, name: string) => {
    if (!window.confirm(`删除「${name}」会同时删除该科目的题库、知识图谱和工作流，确定吗？`)) return;
    setBusy(true);
    try {
      await api.delete(`/api/subjects/${id}?confirm=true`);
      notify(`已移除科目：${name}`);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    } finally {
      setBusy(false);
    }
  };

  const runBookSearch = async () => {
    setBookBusy(true);
    setError(null);
    try {
      const body = await api.post<{ items: BookItem[]; total: number; inferred_subject_type: string | null }>(
        "/api/books/search",
        { query: bookQuery },
      );
      setBookResults(body.items);
      notify(
        `找到 ${body.total} 本书${body.inferred_subject_type ? `（识别方向：${body.inferred_subject_type}）` : ""}`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "搜索失败");
    } finally {
      setBookBusy(false);
    }
  };

  const clearBookSearch = () => {
    setBookResults(null);
    setBookQuery("");
  };

  const addBook = async () => {
    if (!bookForm.title.trim()) return;
    setBookBusy(true);
    try {
      await api.post("/api/books", {
        title: bookForm.title.trim(),
        author: bookForm.author,
        major: bookForm.major,
        level: bookForm.level,
      });
      notify(`已加入书籍：${bookForm.title}`);
      setBookForm({ title: "", author: "", major: "", level: "基础" });
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "添加失败");
    } finally {
      setBookBusy(false);
    }
  };

  const archiveBook = async (book: BookItem) => {
    if (!window.confirm(`从清单移除「${book.title}」？`)) return;
    try {
      await api.delete(`/api/books/${book.id}`);
      notify(`已移除书籍：${book.title}`);
      if (bookResults) void runBookSearch();
    } catch (err) {
      setError(err instanceof Error ? err.message : "移除失败");
    }
  };

  const composeWorkflow = async (save: boolean) => {
    if (!workflowText.trim()) return;
    setWorkflowBusy(true);
    try {
      const body = await api.post<{ draft: StudyWorkflowItem; workflow: StudyWorkflowItem | null; saved: boolean }>(
        "/api/workflows/compose",
        { text: workflowText, save },
      );
      notify(
        `已${save ? "保存" : "生成草稿"}工作流：${body.draft.steps.map((step) => step.name).join(" → ")}`,
      );
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "编排失败");
    } finally {
      setWorkflowBusy(false);
    }
  };

  const patchWorkflow = async (id: number, patch: Record<string, unknown>) => {
    try {
      await api.patch(`/api/workflows/${id}`, patch);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新失败");
    }
  };

  const deleteWorkflow = async (workflow: StudyWorkflowItem) => {
    if (!window.confirm(`删除工作流「${workflow.name}」？`)) return;
    try {
      await api.delete(`/api/workflows/${workflow.id}`);
      notify(`已删除工作流：${workflow.name}`);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    }
  };

  const books = bookResults ?? [];

  return (
    <>
      <Panel
        title="自然语言指挥"
        right={
          <button className="btn" onClick={refresh} disabled={overview.loading}>
            刷新全部
          </button>
        }
      >
        <div className="small muted" style={{ marginBottom: 10 }}>
          用一句话完成：找其他学科/专业的书、编排新学习工作流、增删科目、调整智能体权限。
        </div>
        <div className="field-row">
          <input
            className="input"
            style={{ flex: 1, minWidth: 320 }}
            value={command}
            onChange={(event) => setCommand(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void runAgent();
            }}
            placeholder="例如：帮我找心理学考研的实验心理学教材 / 给数据结构编排基础-强化-真题工作流 / 移除英语一"
          />
          <button className="btn primary" onClick={runAgent} disabled={busy}>
            {busy ? "执行中…" : "发送给智能体"}
          </button>
        </div>
        <div className="chips" style={{ marginTop: 8 }}>
          {[
            "帮我找计算机考研的数据结构和操作系统教材",
            "给英语一编排 词汇-阅读-写作-真题 工作流",
            "新增科目 数据结构与算法",
            "移除英语一",
          ].map((example) => (
            <button key={example} className="btn small" onClick={() => setCommand(example)}>
              {example}
            </button>
          ))}
        </div>
        {agentReply && (
          <div className="panel flat" style={{ marginTop: 12, padding: 12 }}>
            <div className="row between">
              <Badge tone="accent">{agentReply.intent}</Badge>
              <span className="tiny dim">由 Router → Worker 执行，结果已同步到下方</span>
            </div>
            <div className="small pre-line" style={{ marginTop: 8 }}>
              {agentReply.reply}
            </div>
          </div>
        )}
        {message && <div className="small accent" style={{ marginTop: 8 }}>{message}</div>}
        {error && <div style={{ marginTop: 8 }}><ErrorBox error={error} /></div>}
      </Panel>

      {overview.loading && !overview.data && <Loading text="加载自主编排数据" />}
      {overview.error && <ErrorBox error={overview.error} retry={overview.reload} />}
      {overview.data && (
        <>
          <div className="grid g4">
            <StatCard label="备考科目" value={overview.data.subjects.length} hint="可增删" />
            <StatCard label="书籍资料" value={overview.data.books.active} hint={`共 ${overview.data.books.total} 本`} />
            <StatCard label="学习工作流" value={overview.data.workflows.length} hint="可编排 / 暂停 / 删除" />
            <StatCard
              label="智能体权限"
              value={Object.values(overview.data.autonomy).filter(Boolean).length}
              hint="已开启项"
              tone="accent"
            />
          </div>

          <div className="grid g2">
            <Panel title="科目管理">
              <div className="list">
                {overview.data.subjects.map((subject) => (
                  <div key={subject.id} className="panel flat" style={{ padding: 10 }}>
                    <div className="row between">
                      <div>
                        <span className="strong">{subject.name}</span>{" "}
                        <Badge>{SUBJECT_TYPE_LABELS[subject.subject_type] ?? subject.subject_type}</Badge>{" "}
                        <span className="mono tiny dim">{subject.total_score} 分</span>
                      </div>
                      <button className="btn small danger" onClick={() => removeSubject(subject.id, subject.name)}>
                        移除
                      </button>
                    </div>
                  </div>
                ))}
                {overview.data.subjects.length === 0 && <EmptyState>还没有科目</EmptyState>}
              </div>
              <div className="sep" />
              <div className="field-row">
                <input
                  className="input"
                  placeholder="科目名，如 数据结构 / 经济学"
                  value={subjectForm.name}
                  onChange={(event) => setSubjectForm({ ...subjectForm, name: event.target.value })}
                />
                <select
                  className="select"
                  value={subjectForm.subject_type}
                  onChange={(event) => setSubjectForm({ ...subjectForm, subject_type: event.target.value })}
                >
                  {Object.entries(SUBJECT_TYPE_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
                <input
                  className="input"
                  type="number"
                  style={{ width: 90 }}
                  value={subjectForm.total_score}
                  onChange={(event) =>
                    setSubjectForm({ ...subjectForm, total_score: Number(event.target.value) })
                  }
                />
                <button className="btn primary" onClick={addSubject} disabled={busy}>
                  新增科目
                </button>
              </div>
            </Panel>

            <Panel title="智能体权限（自主更改）">
              <div className="list">
                {AUTONOMY_LABELS.map((item) => (
                  <label key={item.key} className="row between" style={{ cursor: "pointer", padding: "4px 0" }}>
                    <span>
                      <span className="strong small">{item.label}</span>
                      <div className="tiny dim">{item.hint}</div>
                    </span>
                    <input
                      type="checkbox"
                      checked={Boolean(overview.data?.autonomy[item.key])}
                      onChange={() => toggleAutonomy(item.key)}
                    />
                  </label>
                ))}
              </div>
            </Panel>
          </div>

          <Panel
            title="书籍资料（自然语言检索）"
            right={
              bookResults ? (
                <button className="btn small" onClick={clearBookSearch}>
                  返回书库
                </button>
              ) : undefined
            }
          >
            <div className="field-row">
              <input
                className="input"
                style={{ flex: 1, minWidth: 280 }}
                value={bookQuery}
                onChange={(event) => setBookQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void runBookSearch();
                }}
                placeholder="描述你要找的书或方向，例如：计算机 408 数据结构 / 管理学 罗宾斯"
              />
              <button className="btn primary" onClick={runBookSearch} disabled={bookBusy}>
                {bookBusy ? "搜索中…" : "搜索书籍"}
              </button>
            </div>

            <div className="list" style={{ marginTop: 12 }}>
              {books.length === 0 && (
                <EmptyState>
                  {bookResults ? "没有匹配的书籍，换个关键词或手动添加" : "输入自然语言检索，例如“心理学考研教材”"}
                </EmptyState>
              )}
              {books.map((book) => (
                <div key={book.id} className="panel flat" style={{ padding: 12 }}>
                  <div className="row between">
                    <div className="strong">{book.title}</div>
                    <div className="chips">
                      <Badge tone={LEVEL_TONES[book.level] ?? "accent"}>{book.level}</Badge>
                      {book.major && <Badge>{book.major}</Badge>}
                      {book.score !== null && <Badge tone="accent">匹配 {book.score}</Badge>}
                    </div>
                  </div>
                  <div className="tiny dim">
                    {book.author}
                    {book.publisher ? ` · ${book.publisher}` : ""}
                  </div>
                  {book.reason && <div className="tiny accent">{book.reason}</div>}
                  <div className="chips" style={{ marginTop: 6 }}>
                    {(book.tags ?? []).slice(0, 5).map((tag) => (
                      <Badge key={tag}>{tag}</Badge>
                    ))}
                  </div>
                  <div className="btn-row" style={{ marginTop: 8 }}>
                    <button className="btn small danger" onClick={() => archiveBook(book)}>
                      从清单移除
                    </button>
                  </div>
                </div>
              ))}
            </div>

            <div className="sep" />
            <div className="field-row">
              <input
                className="input"
                placeholder="书名"
                value={bookForm.title}
                onChange={(event) => setBookForm({ ...bookForm, title: event.target.value })}
              />
              <input
                className="input"
                placeholder="作者"
                value={bookForm.author}
                onChange={(event) => setBookForm({ ...bookForm, author: event.target.value })}
              />
              <input
                className="input"
                placeholder="专业/方向"
                value={bookForm.major}
                onChange={(event) => setBookForm({ ...bookForm, major: event.target.value })}
              />
              <select
                className="select"
                value={bookForm.level}
                onChange={(event) => setBookForm({ ...bookForm, level: event.target.value })}
              >
                {["基础", "强化", "冲刺", "进阶"].map((level) => (
                  <option key={level} value={level}>
                    {level}
                  </option>
                ))}
              </select>
              <button className="btn primary" onClick={addBook} disabled={bookBusy}>
                加入书单
              </button>
            </div>
          </Panel>

          <Panel
            title="学习工作流"
            right={
              <button className="btn small" onClick={() => composeWorkflow(false)} disabled={workflowBusy}>
                生成草稿
              </button>
            }
          >
            <div className="field-row">
              <input
                className="input"
                style={{ flex: 1, minWidth: 300 }}
                value={workflowText}
                onChange={(event) => setWorkflowText(event.target.value)}
                placeholder="描述学习流程，例如：给数学一编排 基础-强化-真题-冲刺 工作流"
              />
              <button className="btn primary" onClick={() => composeWorkflow(true)} disabled={workflowBusy}>
                {workflowBusy ? "编排中…" : "编排并保存"}
              </button>
            </div>

            <div className="list" style={{ marginTop: 12 }}>
              {overview.data.workflows.length === 0 && <EmptyState>还没有工作流，用上面的输入框编排一个</EmptyState>}
              {overview.data.workflows.map((workflow) => (
                <div key={workflow.id} className="panel flat" style={{ padding: 12 }}>
                  <div className="row between">
                    <div className="row wrap" style={{ gap: 8 }}>
                      <span className="strong">{workflow.name}</span>
                      {workflow.subject_name && <Badge tone="accent">{workflow.subject_name}</Badge>}
                      <Badge tone={workflow.status === "active" ? "good" : "warn"}>{workflow.status}</Badge>
                      <span className="tiny dim">来源：{workflow.source}</span>
                    </div>
                    <div className="btn-row">
                      <button
                        className="btn small"
                        onClick={() =>
                          patchWorkflow(workflow.id, {
                            status: workflow.status === "active" ? "paused" : "active",
                          })
                        }
                      >
                        {workflow.status === "active" ? "暂停" : "启用"}
                      </button>
                      <button className="btn small danger" onClick={() => deleteWorkflow(workflow)}>
                        删除
                      </button>
                    </div>
                  </div>
                  {workflow.description && <div className="tiny dim">{shortText(workflow.description, 90)}</div>}
                  <div className="row wrap" style={{ gap: 6, marginTop: 8 }}>
                    {workflow.steps.map((step, index) => (
                      <span key={`${workflow.id}-${index}`} className="row" style={{ gap: 6 }}>
                        <span className="badge accent">
                          {step.name} · {step.minutes}min
                        </span>
                        {index < workflow.steps.length - 1 && <span className="dim tiny">→</span>}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </Panel>
        </>
      )}
    </>
  );
}
