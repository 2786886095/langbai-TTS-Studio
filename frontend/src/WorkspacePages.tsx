import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle, CheckCircle2, Clock3, Copy, Download, FileAudio, FolderOpen,
  History, ListMusic, LoaderCircle, Pause, Play, RefreshCw, RotateCcw,
  Search, Square, Trash2, X,
} from "lucide-react";
import { engines, type EngineId } from "./parameterSchemas";

type PageKind = "queue" | "library" | "history";
type Job = { id: string; title: string; engine: EngineId; status: string; progress: number; outputPath?: string; error?: string; createdAt?: string; updatedAt?: string; segmentsDone: number; segmentsTotal: number };
const apiBase = new URLSearchParams(window.location.search).get("backendUrl") ?? "";
const apiUrl = (path: string) => `${apiBase}${path}`;
const validEngines: EngineId[] = ["indextts2", "voxcpm", "gpt_sovits"];

function friendlyError(reason: unknown, fallback: string) {
  const message = reason instanceof Error ? reason.message : "";
  if (/Failed to fetch|NetworkError|fetch failed/i.test(message)) {
    return "无法连接本地后端，请确认应用服务已启动后重试。";
  }
  return message || fallback;
}

function normalize(raw: unknown): Job {
  const item = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
  const output = item.output && typeof item.output === "object" ? item.output as Record<string, unknown> : {};
  const rawSegments = Array.isArray(item.segments) ? item.segments : [];
  const engine = validEngines.includes(String(item.engine) as EngineId) ? String(item.engine) as EngineId : "indextts2";
  return {
    id: String(item.id ?? crypto.randomUUID()), title: String(item.title ?? "未命名语音任务"), engine,
    status: String(item.status ?? "queued"), progress: Math.round(Math.min(1, Number(item.progress ?? 0)) * 100),
    outputPath: output.path ? String(output.path) : item.outputPath ? String(item.outputPath) : item.output_path ? String(item.output_path) : undefined,
    error: item.error ? String(item.error) : undefined, createdAt: item.createdAt ? String(item.createdAt) : item.created_at ? String(item.created_at) : undefined,
    updatedAt: item.updatedAt ? String(item.updatedAt) : item.updated_at ? String(item.updated_at) : undefined,
    segmentsDone: rawSegments.filter(segment => segment && typeof segment === "object" && (segment as { status?: string }).status === "completed").length,
    segmentsTotal: rawSegments.length,
  };
}

function formatDate(value?: string) { if (!value) return "—"; const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }); }
function statusLabel(status: string) { return ({ queued: "排队中", running: "生成中", completed: "已完成", failed: "失败", cancelled: "已取消" } as Record<string, string>)[status] ?? status; }

export function WorkspacePage({ kind, onCreate }: { kind: PageKind; onCreate: () => void }) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [audioUrl, setAudioUrl] = useState("");
  const [audioTitle, setAudioTitle] = useState("");
  const [audioJobId, setAudioJobId] = useState("");
  const [cancelConfirm, setCancelConfirm] = useState<{ id: string; title: string } | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<Job | null>(null);
  const [feedback, setFeedback] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const refreshInFlight = useRef(false);
  const config = kind === "queue" ? { eyebrow: "实时任务", title: "任务队列", description: "查看长音频分段进度，取消或重试生成任务。", icon: ListMusic } : kind === "library" ? { eyebrow: "生成成果", title: "音频库", description: "集中管理已完成音频，并打开本地输出位置。", icon: FileAudio } : { eyebrow: "全部记录", title: "历史记录", description: "回看全部引擎任务、失败原因和完成时间。", icon: History };

  const refresh = async () => {
    if (refreshInFlight.current) return false;
    refreshInFlight.current = true;
    try {
      const endpoint = kind === "queue" ? "/api/jobs" : kind === "library" ? "/api/library/audio?limit=100" : "/api/history?limit=100";
      const response = await fetch(apiUrl(endpoint));
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const raw = await response.json();
      const rows = Array.isArray(raw) ? raw : Array.isArray(raw.jobs) ? raw.jobs : Array.isArray(raw.items) ? raw.items : [];
      const normalizedAll: Job[] = rows.map((row: unknown) => normalize(row));
      const normalized = kind === "queue" ? normalizedAll.filter(job => ["queued", "running"].includes(job.status)) : normalizedAll;
      setJobs(current => JSON.stringify(current) === JSON.stringify(normalized) ? current : normalized);
      setError(current => current ? "" : current);
      return normalized.some(job => ["queued", "running"].includes(job.status));
    }
    catch (reason) {
      const nextError = friendlyError(reason, "无法读取任务");
      setError(current => current === nextError ? current : nextError);
      return false;
    }
    finally { refreshInFlight.current = false; setLoading(false); }
  };
  useEffect(() => {
    let disposed = false;
    let timer = 0;
    setLoading(true);
    const schedule = (active: boolean) => {
      if (disposed) return;
      const delay = document.hidden ? 60000 : kind === "queue" && active ? 3000 : kind === "queue" ? 12000 : 30000;
      timer = window.setTimeout(poll, delay);
    };
    const poll = async () => schedule(await refresh());
    const onVisibilityChange = () => {
      if (document.hidden || disposed) return;
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(poll, 0);
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    void poll();
    return () => {
      disposed = true;
      document.removeEventListener("visibilitychange", onVisibilityChange);
      if (timer) window.clearTimeout(timer);
    };
  }, [kind]);
  const rows = useMemo(() => jobs.filter(job => kind === "queue" ? ["queued", "running"].includes(job.status) : kind === "library" ? job.status === "completed" && job.outputPath : true).filter(job => !query || `${job.title}${engines[job.engine].name}${job.status}`.toLowerCase().includes(query.toLowerCase())), [jobs, kind, query]);
  const responseError = async (response: Response, fallback: string) => {
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    return payload?.detail || fallback;
  };
  const action = async (id: string, name: "cancel" | "retry") => { try { const response = await fetch(apiUrl(`/api/jobs/${id}/${name}`), { method: "POST" }); if (!response.ok) throw new Error(await responseError(response, "操作失败")); setCancelConfirm(null); setFeedback({ tone: "success", text: name === "cancel" ? "已请求取消任务。" : "任务已重新加入本次生成队列。" }); await refresh(); } catch (reason) { setFeedback({ tone: "error", text: reason instanceof Error ? reason.message : "操作失败" }); } };
  const deleteJob = async (job: Job, deleteOutput: boolean) => {
    if (deleting) return;
    setDeleting(true);
    try {
      const response = await fetch(apiUrl(`/api/jobs/${job.id}?deleteOutput=${deleteOutput ? "true" : "false"}`), { method: "DELETE" });
      if (!response.ok) throw new Error(await responseError(response, "删除失败"));
      const result = await response.json() as { outputDeleted?: boolean };
      if (audioJobId === job.id) { setAudioUrl(""); setAudioTitle(""); setAudioJobId(""); }
      setDeleteConfirm(null);
      setFeedback({ tone: "success", text: deleteOutput && result.outputDeleted ? "任务记录和音频文件均已删除。" : deleteOutput && job.outputPath ? "任务记录已删除；音频文件原本已不存在。" : job.outputPath ? "任务记录已删除，音频文件已保留。" : "任务记录已删除。" });
      await refresh();
    } catch (reason) {
      setFeedback({ tone: "error", text: reason instanceof Error ? reason.message : "删除失败" });
    } finally { setDeleting(false); }
  };
  const resolveOutput = async (job: Job) => {
    const response = await fetch(apiUrl(`/api/jobs/${job.id}/output`));
    if (!response.ok) throw new Error(`无法读取输出文件（HTTP ${response.status}）`);
    const raw = await response.json() as { output?: { path?: string }; openContract?: { open?: { path?: string }; reveal?: { path?: string } } };
    return { openPath: raw.openContract?.open?.path ?? raw.output?.path ?? job.outputPath, revealPath: raw.openContract?.reveal?.path ?? raw.output?.path ?? job.outputPath };
  };
  const showOutput = async (job: Job) => { try { const output = await resolveOutput(job); if (!output.revealPath) throw new Error("输出文件不存在"); await window.langbaiDesktop?.showItemInFolder?.(output.revealPath); } catch (reason) { setError(reason instanceof Error ? reason.message : "无法打开文件位置"); } };
  const playOutput = async (job: Job) => { try { const output = await resolveOutput(job); if (!output.openPath) throw new Error("输出文件不存在"); const url = await window.langbaiDesktop?.getAudioUrl?.(output.openPath); if (!url) throw new Error("桌面端未返回可播放地址"); setAudioUrl(url); setAudioTitle(job.title); setAudioJobId(job.id); } catch (reason) { setFeedback({ tone: "error", text: reason instanceof Error ? reason.message : "无法播放音频" }); } };
  const exportOutput = async (job: Job) => { try {
    const output = await resolveOutput(job);
    if (!output.openPath) throw new Error("输出文件不存在");
    const exported = await window.langbaiDesktop?.exportAudio?.(output.openPath);
    if (exported?.path) await window.langbaiDesktop?.showItemInFolder?.(exported.path);
  } catch (reason) { setError(friendlyError(reason, "无法导出音频")); } };

  return <div className="data-page">
    <header className="data-page-header"><div><p className="eyebrow">{config.eyebrow}</p><h1>{config.title}</h1><p>{config.description}</p></div><div className="data-header-actions"><label className="data-search"><Search size={17} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索任务或引擎" /></label><button className="secondary-button" onClick={refresh}><RefreshCw size={17} />刷新</button><button className="primary-button" onClick={onCreate}><Play size={17} />新建语音</button></div></header>
    {feedback && <div className={`data-feedback ${feedback.tone}`} role={feedback.tone === "error" ? "alert" : "status"}><span>{feedback.text}</span><button onClick={() => setFeedback(null)} aria-label="关闭提示"><X size={17} /></button></div>}
    {kind === "library" && rows[0]?.outputPath && !audioUrl && <div className="library-player idle"><div><FileAudio size={18} /><span><strong>快速试听</strong><small>{rows[0].title}</small></span></div><button className="secondary-button" onClick={() => playOutput(rows[0])}><Play size={17} />播放最近音频</button></div>}
    {audioUrl && <div className="library-player"><div><Play size={18} /><span><strong>正在播放</strong><small>{audioTitle}</small></span></div><audio src={audioUrl} controls autoPlay /><button onClick={() => { setAudioUrl(""); setAudioJobId(""); }} aria-label="关闭播放器"><X size={18} /></button></div>}
    <div className="data-summary"><div><config.icon size={20} /><span><strong>{rows.length}</strong><small>{kind === "library" ? "份可用音频" : "条任务记录"}</small></span></div><div><CheckCircle2 size={20} /><span><strong>{jobs.filter(job => job.status === "completed").length}</strong><small>已完成</small></span></div><div><Clock3 size={20} /><span><strong>{jobs.filter(job => ["queued", "running"].includes(job.status)).length}</strong><small>进行中</small></span></div></div>
    <section className="data-surface">
      {loading ? <div className="page-state"><LoaderCircle className="spin" size={30} /><strong>正在读取本地任务</strong><span>这通常只需要几秒钟。</span></div> : error ? <div className="page-state error"><AlertCircle size={30} /><strong>加载失败</strong><span>{error}</span><button className="secondary-button" onClick={refresh}><RefreshCw size={17} />重试</button></div> : rows.length === 0 ? <div className="page-state"><config.icon size={34} /><strong>{query ? "没有匹配结果" : kind === "library" ? "还没有完成的音频" : kind === "queue" ? "当前没有生成任务" : "暂无历史记录"}</strong><span>{query ? "尝试更换搜索词。" : "从创作台创建第一个语音任务。"}</span>{!query && <button className="primary-button" onClick={onCreate}>前往创作台</button>}</div> : <div className="data-table"><div className="data-table-head"><span>任务</span><span>引擎</span><span>状态 / 进度</span><span>更新时间</span><span>操作</span></div>{rows.map(job => <article className="data-row" key={job.id}><div className="data-job"><span className="data-job-icon"><FileAudio size={18} /></span><span><strong title={job.title}>{job.title}</strong><small title={job.outputPath}>{job.segmentsTotal ? `${job.segmentsDone}/${job.segmentsTotal} 个分段` : job.outputPath || "等待处理"}</small></span></div><span className="engine-pill">{engines[job.engine].name}</span><div className="data-progress"><div><span className={`status-dot ${job.status}`} />{statusLabel(job.status)}<strong>{job.progress}%</strong></div><div className="progress-track"><i style={{ width: `${job.progress}%` }} /></div>{job.error && <small title={job.error}>{job.error}</small>}</div><time>{formatDate(job.updatedAt || job.createdAt)}</time><div className="row-actions">{job.status === "completed" && job.outputPath && <button onClick={() => playOutput(job)} title="试听音频" aria-label={`试听 ${job.title}`}><Play size={17} /></button>}{kind === "library" && job.outputPath && <button onClick={() => exportOutput(job)} title="导出音频副本" aria-label={`导出 ${job.title}`}><Download size={17} /></button>}{job.outputPath && <button onClick={() => showOutput(job)} title="打开文件位置" aria-label={`打开 ${job.title} 的文件位置`}><FolderOpen size={17} /></button>}{["queued", "running"].includes(job.status) && <button onClick={() => setCancelConfirm({ id: job.id, title: job.title })} title="取消任务" aria-label={`取消 ${job.title}`}><Square size={16} /></button>}{["failed", "cancelled"].includes(job.status) && <button onClick={() => action(job.id, "retry")} title="重试" aria-label={`重试 ${job.title}`}><RotateCcw size={17} /></button>}{job.outputPath && <button onClick={() => navigator.clipboard.writeText(job.outputPath!)} title="复制路径" aria-label={`复制 ${job.title} 的路径`}><Copy size={17} /></button>}{!["queued", "running"].includes(job.status) && <button className="delete-row-button" onClick={() => setDeleteConfirm(job)} title="删除记录" aria-label={`删除 ${job.title}`}><Trash2 size={17} /></button>}</div></article>)}</div>}
    </section>
    {cancelConfirm && <div className="confirm-overlay" role="presentation"><div className="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="cancel-confirm-title"><span className="confirm-icon"><Pause size={22} /></span><h2 id="cancel-confirm-title">取消这个生成任务？</h2><p>“{cancelConfirm.title}”已完成的分段会保留，之后可以从断点重试。</p><div><button className="secondary-button" onClick={() => setCancelConfirm(null)}>继续生成</button><button className="danger-button" onClick={() => action(cancelConfirm.id, "cancel")}>确认取消</button></div><button className="confirm-close" onClick={() => setCancelConfirm(null)} aria-label="关闭"><X size={18} /></button></div></div>}
    {deleteConfirm && <div className="confirm-overlay" role="presentation"><div className="confirm-dialog delete-dialog" role="alertdialog" aria-modal="true" aria-labelledby="delete-confirm-title"><span className="confirm-icon danger"><Trash2 size={22} /></span><h2 id="delete-confirm-title">删除“{deleteConfirm.title}”？</h2><p>{deleteConfirm.outputPath ? "请选择是否同时删除已生成的本地音频。此操作无法撤销。" : "此任务没有可用音频，将只删除任务记录。此操作无法撤销。"}</p><div className="delete-choice-list">{deleteConfirm.outputPath && <button className="delete-choice preserve" disabled={deleting} onClick={() => deleteJob(deleteConfirm, false)}><span><strong>仅删除记录</strong><small>保留 output 目录中的音频文件</small></span></button>}<button className="delete-choice destructive" disabled={deleting} onClick={() => deleteJob(deleteConfirm, Boolean(deleteConfirm.outputPath))}><Trash2 size={18} /><span><strong>{deleteConfirm.outputPath ? "记录和音频一起删除" : "删除任务记录"}</strong><small>{deleteConfirm.outputPath ? "同时永久删除本地 WAV 文件" : "不会删除其他本地文件"}</small></span></button></div><button className="secondary-button delete-cancel" disabled={deleting} onClick={() => setDeleteConfirm(null)}>取消</button><button className="confirm-close" disabled={deleting} onClick={() => setDeleteConfirm(null)} aria-label="关闭"><X size={18} /></button></div></div>}
  </div>;
}
