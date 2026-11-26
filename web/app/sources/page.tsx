"use client";

import { useState } from "react";
import {
  Badge,
  ErrorBox,
  Loading,
  Panel,
  useApi,
} from "@/components/ui";
import { api, type PaperItem, type SourceItem, type Subject } from "@/lib/api";
import { fmtDate, fmtNum } from "@/lib/format";

export default function SourcesPage() {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const profile = useApi<{ subjects: Subject[] }>(() => api.get("/api/profile"), []);
  const sources = useApi<{ items: SourceItem[] }>(() => api.get("/api/sources"), []);
  const papers = useApi<{ items: PaperItem[] }>(() => api.get("/api/papers"), []);

  const [sourceForm, setSourceForm] = useState({
    key: "user-notes",
    source_type: "user_provided",
    title: "用户复习笔记",
    trust_score: 0.6,
  });
  const [docForm, setDocForm] = useState({
    source_key: "user-notes",
    filename: "paper.txt",
    doc_type: "paper",
    subject_id: "",
    paper_year: new Date().getFullYear(),
    paper_type: "mock",
    content_text: "1. 求 lim_{x→0} sin(2x)/x。\n答案：2\n2. 计算 ∫_0^1 x dx。\n答案：1/2",
  });

  const addSource = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/sources", {
        ...sourceForm,
        trust_score: Number(sourceForm.trust_score),
      });
      setMessage(`来源 ${sourceForm.key} 已登记`);
      sources.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "登记失败");
    } finally {
      setBusy(false);
    }
  };

  const importDocument = async () => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await api.post<{
        questions_created: number;
        paper: { id: number } | null;
        duplicate: boolean;
      }>("/api/documents", {
        source_key: docForm.source_key,
        filename: docForm.filename,
        doc_type: docForm.doc_type,
        subject_id: docForm.subject_id ? Number(docForm.subject_id) : undefined,
        paper_year: Number(docForm.paper_year),
        paper_type: docForm.paper_type,
        content_text: docForm.content_text,
      });
      setMessage(
        result.duplicate
          ? "检测到重复文档（SHA-256 已存在），已跳过"
          : `导入成功：新增 ${result.questions_created} 题${result.paper ? `，试卷 #${result.paper.id}` : ""}`,
      );
      sources.reload();
      papers.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "导入失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="grid g2">
        <Panel title="资料与来源">
          {sources.loading && !sources.data && <Loading text="加载来源" />}
          {sources.error && <ErrorBox error={sources.error} retry={sources.reload} />}
          {sources.data && (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Key</th>
                    <th>类型</th>
                    <th>标题</th>
                    <th className="num">可信度</th>
                    <th className="num">文档</th>
                    <th className="num">试卷</th>
                  </tr>
                </thead>
                <tbody>
                  {sources.data.items.map((source) => (
                    <tr key={source.id}>
                      <td className="mono tiny">{source.key}</td>
                      <td className="tiny">{source.source_type}</td>
                      <td>{source.title}</td>
                      <td className="num">{fmtNum(source.trust_score, 2)}</td>
                      <td className="num">{source.document_count}</td>
                      <td className="num">{source.paper_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="sep" />
          <div className="tiny dim" style={{ marginBottom: 8 }}>
            新增来源（资料入库必须可追溯来源、年份与可信度）
          </div>
          <div className="field-row">
            <label className="field">
              Key
              <input
                className="input"
                value={sourceForm.key}
                onChange={(event) => setSourceForm({ ...sourceForm, key: event.target.value })}
              />
            </label>
            <label className="field">
              类型
              <select
                className="select"
                value={sourceForm.source_type}
                onChange={(event) => setSourceForm({ ...sourceForm, source_type: event.target.value })}
              >
                {["official", "university", "syllabus", "past_paper", "mock_exam", "textbook", "user_provided", "authorized_bank"].map(
                  (type) => (
                    <option key={type} value={type}>
                      {type}
                    </option>
                  ),
                )}
              </select>
            </label>
            <label className="field" style={{ flex: 1 }}>
              标题
              <input
                className="input"
                value={sourceForm.title}
                onChange={(event) => setSourceForm({ ...sourceForm, title: event.target.value })}
              />
            </label>
            <label className="field">
              可信度
              <input
                className="input"
                type="number"
                min={0}
                max={1}
                step={0.05}
                value={sourceForm.trust_score}
                onChange={(event) => setSourceForm({ ...sourceForm, trust_score: Number(event.target.value) })}
                style={{ width: 90 }}
              />
            </label>
            <button className="btn primary" onClick={addSource} disabled={busy}>
              登记来源
            </button>
          </div>
        </Panel>

        <Panel title="导入试卷 / 资料">
          <div className="field-row">
            <label className="field">
              来源 Key
              <input
                className="input"
                value={docForm.source_key}
                onChange={(event) => setDocForm({ ...docForm, source_key: event.target.value })}
              />
            </label>
            <label className="field">
              科目
              <select
                className="select"
                value={docForm.subject_id}
                onChange={(event) => setDocForm({ ...docForm, subject_id: event.target.value })}
              >
                <option value="">默认（第一个科目）</option>
                {(profile.data?.subjects ?? []).map((subject) => (
                  <option key={subject.id} value={subject.id}>
                    {subject.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              年份
              <input
                className="input"
                type="number"
                value={docForm.paper_year}
                onChange={(event) => setDocForm({ ...docForm, paper_year: Number(event.target.value) })}
                style={{ width: 90 }}
              />
            </label>
            <label className="field">
              类型
              <select
                className="select"
                value={docForm.paper_type}
                onChange={(event) => setDocForm({ ...docForm, paper_type: event.target.value })}
              >
                <option value="real">real</option>
                <option value="mock">mock</option>
              </select>
            </label>
          </div>
          <div className="field" style={{ marginTop: 8 }}>
            试卷文本（支持题号切分；也可粘贴结构化 JSON 数组）
            <textarea
              className="textarea"
              style={{ minHeight: 150 }}
              value={docForm.content_text}
              onChange={(event) => setDocForm({ ...docForm, content_text: event.target.value })}
            />
          </div>
          <div className="row between" style={{ marginTop: 10 }}>
            <span className="tiny dim">来源、年份、可信度与文件哈希都会保存，重复文件自动去重</span>
            <button className="btn primary" onClick={importDocument} disabled={busy}>
              {busy ? "导入中…" : "导入并解析"}
            </button>
          </div>
          {message && <div className="small accent" style={{ marginTop: 10 }}>{message}</div>}
          {error && <div style={{ marginTop: 10 }}><ErrorBox error={error} /></div>}
        </Panel>
      </div>

      <Panel title="试卷库">
        {papers.loading && !papers.data && <Loading text="加载试卷" />}
        {papers.error && <ErrorBox error={papers.error} retry={papers.reload} />}
        {papers.data && (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>年份</th>
                  <th>标题</th>
                  <th>类型</th>
                  <th className="num">题目数</th>
                  <th className="num">总分</th>
                  <th>创建时间</th>
                </tr>
              </thead>
              <tbody>
                {papers.data.items.map((paper) => (
                  <tr key={paper.id}>
                    <td className="mono">{paper.year}</td>
                    <td>{paper.title}</td>
                    <td>
                      <Badge tone={paper.paper_type === "real" ? "accent" : "violet"}>{paper.paper_type}</Badge>
                    </td>
                    <td className="num">{paper.question_count}</td>
                    <td className="num">{fmtNum(paper.total_score, 0)}</td>
                    <td className="tiny dim">{fmtDate(paper.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}
