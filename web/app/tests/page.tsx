"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Badge,
  Drawer,
  ErrorBox,
  KV,
  Loading,
  Panel,
  StatCard,
  scoreTone,
  statusTone,
  useApi,
  useNow,
} from "@/components/ui";
import { api, type QuestionItem, type Subject, type TestReport, type TestSummary } from "@/lib/api";
import { fmtDate, fmtNum, fmtPct, shortText, TEST_LABELS } from "@/lib/format";

interface GeneratedTest {
  test: {
    id: number;
    subject_id: number;
    test_type: string;
    max_score: number;
    blueprint: Record<string, unknown>;
    status: string;
  };
  questions: QuestionItem[];
}

interface AnswerDraft {
  answer_text: string;
  confidence_self: number;
  hint_count: number;
}

function formatClock(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const mm = String(Math.floor(safe / 60)).padStart(2, "0");
  const ss = String(safe % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

export default function TestsPage() {
  const profile = useApi<{ subjects: Subject[] }>(() => api.get("/api/profile"), []);
  const past = useApi<{ items: TestSummary[] }>(() => api.get("/api/tests?limit=20"), []);
  const now = useNow(1000);

  const [subjectType, setSubjectType] = useState("math_1");
  const [testType, setTestType] = useState("micro");
  const [duration, setDuration] = useState(15);
  const [generated, setGenerated] = useState<GeneratedTest | null>(null);
  const [answers, setAnswers] = useState<Record<number, AnswerDraft>>({});
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [result, setResult] = useState<TestReport | null>(null);
  const [reportOpen, setReportOpen] = useState<TestSummary | null>(null);
  const [report, setReport] = useState<TestReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const elapsed = startedAt ? (now - startedAt) / 1000 : 0;
  const limitSeconds = duration * 60;
  const remaining = limitSeconds - elapsed;

  const generate = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const body = await api.post<GeneratedTest>("/api/tests/generate", {
        subject_type: subjectType,
        test_type: testType,
        duration_min: duration,
      });
      setGenerated(body);
      setAnswers({});
      setStartedAt(Date.now());
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成失败");
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    if (!generated) return;
    setBusy(true);
    setError(null);
    try {
      const perQuestion = generated.questions.length
        ? Math.max(5, Math.round(elapsed / generated.questions.length))
        : 30;
      const payload = {
        answers: generated.questions.map((question) => ({
          question_id: question.id,
          answer_text: answers[question.id]?.answer_text ?? "",
          duration_sec: perQuestion,
          hint_count: answers[question.id]?.hint_count ?? 0,
          confidence_self: answers[question.id]?.confidence_self ?? 0.5,
          is_independent: true,
        })),
      };
      const report = await api.post<TestReport>(`/api/tests/${generated.test.id}/submit`, payload);
      setResult(report);
      setGenerated(null);
      setStartedAt(null);
      past.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败");
    } finally {
      setBusy(false);
    }
  };

  const openReport = async (test: TestSummary) => {
    setReportOpen(test);
    setReport(null);
    try {
      const body = await api.get<{ report: TestReport }>(`/api/tests/${test.id}/report`);
      setReport(body.report);
    } catch {
      const blueprintReport = (test.blueprint as { report?: TestReport })?.report;
      setReport(blueprintReport ?? null);
    }
  };

  const blueprintEntries = useMemo(() => {
    if (!generated) return [] as [string, unknown][];
    const blueprint = generated.test.blueprint as Record<string, unknown>;
    return Object.entries(blueprint).filter(([key]) => ["mix_target", "bucket_counts", "description"].includes(key));
  }, [generated]);

  return (
    <>
      <Panel title="生成测试">
        <div className="field-row">
          <label className="field">
            科目
            <select className="select" value={subjectType} onChange={(event) => setSubjectType(event.target.value)}>
              {(profile.data?.subjects ?? []).map((subject) => (
                <option key={subject.id} value={subject.subject_type}>
                  {subject.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            测试类型
            <select className="select" value={testType} onChange={(event) => setTestType(event.target.value)}>
              {Object.entries(TEST_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            限时（分钟）
            <input
              className="input"
              type="number"
              min={5}
              max={240}
              value={duration}
              onChange={(event) => setDuration(Number(event.target.value))}
              style={{ width: 90 }}
            />
          </label>
          <button className="btn primary" onClick={generate} disabled={busy}>
            {busy ? "生成中…" : "按测试蓝图生成"}
          </button>
          {generated && (
            <button className="btn" onClick={submit} disabled={busy}>
              提交测试
            </button>
          )}
        </div>
        {error && <div style={{ marginTop: 10 }}><ErrorBox error={error} /></div>}
      </Panel>

      {generated && (
        <Panel
          title={`正在作答 · ${TEST_LABELS[generated.test.test_type] ?? generated.test.test_type}`}
          right={
            <span className={`timer ${remaining < 120 ? "danger" : ""}`}>
              {formatClock(Math.max(0, remaining))}
            </span>
          }
        >
          <div className="small muted" style={{ marginBottom: 10 }}>
            {generated.questions.length} 题 · 满分 {fmtNum(generated.test.max_score, 0)} · 蓝图：
            {blueprintEntries.map(([key, value]) => (
              <span key={key} className="mono tiny dim" style={{ marginLeft: 8 }}>
                {key}={JSON.stringify(value)}
              </span>
            ))}
          </div>
          <div className="list">
            {generated.questions.map((question, index) => (
              <div key={question.id} className="panel flat">
                <div className="row between">
                  <span className="mono dim tiny">
                    第 {index + 1} 题 · {question.question_type} · {fmtNum(question.score, 0)} 分
                  </span>
                  <div className="chips">
                    {question.knowledge.slice(0, 2).map((link) => (
                      <Badge key={link.knowledge_id}>{link.code}</Badge>
                    ))}
                  </div>
                </div>
                <div className="question-content" style={{ margin: "10px 0" }}>
                  {question.content}
                </div>
                <textarea
                  className="textarea"
                  placeholder="输入你的答案（选择题填选项字母，计算/简答题写过程与结果）"
                  value={answers[question.id]?.answer_text ?? ""}
                  onChange={(event) =>
                    setAnswers((previous) => ({
                      ...previous,
                      [question.id]: {
                        answer_text: event.target.value,
                        confidence_self: previous[question.id]?.confidence_self ?? 0.5,
                        hint_count: previous[question.id]?.hint_count ?? 0,
                      },
                    }))
                  }
                />
                <div className="field-row" style={{ marginTop: 8 }}>
                  <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                    信心
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.1}
                      value={answers[question.id]?.confidence_self ?? 0.5}
                      onChange={(event) =>
                        setAnswers((previous) => ({
                          ...previous,
                          [question.id]: {
                            answer_text: previous[question.id]?.answer_text ?? "",
                            confidence_self: Number(event.target.value),
                            hint_count: previous[question.id]?.hint_count ?? 0,
                          },
                        }))
                      }
                    />
                    <span className="mono tiny">{fmtNum(answers[question.id]?.confidence_self ?? 0.5, 1)}</span>
                  </label>
                  <button
                    className="btn small"
                    onClick={() =>
                      setAnswers((previous) => ({
                        ...previous,
                        [question.id]: {
                          answer_text: previous[question.id]?.answer_text ?? "",
                          confidence_self: previous[question.id]?.confidence_self ?? 0.5,
                          hint_count: (previous[question.id]?.hint_count ?? 0) + 1,
                        },
                      }))
                    }
                  >
                    记录提示 {answers[question.id]?.hint_count ?? 0}
                  </button>
                  <span className="tiny dim">提示后答对不计入独立掌握</span>
                </div>
              </div>
            ))}
          </div>
        </Panel>
      )}

      {result && (
        <>
          <div className="grid g4">
            <StatCard label="得分" value={fmtNum(result.score, 1)} hint={`满分 ${fmtNum(result.max_score, 0)}`} tone={scoreTone(result.score, result.max_score)} />
            <StatCard label="正确率" value={fmtPct(result.accuracy)} hint={`${result.correct}/${result.question_count}`} />
            <StatCard label="诊断条目" value={result.diagnosis.length} tone="warn" />
            <StatCard label="回滚知识点" value={result.rollback.nodes.length} tone="bad" />
          </div>
          <Panel title="测试报告与诊断">
            <div className="list">
              {result.diagnosis.map((item, index) => (
                <div key={`${item.question_id}-${index}`} className="panel flat" style={{ padding: 12 }}>
                  <div className="row wrap">
                    <Badge tone="bad">{item.label}</Badge>
                    <span className="mono tiny dim">{item.question_key}</span>
                    <span className="tiny dim">{item.knowledge.join(", ")}</span>
                  </div>
                  <div className="small" style={{ marginTop: 6 }}>{item.explanation}</div>
                </div>
              ))}
              {result.diagnosis.length === 0 && <div className="empty">本次测试没有错误诊断项</div>}
            </div>
            {result.rollback.needed && (
              <div className="panel flat" style={{ marginTop: 12 }}>
                <div className="row between">
                  <span className="strong">最小必要回滚</span>
                  <Badge tone="warn">{result.rollback.strategy}</Badge>
                </div>
                <div className="list" style={{ marginTop: 8 }}>
                  {result.rollback.nodes.map((node) => (
                    <div key={node.knowledge_id} className="row between small">
                      <span>
                        <span className="mono tiny dim">{node.code}</span> {node.name}
                      </span>
                      <span className="muted">{node.reason}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {result.next_actions.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <div className="tiny dim" style={{ marginBottom: 6 }}>下一步动作</div>
                <div className="list small">
                  {result.next_actions.map((action, index) => (
                    <div key={index}>· {action}</div>
                  ))}
                </div>
              </div>
            )}
          </Panel>
          <Panel title="逐题结果">
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>题目</th>
                    <th>结果</th>
                    <th>得分</th>
                    <th>标准答案</th>
                    <th>解析</th>
                  </tr>
                </thead>
                <tbody>
                  {result.results.map((item) => (
                    <tr key={item.question_id}>
                      <td className="mono tiny">{item.key}</td>
                      <td>
                        <Badge tone={item.correct ? "good" : "bad"}>{item.correct ? "正确" : "错误"}</Badge>
                      </td>
                      <td className="num">
                        {fmtNum(item.score, 1)} / {fmtNum(item.max_score, 0)}
                      </td>
                      <td className="mono tiny accent">{item.answer}</td>
                      <td className="tiny dim clamp-3">{shortText(item.explanation, 80)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
          {result.mastery_delta.length > 0 && (
            <Panel title="掌握度变化">
              <div className="list">
                {result.mastery_delta.map((item) => (
                  <div key={item.knowledge_id} className="row between small">
                    <span className="mono tiny dim">knowledge #{item.knowledge_id}</span>
                    <span className="mono">
                      {fmtPct(item.before)} → {fmtPct(item.after)}{" "}
                      <span className={item.delta >= 0 ? "good" : "bad"}>
                        ({item.delta >= 0 ? "+" : ""}
                        {fmtPct(item.delta, 1)})
                      </span>
                    </span>
                  </div>
                ))}
              </div>
            </Panel>
          )}
        </>
      )}

      {!generated && !result && (
        <Panel title="历史测试">
          {past.loading && !past.data && <Loading text="加载测试记录" />}
          {past.error && <ErrorBox error={past.error} retry={past.reload} />}
          {past.data && past.data.items.length === 0 && <div className="empty">还没有测试记录</div>}
          {past.data && past.data.items.length > 0 && (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>编号</th>
                    <th>类型</th>
                    <th>状态</th>
                    <th className="num">得分</th>
                    <th className="num">正确率</th>
                    <th>时间</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {past.data.items.map((test) => (
                    <tr key={test.id}>
                      <td className="mono">#{test.id}</td>
                      <td>{TEST_LABELS[test.test_type] ?? test.test_type}</td>
                      <td>
                        <Badge tone={statusTone(test.status)}>{test.status}</Badge>
                      </td>
                      <td className="num">
                        {fmtNum(test.score, 1)} / {fmtNum(test.max_score, 0)}
                      </td>
                      <td className="num">{test.accuracy === null ? "—" : fmtPct(test.accuracy)}</td>
                      <td className="tiny dim">{fmtDate(test.created_at)}</td>
                      <td>
                        <button className="btn small" onClick={() => openReport(test)}>
                          查看报告
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      )}

      {reportOpen && (
        <Drawer
          title={`测试报告 #${reportOpen.id}`}
          subtitle={`${TEST_LABELS[reportOpen.test_type] ?? reportOpen.test_type} · ${fmtDate(reportOpen.created_at)}`}
          onClose={() => {
            setReportOpen(null);
            setReport(null);
          }}
        >
          {!report && <div className="empty">该测试尚未提交，或报告不可用</div>}
          {report && (
            <>
              <div className="grid g2">
                <StatCard label="得分" value={fmtNum(report.score, 1)} hint={`满分 ${fmtNum(report.max_score, 0)}`} />
                <StatCard label="正确率" value={fmtPct(report.accuracy)} hint={`${report.correct}/${report.question_count}`} />
              </div>
              <div className="list">
                {report.results.map((item) => (
                  <div key={item.question_id} className="panel flat" style={{ padding: 10 }}>
                    <div className="row between">
                      <span className="mono tiny dim">{item.key}</span>
                      <Badge tone={item.correct ? "good" : "bad"}>{item.correct ? "正确" : "错误"}</Badge>
                    </div>
                    <div className="tiny accent" style={{ marginTop: 4 }}>答案：{item.answer}</div>
                  </div>
                ))}
              </div>
              {report.rollback.needed && (
                <div>
                  <div className="tiny dim" style={{ marginBottom: 6 }}>回滚计划</div>
                  <KV
                    rows={report.rollback.nodes.map((node) => [
                      node.code,
                      `${node.name} · 掌握度 ${fmtPct(node.mastery)} · ${node.reason}`,
                    ])}
                  />
                </div>
              )}
            </>
          )}
        </Drawer>
      )}
    </>
  );
}
