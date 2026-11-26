"use client";

import { useState } from "react";
import {
  Badge,
  Drawer,
  ErrorBox,
  KV,
  Loading,
  Panel,
  useApi,
} from "@/components/ui";
import { api, type QuestionItem, type Subject } from "@/lib/api";
import { fmtNum, shortText } from "@/lib/format";

interface QuestionList {
  items: QuestionItem[];
  total: number;
}

interface TransformResult {
  items: {
    question: QuestionItem;
    verification: {
      status: string;
      confidence: number;
      checks: { check: string; passed: boolean; detail: string }[];
    };
    rationale: string;
    level: string;
  }[];
  status: string;
  note: string;
}

const PAGE_SIZE = 20;

export default function QuestionsPage() {
  const [subjectId, setSubjectId] = useState("");
  const [synthetic, setSynthetic] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<QuestionItem | null>(null);
  const [level, setLevel] = useState("L1");
  const [count, setCount] = useState(1);
  const [transformBusy, setTransformBusy] = useState(false);
  const [transformResult, setTransformResult] = useState<TransformResult | null>(null);
  const [transformError, setTransformError] = useState<string | null>(null);
  const profile = useApi<{ subjects: Subject[] }>(() => api.get("/api/profile"), []);

  const query = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  if (subjectId) query.set("subject_id", subjectId);
  if (synthetic !== "") query.set("synthetic", synthetic);

  const { data, error, loading, reload } = useApi<QuestionList>(
    () => api.get<QuestionList>(`/api/questions?${query.toString()}`),
    [subjectId, synthetic, offset],
  );

  const openQuestion = async (id: number) => {
    try {
      const detail = await api.get<QuestionItem>(`/api/questions/${id}`);
      setSelected(detail);
      setTransformResult(null);
      setTransformError(null);
    } catch (err) {
      setTransformError(err instanceof Error ? err.message : "加载失败");
    }
  };

  const transform = async () => {
    if (!selected) return;
    setTransformBusy(true);
    setTransformError(null);
    setTransformResult(null);
    try {
      const result = await api.post<TransformResult>("/api/questions/transform", {
        question_id: selected.id,
        level,
        count,
      });
      setTransformResult(result);
      reload();
    } catch (err) {
      setTransformError(err instanceof Error ? err.message : "生成失败");
    } finally {
      setTransformBusy(false);
    }
  };

  return (
    <>
      <Panel
        title="题库"
        right={
          <div className="btn-row">
            <select
              className="select"
              value={subjectId}
              onChange={(event) => {
                setSubjectId(event.target.value);
                setOffset(0);
              }}
            >
              <option value="">全部科目</option>
              {(profile.data?.subjects ?? []).map((subject) => (
                <option key={subject.id} value={subject.id}>
                  {subject.name}
                </option>
              ))}
            </select>
            <select
              className="select"
              value={synthetic}
              onChange={(event) => {
                setSynthetic(event.target.value);
                setOffset(0);
              }}
            >
              <option value="">全部来源</option>
              <option value="false">真实/导入题</option>
              <option value="true">AI 生成题</option>
            </select>
          </div>
        }
      >
        {loading && !data && <Loading text="加载题库" />}
        {error && <ErrorBox error={error} retry={reload} />}
        {data && (
          <>
            <div className="small muted" style={{ marginBottom: 8 }}>
              共 {data.total} 题 · 生成题标记 synthetic，不计入真实考频统计
            </div>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>题目</th>
                    <th>题型</th>
                    <th>内容</th>
                    <th>知识点</th>
                    <th className="num">分值</th>
                    <th>来源</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((question) => (
                    <tr key={question.id} className="clickable" onClick={() => openQuestion(question.id)}>
                      <td className="mono tiny">{question.key}</td>
                      <td>{question.question_type}</td>
                      <td style={{ maxWidth: 420 }} className="clamp-3">
                        {shortText(question.content, 120)}
                      </td>
                      <td>
                        <div className="chips">
                          {question.knowledge.slice(0, 3).map((link) => (
                            <Badge key={link.knowledge_id}>{link.code}</Badge>
                          ))}
                        </div>
                      </td>
                      <td className="num">{fmtNum(question.score, 0)}</td>
                      <td>
                        {question.is_synthetic ? (
                          <Badge tone="violet">{question.transform_level ?? "生成"}</Badge>
                        ) : (
                          <Badge>{question.source_type}</Badge>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="btn-row" style={{ marginTop: 12 }}>
              <button
                className="btn small"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                上一页
              </button>
              <span className="small muted">
                {offset + 1}–{Math.min(offset + PAGE_SIZE, data.total)} / {data.total}
              </span>
              <button
                className="btn small"
                disabled={offset + PAGE_SIZE >= data.total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                下一页
              </button>
            </div>
          </>
        )}
      </Panel>

      {selected && (
        <Drawer
          title={`题目 ${selected.key}`}
          subtitle={`${selected.question_type} · ${selected.score} 分 · 难度 ${fmtNum(selected.difficulty, 2)}`}
          onClose={() => {
            setSelected(null);
            setTransformResult(null);
            setTransformError(null);
          }}
        >
          <div className="question-content">{selected.content}</div>
          <div className="answer-box">
            <div className="tiny dim">参考答案</div>
            <div>{selected.answer || "（无）"}</div>
          </div>
          {selected.explanation && (
            <div className="small muted pre-line">{selected.explanation}</div>
          )}
          <KV
            rows={[
              ["来源", selected.is_synthetic ? `生成题（父题 ${selected.parent_question_id}）` : selected.source_type],
              ["知识点", selected.knowledge.map((link) => link.code).join(", ") || "未标注"],
            ]}
          />

          {selected.is_synthetic ? (
            <div className="panel flat">
              <div className="tiny dim" style={{ marginBottom: 6 }}>验证记录</div>
              <pre className="tiny mono" style={{ whiteSpace: "pre-wrap", margin: 0 }}>
                {JSON.stringify(selected.verification, null, 2)}
              </pre>
            </div>
          ) : (
            <div className="panel flat">
              <div className="row between" style={{ marginBottom: 10 }}>
                <div className="strong">生成变式题</div>
                <span className="tiny dim">独立求解 + SymPy 验证后才入库</span>
              </div>
              <div className="field-row">
                <label className="field">
                  改造等级
                  <select className="select" value={level} onChange={(event) => setLevel(event.target.value)}>
                    <option value="L1">L1 单知识点变式</option>
                    <option value="L2">L2 双知识点组合</option>
                    <option value="L3">L3 多知识点综合</option>
                    <option value="L4">L4 跨题型迁移</option>
                  </select>
                </label>
                <label className="field">
                  数量
                  <select className="select" value={count} onChange={(event) => setCount(Number(event.target.value))}>
                    <option value={1}>1</option>
                    <option value={2}>2</option>
                    <option value={3}>3</option>
                  </select>
                </label>
                <button className="btn primary" onClick={transform} disabled={transformBusy}>
                  {transformBusy ? "生成并验证中…" : "生成变式题"}
                </button>
              </div>
              {transformError && <div className="small bad" style={{ marginTop: 8 }}>{transformError}</div>}
              {transformResult && (
                <div className="list" style={{ marginTop: 12 }}>
                  <div className="small accent">{transformResult.note}</div>
                  {transformResult.items.map((item) => (
                    <div key={item.question.id} className="panel" style={{ padding: 12 }}>
                      <div className="row between">
                        <Badge tone="violet">{item.level}</Badge>
                        <Badge tone={item.verification.status === "verified" ? "good" : "bad"}>
                          {item.verification.status}
                        </Badge>
                      </div>
                      <div className="small pre-line" style={{ margin: "8px 0" }}>
                        {item.question.content}
                      </div>
                      <div className="tiny dim">{item.rationale}</div>
                      <div className="list" style={{ marginTop: 6 }}>
                        {item.verification.checks.map((check, index) => (
                          <div key={index} className="tiny">
                            <span className={check.passed ? "good" : "bad"}>
                              {check.passed ? "✓" : "✗"} {check.check}
                            </span>{" "}
                            <span className="dim">{check.detail}</span>
                          </div>
                        ))}
                      </div>
                      <div className="tiny accent" style={{ marginTop: 6 }}>
                        答案：{item.question.answer}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </Drawer>
      )}
    </>
  );
}
