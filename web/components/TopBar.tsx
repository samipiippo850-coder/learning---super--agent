"use client";

import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, API_BASE, type Dashboard } from "@/lib/api";

const TITLES: Record<string, string> = {
  "/": "总览 · ExamOS",
  "/plan": "今日计划",
  "/roi": "ROI 排行",
  "/knowledge": "知识图谱",
  "/questions": "题库与变式改造",
  "/tests": "测试与诊断",
  "/memory": "背诵与间隔复习",
  "/agent": "Super-Agent 控制台",
  "/studio": "自主编排 · 找书 / 工作流 / 权限",
  "/sources": "资料与来源",
};

export function TopBar() {
  const pathname = usePathname() ?? "/";
  const normalized = pathname !== "/" ? pathname.replace(/\/$/, "") : "/";
  const title = TITLES[normalized] ?? "ExamOS";
  const [online, setOnline] = useState<boolean | null>(null);
  const [countdown, setCountdown] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const ping = useCallback(async () => {
    try {
      await api.health();
      setOnline(true);
      const dashboard = await api.get<Dashboard>("/api/dashboard");
      setCountdown(dashboard.countdown_days);
    } catch {
      setOnline(false);
    }
  }, []);

  useEffect(() => {
    ping();
    // while offline retry every 4s so the page heals by itself once the API is up
    const timer = setInterval(ping, online === false ? 4000 : 30000);
    return () => clearInterval(timer);
  }, [ping, online]);

  const recompute = async () => {
    setBusy(true);
    setToast(null);
    try {
      const result = await api.post<{ snapshots: number }>("/api/roi/recompute", {});
      setToast(`已重算 ${result.snapshots} 个知识点的 ROI`);
      setTimeout(() => window.location.reload(), 700);
    } catch (error) {
      setToast(error instanceof Error ? error.message : "重算失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <header className="topbar">
      <h1>{title}</h1>
      <div className="topbar-meta">
        {countdown !== null && (
          <span className="countdown">距考试 {countdown} 天</span>
        )}
        <span
          title={
            online
              ? `API 在线：${API_BASE}`
              : `API 离线：${API_BASE} · 正在自动重试（也可点此手动重试）`
          }
          onClick={() => {
            if (online === false) ping();
          }}
          style={{ cursor: online === false ? "pointer" : "default" }}
        >
          <span className={`dot ${online ? "good" : "bad"}`} />
          {online === null ? "检测中" : online ? "API 正常" : "API 离线 · 自动重试中"}
        </span>
        <button className="btn primary small" onClick={recompute} disabled={busy}>
          {busy ? "重算中…" : "重新计算 ROI"}
        </button>
      </div>
      {toast && (
        <div className="tooltip" style={{ right: 22, top: 60 }}>
          {toast}
        </div>
      )}
    </header>
  );
}
