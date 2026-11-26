"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "总览", icon: "◈" },
  { href: "/plan", label: "今日计划", icon: "▤" },
  { href: "/roi", label: "ROI 排行", icon: "▲" },
  { href: "/knowledge", label: "知识图谱", icon: "◉" },
  { href: "/questions", label: "题库", icon: "▦" },
  { href: "/tests", label: "测试", icon: "◫" },
  { href: "/memory", label: "背诵", icon: "◧" },
  { href: "/agent", label: "智能体", icon: "⌁" },
  { href: "/studio", label: "自主编排", icon: "⚙" },
  { href: "/sources", label: "资料", icon: "▤" },
];

export function Sidebar() {
  const pathname = usePathname() ?? "/";
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">考</div>
        <div>
          <div className="brand-title">ExamOS</div>
          <div className="brand-sub">super-agent</div>
        </div>
      </div>
      <nav className="nav">
        {NAV.map((item) => {
          const active =
            item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`nav-link ${active ? "active" : ""}`}
            >
              <span className="nav-icon">{item.icon}</span>
              <span className="nav-label">{item.label}</span>
            </Link>
          );
        })}
      </nav>
      <div className="sidebar-foot">
        <span>数学一 · 英语一 · 专业课</span>
        <span>v0.1 · MVP 闭环</span>
      </div>
    </aside>
  );
}
