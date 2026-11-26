"use client";

import { useState } from "react";
import {
  Badge,
  ErrorBox,
  Loading,
  Panel,
  ProgressBar,
  StatCard,
  statusTone,
  useApi,
  useApi as _useApi,
} from "@/components/ui";
import { api, type Plan, type Subject } from "@/lib/api";
import { fmtMinutes, fmtPct, shortText, TASK_LABELS, TEST_LABELS } from "@/lib/format";

function todayString(): string {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

export default function PlanPage() {
  const [date, setDate] = useState(todayString());
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const profile = useApi<{ subjects: Subject[] }>(() => api.get("/api/profile"), []);
  const { data, error, loading, reload, setData } = useApi<Plan>(
    () => api.get<Plan>(`/api/plan/today?date=${date}`),
    [date],
  );

  const updateTask = async (taskId: number, status: string) => {
    setBusy(true);
    try {
      await api.patch(`/api/tasks/${taskId}`, { status });
      setMessage("任务状态已更新");
      reload();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "更新失败");
    } finally {
      setBusy(false);
    }
  };

  const regenerate = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const plan = await api.post<Plan>("/api/plan/regenerate", { date });
      setData(plan);
      setMessage("已按最新 ROI 与到期复习重新生成计划");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "生成失败");
    } finally {
      setBusy(false);
    }
  };

  const budgetRatio = data ? data.used_minutes / Math.max(1, data.budget_minutes) : 0;
  const subjects = profile.data?.subjects ?? [];

  return (
    <>
      <Panel
        title="计划控制"
        right={
          <div className="btn-row">
            <input
              className="input"
              type="date"
              value={date}
              onChange={(event) => setDate(event.target.value)}
            />
            <button className="btn" onClick={reload} disabled={loading}>
              刷新
            </button>
            <button className="btn primary" onClick={regenerate} disabled={busy}>
              {busy ? "生成中…" : "重新生成计划"}
            </button>
          </div>
        }
      >
        {message && <div className="small accent" style={{ marginBottom: 10 }}>{message}</div>}
        {loading && !data && <Loading text="生成今日计划" />}
        {error && <ErrorBox error={error} retry={reload} />}
        {data && (
          <>
            <div className="grid g4">
              <StatCard label="日期" value={data.date} hint={data.generated_at?.slice(0, 16).replace("T", " ")} />
              <StatCard label="时间预算" value={fmtMinutes(data.budget_minutes)} />
              <StatCard
                label="已安排"
                value={fmtMinutes(data.used_minutes)}
                hint={`占用 ${fmtPct(budgetRatio)}`}
                tone={budgetRatio > 1.1 ? "bad" : "accent"}
              />
              <StatCard
                label="任务数"
                value={data.tasks.length}
                hint={`${data.tasks.filter((task) => task.status === "DONE").length} 项已完成`}
              />
            </div>
            <div style={{ marginTop: 14 }}>
              <ProgressBar
                value={data.used_minutes}
                total={data.budget_minutes}
                color={budgetRatio > 1 ? "var(--warn)" : "var(--accent)"}
                height={8}
              />
            </div>
          </>
        )}
      </Panel>

      {data && (
        <div className="grid g2">
          <Panel title="学习任务">
            {data.tasks.length === 0 ? (
              <div className="empty">今天还没有任务，点击「重新生成计划」</div>
            ) : (
              <div className="list">
                {data.tasks.map((task) => (
                  <div key={task.id} className="panel flat" style={{ padding: 12 }}>
                    <div className="row between">
                      <div style={{ minWidth: 0 }}>
                        <div className="row wrap" style={{ gap: 8 }}>
                          <Badge tone={task.task_type === "test" ? "warn" : "accent"}>
                            {TASK_LABELS[task.task_type] ?? task.task_type}
                          </Badge>
                          <span className="strong">{task.title}</span>
                          {task.subject_name && <span className="tiny dim">{task.subject_name}</span>}
                          <Badge tone={statusTone(task.status)}>{task.status}</Badge>
                        </div>
                        <div className="tiny dim" style={{ marginTop: 4 }}>
                          {shortText(task.detail, 110)}
                        </div>
                      </div>
                      <div className="btn-row">
                        <span className="mono tiny">{Math.round(task.est_minutes)} 分</span>
                        <button
                          className="btn small"
                          disabled={busy}
                          onClick={() => updateTask(task.id, "DONE")}
                        >
                          完成
                        </button>
                        <button
                          className="btn small"
                          disabled={busy}
                          onClick={() => updateTask(task.id, "SKIPPED")}
                        >
                          跳过
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <div className="list">
            <Panel title="到期复习">
              {data.due_reviews.length === 0 ? (
                <div className="empty">今天没有到期卡片</div>
              ) : (
                <div className="list">
                  {data.due_reviews.map((card) => (
                    <div key={card.id} className="row between small">
                      <span className="muted">{shortText(card.front, 60)}</span>
                      <Badge>{card.state}</Badge>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
            <Panel title="周期测试">
              {data.due_tests.length === 0 ? (
                <div className="empty">今天没有到期的测试</div>
              ) : (
                <div className="list">
                  {data.due_tests.map((item) => (
                    <div key={item.test_type} className="row between small">
                      <span>
                        <Badge tone="warn">{TEST_LABELS[item.test_type] ?? item.test_type}</Badge>{" "}
                        <span className="muted">{item.reason}</span>
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
            <Panel title="可用科目">
              <div className="chips">
                {subjects.map((subject) => (
                  <Badge key={subject.id} tone="accent">
                    {subject.name}
                  </Badge>
                ))}
              </div>
            </Panel>
          </div>
        </div>
      )}
    </>
  );
}
