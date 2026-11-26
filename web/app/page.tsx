"use client";

import Link from "next/link";
import {
  Badge,
  ErrorBox,
  Loading,
  MasteryBar,
  Panel,
  ProgressBar,
  StatCard,
  statusTone,
  useDashboard,
} from "@/components/ui";
import { fmtDate, fmtMinutes, fmtNum, fmtPct, SUBJECT_LABELS, TEST_LABELS } from "@/lib/format";

const STATE_LABELS: Record<string, string> = {
  UNSEEN: "未接触",
  LEARNING: "学习中",
  RECOGNITION: "再认",
  ACTIVE_RECALL: "主动回忆",
  APPLICATION: "应用",
  STABLE: "稳定掌握",
};

export default function DashboardPage() {
  const { data, error, loading, reload } = useDashboard();

  if (loading && !data) return <Loading text="加载总览" />;
  if (error && !data) return <ErrorBox error={error} retry={reload} />;
  if (!data) return null;

  const masteryTotal = Math.max(
    1,
    Object.values(data.mastery.by_state).reduce((sum, value) => sum + value, 0),
  );
  const stateOrder = ["STABLE", "APPLICATION", "ACTIVE_RECALL", "RECOGNITION", "LEARNING", "UNSEEN"];
  const stateColor: Record<string, string> = {
    STABLE: "var(--good)",
    APPLICATION: "var(--accent)",
    ACTIVE_RECALL: "var(--accent-2)",
    RECOGNITION: "var(--violet)",
    LEARNING: "var(--warn)",
    UNSEEN: "var(--dim)",
  };

  return (
    <>
      <div className="grid g4">
        <StatCard
          label="距考试"
          value={data.countdown_days === null ? "—" : `${data.countdown_days} 天`}
          hint={`${data.university} · ${data.major}`}
          tone="warn"
        />
        <StatCard
          label="平均掌握度"
          value={fmtPct(data.mastery.average)}
          hint={`稳定 ${data.coverage.stable} / 已学 ${data.coverage.studied} / 共 ${data.coverage.knowledge_total}`}
          tone="accent"
        />
        <StatCard
          label="近 7 日正确率"
          value={fmtPct(data.accuracy.last7d)}
          hint={`${data.accuracy.attempts} 次作答`}
          tone={data.accuracy.last7d >= 0.65 ? "good" : "bad"}
        />
        <StatCard
          label="今日计划"
          value={`${Math.round(data.today.used_minutes)} / ${data.today.budget_minutes} 分`}
          hint={`${data.today.done} / ${data.today.task_count} 项已完成 · 待复习 ${data.due.cards} 张`}
        />
      </div>

      <div className="grid g2">
        <Panel
          title="掌握度分布"
          right={<Link className="btn small" href="/knowledge">查看知识图谱</Link>}
        >
          <div className="list">
            {stateOrder.map((state) => {
              const count = data.mastery.by_state[state] ?? 0;
              return (
                <div key={state} className="row" style={{ gap: 12 }}>
                  <span className="small muted" style={{ width: 76 }}>
                    {STATE_LABELS[state]}
                  </span>
                  <div style={{ flex: 1 }}>
                    <ProgressBar
                      value={count}
                      total={masteryTotal}
                      color={stateColor[state]}
                      height={8}
                    />
                  </div>
                  <span className="mono tiny" style={{ width: 34, textAlign: "right" }}>
                    {count}
                  </span>
                </div>
              );
            })}
          </div>
          <div className="sep" />
          <div className="row between small">
            <span className="muted">考纲覆盖率</span>
            <span className="mono">
              {fmtPct(data.coverage.studied / Math.max(1, data.coverage.knowledge_total))}
            </span>
          </div>
        </Panel>

        <Panel
          title="高 ROI 知识点"
          right={
            <Link className="btn small" href="/roi">
              完整排行
            </Link>
          }
        >
          {data.top_roi.length === 0 ? (
            <div className="empty">暂无 ROI 数据，点击右上角重新计算</div>
          ) : (
            <div className="list">
              {data.top_roi.map((item, index) => (
                <div key={item.knowledge_id} className="row between">
                  <div style={{ minWidth: 0 }}>
                    <div className="row" style={{ gap: 8 }}>
                      <span className="mono dim tiny">#{index + 1}</span>
                      <span className="strong">{item.name}</span>
                      <Badge tone="accent">{item.subject}</Badge>
                    </div>
                    <div className="tiny dim">
                      {item.code} · 考频 {fmtPct(item.frequency)} · 预计 +{fmtNum(item.expected_gain)} 分
                    </div>
                  </div>
                  <span className="mono accent">{fmtNum(item.roi, 2)}</span>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>

      <div className="grid g3">
        <Panel title="智能体状态">
          <div className="grid g2" style={{ gap: 12 }}>
            <StatCard label="待处理任务" value={data.agent.open_tickets} />
            <StatCard label="执行中" value={data.agent.running} tone="warn" />
          </div>
          <div className="sep" />
          <div className="list">
            {data.agent.recent_reports.length === 0 && <span className="dim small">暂无报告</span>}
            {data.agent.recent_reports.map((report) => (
              <div key={report.id} className="small">
                <Badge tone={statusTone(report.status)}>{report.status}</Badge>{" "}
                <span className="muted">{report.summary.slice(0, 60)}</span>
              </div>
            ))}
          </div>
          <div className="sep" />
          <Link className="btn small" href="/agent">
            打开控制台
          </Link>
        </Panel>

        <Panel title="最近测试">
          {data.last_test ? (
            <>
              <div className="row between">
                <span className="strong">{TEST_LABELS[data.last_test.test_type] ?? data.last_test.test_type}</span>
                <Badge tone={statusTone(data.last_test.status)}>{data.last_test.status}</Badge>
              </div>
              <div className="stat-value" style={{ fontSize: 30 }}>
                {fmtNum(data.last_test.score, 1)}
                <span className="dim" style={{ fontSize: 14 }}>
                  {" "}
                  / {fmtNum(data.last_test.max_score, 0)}
                </span>
              </div>
              <div className="tiny dim">{fmtDate(data.last_test.created_at)}</div>
              <div className="sep" />
              <Link className="btn small" href="/tests">
                查看测试与诊断
              </Link>
            </>
          ) : (
            <div className="empty">还没有测试记录，去 /tests 生成第一份微测</div>
          )}
        </Panel>

        <Panel title="今日行动">
          <div className="list">
            <Link className="btn" href="/plan">
              今日计划（{data.today.task_count} 项）
            </Link>
            <Link className="btn" href="/memory">
              背诵复习（{data.due.cards} 张到期）
            </Link>
            <Link className="btn" href="/tests">
              生成每日微测
            </Link>
            <Link className="btn" href="/questions">
              题库与变式改造
            </Link>
          </div>
        </Panel>
      </div>

      <Panel title="最近作答">
        {data.recent_attempts.length === 0 ? (
          <div className="empty">暂无作答记录</div>
        ) : (
          <table className="data">
            <thead>
              <tr>
                <th>题目</th>
                <th>题型</th>
                <th>结果</th>
                <th>错误类型</th>
                <th className="num">用时</th>
                <th>时间</th>
              </tr>
            </thead>
            <tbody>
              {data.recent_attempts.map((attempt) => (
                <tr key={attempt.id}>
                  <td className="mono tiny">{attempt.question_key}</td>
                  <td>{attempt.question_type}</td>
                  <td>
                    <Badge tone={attempt.correct ? "good" : "bad"}>
                      {attempt.correct ? "正确" : "错误"}
                    </Badge>
                  </td>
                  <td className="muted tiny">{attempt.error_type}</td>
                  <td className="num">{Math.round(attempt.duration_sec)}s</td>
                  <td className="muted tiny">{fmtDate(attempt.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </>
  );
}
