import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";
import { TopBar } from "@/components/TopBar";

export const metadata: Metadata = {
  title: "ExamOS · 个人定制化考研应试智能体",
  description:
    "ExamOS：以历年真题为数据基础、以 ROI 为学习顺序、以周期性测试驱动回滚的考研 Super-Agent。",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <div className="shell">
          <Sidebar />
          <div className="main">
            <TopBar />
            <main className="page">{children}</main>
          </div>
        </div>
      </body>
    </html>
  );
}
