import { FormEvent, useEffect, useState } from "react";
import {
  AlertCircle, ChevronLeft, ChevronRight, Copy, FileAudio2,
  FolderOpen, LoaderCircle, Plus, RefreshCw, Search, Trash2, X,
} from "lucide-react";
import { engines, type EngineId } from "./parameterSchemas";

export type ProjectRecord = {
  schemaVersion?: number;
  id: string;
  name: string;
  description?: string;
  engine: EngineId;
  text: string;
  params: Record<string, unknown>;
  longAudio: Record<string, unknown>;
  sourceProjectId?: string | null;
  createdAt?: string;
  updatedAt?: string;
};

type ProjectListResponse = {
  items: ProjectRecord[];
  total: number;
  offset: number;
  limit: number;
};

type ConfirmAction =
  | { kind: "copy"; project: ProjectRecord }
  | { kind: "delete"; project: ProjectRecord };

const pageSize = 8;

function friendlyError(reason: unknown, fallback: string) {
  const message = reason instanceof Error ? reason.message : "";
  if (/Failed to fetch|NetworkError|fetch failed/i.test(message)) {
    return "无法连接本地工作区服务。请确认应用后端已启动，然后重试。";
  }
  return message || fallback;
}

async function responseError(response: Response, fallback: string) {
  try {
    const payload = await response.json() as { detail?: string };
    return payload.detail || `${fallback}（HTTP ${response.status}）`;
  } catch {
    return `${fallback}（HTTP ${response.status}）`;
  }
}

function formatDate(value?: string) {
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

function textSummary(text: string) {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean || "这个项目还没有正文内容。";
}

export function ProjectLibrary({
  apiUrl,
  currentProjectId,
  onClose,
  onOpen,
  onRequestNew,
  onDeletedCurrent,
}: {
  apiUrl: (path: string) => string;
  currentProjectId: string | null;
  onClose: () => void;
  onOpen: (projectId: string) => Promise<void>;
  onRequestNew: () => void;
  onDeletedCurrent: () => void;
}) {
  const [projects, setProjects] = useState<ProjectRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [openingId, setOpeningId] = useState("");
  const [confirm, setConfirm] = useState<ConfirmAction | null>(null);
  const [actionBusy, setActionBusy] = useState(false);

  const loadProjects = async (nextOffset = offset, nextQuery = appliedQuery) => {
    setLoading(true);
    setError("");
    try {
      const search = new URLSearchParams({ offset: String(nextOffset), limit: String(pageSize) });
      if (nextQuery.trim()) search.set("query", nextQuery.trim());
      const response = await fetch(apiUrl(`/api/projects?${search.toString()}`));
      if (!response.ok) throw new Error(await responseError(response, "读取项目失败"));
      const payload = await response.json() as ProjectListResponse;
      setProjects(Array.isArray(payload.items) ? payload.items : []);
      setTotal(Number(payload.total ?? 0));
      setOffset(Number(payload.offset ?? nextOffset));
    } catch (reason) {
      setError(friendlyError(reason, "无法读取项目库。"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void loadProjects(0, ""); }, []);
  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !confirm && !actionBusy && !openingId) onClose();
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [confirm, actionBusy, openingId, onClose]);

  const searchProjects = (event: FormEvent) => {
    event.preventDefault();
    const next = query.trim();
    setAppliedQuery(next);
    setOffset(0);
    void loadProjects(0, next);
  };

  const openProject = async (projectId: string) => {
    setOpeningId(projectId);
    setError("");
    try {
      await onOpen(projectId);
    } catch (reason) {
      setError(friendlyError(reason, "项目打开失败。"));
    } finally {
      setOpeningId("");
    }
  };

  const confirmAction = async () => {
    if (!confirm) return;
    setActionBusy(true);
    setError("");
    try {
      if (confirm.kind === "copy") {
        const response = await fetch(apiUrl(`/api/projects/${confirm.project.id}/copy`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: `${confirm.project.name} - 副本` }),
        });
        if (!response.ok) throw new Error(await responseError(response, "复制项目失败"));
        const copied = await response.json() as ProjectRecord;
        setConfirm(null);
        await openProject(copied.id);
      } else {
        const response = await fetch(apiUrl(`/api/projects/${confirm.project.id}`), { method: "DELETE" });
        if (!response.ok) throw new Error(await responseError(response, "删除项目失败"));
        if (confirm.project.id === currentProjectId) onDeletedCurrent();
        setConfirm(null);
        const nextOffset = projects.length === 1 && offset > 0 ? Math.max(0, offset - pageSize) : offset;
        await loadProjects(nextOffset, appliedQuery);
      }
    } catch (reason) {
      setError(friendlyError(reason, confirm.kind === "copy" ? "复制项目失败。" : "删除项目失败。"));
    } finally {
      setActionBusy(false);
    }
  };

  const page = Math.floor(offset / pageSize) + 1;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));

  return <div className="project-library-overlay" role="presentation">
    <section className="project-library-dialog" role="dialog" aria-modal="true" aria-labelledby="project-library-title">
      <header className="project-library-header">
        <div>
          <p className="eyebrow">本地工作区</p>
          <h2 id="project-library-title">打开方案</h2>
          <p>项目正文、引擎、完整参数与长音频设置都会一起恢复。</p>
        </div>
        <div className="project-library-header-actions">
          <button className="secondary-button project-new-button" onClick={onRequestNew}><Plus size={18} />新建项目</button>
          <button className="project-library-close" onClick={onClose} aria-label="关闭项目库"><X size={21} /></button>
        </div>
      </header>

      <div className="project-library-toolbar">
        <form className="project-library-search" onSubmit={searchProjects}>
          <Search size={18} />
          <input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索项目名称、说明或正文" aria-label="搜索项目" />
          {query && <button type="button" onClick={() => setQuery("")} aria-label="清空搜索"><X size={17} /></button>}
          <button type="submit">搜索</button>
        </form>
        <button className="secondary-button" onClick={() => void loadProjects()} disabled={loading}><RefreshCw className={loading ? "spin" : ""} size={18} />刷新</button>
      </div>

      {error && <div className="project-library-error" role="alert"><AlertCircle size={19} /><span>{error}</span><button onClick={() => setError("")} aria-label="关闭错误"><X size={17} /></button></div>}

      <div className="project-library-content">
        {loading ? <div className="project-library-state" aria-live="polite">
          <LoaderCircle className="spin" size={34} /><strong>正在读取本地项目</strong><span>项目只从真实工作区加载，不会填充示例数据。</span>
        </div> : projects.length === 0 ? <div className="project-library-state">
          <FolderOpen size={38} /><strong>{appliedQuery ? "没有找到匹配的项目" : "项目库还是空的"}</strong>
          <span>{appliedQuery ? "可以更换关键词，或清空搜索查看全部项目。" : "新建项目并保存后，它会出现在这里。"}</span>
          {appliedQuery ? <button className="secondary-button" onClick={() => { setQuery(""); setAppliedQuery(""); void loadProjects(0, ""); }}>清空搜索</button> : <button className="primary-button" onClick={onRequestNew}>新建第一个项目</button>}
        </div> : <div className="project-library-list">
          {projects.map(project => {
            const isCurrent = project.id === currentProjectId;
            const isOpening = openingId === project.id;
            return <article className={`project-card ${isCurrent ? "is-current" : ""}`} key={project.id}>
              <span className="project-card-icon"><FileAudio2 size={22} /></span>
              <div className="project-card-main">
                <div className="project-card-title"><strong>{project.name}</strong>{isCurrent && <span>当前项目</span>}</div>
                <p>{project.description || textSummary(project.text)}</p>
                <div><span>{engines[project.engine]?.name ?? project.engine}</span><time>{formatDate(project.updatedAt || project.createdAt)}</time><span>{project.text.replace(/\s/g, "").length} 字</span></div>
              </div>
              <div className="project-card-actions">
                <button className="project-icon-action" onClick={() => setConfirm({ kind: "copy", project })} title="复制项目" aria-label={`复制 ${project.name}`} disabled={Boolean(openingId)}><Copy size={18} /></button>
                <button className="project-icon-action danger" onClick={() => setConfirm({ kind: "delete", project })} title="删除项目" aria-label={`删除 ${project.name}`} disabled={Boolean(openingId)}><Trash2 size={18} /></button>
                <button className="primary-button project-open-button" onClick={() => void openProject(project.id)} disabled={Boolean(openingId)}>{isOpening ? <LoaderCircle className="spin" size={18} /> : <FolderOpen size={18} />}{isOpening ? "正在打开" : isCurrent ? "重新载入" : "打开"}</button>
              </div>
            </article>;
          })}
        </div>}
      </div>

      <footer className="project-library-footer">
        <span>共 {total} 个项目</span>
        <div><button onClick={() => void loadProjects(Math.max(0, offset - pageSize), appliedQuery)} disabled={loading || offset === 0} aria-label="上一页"><ChevronLeft size={19} /></button><span>第 {page} / {pageCount} 页</span><button onClick={() => void loadProjects(offset + pageSize, appliedQuery)} disabled={loading || offset + pageSize >= total} aria-label="下一页"><ChevronRight size={19} /></button></div>
      </footer>
    </section>

    {confirm && <div className="project-confirm-overlay" role="presentation">
      <div className="project-confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="project-confirm-title">
        <span className={`project-confirm-icon ${confirm.kind === "delete" ? "danger" : ""}`}>{confirm.kind === "delete" ? <Trash2 size={24} /> : <Copy size={24} />}</span>
        <h3 id="project-confirm-title">{confirm.kind === "delete" ? "确认删除这个项目？" : "创建项目副本？"}</h3>
        <p>{confirm.kind === "delete" ? `“${confirm.project.name}”会从本地工作区永久删除，已生成的音频不会随项目删除。` : `将完整复制“${confirm.project.name}”的正文、引擎和全部参数，并直接打开副本。`}</p>
        <div><button className="secondary-button" onClick={() => setConfirm(null)} disabled={actionBusy}>取消</button><button className={confirm.kind === "delete" ? "danger-button" : "primary-button"} onClick={() => void confirmAction()} disabled={actionBusy}>{actionBusy && <LoaderCircle className="spin" size={18} />}{actionBusy ? "正在处理" : confirm.kind === "delete" ? "确认删除" : "创建副本"}</button></div>
      </div>
    </div>}
  </div>;
}
