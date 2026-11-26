"use client";

import { Fragment, useState } from "react";
import {
  Badge,
  ErrorBox,
  Loading,
  Panel,
  StatCard,
  useApi,
} from "@/components/ui";
import { api, type RoiRow, type Subject } from "@/lib/api";
import { fmtNum, fmtPct, SUBJECT_LABELS } from "@/lib/format";

interface RoiResponse {
  items: RoiRow[];
  summary: {
    total_expected_gain: number;
    computed_at: string | null;
    eligible: number;
  };
}

export default function RoiPage() {
  const [subjectId, setSubjectId] = useState<string>("");
  const [expanded, setExpanded] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const profile = useApi<{ subjects: Subject[] }>(() => api.get("/api/profile"), []);
  const { data, error, loading, reload } = useApi<RoiResponse>(
    () =>
      api.get<RoiResponse>(
        `/api/roi?limit=200${subjectId ? `&subject_id=${subjectId}` : ""}`,
      ),
    [subjectId],
  );

  const recompute = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const result = await api.post<{ snapshots: number }>("/api/roi/recompute", {
        subject_id: subjectId ? Number(subjectId) : undefined,
      });
      setMessage(`重算完成：${result.snapshots} 个知识点快照`);
      reload();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "重算失败");
    } finally {
      setBusy(false);
    }
  };

  const items = data?.items ?? [];
  const eligible = items.filter((item) => !item.blocked);

  return (
    <>
      <Panel
        title="ROI 排序"
        right={
          <div className="btn-row">
            <select
              className="select"
              value={subjectId}
              onChange={(event) => setSubjectId(event.target.value)}
            >
              <option value="">全部科目</option>
              {(profile.data?.subjects ?? []).map((subject) => (
                <option key={subject.id} value={subject.id}>
                  {subject.name}
                </option>
              ))}
            </select>
            <button className="btn primary" onClick={recompute} disabled={busy}>
              {busy ? "重算中…" : "重算 ROI"}
            </button>
          </div>
        }
      >
        {message && <div className="small accent" style={{ marginBottom: 10 }}>{message}</div>}
        <div className="small muted" style={{ marginBottom: 10 }}>
          ROI = 数据可信度 × 边际收益 ΔV / 预计学习时间 H。点击行展开完整计算依据；
          「前置未达标」的节点不进入排序，需先回滚前置知识。
        </div>
        {loading && !data && <Loading text="加载 ROI" />}
        {error && <ErrorBox error={error} retry={reload} />}
        {data && (
          <>
            <div className="grid g3" style={{ marginBottom: 14 }}>
              <StatCard label="可学习知识点" value={eligible.length} hint={`共 ${items.length} 个`} tone="accent" />
              <StatCard
                label="预计总收益"
                value={`+${fmtNum(data.summary.total_expected_gain)} 分`}
                hint="按可学习节点边际收益求和"
                tone="good"
              />
              <StatCard
                label="计算时间"
                value={data.summary.computed_at ? data.summary.computed_at.slice(0, 16).replace("T", " ") : "—"}
              />
            </div>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>知识点</th>
                    <th>科目</th>
                    <th className="num">考频</th>
                    <th className="num">分值占比</th>
                    <th className="num">ΔV 分</th>
                    <th className="num">H 小时</th>
                    <th className="num">ROI</th>
                    <th className="num">置信度</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <Fragment key={item.knowledge_id}>
                      <tr
                        className="clickable"
                        onClick={() =>
                          setExpanded(expanded === item.knowledge_id ? null : item.knowledge_id)
                        }
                      >
                        <td className="mono dim">{item.rank}</td>
                        <td>
                          <div className="strong">{item.name}</div>
                          <div className="tiny dim mono">{item.code}</div>
                        </td>
                        <td className="muted tiny">{item.subject_name ?? SUBJECT_LABELS[item.subject_id] ?? item.subject_id}</td>
                        <td className="num">{fmtPct(item.frequency)}</td>
                        <td className="num">{fmtPct(item.score_share)}</td>
                        <td className="num">{fmtNum(item.expected_gain, 2)}</td>
                        <td className="num">{fmtNum(item.hours, 2)}</td>
                        <td className="num accent strong">{fmtNum(item.roi, 2)}</td>
                        <td className="num">{fmtNum(item.confidence, 2)}</td>
                        <td>
                          {item.blocked ? <Badge tone="bad">前置未达标</Badge> : <Badge tone="good">可学习</Badge>}
                        </td>
                      </tr>
                      {expanded === item.knowledge_id && (
                        <tr>
                          <td colSpan={10} style={{ background: "#0c141c" }}>
                            <div className="grid g2" style={{ gap: 12 }}>
                              <div>
                                <div className="tiny dim" style={{ marginBottom: 6 }}>可解释计算过程</div>
                                <div className="list">
                                  {item.reasons.formula.map((line, index) => (
                                    <div key={index} className="mono small">
                                      {line}
                                    </div>
                                  ))}
                                </div>
                              </div>
                              <div>
                                <div className="tiny dim" style={{ marginBottom: 6 }}>依据与约束</div>
                                <div className="list small">
                                  <div>数据基础：{item.reasons.data_basis}</div>
                                  <div>前置知识：{item.reasons.prerequisite}</div>
                                  <div>掌握度缺口：{fmtPct(item.reasons.gap)}</div>
                                  <div>章节：{item.chapter}</div>
                                </div>
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Panel>
    </>
  );
}
