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
import { api, type MemoryCard } from "@/lib/api";
import { fmtDate, shortText } from "@/lib/format";

const STATE_LABELS: Record<string, string> = {
  UNSEEN: "未学习",
  LEARNING: "学习中",
  RECOGNITION: "再认",
  ACTIVE_RECALL: "主动回忆",
  APPLICATION: "应用",
  STABLE: "稳定掌握",
};

const QUALITY = [
  { value: 0, label: "完全忘记", color: "var(--bad)" },
  { value: 1, label: "几乎忘记", color: "var(--bad)" },
  { value: 2, label: "有印象", color: "var(--warn)" },
  { value: 3, label: "回忆吃力", color: "var(--warn)" },
  { value: 4, label: "基本正确", color: "var(--good)" },
  { value: 5, label: "熟练", color: "var(--good)" },
];

export default function MemoryPage() {
  const [dueOnly, setDueOnly] = useState(true);
  const [index, setIndex] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [reviewed, setReviewed] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const { data, error, loading, reload } = useApi<{ items: MemoryCard[]; total: number }>(
    () => api.get(`/api/memory/cards?due_only=${dueOnly}&limit=100`),
    [dueOnly],
  );

  const cards = data?.items ?? [];
  const card = cards[index];

  const review = async (quality: number) => {
    if (!card) return;
    setBusy(true);
    try {
      const result = await api.post<{ state: string; next_due: string }>(
        `/api/memory/cards/${card.id}/review`,
        { quality, response_text: "" },
      );
      setReviewed((value) => value + 1);
      setMessage(`已评分 ${quality} → 状态 ${STATE_LABELS[result.state] ?? result.state}，下次复习 ${fmtDate(result.next_due)}`);
      setFlipped(false);
      if (index + 1 < cards.length) {
        setIndex(index + 1);
      } else {
        reload();
        setIndex(0);
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "评分失败");
    } finally {
      setBusy(false);
    }
  };

  const build = async () => {
    setBusy(true);
    try {
      const result = await api.post<{ created: number }>("/api/memory/build", {});
      setMessage(`已构建 ${result.created} 张新卡片`);
      reload();
      setIndex(0);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "构建失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Panel
        title="背诵与间隔复习"
        right={
          <div className="btn-row">
            <div className="pill-tabs">
              <button className={dueOnly ? "active" : ""} onClick={() => { setDueOnly(true); setIndex(0); }}>
                今日到期
              </button>
              <button className={!dueOnly ? "active" : ""} onClick={() => { setDueOnly(false); setIndex(0); }}>
                全部卡片
              </button>
            </div>
            <button className="btn" onClick={reload} disabled={busy}>
              刷新
            </button>
            <button className="btn primary" onClick={build} disabled={busy}>
              构建背诵卡
            </button>
          </div>
        }
      >
        {message && <div className="small accent" style={{ marginBottom: 10 }}>{message}</div>}
        <div className="grid g4" style={{ marginBottom: 14 }}>
          <StatCard label="卡片总数" value={data?.total ?? "—"} />
          <StatCard label="今日已复习" value={reviewed} tone="accent" />
          <StatCard label="当前进度" value={cards.length ? `${Math.min(index + 1, cards.length)} / ${cards.length}` : "0"} />
          <StatCard label="模式" value={dueOnly ? "到期优先" : "全部"} hint="主动回忆：先作答再看答案" />
        </div>
        {loading && !data && <Loading text="加载卡片" />}
        {error && <ErrorBox error={error} retry={reload} />}
        {data && cards.length === 0 && (
          <EmptyState>
            没有到期的卡片。点击「构建背诵卡」从高频知识点生成记忆材料，或切换到「全部卡片」
          </EmptyState>
        )}
        {card && (
          <>
            <div className="row between small" style={{ marginBottom: 8 }}>
              <div className="chips">
                <Badge tone="accent">{card.card_type}</Badge>
                <Badge>{STATE_LABELS[card.state] ?? card.state}</Badge>
                <span className="dim tiny">{card.source_ref}</span>
              </div>
              <span className="mono tiny dim">
                ease {card.ease} · interval {card.interval_days}d · reps {card.reps} · lapses {card.lapses}
              </span>
            </div>
            <div
              className={`flash ${flipped ? "back" : ""}`}
              onClick={() => setFlipped((value) => !value)}
              role="button"
              tabIndex={0}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") setFlipped((value) => !value);
              }}
            >
              {flipped ? (
                <div className="pre-line">{card.back}</div>
              ) : (
                <div>
                  <div className="tiny dim" style={{ marginBottom: 10 }}>主动回忆：先在脑中作答，再点击翻面</div>
                  <div className="pre-line">{card.front}</div>
                </div>
              )}
            </div>
            <div className="row between" style={{ marginTop: 12 }}>
              <span className="tiny dim">{flipped ? "回答正确程度（0 完全忘记 – 5 熟练）" : "点击卡片翻面查看答案"}</span>
              <div className="btn-row">
                <button className="btn small" onClick={() => setFlipped((value) => !value)}>
                  翻面
                </button>
                <button
                  className="btn small"
                  disabled={index === 0}
                  onClick={() => { setIndex(Math.max(0, index - 1)); setFlipped(false); }}
                >
                  上一张
                </button>
                <button
                  className="btn small"
                  disabled={index + 1 >= cards.length}
                  onClick={() => { setIndex(Math.min(cards.length - 1, index + 1)); setFlipped(false); }}
                >
                  下一张
                </button>
              </div>
            </div>
            {flipped && (
              <div className="quality-row" style={{ marginTop: 12 }}>
                {QUALITY.map((item) => (
                  <button
                    key={item.value}
                    className="quality-btn"
                    style={{ color: item.color }}
                    disabled={busy}
                    onClick={() => review(item.value)}
                  >
                    <span className="mono">{item.value}</span>
                    <span>{item.label}</span>
                  </button>
                ))}
              </div>
            )}
            <div className="sep" />
            <div className="tiny dim">
              复习策略：主动回忆 + 间隔重复；看答案后的熟悉感不计入掌握度。连续低分会回退记忆状态。
            </div>
          </>
        )}
      </Panel>
    </>
  );
}
