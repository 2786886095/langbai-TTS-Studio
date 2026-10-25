import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, AudioLines, Check, ChevronDown, ChevronLeft, ChevronRight, Download, FileText, FolderOpen, LoaderCircle, Pause, Play, RefreshCw, Save, Settings2, ShieldCheck, Square, Upload, X, Zap } from "lucide-react";
import { defaultsFor, engines, parameterGroups, type EngineId } from "./parameterSchemas";
import { toApiParams } from "./parameterMapping";
import { FieldControl } from "./ParameterFields";
import { serviceRequest, serviceUrl } from "./serviceClient";
import { mergeLongformJobs, subscribeLongformEvents } from "./longformEvents";
import { readLongformPreferences, saveEnginePreference, savedLegacyParameters } from "./longformPreferences";
import { controlLongformJob, type JobControl } from "./longformControls";
import "./longform.css";

export type LongformEngineId = EngineId | "breeze";
export const longformEngines = { ...engines, breeze: { name: "Breeze TTS 2 · 加速", description: "加速版 · CFG 1 参考克隆", accent: "#0284c7" } };
export type LongformSegment = { id: string; index: number; start: number; end: number; text: string; requestText: string; status: string; attempts: number; outputPath?: string; durationSeconds?: number; error?: string; warnings?: string[] };
export type LongformJob = {
  id: string; title: string; engine: LongformEngineId; status: string; phase: string; progress: number;
  acceptedSegments: number; totalSegments: number; totalSegmentRecords?: number; segmentsRetained?: boolean; segmentsCleanupPending?: boolean; cleanupWarning?: string; outputPath?: string; error?: string; version: number;
  createdAt: string; updatedAt: string; params?: Record<string, unknown>; output?: OutputOptions; text?: string; segments?: LongformSegment[];
  timing?: { totalWallSeconds?: number | null; activeSeconds?: number | null; generationSeconds?: number | null; pauseSeconds?: number | null; unknownSeconds?: number | null; clockAnomaly?: boolean; etaLowSeconds?: number | null; etaHighSeconds?: number | null };
};
type OutputOptions = { format: "WAV" | "FLAC" | "MP3"; sampleRate: "native" | number; silenceMs: number; keepSegments: boolean };
type Voice = { id: string; name: string; engine: LongformEngineId; parameters: Record<string, unknown>; source?: string; license?: string; consent?: boolean | string };
type Preview = { documentId: string; planId: string; totalSegments: number; sourceCharacters: number; exactCoverage: boolean; segments: LongformSegment[] };
type Props = { standalone?: boolean; text?: string; onTextChange?: (text: string) => void; initialEngine?: LongformEngineId; onEngineChange?: (engine: LongformEngineId) => void; legacyParameters?: Partial<Record<EngineId, Record<string, unknown>>>; mapLegacyParameters?: (engine: EngineId, params: Record<string, unknown>) => Record<string, unknown>; onOpenParameters?: (engine: EngineId) => void; onTasks?: () => void; onProject?: () => void; onLegacyMode?: (context: { engine: LongformEngineId; params: Record<string, unknown> }) => void; focusJobId?: string; breezeVoice?: Voice | null; legacyVoice?: Voice | null; onVoiceConsumed?: () => void };

const labels: Record<string, string> = { queued: "排队中", preparing: "准备与校验", acquiring_gpu: "等待显卡", loading: "加载模型", warming: "预热模型", generating: "生成正文", pausing: "正在暂停", paused: "已暂停", merging: "合并音频", exporting: "导出音频", verifying: "校验成品", completed: "已完成", interrupted: "意外中断", storage_blocked: "存储受阻", failed: "失败", cancelled: "已取消", pending: "待生成", running: "生成中", produced: "待校验", accepted: "已验收", rejected: "未通过", damaged: "文件损坏", needs_attention: "需要处理", stale: "配置已变化", skipped: "无需配音", superseded: "已被替换", stop_unconfirmed: "停止尚未确认" };
const resumable = ["paused", "interrupted", "storage_blocked", "failed"];
const active = ["preparing", "queued", "acquiring_gpu", "loading", "warming", "generating", "pausing"];
const initialDraft = "";
const draftKey = "langbai-longform-draft-v2";
export const readLongformDraft = (fallback = initialDraft) => { try { return localStorage.getItem(draftKey) ?? fallback; } catch { return fallback; } };
export function formatDuration(seconds?: number | null) { if (seconds == null || !Number.isFinite(seconds)) return "无记录"; const n = Math.max(0, Math.round(seconds)); return `${Math.floor(n / 3600) ? `${Math.floor(n / 3600)}:` : ""}${String(Math.floor(n / 60) % 60).padStart(2, "0")}:${String(n % 60).padStart(2, "0")}`; }
export const longformStatusLabel = (status: string) => labels[status] ?? status;

export function LongformWorkspace(props: Props) {
  const [draft, setDraft] = useState(() => readLongformDraft());
  const initialPreferences = useRef(readLongformPreferences());
  const initialEngine = props.initialEngine ?? initialPreferences.current.engine ?? "breeze";
  const initialEnginePreference = initialPreferences.current.perEngine[initialEngine];
  const text = props.text ?? draft;
  const changeText = (value: string) => { setDraft(value); props.onTextChange?.(value); setPreview(null); };
  const [engine, setEngine] = useState<LongformEngineId>(initialEngine);
  const [hydratedEngine, setHydratedEngine] = useState<LongformEngineId | null>(null);
  const [title, setTitle] = useState("");
  const [voices, setVoices] = useState<Voice[]>([]);
  const [voiceId, setVoiceId] = useState("");
  const [refAudio, setRefAudio] = useState(initialPreferences.current.perEngine.breeze?.refAudio ?? "");
  const [refText, setRefText] = useState(initialPreferences.current.perEngine.breeze?.refText ?? "");
  const [source, setSource] = useState("");
  const [voiceName, setVoiceName] = useState("");
  const [consent, setConsent] = useState(false);
  const [licenseAccepted, setLicenseAccepted] = useState(false);
  const [maxChars, setMaxChars] = useState(initialEnginePreference?.maxChars ?? (initialEngine === "breeze" ? 120 : 180));
  const [strategy, setStrategy] = useState<"breeze_han_v1" | "generic_v2" | "paragraph">(initialEnginePreference?.strategy ?? (initialEngine === "breeze" ? "breeze_han_v1" : "generic_v2"));
  const [output, setOutput] = useState<OutputOptions>(initialEnginePreference?.output ?? { format: "WAV", sampleRate: "native", silenceMs: 150, keepSegments: true });
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [jobs, setJobs] = useState<LongformJob[]>([]);
  const [selectedJob, setSelectedJob] = useState<LongformJob | null>(null);
  const [segments, setSegments] = useState<LongformSegment[]>([]);
  const [segmentPage, setSegmentPage] = useState(0);
  const [segmentTotal, setSegmentTotal] = useState(0);
  const [previewPage, setPreviewPage] = useState(0);
  const [connected, setConnected] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [mobileTab, setMobileTab] = useState("document");
  const [showVoice, setShowVoice] = useState(false);
  const [showBinding, setShowBinding] = useState(false);
  const [parametersOpen, setParametersOpen] = useState(false);
  const [redoSegment, setRedoSegment] = useState<{ id: string; index: number; seed: number } | null>(null);
  const [events, setEvents] = useState<Array<{ id?: number; type?: string; event?: string; createdAt?: string; utc?: string; kind?: string; payload?: unknown; data?: unknown }>>([]);
  const [parameterTab, setParameterTab] = useState("common");
  const [legacyOverrides, setLegacyOverrides] = useState<Partial<Record<EngineId, Record<string, unknown>>>>(savedLegacyParameters);
  const [formats, setFormats] = useState<OutputOptions["format"][]>(["WAV", "FLAC"]);
  const [environment, setEnvironment] = useState<{ available: boolean; detail?: string } | null>(null);
  const [binding, setBinding] = useState({ sourcePath: "F:\\AI\\Breeze-TTS-2", pythonPath: "/home/langbai666/.venvs/breeze-tts-2/bin/python", distribution: "Ubuntu", modelPath: "F:\\AI\\Breeze-TTS-2\\checkpoints\\Breeze-TTS-2" });
  const fileInput = useRef<HTMLInputElement>(null);
  const selectedId = useRef("");
  const polling = useRef(false);
  const liveEvents = useRef(false);
  const pageSize = 20;

  useEffect(() => { const timer = window.setTimeout(() => { try { localStorage.setItem(draftKey, text); } catch { setFeedback("本地草稿空间不足，请先导出文稿。"); } }, 600); return () => window.clearTimeout(timer); }, [text]);
  useEffect(() => { selectedId.current = selectedJob?.id ?? ""; }, [selectedJob?.id]);
  const refreshJobs = useCallback(async () => {
    if (polling.current) return;
    polling.current = true;
    try {
      const data = await serviceRequest<{ items: LongformJob[] }>("/api/jobs/v2");
      setJobs(previous => mergeLongformJobs(previous, data.items)); setConnected(true);
      const updated = data.items.find(item => item.id === selectedId.current);
      if (updated) setSelectedJob(previous => previous?.id === updated.id && updated.version >= previous.version ? { ...previous, ...updated } : previous);
    } catch { setConnected(false); }
    finally { polling.current = false; }
  }, []);
  useEffect(() => {
    let timer = 0;
    let disposed = false;
    const poll = async () => { if (!document.hidden) await refreshJobs(); if (!disposed) timer = window.setTimeout(poll, liveEvents.current ? 60000 : 6000); };
    void poll();
    const unsubscribe = subscribeLongformEvents(update => {
      liveEvents.current = update.connected;
      if (update.connected) setConnected(true);
      if (update.items) {
        setJobs(previous => mergeLongformJobs(previous, update.items!));
        const selected = update.items.find(item => item.id === selectedId.current);
        if (selected) setSelectedJob(previous => previous && selected.version >= previous.version ? { ...previous, ...selected } : previous);
      }
    });
    return () => { disposed = true; window.clearTimeout(timer); unsubscribe(); };
  }, [refreshJobs]);
  useEffect(() => {
    const saved = readLongformPreferences().perEngine[engine];
    setVoiceId(""); setPreview(null); setEnvironment(null); setHydratedEngine(null);
    setMaxChars(saved?.maxChars ?? (engine === "breeze" ? 120 : 180));
    setStrategy(saved?.strategy ?? (engine === "breeze" ? "breeze_han_v1" : "generic_v2"));
    setOutput(saved?.output ?? { format: "WAV", sampleRate: "native", silenceMs: 150, keepSegments: true });
    setVoices([]); props.onEngineChange?.(engine);
    if (engine === "breeze") { setRefAudio(saved?.refAudio ?? ""); setRefText(saved?.refText ?? ""); }
    let current = true;
    const endpoint = engine === "breeze" ? "/api/breeze/voices" : `/api/voice-profiles?engine=${engine}`;
    void serviceRequest<{ items: Voice[] }>(endpoint).then(data => {
      if (!current) return;
      const valid = data.items.filter(voice => voice.engine === engine);
      setVoices(valid);
      const preferredId = engine === "breeze" ? props.breezeVoice?.id ?? saved?.voiceId : props.legacyVoice?.engine === engine ? props.legacyVoice.id : saved?.voiceId;
      const restored = valid.find(voice => voice.id === preferredId);
      if (restored) {
        setVoiceId(restored.id);
        if (engine === "breeze") { setRefAudio(String(restored.parameters.ref_audio_path ?? "")); setRefText(String(restored.parameters.ref_text ?? "")); }
        else setLegacyOverrides(previous => ({ ...previous, [engine]: { ...previous[engine], ...restored.parameters } }));
      }
    }).catch(() => { if (current) setVoices([]); }).finally(() => { if (current) setHydratedEngine(engine); });
    return () => { current = false; };
  }, [engine]);
  useEffect(() => {
    if (hydratedEngine !== engine) return;
    saveEnginePreference(engine, { voiceId, ...(engine === "breeze" ? { refAudio, refText } : { params: legacyOverrides[engine] }), output, maxChars, strategy });
  }, [engine, hydratedEngine, voiceId, refAudio, refText, output, maxChars, strategy, legacyOverrides]);
  useEffect(() => {
    void serviceRequest<Record<string, unknown>>("/api/breeze/binding").then(data => {
      const record = (data.binding && typeof data.binding === "object" ? data.binding : data) as Record<string, unknown>;
      setBinding(previous => ({ ...previous, ...Object.fromEntries(Object.keys(previous).filter(key => typeof record[key] === "string").map(key => [key, String(record[key])])) }));
      if (record.licenseAccepted === true && record.licenseVersion === "Breeze TTS 2 Model License v1.1 (2026-09-01)") setLicenseAccepted(true);
      if (data.status && typeof data.status === "object") setEnvironment(data.status as { available: boolean; detail?: string });
    }).catch(() => {});
  }, []);
  useEffect(() => { void serviceRequest<{ formats: OutputOptions["format"][] }>("/api/longform/capabilities").then(data => setFormats(data.formats)).catch(() => {}); }, []);
  useEffect(() => {
    if (!selectedJob?.id) { setSegments([]); return; }
    let current = true;
    void serviceRequest<{ items: LongformSegment[]; total: number }>(`/api/jobs/v2/${selectedJob.id}/segments?offset=${segmentPage * pageSize}&limit=${pageSize}`).then(data => { if (current) { setSegments(data.items); setSegmentTotal(data.total); } }).catch(() => {});
    return () => { current = false; };
  }, [selectedJob?.id, selectedJob?.version, segmentPage]);

  const run = async (name: string, fn: () => Promise<void>) => { setBusy(name); setError(""); setFeedback(""); try { await fn(); } catch (reason) { setError(reason instanceof Error ? reason.message : "操作失败"); } finally { setBusy(""); } };
  const chooseVoice = (id: string) => { setVoiceId(id); const voice = voices.find(v => v.id === id); if (voice && engine === "breeze") { setRefAudio(String(voice.parameters.ref_audio_path ?? "")); setRefText(String(voice.parameters.ref_text ?? "")); } else if (voice && engine !== "breeze") { setLegacyOverrides(previous => ({ ...previous, [engine]: { ...previous[engine], ...voice.parameters } })); } };
  const legacyValues = engine === "breeze" ? {} : { ...defaultsFor(engine), ...props.legacyParameters?.[engine], ...legacyOverrides[engine] };
  const params = () => {
    if (engine === "breeze") return { ref_audio_path: refAudio, ref_text: refText, performance_mode: "accelerated", cfg_scale: 1, seed: 42, temperature: 0.9, top_k: 50, top_p: 1, repetition_penalty: 1.1, max_new_tokens: 600 };
    return (props.mapLegacyParameters ?? toApiParams)(engine, legacyValues);
  };
  const doPreview = () => run("preview", async () => {
    if (!text.trim()) throw new Error("请先输入文稿。");
    const result = await serviceRequest<Preview>("/api/segmentation/preview", { method: "POST", body: { text, engine, segmentation: { maxChars, strategy } } });
    setPreview(result); setPreviewPage(0); setMobileTab("segments");
  });
  const generate = (trial = false) => run(trial ? "trial" : "generate", async () => {
    if (!text.trim()) throw new Error("请先输入文稿。");
    if (engine === "breeze" && (!refAudio.trim() || !refText.trim())) throw new Error("Breeze 克隆需要参考音频路径和准确参考文字。");
    if (engine === "breeze" && !licenseAccepted) throw new Error("请阅读并确认模型许可说明。");
    let requestText = text;
    if (trial) {
      const plan = preview ?? await serviceRequest<Preview>("/api/segmentation/preview", { method: "POST", body: { text, engine, segmentation: { maxChars, strategy } } });
      const first = plan.segments.find(s => s.requestText?.trim());
      if (!first) throw new Error("文稿中没有可以配音的内容。");
      requestText = first.text;
    }
    const created = await serviceRequest<LongformJob>("/api/jobs/v2", { method: "POST", body: { text: requestText, title: `${trial ? "试音 · " : ""}${title.trim() || text.trim().slice(0, 28) || "长文配音"}`, engine, params: params(), ...(voiceId ? { voiceProfileId: voiceId } : {}), licenseAccepted, segmentation: { maxChars, strategy }, output } });
    selectedId.current = created.id; setSelectedJob(created); setDetailOpen(true); setSegmentPage(0); await refreshJobs();
  });
  const action = (name: string, extra: Record<string, unknown> = {}) => run(name, async () => {
    if (!selectedJob) return;
    if (["pause", "stop", "cancel", "resume"].includes(name)) {
      const result = await controlLongformJob(selectedJob.id, name as JobControl, (path, options) => serviceRequest<LongformJob>(path, options), extra);
      setSelectedJob(result.job); setFeedback(result.message); await refreshJobs(); return;
    }
    const updated = await serviceRequest<LongformJob>(`/api/jobs/v2/${selectedJob.id}/${name}`, { method: "POST", body: { version: selectedJob.version, ...extra } });
    setSelectedJob(updated); await refreshJobs();
  });
  useEffect(() => {
    if (!detailOpen || !selectedJob) return;
    let current = true;
    void serviceRequest<{ items: typeof events }>(`/api/jobs/v2/${selectedJob.id}/events?tail=true&limit=40`).then(data => { if (current) setEvents(data.items.slice(-40)); }).catch(() => {});
    return () => { current = false; };
  }, [detailOpen, selectedJob?.id, selectedJob?.version]);
  const openJob = (job: LongformJob) => run("detail", async () => { const detail = await serviceRequest<LongformJob>(`/api/jobs/v2/${job.id}`); selectedId.current = detail.id; setSelectedJob(detail); setSegmentPage(0); setDetailOpen(true); });
  useEffect(() => {
    if (props.focusJobId) void run("detail", async () => {
      const detail = await serviceRequest<LongformJob>(`/api/jobs/v2/${props.focusJobId}`);
      selectedId.current = detail.id; setSelectedJob(detail); setDetailOpen(true); setSegmentPage(0);
    });
  }, [props.focusJobId]);
  useEffect(() => { if (props.legacyVoice && props.legacyVoice.engine !== "breeze") { setEngine(props.legacyVoice.engine); setVoiceId(props.legacyVoice.id); setLegacyOverrides(previous => ({ ...previous, [props.legacyVoice!.engine as EngineId]: props.legacyVoice!.parameters })); props.onVoiceConsumed?.(); } }, [props.legacyVoice]);
  useEffect(() => { if (props.breezeVoice) { setEngine("breeze"); setVoiceId(props.breezeVoice.id); setRefAudio(String(props.breezeVoice.parameters.ref_audio_path ?? "")); setRefText(String(props.breezeVoice.parameters.ref_text ?? "")); props.onVoiceConsumed?.(); } }, [props.breezeVoice]);
  useEffect(() => { const handler = () => void generate(); window.addEventListener("langbai:longform-generate", handler); return () => window.removeEventListener("langbai:longform-generate", handler); });
  const importFile = async (file?: File) => { if (!file) return; await run("import", async () => { changeText(await file.text()); setTitle(file.name.replace(/\.[^.]+$/, "")); }); };
  const browseReference = () => run("reference", async () => { const selected = await window.langbaiDesktop?.chooseFile({ filters: [{ name: "参考音频", extensions: ["wav", "flac", "mp3"] }] }); if (selected) { setRefAudio(selected); setVoiceId(""); } else if (!window.langbaiDesktop) setFeedback("网页版请填写任务服务所在电脑可访问的音频绝对路径。"); });
  const saveVoice = () => run("voice", async () => {
    if (!voiceName.trim() || !refAudio.trim() || !refText.trim()) throw new Error("保存声音资料前，请填写名称、参考音频和准确文字。");
    const saved = await serviceRequest<Voice>("/api/breeze/voices", { method: "POST", body: { name: voiceName, parameters: { ref_audio_path: refAudio, ref_text: refText }, source, license: "由用户提供参考音频权利说明", consent } });
    setVoices(prev => [saved, ...prev]); setVoiceId(saved.id); setFeedback("声音资料已保存，仅用于 Breeze。");
  });
  const saveBinding = () => run("binding", async () => { await serviceRequest("/api/breeze/binding", { method: "PUT", body: { ...binding, confirm: true, licenseAccepted } }); setFeedback("已保存 WSL 绑定；生成前将核验运行环境。"); setShowBinding(false); });
  const checkEnvironment = () => run("check", async () => {
    if (engine === "breeze") {
      const result = await serviceRequest<{ status: { available: boolean; detail?: string } }>("/api/breeze/binding");
      setEnvironment(result.status);
      if (!result.status?.available) throw new Error(result.status?.detail || "Breeze 运行环境尚未就绪，请先配置绑定。");
    } else {
      const result = await serviceRequest<Array<{ id: string; available: boolean; detail?: string }>>("/api/engines/status");
      const status = result.find(item => item.id === engine);
      setEnvironment(status ?? null);
      if (!status?.available) throw new Error(status?.detail || "所选引擎尚未就绪，请在引擎页绑定或安装。");
    }
    setFeedback("引擎状态检查通过；提交时还会核验参考文件、模型和任务预算。");
  });
  const recoverable = jobs.filter(j => resumable.includes(j.status));
  const currentJob = selectedJob ?? jobs.find(j => active.includes(j.status));

  return <section className={`longform-workspace ${props.standalone ? "standalone" : ""}`} aria-label="长文本配音工作台">
    <header className="lf-topbar"><div><p className="eyebrow">长文配音工作台</p><h1>让长篇文稿，一次完整成声</h1></div><div className="lf-top-actions">{props.onLegacyMode && <button className="lf-text-button" onClick={() => props.onLegacyMode?.({ engine, params: legacyValues })}><Settings2 size={15} />原生 / 多人模式</button>}<span className={`connection ${connected ? "ok" : "warn"}`}><i />{connected ? "任务服务已连接" : "任务服务未连接"}</span>{props.onProject && engine !== "breeze" && <button className="secondary-button" onClick={props.onProject}><FolderOpen size={16} />项目</button>}<button className="secondary-button" onClick={() => void generate(true)} disabled={!!busy || !text.trim()}><Play size={15} />单段试音</button><button className="primary-button" onClick={() => void generate()} disabled={!!busy || !text.trim()}>{busy === "generate" ? <LoaderCircle className="spin" size={16} /> : <Zap size={16} />}一键生成</button></div></header>
    {props.standalone && <div className="lf-service-note"><ShieldCheck size={14} />由 langbai-TTS-Studio 任务服务执行 · 与桌面端同步 · 网页不单独加载模型</div>}
    {(error || feedback) && <div className={`lf-message ${error ? "error" : "success"}`} role={error ? "alert" : "status"}><AlertCircle size={16} /><span>{error || feedback}</span><button className="icon-button" aria-label="关闭提示" onClick={() => { setError(""); setFeedback(""); }}><X size={15} /></button></div>}
    {recoverable.length > 0 && <div className="lf-recovery"><RefreshCw size={17} /><div><strong>{recoverable.length} 个任务可以继续</strong><span>{recoverable[0].title} · 已验收 {recoverable[0].acceptedSegments}/{recoverable[0].totalSegments} 段</span></div><button className="secondary-button" onClick={() => void openJob(recoverable[0])}>查看恢复任务<ChevronRight size={15} /></button></div>}
    <div className="lf-context"><label>配音引擎<select value={engine} onChange={e => setEngine(e.target.value as LongformEngineId)}>{Object.entries(longformEngines).map(([id, item]) => <option key={id} value={id}>{item.name}</option>)}</select></label><label className="lf-voice-select">声音资料<select value={voiceId} onChange={e => chooseVoice(e.target.value)}><option value="">使用当前临时配置</option>{voices.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}</select></label><span className="lf-capability"><Zap size={13} />{engine === "breeze" ? "accelerated · CFG 1 · 24kHz" : longformEngines[engine].description}</span>{engine === "breeze" ? <button className="lf-text-button" onClick={() => setShowVoice(v => !v)}><AudioLines size={15} />参考声音<ChevronDown size={14} /></button> : <button className="secondary-button" onClick={() => setParametersOpen(true)}><Settings2 size={15} />推理参数</button>}</div>
    {engine === "breeze" && showVoice && <section className="lf-reference"><label className="lf-reference-path">参考音频<div className="lf-inline"><input value={refAudio} onChange={e => { setRefAudio(e.target.value); setVoiceId(""); }} placeholder="例如 F:\\Audio\\reference.wav" /><button className="secondary-button" onClick={() => void browseReference()} aria-label="选择参考音频"><FolderOpen size={16} /></button></div></label><label className="lf-reference-text">准确参考文字<input value={refText} onChange={e => { setRefText(e.target.value); setVoiceId(""); }} placeholder="填写参考音频中实际说出的文字" /></label>{voiceId && <p className="lf-reference-note">参考授权记录：{voices.find(voice => voice.id === voiceId)?.consent === true ? "用户已记录确认" : `未核实${typeof voices.find(voice => voice.id === voiceId)?.consent === "string" ? ` · ${voices.find(voice => voice.id === voiceId)?.consent}` : ""}`}</p>}<details><summary>保存为 Breeze 声音资料</summary><div className="lf-voice-save"><input aria-label="声音资料名称" placeholder="声音资料名称" value={voiceName} onChange={e => setVoiceName(e.target.value)} /><input aria-label="音频来源或授权" placeholder="音频来源 / 授权说明" value={source} onChange={e => setSource(e.target.value)} /><label className="lf-check"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} />已取得所需授权与同意</label><button className="secondary-button" onClick={() => void saveVoice()} disabled={!!busy}><Save size={15} />保存资料</button></div></details></section>}
    <div className="lf-mobile-tabs" role="tablist" aria-label="工作区域"><button role="tab" aria-selected={mobileTab === "document"} onClick={() => setMobileTab("document")}>文稿</button><button role="tab" aria-selected={mobileTab === "segments"} onClick={() => setMobileTab("segments")}>分段与输出</button></div>
    <div className={`lf-main-grid show-${mobileTab}`}><section className="lf-document"><div className="lf-panel-heading"><div><FileText size={17} /><strong>文稿</strong></div><input className="lf-title" aria-label="长文任务名称" placeholder="输入任务名称（可选）" value={title} onChange={e => setTitle(e.target.value)} /><div><input type="file" accept=".txt,.md" ref={fileInput} hidden onChange={e => { void importFile(e.target.files?.[0]); e.target.value = ""; }} /><button className="lf-text-button" onClick={() => fileInput.current?.click()}><Upload size={15} />导入 TXT</button><button className="lf-text-button" onClick={() => { const blob = new Blob([text], { type: "text/plain;charset=utf-8" }); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = `${title || "配音文稿"}.txt`; link.click(); URL.revokeObjectURL(url); }}><Download size={15} />导出文稿</button></div></div><textarea className="lf-editor" aria-label="长文配音正文" value={text} onChange={e => changeText(e.target.value)} placeholder="直接粘贴一万字及以上的中文文稿…\n\n系统会按语句分段，逐段生成并保存；中断后可从已验收的片段继续。" spellCheck={false} /><footer><span>{Array.from(text).length.toLocaleString()} 字符</span><span>草稿自动保存在本机</span><span>首版以 1–10 万汉字为验收范围</span></footer></section>
    <aside className="lf-output-panel"><div className="lf-panel-heading"><div><AudioLines size={17} /><strong>分段与输出</strong></div><button className="lf-text-button" onClick={() => void doPreview()} disabled={!!busy || !text.trim()}>{busy === "preview" ? <LoaderCircle size={14} className="spin" /> : <RefreshCw size={14} />}预览分段</button></div><div className="lf-output-scroll"><div className="lf-output-fields"><label className="lf-strategy">分段策略<select value={strategy} onChange={e => { setStrategy(e.target.value as typeof strategy); setPreview(null); }}><option value={engine === "breeze" ? "breeze_han_v1" : "generic_v2"}>按语句与长度</option><option value="paragraph">段落锚定（便于局部修改）</option></select></label><label>每段字数上限<input type="number" min={20} max={engine === "breeze" ? 120 : 1000} value={maxChars} onChange={e => { setMaxChars(Number(e.target.value)); setPreview(null); }} /></label><label>段间停顿<span className="lf-input-unit"><input type="number" min={0} max={10000} step={50} value={output.silenceMs} onChange={e => setOutput(v => ({ ...v, silenceMs: Number(e.target.value) }))} /><span>ms</span></span></label><label>成品格式<select value={output.format} onChange={e => setOutput(v => ({ ...v, format: e.target.value as OutputOptions["format"] }))}>{formats.map(format => <option key={format}>{format}</option>)}{!formats.includes("MP3") && <option disabled value="MP3">MP3（需安装 FFmpeg）</option>}</select></label><label>采样率<select value={output.sampleRate} onChange={e => setOutput(v => ({ ...v, sampleRate: e.target.value === "native" ? "native" : Number(e.target.value) }))}><option value="native">保持引擎原生</option>{[24000, 32000, 44100, 48000].map(n => <option value={n} key={n}>{n} Hz</option>)}</select></label></div><label className="lf-check"><input type="checkbox" checked={output.keepSegments} onChange={e => setOutput(v => ({ ...v, keepSegments: e.target.checked }))} />保留分段音频，便于恢复与局部重做</label>{!output.keepSegments && <p className="lf-warning">成品发布后清理分段，将无法继续复用这些片段。</p>}
    {engine === "breeze" && <div className="lf-license"><ShieldCheck size={17} /><div><strong>Breeze 模型许可</strong><p>模型权重限研究与非商业用途；商业使用需另行获得书面授权。参考音频应具有所需权利。</p><label className="lf-check"><input type="checkbox" checked={licenseAccepted} onChange={e => setLicenseAccepted(e.target.checked)} />我已阅读并确认使用范围</label><button className="lf-text-button" onClick={() => setShowBinding(v => !v)}><Settings2 size={13} />WSL 引擎绑定</button></div></div>}
    {showBinding && engine === "breeze" && <div className="lf-binding">{Object.entries({ sourcePath: "Windows 源码目录", pythonPath: "WSL Python 路径", distribution: "WSL 发行版", modelPath: "模型目录" }).map(([key, label]) => <label key={key}>{label}<input value={binding[key as keyof typeof binding]} onChange={e => setBinding(v => ({ ...v, [key]: e.target.value }))} /></label>)}<button className="secondary-button" onClick={() => void saveBinding()} disabled={!!busy}>保存只读绑定</button><small>仅绑定已有安装，不下载权重或修改安装目录。</small></div>}
    <div className="lf-preflight"><div><ShieldCheck size={15} /><strong>生成前检查</strong><button className="lf-text-button" onClick={() => void checkEnvironment()} disabled={!!busy}>{busy === "check" ? "检查中…" : "检查环境"}</button></div><p>{environment?.available ? "引擎状态就绪" : "运行环境将在提交时核验"}</p>{engine === "breeze" && <p>{refAudio.trim() && refText.trim() ? "已填写参考音频与文字，提交时检查文件" : "请补充参考音频与准确文字"} · {licenseAccepted ? "已确认许可范围" : "尚未确认许可范围"}</p>}<p>段后保存 · 恢复时校验片段 · 成品通过校验后发布</p></div>
    <p className="lf-strategy-help">修改文稿后，请求文字与配置未变的片段可以复用。段落锚定可缩小边界变化范围，超长段落仍会拆分。</p><div className="lf-preview-heading"><strong>{preview ? `${preview.totalSegments} 段 · ${preview.exactCoverage ? "原文覆盖校验通过" : "覆盖校验未通过"}` : "分段预览"}</strong>{preview && <span>{preview.sourceCharacters.toLocaleString()} 字符</span>}</div>{preview ? <><div className="lf-preview-list">{preview.segments.slice(previewPage * 8, previewPage * 8 + 8).map(s => <div key={s.id ?? s.index}><span>{s.index + 1}</span><p>{s.requestText || s.text || "空白片段"}</p>{s.warnings?.length ? <AlertCircle size={14}><title>{s.warnings.join("；")}</title></AlertCircle> : <Check size={14} />}</div>)}</div><div className="lf-pagination"><button disabled={previewPage === 0} onClick={() => setPreviewPage(p => p - 1)} aria-label="上一页预览"><ChevronLeft size={16} /></button><span>{previewPage + 1} / {Math.max(1, Math.ceil(preview.segments.length / 8))}</span><button disabled={(previewPage + 1) * 8 >= preview.segments.length} onClick={() => setPreviewPage(p => p + 1)} aria-label="下一页预览"><ChevronRight size={16} /></button></div></> : <div className="lf-empty"><FileText size={26} /><strong>先查看系统怎样分段</strong><p>预览与正式生成共用同一个分段计划。生成前会检查环境和参考声音。</p><button className="secondary-button" onClick={() => void doPreview()} disabled={!text.trim() || !!busy}>预览分段</button></div>}</div><div className="lf-panel-footer"><span>已完成片段会逐段保存</span><button className="primary-button" disabled={!!busy || !text.trim()} onClick={() => void generate()}><Zap size={15} />生成完整配音</button></div></aside></div>
    <div className="lf-task-strip"><button onClick={() => currentJob && void openJob(currentJob)} disabled={!currentJob}><AudioLines size={16} /><strong>{currentJob ? currentJob.title : "尚未创建长文任务"}</strong>{currentJob && <><span className={`lf-state state-${currentJob.status}`}>{longformStatusLabel(currentJob.phase || currentJob.status)}</span><span>{currentJob.acceptedSegments}/{currentJob.totalSegments} 段</span></>}</button><div><button className="lf-text-button" onClick={() => setDetailOpen(true)}><RefreshCw size={14} />长文任务 {jobs.length}</button>{props.onTasks && <button className="lf-text-button" onClick={props.onTasks}>全部任务<ChevronRight size={14} /></button>}</div></div>
    {redoSegment && <div className="project-confirm-overlay lf-redo-overlay"><section className="project-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="lf-redo-title"><h3 id="lf-redo-title">重做第 {redoSegment.index + 1} 段</h3><p>以新种子生成这一段，其余已验收片段保持复用；完成后重新合并。</p><label className="lf-redo-seed">新种子<input type="number" min={0} max={2147483646} value={redoSegment.seed} onChange={event => setRedoSegment(value => value ? { ...value, seed: Number(event.target.value) } : value)} /></label><div><button className="secondary-button" onClick={() => setRedoSegment(null)}>取消</button><button className="primary-button" disabled={!!busy || selectedJob?.segmentsRetained === false || selectedJob?.output?.keepSegments === false} onClick={() => { void action("regenerate-segment", { segmentIds: [redoSegment.id], seed: redoSegment.seed }); setRedoSegment(null); }}>重做这一段</button></div></section></div>}
    {parametersOpen && engine !== "breeze" && <div className="lf-detail-overlay lf-parameters-overlay"><button className="lf-scrim" aria-label="关闭推理参数" onClick={() => setParametersOpen(false)} /><section className="lf-detail lf-parameter-detail" role="dialog" aria-modal="true" aria-labelledby="lf-parameters-title"><header><div><p className="eyebrow">{longformEngines[engine].name}</p><h2 id="lf-parameters-title">推理参数</h2></div><button className="icon-button" aria-label="关闭推理参数" onClick={() => setParametersOpen(false)}><X size={18} /></button></header><div className="lf-parameter-tabs"><button aria-pressed={parameterTab === "common"} onClick={() => setParameterTab("common")}>常用</button><button aria-pressed={parameterTab === "all"} onClick={() => setParameterTab("all")}>全部原生参数</button></div><div className="lf-detail-scroll">{parameterGroups[engine].filter(group => group.title !== "长音频与输出").filter((_, index) => parameterTab === "all" || index === 0).map(group => <section className="lf-parameter-group" key={group.title}><h3>{group.title}</h3><p>{group.summary}</p>{group.fields.map(field => <FieldControl key={field.key} field={field} value={legacyValues[field.key]} onChange={value => { setLegacyOverrides(current => ({ ...current, [engine]: { ...current[engine], [field.key]: value } })); setVoiceId(""); }} />)}</section>)}{parameterTab === "common" && <button className="secondary-button" onClick={() => setParameterTab("all")}>查看全部推理参数</button>}</div><div className="lf-panel-footer"><span>参数仅用于 {longformEngines[engine].name}</span><button className="primary-button" onClick={() => setParametersOpen(false)}><Check size={15} />完成设置</button></div></section></div>}
    {detailOpen && <div className="lf-detail-overlay"><button className="lf-scrim" aria-label="关闭长文任务" onClick={() => setDetailOpen(false)} /><section className="lf-detail" role="dialog" aria-modal="true" aria-labelledby="lf-detail-title"><header><div><p className="eyebrow">任务与恢复</p><h2 id="lf-detail-title">{selectedJob?.title || "长文任务"}</h2></div><button className="icon-button" aria-label="关闭长文任务" onClick={() => setDetailOpen(false)}><X size={18} /></button></header><div className="lf-detail-scroll">{(error || feedback) && <div className={`lf-message ${error ? "error" : "success"}`} role={error ? "alert" : "status"}><AlertCircle size={16} /><span>{error || feedback}</span><button className="icon-button" aria-label="关闭任务操作提示" onClick={() => { setError(""); setFeedback(""); }}><X size={14} /></button></div>}<div className="lf-job-chooser"><label>选择任务<select value={selectedJob?.id ?? ""} onChange={e => { const job = jobs.find(j => j.id === e.target.value); if (job) void openJob(job); }}><option value="">选择要查看的任务</option>{jobs.map(j => <option value={j.id} key={j.id}>{longformStatusLabel(j.status)} · {j.title}</option>)}</select></label><button className="secondary-button" onClick={() => void refreshJobs()}><RefreshCw size={15} />刷新</button></div>{selectedJob ? <><div className="lf-phase-steps">{["generating", "merging", "exporting", "verifying", "completed"].map(phase => <span className={selectedJob.phase === phase || selectedJob.status === phase ? "current" : ""} key={phase}>{longformStatusLabel(phase)}</span>)}</div><div className="lf-detail-progress"><div><strong>{longformStatusLabel(selectedJob.status)}</strong><span>已验收 {selectedJob.acceptedSegments}/{selectedJob.totalSegments} 段 · 正文 {Math.round(Math.max(0, Math.min(1, selectedJob.progress)) * 100)}%</span></div><progress value={selectedJob.acceptedSegments} max={Math.max(1, selectedJob.totalSegments)} /></div>{selectedJob.error && <p className="lf-message error">{selectedJob.error}</p>}{(selectedJob.cleanupWarning || selectedJob.segmentsCleanupPending) && <p className="lf-warning" role="status">{selectedJob.cleanupWarning || "完整成品已保存，分段正在清理或暂缓清理；此任务不支持重新导出与局部重做。"}</p>}<div className="lf-time-grid">{[["总等待", selectedJob.timing?.totalWallSeconds], ["有效生成", selectedJob.timing?.generationSeconds], ["用户暂停", selectedJob.timing?.pauseSeconds], ["未知间隔", selectedJob.timing?.unknownSeconds]].map(([label, value]) => <div key={String(label)}><span>{String(label)}</span><strong>{formatDuration(value as number | null | undefined)}</strong></div>)}</div>{selectedJob.timing?.clockAnomaly && <p className="lf-warning">检测到时钟或休眠变化，总等待时间仅供参考。</p>}{selectedJob.timing?.etaLowSeconds != null && <p className="lf-eta">预计正文剩余 {formatDuration(selectedJob.timing.etaLowSeconds)}–{formatDuration(selectedJob.timing.etaHighSeconds)} · 估算</p>}<div className="lf-controls">{active.includes(selectedJob.status) && <><button className="secondary-button" disabled={!!busy} onClick={() => void action("pause")}><Pause size={15} />段后暂停</button><button className="secondary-button" disabled={!!busy} onClick={() => void action("stop", { mode: "now" })}><Square size={14} />立即停止</button><button className="lf-text-button" disabled={!!busy} onClick={() => void action("cancel")}>取消任务</button></>}{resumable.includes(selectedJob.status) && <button className="primary-button" disabled={!!busy} onClick={() => void action("resume")}><Play size={15} />继续剩余 {Math.max(0, selectedJob.totalSegments - selectedJob.acceptedSegments)} 段</button>}{selectedJob.status === "failed" && <button className="secondary-button" disabled={!!busy} onClick={() => void action("retry-segments")}><RefreshCw size={15} />重试失败段</button>}{selectedJob.status === "completed" && <><a className="primary-button" href={serviceUrl(`/api/jobs/v2/${selectedJob.id}/output`)} download><Download size={15} />下载完整配音</a><button className="secondary-button" disabled={!!busy || selectedJob.segmentsRetained === false || selectedJob.output?.keepSegments === false} title={selectedJob.output?.keepSegments === false ? "此任务设置为完成后清理分段，无法重新导出" : selectedJob.segmentsRetained === false ? "此任务完成后已清理分段，无法重新导出" : "只重新合并导出，不重新配音"} onClick={() => void action("reexport", { output })}>按当前输出设置重新导出</button></>}</div>{selectedJob.status === "completed" && <><audio className="lf-full-audio" controls src={serviceUrl(`/api/jobs/v2/${selectedJob.id}/output`)} /><p className="lf-warning">AI 合成音频 · 文件校验不等于逐字核听。</p></>}<div className="lf-segment-table"><div className="lf-segment-table-title"><strong>分段详情</strong><span>仅加载当前页 {pageSize} 段</span></div>{segments.map(s => <div className="lf-segment-row" key={s.id}><span>{s.index + 1}</span><div><p>{s.requestText || s.text}</p><small>{longformStatusLabel(s.status)} · 尝试 {s.attempts} 次 · {formatDuration(s.durationSeconds)}{s.error ? ` · ${s.error}` : ""}</small>{s.outputPath && selectedJob.segmentsRetained !== false && <audio controls preload="none" src={serviceUrl(`/api/jobs/v2/${selectedJob.id}/segments/${encodeURIComponent(s.id)}/audio`)} />}</div>{s.status === "accepted" && selectedJob.segmentsRetained !== false && selectedJob.output?.keepSegments !== false && !active.includes(selectedJob.status) && !["merging", "exporting", "verifying"].includes(selectedJob.status) ? <button className="lf-text-button" onClick={() => setRedoSegment({ id: s.id, index: s.index, seed: crypto.getRandomValues(new Uint32Array(1))[0] % 2147483647 })}>换种子重做</button> : s.status === "accepted" ? <Check size={15} /> : null}</div>)}</div><div className="lf-pagination"><button disabled={segmentPage === 0} onClick={() => setSegmentPage(p => p - 1)} aria-label="上一页分段"><ChevronLeft size={16} /></button><span>{segmentPage + 1} / {Math.max(1, Math.ceil(segmentTotal / pageSize))}</span><button disabled={(segmentPage + 1) * pageSize >= segmentTotal} onClick={() => setSegmentPage(p => p + 1)} aria-label="下一页分段"><ChevronRight size={16} /></button></div><details className="lf-technical"><summary>事件日志（最近 40 条）</summary><pre>{events.map(event => `${event.utc ?? event.createdAt ?? ""} ${event.kind ?? event.type ?? event.event ?? "事件"} ${JSON.stringify(event.payload ?? event.data ?? "")}`).join("\n") || "暂无事件记录"}</pre></details><details className="lf-technical"><summary>任务配置与记录</summary><dl><dt>任务 ID</dt><dd>{selectedJob.id}</dd><dt>引擎</dt><dd>{longformEngines[selectedJob.engine]?.name ?? selectedJob.engine}</dd><dt>更新</dt><dd>{selectedJob.updatedAt}</dd></dl><pre>{JSON.stringify(selectedJob.params, null, 2)}</pre></details></> : <div className="lf-empty"><AudioLines size={32} /><strong>没有选中的任务</strong><p>在上方选择历史任务，或先生成一份配音。</p></div>}</div></section></div>}
  </section>;
}
