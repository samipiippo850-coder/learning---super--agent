"use client";

import { ReactNode, useEffect, useMemo, useState } from "react";
import { api, type Dashboard } from "@/lib/api";
import { fmtNum, fmtPct } from "@/lib/format";

// ------------------------------------------------------------------ panels
export function Panel({
  title,
  right,
  children,
  flat,
  className = "",
}: {
  title?: string;
  right?: ReactNode;
  children: ReactNode;
  flat?: boolean;
  className?: string;
}) {
  return (
    <section className={`panel ${flat ? "flat" : ""} ${className}`}>
      {(title || right) && (
        <div className="panel-head">
          {title && <h2>{title}</h2>}
          {right && <div className="right">{right}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function StatCard({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "good" | "warn" | "bad" | "accent";
}) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${tone ?? ""}`}>{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

export function Badge({
  children,
  tone,
}: {
  children: ReactNode;
  tone?: "good" | "warn" | "bad" | "accent" | "violet";
}) {
  return <span className={`badge ${tone ?? ""}`}>{children}</span>;
}

export function statusTone(status: string): "good" | "warn" | "bad" | undefined {
  const key = status.toUpperCase();
  if (["READY", "DONE", "STABLE", "SUBMITTED", "OK", "VERIFIED"].includes(key)) return "good";
  if (["FAILED", "REJECTED", "BLOCKED"].includes(key)) return "bad";
  if (["RUNNING", "PROGRESS", "DOING", "WAITING", "PENDING", "OPEN"].includes(key)) return "warn";
  return undefined;
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Loading({ text = "加载中" }: { text?: string }) {
  return <span className="loading">{text}</span>;
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="list">
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="skeleton" style={{ height: 16, width: `${90 - index * 12}%` }} />
      ))}
    </div>
  );
}

export function ErrorBox({ error, retry }: { error: string; retry?: () => void }) {
  return (
    <div className="errorbox">
      <div>
        <div className="strong">请求失败</div>
        <div>{error}</div>
      </div>
      {retry && (
        <button className="btn small" style={{ marginLeft: "auto" }} onClick={retry}>
          重试
        </button>
      )}
    </div>
  );
}

export function ProgressBar({
  value,
  total,
  color = "var(--accent)",
  height = 6,
}: {
  value: number;
  total: number;
  color?: string;
  height?: number;
}) {
  const width = total > 0 ? Math.max(0, Math.min(100, (value / total) * 100)) : 0;
  return (
    <div className="bar" style={{ height }}>
      <span style={{ width: `${width}%`, background: color }} />
    </div>
  );
}

export function MasteryBar({ mastery }: { mastery: number }) {
  const color =
    mastery >= 0.9
      ? "var(--good)"
      : mastery >= 0.6
        ? "var(--accent)"
        : mastery >= 0.4
          ? "var(--warn)"
          : "var(--bad)";
  return (
    <div className="row" style={{ gap: 8 }}>
      <div className="bar" style={{ flex: 1, height: 5 }}>
        <span style={{ width: `${Math.round(mastery * 100)}%`, background: color }} />
      </div>
      <span className="mono tiny" style={{ width: 34, textAlign: "right" }}>
        {fmtPct(mastery)}
      </span>
    </div>
  );
}

export function Sparkline({
  values,
  width = 120,
  height = 32,
  color = "var(--accent)",
}: {
  values: number[];
  width?: number;
  height?: number;
  color?: string;
}) {
  if (!values.length) return <span className="dim tiny">无数据</span>;
  const max = Math.max(...values, 0.0001);
  const min = Math.min(...values, 0);
  const range = max - min || 1;
  const points = values.map((value, index) => {
    const x = values.length === 1 ? width / 2 : (index / (values.length - 1)) * (width - 4) + 2;
    const y = height - 2 - ((value - min) / range) * (height - 6);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return (
    <svg width={width} height={height} aria-hidden="true">
      <polyline points={points.join(" ")} fill="none" stroke={color} strokeWidth="1.6" />
    </svg>
  );
}

export function Drawer({
  title,
  subtitle,
  onClose,
  children,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);
  return (
    <>
      <div className="drawer-mask" onClick={onClose} />
      <aside className="drawer">
        <div className="drawer-head">
          <div style={{ minWidth: 0 }}>
            <h3>{title}</h3>
            {subtitle && <div className="muted small">{subtitle}</div>}
          </div>
          <button className="btn small" style={{ marginLeft: "auto" }} onClick={onClose}>
            关闭
          </button>
        </div>
        {children}
      </aside>
    </>
  );
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return <div className="panel-head"><h2>{children}</h2></div>;
}

export function KV({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="kv">
      {rows.map(([key, value]) => (
        <div key={key} style={{ display: "contents" }}>
          <dt>{key}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function scoreTone(score: number, max: number): "good" | "warn" | "bad" {
  const ratio = max ? score / max : 0;
  if (ratio >= 0.8) return "good";
  if (ratio >= 0.5) return "warn";
  return "bad";
}

export function ErrorWithRetry({ error, onRetry }: { error: string; onRetry: () => void }) {
  return <ErrorBox error={error} retry={onRetry} />;
}

export function useApi<T>(loader: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [retrying, setRetrying] = useState(false);
  const [nonce, setNonce] = useState(0);

  const reload = () => setNonce((value) => value + 1);

  useEffect(() => {
    let active = true;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    setLoading(true);
    setRetrying(false);
    setError(null);
    loader()
      .then((result) => {
        if (active) {
          setData(result);
          setRetrying(false);
        }
      })
      .catch((err: unknown) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : String(err));
        setRetrying(true);
        // self-healing: keep retrying while the component is mounted so the
        // page recovers as soon as the API comes back (no manual refresh)
        retryTimer = setTimeout(() => {
          if (active) setNonce((value) => value + 1);
        }, 5000);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      if (retryTimer) clearTimeout(retryTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { data, error, loading, retrying, reload, setData };
}

export function useDashboard() {
  return useApi<Dashboard>(() => api.get<Dashboard>("/api/dashboard"), []);
}

export function masteryColor(mastery: number): string {
  if (mastery >= 0.9) return "var(--good)";
  if (mastery >= 0.6) return "var(--accent)";
  if (mastery >= 0.4) return "var(--warn)";
  return "var(--bad)";
}

export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

export function computeMasterySummary(byState: Record<string, number>) {
  const total = useMemo(
    () => Object.values(byState).reduce((sum, value) => sum + value, 0),
    [byState],
  );
  return { total };
}

export { fmtNum, fmtPct };
