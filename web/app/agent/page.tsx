"use client";

import { useState } from "react";
import {
  Badge,
  ErrorBox,
  Loading,
  Panel,
  statusTone,
  useApi,
} from "@/components/ui";
import { api, type AgentResult, type Ticket } from "@/lib/api";
import { fmtDate, fmtNum, shortText, WORKER_LABELS } from "@/lib/format";

interface ChatMessage {
  role: "user" | "agent";
  text: string;
  trace?: AgentResult["trace"];
  requiresReview?: boolean;
}

interface Finding {
  code: string;
  severity: string;
  title: string;
  detail: string;
  action: string;
  auto_fixed: boolean;
}

export default function AgentPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("帮我重新计算 ROI 并安排明天上午的学习");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [scanBusy, setScanBusy] = useState(false);
  const tickets = useApi<{ items: Ticket[] }>(() => api.get("/api/tickets?limit=30"), []);
  const reviews = useApi<{ items: { id: number; question: string; status: string; decision: string }[] }>(
    () => api.get("/api/human-review?status=pending"),
    [],
  );

  const runAgent = async () => {
    if (!input.trim()) return;
    const message = input.trim();
    setInput("");
    setMessages((previous) => [...previous, { role: "user", text: message }]);
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<AgentResult>("/api/agent/run", { message });
      setMessages((previous) => [
        ...previous,
        { role: "agent", text: result.reply, trace: result.trace, requiresReview: result.requires_human_review },
      ]);
      tickets.reload();
      reviews.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "执行失败");
    } finally {
      setBusy(false);
    }
  };

  const runTicket = async (ticket: Ticket) => {
    setBusy(true);
    try {
      const result = await api.post<{ report: { summary: string } }>(`/api/tickets/${ticket.id}/run`, {});
      setMessages((previous) => [
        ...previous,
        { role: "agent", text: `Ticket #${ticket.id} 完成：${result.report.summary}` },
      ]);
      tickets.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "任务执行失败");
    } finally {
      setBusy(false);
    }
  };

  const scan = async () => {
    setScanBusy(true);
    try {
      const result = await api.post<{ findings: Finding[] }>("/api/watchdog/scan", { auto_fix: true });
      setFindings(result.findings);
    } catch (err) {
      setError(err instanceof Error ? err.message : "自检失败");
    } finally {
      setScanBusy(false);
    }
  };

  const resolveReview = async (id: number, decision: "approve" | "reject") => {
    try {
      await api.post(`/api/human-review/${id}/resolve`, { decision, comment: "" });
      reviews.reload();
      tickets.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "核验失败");
    }
  };

  return (
    <>
      <div className="grid g2">
        <Panel title="Router 控制台">
          <div className="chat">
            {messages.length === 0 && (
              <div className="small dim">
                输入自然语言指令，Router 会解析意图 → 创建 Ticket → 调用对应 Worker → 返回结构化报告。
                试试：统计数学一考频 / 生成一道 L2 变式题 / 安排今天的学习 / 诊断最近的错题。
              </div>
            )}
            {messages.map((message, index) => (
              <div key={index} className={`bubble ${message.role}`}>
                <div className="pre-line">{message.text}</div>
                {message.trace && (
                  <div className="trace" style={{ marginTop: 8 }}>
                    {message.trace.map((step, stepIndex) => (
                      <div key={stepIndex} className="trace-step">
                        <span className="accent">{step.step}</span>
                        <span>{shortText(JSON.stringify(step), 120)}</span>
                      </div>
                    ))}
                  </div>
                )}
                {message.requiresReview && (
                  <div className="tiny warn" style={{ marginTop: 6 }}>
                    该报告进入人工核验队列
                  </div>
                )}
              </div>
            ))}
            {busy && <span className="loading">Worker 执行中</span>}
          </div>
          <div className="field-row" style={{ marginTop: 12 }}>
            <input
              className="input"
              style={{ flex: 1 }}
              value={input}
              placeholder="输入指令，例如：统计数学一历年考频"
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") runAgent();
              }}
            />
            <button className="btn primary" onClick={runAgent} disabled={busy}>
              发送
            </button>
          </div>
          {error && <div style={{ marginTop: 10 }}><ErrorBox error={error} /></div>}
        </Panel>

        <Panel
          title="Watchdog 自检"
          right={
            <button className="btn" onClick={scan} disabled={scanBusy}>
              {scanBusy ? "扫描中…" : "运行扫描"}
            </button>
          }
        >
          {findings.length === 0 ? (
            <div className="empty">尚未扫描。Watchdog 检查任务停滞、重复任务、生成题验证失败、计划积压、反复遗忘与 ROI 偏离。</div>
          ) : (
            <div className="list">
              {findings.map((finding) => (
                <div key={`${finding.code}-${finding.title}`} className="panel flat" style={{ padding: 10 }}>
                  <div className="row wrap">
                    <Badge tone={finding.severity === "high" ? "bad" : finding.severity === "medium" ? "warn" : "accent"}>
                      {finding.severity}
                    </Badge>
                    <span className="strong">{finding.title}</span>
                    {finding.auto_fixed && <Badge tone="good">已自动修复</Badge>}
                  </div>
                  <div className="tiny dim">{finding.detail}</div>
                  <div className="tiny muted">建议：{finding.action}</div>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>

      <Panel
        title="任务队列（Ticket / Assignment）"
        right={
          <button className="btn" onClick={tickets.reload} disabled={tickets.loading}>
            刷新
          </button>
        }
      >
        {tickets.loading && !tickets.data && <Loading text="加载任务" />}
        {tickets.error && <ErrorBox error={tickets.error} retry={tickets.reload} />}
        {tickets.data && tickets.data.items.length === 0 && <div className="empty">暂无任务</div>}
        {tickets.data && tickets.data.items.length > 0 && (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>类型</th>
                  <th>标题</th>
                  <th>状态</th>
                  <th>Worker</th>
                  <th>最新报告</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {tickets.data.items.map((ticket) => (
                  <tr key={ticket.id}>
                    <td className="mono">#{ticket.id}</td>
                    <td className="tiny">{ticket.ticket_type}</td>
                    <td>{ticket.title}</td>
                    <td>
                      <Badge tone={statusTone(ticket.status)}>{ticket.status}</Badge>
                    </td>
                    <td className="tiny muted">
                      {WORKER_LABELS[(ticket.payload?.worker_type as string) ?? ""] ??
                        ((ticket.payload?.worker_type as string) ?? "—")}
                    </td>
                    <td className="tiny dim" style={{ maxWidth: 300 }}>
                      {ticket.latest_report ? shortText(ticket.latest_report.summary, 70) : "—"}
                    </td>
                    <td>
                      <button className="btn small" onClick={() => runTicket(ticket)} disabled={busy}>
                        重新执行
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel title="人工核验队列">
        {reviews.loading && !reviews.data && <Loading text="加载核验" />}
        {reviews.data && reviews.data.items.length === 0 && <div className="empty">没有待核验项</div>}
        {reviews.data && reviews.data.items.length > 0 && (
          <div className="list">
            {reviews.data.items.map((review) => (
              <div key={review.id} className="panel flat" style={{ padding: 10 }}>
                <div className="row between">
                  <span className="small">{review.question}</span>
                  <div className="btn-row">
                    <button className="btn small" onClick={() => resolveReview(review.id, "approve")}>
                      通过
                    </button>
                    <button className="btn small danger" onClick={() => resolveReview(review.id, "reject")}>
                      驳回
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>
    </>
  );
}
