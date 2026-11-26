"use client";

import { useMemo, useState } from "react";
import {
  Badge,
  Drawer,
  ErrorBox,
  KV,
  Loading,
  Panel,
  useApi,
} from "@/components/ui";
import { api, type GraphData, type Subject } from "@/lib/api";
import { fmtNum, fmtPct } from "@/lib/format";

interface Detail {
  id: number;
  code: string;
  name: string;
  category: string;
  chapter: string;
  description: string;
  importance: number;
  est_minutes: number;
  syllabus_requirement: string;
  frequency: { frequency: number; score_share: number; years_present: number[]; stability_note: string } | null;
  roi: { roi: number; expected_gain: number; hours: number; reasons: { formula: string[] } } | null;
  mastery: { mastery: number; state: string; confidence: number };
  edges_in: { source: number; source_name: string | null; relation_type: string }[];
  edges_out: { target: number; target_name: string | null; relation_type: string }[];
  related_questions: { id: number; key: string; question_type: string; content: string; score: number }[];
}

const CHAPTER_WIDTH = 236;
const ROW_HEIGHT = 78;
const PADDING_X = 40;
const PADDING_Y = 64;

function shortChapter(chapter: string): string {
  const parts = chapter.split("·");
  return parts.length > 1 ? parts[1] : chapter;
}

export default function KnowledgePage() {
  const [subjectType, setSubjectType] = useState("math_1");
  const [hover, setHover] = useState<{ id: number; x: number; y: number } | null>(null);
  const [selected, setSelected] = useState<Detail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const profile = useApi<{ subjects: Subject[] }>(() => api.get("/api/profile"), []);
  const { data, error, loading, reload } = useApi<GraphData>(
    () => api.get<GraphData>(`/api/knowledge/graph?subject_type=${subjectType}`),
    [subjectType],
  );

  const layout = useMemo(() => {
    if (!data) return null;
    const chapters = Array.from(new Set(data.nodes.map((node) => node.chapter)));
    const positions = new Map<number, { x: number; y: number }>();
    const chapterCounters = new Map<string, number>();
    chapters.forEach((chapter) => chapterCounters.set(chapter, 0));
    data.nodes.forEach((node) => {
      const index = chapters.indexOf(node.chapter);
      const row = chapterCounters.get(node.chapter) ?? 0;
      chapterCounters.set(node.chapter, row + 1);
      positions.set(node.id, {
        x: PADDING_X + index * CHAPTER_WIDTH + CHAPTER_WIDTH / 2,
        y: PADDING_Y + row * ROW_HEIGHT,
      });
    });
    const maxRows = Math.max(
      1,
      ...chapters.map((chapter) => data.nodes.filter((node) => node.chapter === chapter).length),
    );
    return {
      chapters,
      positions,
      width: PADDING_X * 2 + chapters.length * CHAPTER_WIDTH,
      height: PADDING_Y + maxRows * ROW_HEIGHT + 30,
    };
  }, [data]);

  const openDetail = async (id: number) => {
    setDetailError(null);
    try {
      const detail = await api.get<Detail>(`/api/knowledge/${id}`);
      setSelected(detail);
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : "加载失败");
    }
  };

  const hoveredNode = hover && data ? data.nodes.find((node) => node.id === hover.id) : null;

  return (
    <Panel
      title="知识图谱"
      right={
        <div className="btn-row">
          <select
            className="select"
            value={subjectType}
            onChange={(event) => setSubjectType(event.target.value)}
          >
            {(profile.data?.subjects ?? []).map((subject) => (
              <option key={subject.id} value={subject.subject_type}>
                {subject.name}
              </option>
            ))}
          </select>
          <button className="btn" onClick={reload} disabled={loading}>
            刷新
          </button>
        </div>
      }
    >
      {loading && !data && <Loading text="加载图谱" />}
      {error && <ErrorBox error={error} retry={reload} />}
      {detailError && <ErrorBox error={detailError} retry={() => selected && openDetail(selected.id)} />}
      {data && layout && (
        <>
          <div className="row wrap" style={{ marginBottom: 10, gap: 10 }}>
            <Badge tone="accent">{data.subject_name}</Badge>
            <span className="small muted">
              {data.dag.node_count} 个知识点 · {data.dag.edge_count} 条前置关系
            </span>
            <Badge tone={data.dag.valid ? "good" : "bad"}>
              {data.dag.valid ? "前置依赖为 DAG" : `检测到环：${data.dag.cycle_nodes.join(", ")}`}
            </Badge>
            <span className="small dim">点击节点查看详情</span>
          </div>
          <div className="graph-wrap">
            <svg width={layout.width} height={layout.height} role="img" aria-label="知识图谱">
              <defs>
                <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                  <path d="M0,0 L8,4 L0,8 Z" fill="#4f9d8f" />
                </marker>
              </defs>
              {layout.chapters.map((chapter, index) => (
                <g key={chapter}>
                  <text
                    x={PADDING_X + index * CHAPTER_WIDTH + CHAPTER_WIDTH / 2}
                    y={30}
                    textAnchor="middle"
                    fill="#8b9bad"
                    fontSize={11}
                  >
                    {shortChapter(chapter).slice(0, 14)}
                  </text>
                  <line
                    x1={PADDING_X + index * CHAPTER_WIDTH + 6}
                    x2={PADDING_X + (index + 1) * CHAPTER_WIDTH - 6}
                    y1={42}
                    y2={42}
                    stroke="#1f2a37"
                  />
                </g>
              ))}
              {data.edges.map((edge, index) => {
                const from = layout.positions.get(edge.source);
                const to = layout.positions.get(edge.target);
                if (!from || !to) return null;
                const midX = (from.x + to.x) / 2;
                const prerequisite = edge.relation_type === "prerequisite";
                return (
                  <path
                    key={`${edge.source}-${edge.target}-${index}`}
                    d={`M ${from.x} ${from.y + 20} C ${midX} ${from.y + 20}, ${midX} ${to.y - 20}, ${to.x} ${to.y - 20}`}
                    fill="none"
                    stroke={prerequisite ? "#4f9d8f" : "#453f31"}
                    strokeWidth={prerequisite ? 1.3 : 1}
                    strokeDasharray={prerequisite ? undefined : "4 4"}
                    markerEnd={prerequisite ? "url(#arrow)" : undefined}
                    opacity={prerequisite ? 0.75 : 0.4}
                  />
                );
              })}
              {data.nodes.map((node) => {
                const position = layout.positions.get(node.id)!;
                const masteryColor =
                  node.mastery >= 0.9
                    ? "#4f9d8f"
                    : node.mastery >= 0.6
                      ? "#c9a227"
                      : node.mastery >= 0.4
                        ? "#b98a2e"
                        : "#c8543a";
                const ring =
                  node.roi >= 8 ? "#c9a227" : node.roi >= 3 ? "#8a7a3a" : "#2c2a22";
                return (
                  <g
                    key={node.id}
                    transform={`translate(${position.x}, ${position.y})`}
                    style={{ cursor: "pointer" }}
                    onMouseEnter={() => setHover({ id: node.id, x: position.x, y: position.y })}
                    onMouseLeave={() => setHover(null)}
                    onClick={() => openDetail(node.id)}
                  >
                    <circle
                      r={21}
                      fill={masteryColor}
                      opacity={0.16}
                      stroke={node.blocked ? "#f43f5e" : ring}
                      strokeWidth={node.blocked ? 2 : 2.4}
                    />
                    <text textAnchor="middle" y={4} fontSize={9} fill="#e6edf3">
                      {fmtPct(node.mastery)}
                    </text>
                    <text textAnchor="middle" y={36} fontSize={9.5} fill="#8b9bad">
                      {node.name.length > 14 ? `${node.name.slice(0, 14)}…` : node.name}
                    </text>
                  </g>
                );
              })}
            </svg>
            {hoveredNode && hover && (
              <div className="tooltip" style={{ left: Math.min(hover.x + 30, layout.width - 280), top: hover.y - 10 }}>
                <div className="strong">{hoveredNode.name}</div>
                <div className="tiny dim mono">{hoveredNode.code}</div>
                <div className="tiny">掌握度 {fmtPct(hoveredNode.mastery)} · {hoveredNode.state}</div>
                <div className="tiny">考频 {fmtPct(hoveredNode.frequency)} · ROI {fmtNum(hoveredNode.roi, 2)}</div>
                <div className="tiny">ROI {hoveredNode.blocked ? "（前置未达标）" : "可学习"}</div>
              </div>
            )}
          </div>
          <div className="graph-legend">
            <span className="legend-item">
              <span className="legend-line" style={{ borderTopColor: "#4f9d8f" }} /> 前置依赖
            </span>
            <span className="legend-item">
              <span className="legend-line dashed" /> 共同考查
            </span>
            <span className="legend-item">
              <span className="dot good" /> 掌握度 ≥ 90%
            </span>
            <span className="legend-item">圆环金色 = 高 ROI</span>
            <span className="legend-item">圆环朱红 = 前置未达标</span>
          </div>
        </>
      )}

      {selected && (
        <Drawer
          title={selected.name}
          subtitle={`${selected.code} · ${selected.chapter} · ${selected.category}`}
          onClose={() => setSelected(null)}
        >
          <KV
            rows={[
              ["考纲要求", selected.syllabus_requirement],
              ["重要度", fmtPct(selected.importance)],
              ["预计学习", `${Math.round(selected.est_minutes)} 分钟`],
              ["掌握度", `${fmtPct(selected.mastery.mastery)}（${selected.mastery.state}）`],
              ["考频", selected.frequency ? fmtPct(selected.frequency.frequency) : "—"],
              ["分值占比", selected.frequency ? fmtPct(selected.frequency.score_share) : "—"],
              ["ROI", selected.roi ? fmtNum(selected.roi.roi, 2) : "—"],
            ]}
          />
          <div className="small muted pre-line">{selected.description}</div>
          {selected.frequency && (
            <div className="small">
              <span className="dim">考频依据：</span>
              {selected.frequency.stability_note}（出现年份：{selected.frequency.years_present.join(", ") || "无"}）
            </div>
          )}
          {selected.roi && (
            <div className="list small">
              {selected.roi.reasons.formula.map((line, index) => (
                <div key={index} className="mono tiny">{line}</div>
              ))}
            </div>
          )}
          <div>
            <div className="tiny dim" style={{ marginBottom: 6 }}>前置 / 后继</div>
            <div className="list small">
              {selected.edges_in.length === 0 && <span className="dim">无前置</span>}
              {selected.edges_in.map((edge, index) => (
                <div key={`in-${index}`}>
                  <Badge>{edge.relation_type}</Badge> {edge.source_name}
                </div>
              ))}
              {selected.edges_out.map((edge, index) => (
                <div key={`out-${index}`}>
                  <Badge tone="violet">{edge.relation_type}</Badge> {edge.target_name}
                </div>
              ))}
            </div>
          </div>
          <div>
            <div className="tiny dim" style={{ marginBottom: 6 }}>相关真题</div>
            <div className="list">
              {selected.related_questions.map((question) => (
                <div key={question.id} className="panel flat" style={{ padding: 10 }}>
                  <div className="row between">
                    <span className="mono tiny dim">{question.key}</span>
                    <Badge>{question.score} 分</Badge>
                  </div>
                  <div className="small clamp-3">{question.content}</div>
                </div>
              ))}
              {selected.related_questions.length === 0 && <span className="dim small">暂无关联题目</span>}
            </div>
          </div>
        </Drawer>
      )}
    </Panel>
  );
}
