import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity, AlertCircle, AudioLines, Check, ChevronDown, ChevronRight,
  Clock3, Cpu, FileAudio, FileText, FolderOpen, History, Info,
  ListMusic, LoaderCircle, Menu, Pause, Play, Plus, RefreshCw, RotateCcw,
  Save, Search, Settings, Sparkles, Upload, WandSparkles,
  X, Zap,
} from "lucide-react";
import { defaultsFor, engines, parameterGroups, type EngineId, type Field } from "./parameterSchemas";
import { EngineManager } from "./EngineManager";
import { WorkspacePage } from "./WorkspacePages";
import { Onboarding } from "./Onboarding";
import { ProjectLibrary, type ProjectRecord } from "./ProjectLibrary";

type Job = { id: string; title: string; engine: EngineId; progress: number; status: "running" | "queued" | "failed" | "cancelled" | "done"; segments: string; duration?: string };
type ApiEngineStatus = { id?: string; name?: string; state?: string; available?: boolean };
type AppSettings = { defaultEngine?: string; autoRevealOutput?: boolean };

declare global {
  interface Window {
    langbaiDesktop?: {
      chooseFile: (options?: { filters?: Array<{ name: string; extensions: string[] }> }) => Promise<string | null>;
      chooseDirectory?: () => Promise<string | null>;
      showItemInFolder?: (targetPath: string) => Promise<unknown>;
      readTextFile?: () => Promise<string | { content?: string; text?: string; path?: string } | null>;
      getAudioUrl?: (targetPath: string) => Promise<string>;
      exportAudio?: (targetPath: string) => Promise<{ path: string; name: string } | null>;
      setZoomFactor?: (factor: number) => Promise<unknown>;
      checkForUpdates?: (channel?: "stable" | "beta") => Promise<unknown>;
      downloadUpdate?: () => Promise<unknown>;
      installUpdate?: () => Promise<unknown>;
      onCommand?: (callback: (command: string) => void) => (() => void) | void;
      onUpdateEvent?: (callback: (event: unknown) => void) => (() => void) | void;
    };
  }
}

const initialJobs: Job[] = [];
const apiBase = new URLSearchParams(window.location.search).get("backendUrl") ?? "";
const apiUrl = (path: string) => `${apiBase}${path}`;

function FieldControl({ field, value, onChange }: { field: Field; value: unknown; onChange: (value: unknown) => void }) {
  const id = `field-${field.key}`;
  if (field.type === "toggle") return <div className="field-row field-toggle"><div><label htmlFor={id}>{field.label}</label><p>{field.help}</p></div><button id={id} type="button" className={`switch ${value ? "is-on" : ""}`} role="switch" aria-checked={Boolean(value)} onClick={() => onChange(!value)}><span /></button></div>;
  if (field.type === "range") return <div className="field-row"><div className="field-label"><label htmlFor={id}>{field.label}</label><output>{String(value)}{field.unit ? ` ${field.unit}` : ""}</output></div><input id={id} className="range" type="range" min={field.min} max={field.max} step={field.step} value={Number(value)} onChange={e => onChange(Number(e.target.value))} /><p>{field.help}</p></div>;
  if (field.type === "select") return <div className="field-row"><label htmlFor={id}>{field.label}</label><div className="select-wrap"><select id={id} value={String(value)} onChange={e => onChange(e.target.value)}>{field.options?.map(option => <option key={option}>{option}</option>)}</select><ChevronDown size={15} /></div><p>{field.help}</p></div>;
  if (field.type === "file") return <div className="field-row"><label htmlFor={id}>{field.label}</label><button id={id} type="button" className="file-picker" onClick={async () => { const selected = await window.langbaiDesktop?.chooseFile({ filters: [{ name: "音频文件", extensions: ["wav", "mp3", "flac", "ogg", "m4a"] }] }); if (selected) onChange(selected); }}><FolderOpen size={15} /><span>{value ? String(value) : "选择本地音频…"}</span></button><p>{field.help}</p></div>;
  if (field.type === "textarea") return <div className="field-row"><label htmlFor={id}>{field.label}</label><textarea id={id} className="small-textarea" value={String(value)} onChange={e => onChange(e.target.value)} /><p>{field.help}</p></div>;
  return <div className="field-row"><label htmlFor={id}>{field.label}</label><div className="number-wrap"><input id={id} type={field.type === "number" ? "number" : "text"} min={field.min} max={field.max} step={field.step} value={String(value)} onChange={e => onChange(field.type === "number" ? Number(e.target.value) : e.target.value)} />{field.unit && <span>{field.unit}</span>}</div><p>{field.help}</p></div>;
}

function AppLogo() { return <div className="app-logo"><img src="./icon.png" alt="" /><div><strong>langbai</strong><span>TTS Studio</span></div></div>; }

function normalizeJob(raw: Record<string, unknown>): Job {
  const engine = (["indextts2", "voxcpm", "gpt_sovits"].includes(String(raw.engine)) ? raw.engine : "indextts2") as EngineId;
  const statusValue = String(raw.status ?? "queued");
  return {
    id: String(raw.id ?? crypto.randomUUID()), title: String(raw.title ?? "未命名任务"), engine,
    progress: Math.round(Number(raw.progress ?? 0) * 100), status: statusValue === "completed" ? "done" : (["running", "queued", "failed", "cancelled", "done"].includes(statusValue) ? statusValue : "queued") as Job["status"],
    segments: Array.isArray(raw.segments) ? `${raw.segments.filter(segment => typeof segment === "object" && segment && (segment as { status?: string }).status === "completed").length} / ${raw.segments.length} 段` : String(raw.segment_progress ?? "等待中"), duration: raw.duration ? String(raw.duration) : undefined,
  };
}
function statusValueLabel(status: Job["status"]) { return status === "running" ? "生成中" : status === "queued" ? "排队" : status === "done" ? "已完成" : status === "cancelled" ? "已取消" : "失败"; }

const longAudioKeys = new Set(["split_mode", "segment_chars", "segment_pause", "retry_count", "resume", "keep_segments", "output_format", "sample_rate"]);
const languageCodes: Record<string, string> = { 中文: "zh", 英文: "en", 日文: "ja", 韩文: "ko", 粤语: "yue", 中英混合: "auto", 日英混合: "auto", 多语种混合: "auto" };
const languageLabels: Record<string, string> = { zh: "中文", en: "英文", ja: "日文", ko: "韩文", yue: "粤语", auto: "多语种混合" };
const validEngineIds: EngineId[] = ["indextts2", "voxcpm", "gpt_sovits"];
const isEngineId = (value: unknown): value is EngineId => validEngineIds.includes(String(value) as EngineId);

function fromProjectParams(engine: EngineId, saved: Record<string, unknown>, longAudio: Record<string, unknown>) {
  const restored: Record<string, unknown> = { ...defaultsFor(engine), ...saved };
  if (engine === "indextts2") {
    if (saved.speaker_audio !== undefined) restored.spk_audio_prompt = saved.speaker_audio;
    if (saved.emotion_audio !== undefined) restored.emo_audio_prompt = saved.emotion_audio ?? "";
    if (saved.emotion_alpha !== undefined) restored.emo_alpha = saved.emotion_alpha;
    if (saved.emotion_text !== undefined) restored.emo_text = saved.emotion_text ?? "";
    if (saved.emotion_mode !== undefined) {
      const mode = String(saved.emotion_mode);
      restored.emo_control = mode === "vector" ? "情感向量" : mode === "text" ? "情感描述文本" : saved.emotion_audio ? "情感参考音频" : "与音色参考一致";
      restored.use_emo_text = mode === "text";
    }
    if (Array.isArray(saved.emotion_vector)) saved.emotion_vector.slice(0, 8).forEach((value, index) => { restored[`emo_${index}`] = value; });
  } else if (engine === "voxcpm") {
    if (saved.control !== undefined) restored.voice_instruction = saved.control ?? "";
    if (saved.reference_audio !== undefined) restored.reference_wav_path = saved.reference_audio ?? "";
    if (saved.prompt_audio !== undefined) restored.prompt_wav_path = saved.prompt_audio ?? "";
  } else {
    if (saved.reference_audio !== undefined) restored.ref_audio_path = saved.reference_audio ?? "";
    if (saved.aux_reference_audios !== undefined) restored.aux_ref_audio_paths = Array.isArray(saved.aux_reference_audios) ? saved.aux_reference_audios[0] ?? "" : saved.aux_reference_audios ?? "";
    if (saved.prompt_language !== undefined) restored.prompt_lang = languageLabels[String(saved.prompt_language)] ?? saved.prompt_language;
    if (saved.text_language !== undefined) restored.text_lang = languageLabels[String(saved.text_language)] ?? saved.text_language;
    if (saved.text_split_method !== undefined) {
      const prefix = String(saved.text_split_method);
      restored.text_split_method = parameterGroups.gpt_sovits.flatMap(group => group.fields).find(field => field.key === "text_split_method")?.options?.find(option => option.startsWith(prefix)) ?? saved.text_split_method;
    }
    if (saved.streaming_mode !== undefined) {
      const prefix = String(saved.streaming_mode);
      restored.streaming_mode = parameterGroups.gpt_sovits.flatMap(group => group.fields).find(field => field.key === "streaming_mode")?.options?.find(option => option.startsWith(prefix)) ?? saved.streaming_mode;
    }
  }
  if (saved.segment_chars === undefined && longAudio.maxChars !== undefined) restored.segment_chars = longAudio.maxChars;
  if (saved.segment_pause === undefined && longAudio.silenceMs !== undefined) restored.segment_pause = longAudio.silenceMs;
  if (saved.retry_count === undefined && longAudio.maxRetries !== undefined) restored.retry_count = longAudio.maxRetries;
  if (saved.keep_segments === undefined && longAudio.keepSegments !== undefined) restored.keep_segments = longAudio.keepSegments;
  if (saved.sample_rate === undefined && longAudio.targetSampleRate !== undefined) {
    const sampleRate = Number(longAudio.targetSampleRate);
    if (Number.isFinite(sampleRate)) restored.sample_rate = `${sampleRate} Hz`;
  }
  return restored;
}

function toApiParams(engine: EngineId, values: Record<string, unknown>) {
  const clean = Object.fromEntries(Object.entries(values).filter(([key]) => !longAudioKeys.has(key)));
  if (engine === "indextts2") {
    const modeMap: Record<string, string> = { "与音色参考一致": "audio", "情感参考音频": "audio", "情感向量": "vector", "情感描述文本": "text" };
    return {
      speaker_audio: clean.spk_audio_prompt, emotion_mode: modeMap[String(clean.emo_control)] ?? "audio",
      emotion_audio: clean.emo_audio_prompt || null, emotion_alpha: clean.emo_alpha,
      emotion_text: clean.emo_text || null, emotion_vector: Array.from({ length: 8 }, (_, i) => Number(clean[`emo_${i}`] ?? 0)),
      use_random: clean.use_random, interval_silence: clean.interval_silence,
      max_text_tokens_per_segment: clean.max_text_tokens_per_segment, stream_return: clean.stream_return,
      do_sample: clean.do_sample, top_p: clean.top_p, top_k: clean.top_k, temperature: clean.temperature,
      length_penalty: clean.length_penalty, num_beams: clean.num_beams, repetition_penalty: clean.repetition_penalty, max_mel_tokens: clean.max_mel_tokens,
    };
  }
  if (engine === "voxcpm") {
    const { mode: _mode, voice_instruction, reference_wav_path, prompt_wav_path, ...rest } = clean;
    return { ...rest, control: voice_instruction || null, reference_audio: reference_wav_path || null, prompt_audio: prompt_wav_path || null };
  }
  const { ref_audio_path, aux_ref_audio_paths, prompt_lang, text_lang, ...rest } = clean;
  return {
    ...rest, reference_audio: ref_audio_path, aux_reference_audios: aux_ref_audio_paths ? [aux_ref_audio_paths] : null,
    prompt_language: languageCodes[String(prompt_lang)] ?? "auto", text_language: languageCodes[String(text_lang)] ?? "auto",
    text_split_method: String(rest.text_split_method).split("｜")[0], streaming_mode: Number(String(rest.streaming_mode).split("｜")[0]),
  };
}

export function App() {
  const [engine, setEngine] = useState<EngineId>("indextts2");
  const [params, setParams] = useState<Record<EngineId, Record<string, unknown>>>(() => ({ indextts2: defaultsFor("indextts2"), voxcpm: defaultsFor("voxcpm"), gpt_sovits: defaultsFor("gpt_sovits") }));
  const [text, setText] = useState("声音不只是信息的载体，它也承载情绪、节奏与想象。\n\n在 langbai TTS Studio 中，你可以为每个任务选择最适合的本地语音引擎，细致调整音色与表达，并将长篇文本稳定地转换成完整音频。");
  const [groupsOpen, setGroupsOpen] = useState<Record<string, boolean>>({ "音色与情感": true, "音色模式": true, "参考与语言": true });
  const [jobs, setJobs] = useState<Job[]>(initialJobs);
  const [engineStatus, setEngineStatus] = useState<Record<EngineId, boolean | null>>({ indextts2: null, voxcpm: null, gpt_sovits: null });
  const [connected, setConnected] = useState<boolean | null>(null);
  const [notice, setNotice] = useState("");
  const [generating, setGenerating] = useState(false);
  const [search, setSearch] = useState("");
  const [queueOpen, setQueueOpen] = useState(true);
  const [sideOpen, setSideOpen] = useState(false);
  const [activeNav, setActiveNav] = useState("创作台");
  const [density, setDensity] = useState<"comfortable" | "compact">(() => localStorage.getItem("langbai-density") === "compact" ? "compact" : "comfortable");
  const [showOnboarding, setShowOnboarding] = useState(() => localStorage.getItem("langbai-onboarding-complete") !== "1");
  const [projectId, setProjectId] = useState<string | null>(null);
  const [projectName, setProjectName] = useState("未命名语音项目");
  const [projectDescription, setProjectDescription] = useState("");
  const [projectLibraryOpen, setProjectLibraryOpen] = useState(false);
  const [confirmNewProject, setConfirmNewProject] = useState(false);
  const [savingProject, setSavingProject] = useState(false);
  const autoRevealOutputRef = useRef(false);
  const previousJobStatusRef = useRef(new Map<string, string>());
  const revealedJobIdsRef = useRef(new Set<string>());
  const refreshInFlightRef = useRef(false);
  const currentParams = params[engine];
  const sentenceCount = useMemo(() => text.split(/[。！？\n]+/).filter(Boolean).length, [text]);
  const visibleGroups = useMemo(() => parameterGroups[engine].map(group => ({ ...group, fields: group.fields.filter(field => !search || `${field.label}${field.help}${field.key}`.toLowerCase().includes(search.toLowerCase())) })).filter(group => !search || group.fields.length), [engine, search]);

  const revealCompletedOutput = async (jobId: string) => {
    if (!window.langbaiDesktop?.showItemInFolder) return;
    try {
      const response = await fetch(apiUrl(`/api/jobs/${jobId}/output`));
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json() as { output?: { path?: string }; openContract?: { reveal?: { path?: string } } };
      const targetPath = payload.openContract?.reveal?.path ?? payload.output?.path;
      if (!targetPath) throw new Error("输出文件路径为空");
      await window.langbaiDesktop.showItemInFolder(targetPath);
    } catch (reason) {
      setNotice(`音频已完成，但无法定位输出文件：${reason instanceof Error ? reason.message : "未知错误"}`);
    }
  };

  const trackCompletedJobs = (items: Array<Record<string, unknown>>) => {
    const previous = previousJobStatusRef.current;
    const current = new Map<string, string>();
    for (const item of items) {
      const id = String(item.id ?? "");
      const status = String(item.status ?? "queued");
      if (!id) continue;
      current.set(id, status);
      const previousStatus = previous.get(id);
      if (autoRevealOutputRef.current && status === "completed" && previousStatus && previousStatus !== "completed" && !revealedJobIdsRef.current.has(id)) {
        revealedJobIdsRef.current.add(id);
        void revealCompletedOutput(id);
      }
    }
    previousJobStatusRef.current = current;
  };

  const refreshApi = async () => {
    if (refreshInFlightRef.current) return false;
    refreshInFlightRef.current = true;
    try {
      const [engineRes, jobsRes] = await Promise.all([fetch(apiUrl("/api/engines/status")), fetch(apiUrl("/api/jobs"))]);
      if (!engineRes.ok) throw new Error(`引擎状态 ${engineRes.status}`);
      const raw = await engineRes.json();
      const rows: ApiEngineStatus[] = Array.isArray(raw) ? raw : Array.isArray(raw.engines) ? raw.engines : Object.entries(raw).map(([id, value]) => ({ id, ...(typeof value === "object" && value ? value : { ready: Boolean(value) }) }));
      const nextEngineStatus = { indextts2: Boolean(rows.find(r => r.id === "indextts2")?.available), voxcpm: Boolean(rows.find(r => r.id === "voxcpm")?.available), gpt_sovits: Boolean(rows.find(r => r.id === "gpt_sovits")?.available) };
      setEngineStatus(current => current.indextts2 === nextEngineStatus.indextts2 && current.voxcpm === nextEngineStatus.voxcpm && current.gpt_sovits === nextEngineStatus.gpt_sovits ? current : nextEngineStatus);
      setConnected(current => current === true ? current : true);
      if (jobsRes.ok) {
        const jobRaw = await jobsRes.json();
        const items = (Array.isArray(jobRaw) ? jobRaw : jobRaw.jobs) as Array<Record<string, unknown>>;
        if (Array.isArray(items)) {
          trackCompletedJobs(items);
          const normalized = items.map(normalizeJob);
          setJobs(current => JSON.stringify(current) === JSON.stringify(normalized) ? current : normalized);
          return normalized.some(job => ["queued", "running"].includes(job.status));
        }
      }
      return false;
    } catch {
      setConnected(current => current === false ? current : false);
      setEngineStatus(current => current.indextts2 === false && current.voxcpm === false && current.gpt_sovits === false ? current : { indextts2: false, voxcpm: false, gpt_sovits: false });
      return false;
    } finally {
      refreshInFlightRef.current = false;
    }
  };
  useEffect(() => {
    let disposed = false;
    let timer = 0;
    const schedule = (active: boolean) => {
      if (disposed) return;
      const delay = document.hidden ? 60000 : active ? 3000 : 20000;
      timer = window.setTimeout(poll, delay);
    };
    const poll = async () => schedule(await refreshApi());
    const onVisibilityChange = () => {
      if (document.hidden || disposed) return;
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(poll, 0);
    };
    const initialize = async () => {
      try {
        const response = await fetch(apiUrl("/api/settings"));
        if (response.ok) {
          const settings = await response.json() as AppSettings;
          if (!disposed && isEngineId(settings.defaultEngine)) setEngine(settings.defaultEngine);
          autoRevealOutputRef.current = Boolean(settings.autoRevealOutput);
        }
      } catch { /* settings are optional while the local service is starting */ }
      if (disposed) return;
      schedule(await refreshApi());
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    void initialize();
    return () => { disposed = true; document.removeEventListener("visibilitychange", onVisibilityChange); if (timer) window.clearTimeout(timer); };
  }, []);

  const submit = async () => {
    if (!text.trim()) { setNotice("请先输入需要生成的文本。"); return; }
    if (engineStatus[engine] !== true) { setNotice(`${engines[engine].name} 尚未就绪。请先在“设置与路径”中检查或绑定本地引擎。`); return; }
    const focusRequiredField = (key: string, group: string, message: string) => {
      setGroupsOpen(current => ({ ...current, [group]: true }));
      setSearch("");
      setNotice(message);
      window.requestAnimationFrame(() => {
        const field = document.getElementById(`field-${key}`);
        field?.scrollIntoView({ block: "center", behavior: "smooth" });
        field?.focus();
      });
    };
    if (engine === "indextts2" && !String(currentParams.spk_audio_prompt ?? "").trim()) {
      focusRequiredField("spk_audio_prompt", "音色与情感", "生成前请先选择 IndexTTS2 的音色参考音频。");
      return;
    }
    if (engine === "indextts2" && currentParams.emo_control === "情感参考音频" && !String(currentParams.emo_audio_prompt ?? "").trim()) {
      focusRequiredField("emo_audio_prompt", "音色与情感", "当前选择了“情感参考音频”，请先添加对应音频，或更换情感控制方式。");
      return;
    }
    if (engine === "voxcpm" && ["可控音色克隆", "极致克隆"].includes(String(currentParams.mode ?? "")) && !String(currentParams.reference_wav_path ?? "").trim()) {
      focusRequiredField("reference_wav_path", "音色模式", "当前克隆模式需要音色参考音频；也可切换为“音色设计”或“普通合成”。");
      return;
    }
    if (engine === "voxcpm" && Boolean(String(currentParams.prompt_wav_path ?? "").trim()) !== Boolean(String(currentParams.prompt_text ?? "").trim())) {
      focusRequiredField(String(currentParams.prompt_wav_path ?? "").trim() ? "prompt_text" : "prompt_wav_path", "音色模式", "续写提示音频与精确转写必须成对填写。");
      return;
    }
    if (engine === "gpt_sovits" && !String(currentParams.ref_audio_path ?? "").trim()) {
      focusRequiredField("ref_audio_path", "参考与语言", "生成前请先选择 GPT-SoVITS 的主参考音频。");
      return;
    }
    setGenerating(true); setNotice("");
    let reachedBackend = false;
    try {
      const sampleRate = Number(String(currentParams.sample_rate ?? "44100").replace(/\D/g, "")) || 44100;
      const longAudio = { maxChars: Number(currentParams.segment_chars ?? 180), maxRetries: Number(currentParams.retry_count ?? 2), keepSegments: Boolean(currentParams.keep_segments), targetSampleRate: sampleRate, silenceMs: Number(currentParams.segment_pause ?? 280) };
      const response = await fetch(apiUrl("/api/jobs"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: projectName.trim() || "未命名语音项目", engine, text, params: toApiParams(engine, currentParams), longAudio }) });
      reachedBackend = true;
      if (!response.ok) {
        const details = await response.json().catch(() => null) as { detail?: string } | null;
        throw new Error(details?.detail || `后端拒绝了任务（HTTP ${response.status}）`);
      }
      const created = normalizeJob(await response.json());
      setJobs(prev => [created, ...prev]); setQueueOpen(true); setNotice("任务已加入生成队列。");
    } catch (reason) {
      if (reachedBackend) {
        setConnected(true);
        setNotice(`任务未提交：${reason instanceof Error ? reason.message : "请检查参数后重试。"}`);
      } else {
        setConnected(false);
        setNotice("后端服务尚未连接，参数与文本已保留。启动服务后可直接重试。");
      }
    }
    finally { setGenerating(false); }
  };
  const retryJob = async (id: string) => { try { await fetch(apiUrl(`/api/jobs/${id}/retry`), { method: "POST" }); await refreshApi(); } catch { setNotice("重试失败：后端服务未连接。"); } };
  const cancelJob = async (id: string) => { try { await fetch(apiUrl(`/api/jobs/${id}/cancel`), { method: "POST" }); await refreshApi(); } catch { setNotice("取消失败：后端服务未连接。"); } };
  const saveProject = async () => {
    if (savingProject) return;
    const sampleRate = Number(String(currentParams.sample_rate ?? "44100").replace(/\D/g, "")) || 44100;
    const payload = { name: projectName.trim() || "未命名语音项目", description: projectDescription, engine, text, params: currentParams, longAudio: { maxChars: Number(currentParams.segment_chars ?? 180), maxRetries: Number(currentParams.retry_count ?? 2), keepSegments: Boolean(currentParams.keep_segments), targetSampleRate: sampleRate, silenceMs: Number(currentParams.segment_pause ?? 280) } };
    setSavingProject(true);
    try {
      const response = await fetch(apiUrl(projectId ? `/api/projects/${projectId}` : "/api/projects"), { method: projectId ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (!response.ok) {
        const details = await response.json().catch(() => null) as { detail?: string } | null;
        throw new Error(details?.detail || `HTTP ${response.status}`);
      }
      const saved = await response.json() as ProjectRecord;
      if (saved.id) setProjectId(saved.id);
      if (saved.name) setProjectName(saved.name);
      setProjectDescription(saved.description ?? projectDescription);
      setNotice(projectId ? "项目更改已保存。" : "项目已保存到本地工作区。");
    } catch (reason) { setNotice(`保存失败：${reason instanceof Error ? reason.message : "后端服务不可用"}`); }
    finally { setSavingProject(false); }
  };
  const clearProject = (message = "已创建空白项目。") => {
    setProjectId(null);
    setProjectName("未命名语音项目");
    setProjectDescription("");
    setText("");
    setParams({ indextts2: defaultsFor("indextts2"), voxcpm: defaultsFor("voxcpm"), gpt_sovits: defaultsFor("gpt_sovits") });
    setActiveNav("创作台");
    setNotice(message);
  };
  const newProject = () => { clearProject(); setConfirmNewProject(false); setProjectLibraryOpen(false); };
  const requestNewProject = () => setConfirmNewProject(true);
  const openProject = async (selectedProjectId: string) => {
    const response = await fetch(apiUrl(`/api/projects/${selectedProjectId}`));
    if (!response.ok) {
      const details = await response.json().catch(() => null) as { detail?: string } | null;
      throw new Error(details?.detail || `打开项目失败（HTTP ${response.status}）`);
    }
    const project = await response.json() as ProjectRecord;
    if (!project.id || !isEngineId(project.engine) || !project.params || typeof project.params !== "object") throw new Error("项目数据不完整，无法安全恢复。");
    const restored = fromProjectParams(project.engine, project.params, project.longAudio && typeof project.longAudio === "object" ? project.longAudio : {});
    setProjectId(project.id);
    setProjectName(project.name || "未命名语音项目");
    setProjectDescription(project.description ?? "");
    setText(project.text ?? "");
    setEngine(project.engine);
    setParams({
      indextts2: project.engine === "indextts2" ? restored : defaultsFor("indextts2"),
      voxcpm: project.engine === "voxcpm" ? restored : defaultsFor("voxcpm"),
      gpt_sovits: project.engine === "gpt_sovits" ? restored : defaultsFor("gpt_sovits"),
    });
    setGroupsOpen({ [parameterGroups[project.engine][0].title]: true, "长音频与输出": true });
    setActiveNav("创作台");
    setProjectLibraryOpen(false);
    setNotice(`已打开项目“${project.name}”，正文、引擎和参数均已恢复。`);
  };
  const importText = async () => { const result = await window.langbaiDesktop?.readTextFile?.(); if (!result) return; const content = typeof result === "string" ? result : result.content ?? result.text ?? ""; if (content) { setText(content); setNotice("文本已导入。"); } };
  useEffect(() => { const unsubscribe = window.langbaiDesktop?.onCommand?.(command => { if (command === "save-project") void saveProject(); else if (command === "new-project") requestNewProject(); else if (command === "open-settings") setActiveNav("settings"); else if (command === "generate") void submit(); }); return () => { if (typeof unsubscribe === "function") unsubscribe(); }; });

  const changeDensity = () => setDensity(current => { const next = current === "comfortable" ? "compact" : "comfortable"; localStorage.setItem("langbai-density", next); return next; });
  const finishOnboarding = () => { localStorage.setItem("langbai-onboarding-complete", "1"); setShowOnboarding(false); };

  return <div className={`app-shell density-${density}`}>
    <aside className={`sidebar ${sideOpen ? "is-open" : ""}`}>
      <div className="sidebar-head"><AppLogo /><button className="icon-button sidebar-close" onClick={() => setSideOpen(false)} aria-label="关闭导航"><X size={18} /></button></div>
      <nav>{[{ name: "创作台", icon: WandSparkles }, { name: "任务队列", icon: ListMusic }, { name: "音频库", icon: FileAudio }, { name: "历史记录", icon: History }].map(item => <button key={item.name} className={activeNav === item.name ? "active" : ""} onClick={() => setActiveNav(item.name)}><item.icon size={18} /><span>{item.name}</span>{item.name === "任务队列" && <b>{jobs.filter(j => ["running", "queued"].includes(j.status)).length}</b>}</button>)}</nav>
      <div className="sidebar-spacer" />
      <div className="engine-health"><div className="health-title"><Cpu size={16} /><span>本地引擎</span><button onClick={refreshApi} aria-label="刷新状态"><RefreshCw size={14} /></button></div>{(Object.keys(engines) as EngineId[]).map(id => <div className="health-row" key={id}><i className={engineStatus[id] ? "online" : ""} /><span>{engines[id].name}</span><small>{engineStatus[id] === null ? "检测中" : engineStatus[id] ? "就绪" : "待连接"}</small></div>)}</div>
      <button className="density-toggle" onClick={changeDensity}><span>{density === "comfortable" ? "舒适密度" : "紧凑密度"}</span><b>{density === "comfortable" ? "A" : "A−"}</b></button>
      <button className={`sidebar-settings ${activeNav === "settings" ? "active" : ""}`} onClick={() => setActiveNav("settings")}><Settings size={17} /><span>设置与路径</span></button>
    </aside>

    <main className="workspace">
      {activeNav === "settings" ? <EngineManager onBack={() => setActiveNav("创作台")} density={density} onDensityChange={changeDensity} /> : activeNav !== "创作台" ? <WorkspacePage kind={activeNav === "任务队列" ? "queue" : activeNav === "音频库" ? "library" : "history"} onCreate={() => setActiveNav("创作台")} /> : <>
      <header className="topbar"><div className="title-row"><button className="icon-button mobile-menu" onClick={() => setSideOpen(true)} aria-label="打开导航"><Menu size={19} /></button><div><p className="eyebrow">语音创作工作台</p><h1>把长文本变成可控的声音</h1></div></div><div className="top-actions"><button className={`connection ${connected ? "ok" : "warn"}`} onClick={refreshApi}><i />{connected === null ? "正在检测服务" : connected ? "后端已连接" : "后端未连接"}</button><button className="secondary-button project-library-entry" onClick={() => setProjectLibraryOpen(true)}><FolderOpen size={17} />打开方案</button><button className="secondary-button project-new-entry" onClick={requestNewProject}><Plus size={17} />新建</button><button className="secondary-button" onClick={() => void saveProject()} disabled={savingProject}>{savingProject ? <LoaderCircle className="spin" size={17} /> : <Save size={17} />}{savingProject ? "正在保存" : "保存方案"}</button><button className="primary-button" onClick={submit} disabled={generating}>{generating ? <RefreshCw className="spin" size={17} /> : <Sparkles size={17} />}{generating ? "正在提交" : "生成音频"}</button></div></header>

      <section className="engine-strip"><div className="section-label"><span>01</span><div><strong>选择引擎</strong><small>每个任务使用一个本地模型</small></div></div><div className="engine-options">{(Object.keys(engines) as EngineId[]).map(id => <button key={id} className={`engine-option ${engine === id ? "selected" : ""}`} onClick={() => setEngine(id)} style={{ "--engine-accent": engines[id].accent } as React.CSSProperties}><div className="engine-icon"><AudioLines size={20} /></div><div><strong>{engines[id].name}</strong><span>{engines[id].description}</span></div><div className="engine-check">{engine === id && <Check size={14} />}</div></button>)}</div></section>
      {notice && <div className={`notice ${/(请|失败|尚未|未提交|无法|缺少|必须|拒绝)/.test(notice) ? "warning" : "success"}`}><AlertCircle size={16} /><span>{notice}</span><button onClick={() => setNotice("")}><X size={15} /></button></div>}

      <div className="studio-grid">
        <section className="editor-panel"><div className="panel-heading"><div className="section-label compact"><span>02</span><div><strong>输入内容</strong><small>自动识别段落与标点</small></div></div><div className="editor-actions"><button onClick={importText}><Upload size={15} />导入 TXT</button><button onClick={async () => { const clip = await navigator.clipboard.readText(); if (clip) setText(clip); }}><FileText size={15} />粘贴纯文本</button></div></div><div className="document-title"><input aria-label="任务名称" value={projectName} onChange={event => setProjectName(event.target.value)} /><span>{projectId ? "已保存项目" : "未保存"}</span></div><textarea className="script-editor" aria-label="要生成的文本" value={text} onChange={e => setText(e.target.value)} placeholder="输入或粘贴需要生成的长文本…" /><div className="editor-footer"><div><span>{text.replace(/\s/g, "").length} 字</span><span>{sentenceCount} 个句段</span><span>预计 {Math.max(1, Math.ceil(text.length / 250))} 分钟</span></div></div><div className="segment-preview"><div><span className="preview-icon"><FileAudio size={17} /></span><div><strong>长音频分段预览</strong><p>约 {Math.max(1, Math.ceil(text.length / Number(currentParams.segment_chars ?? 180)))} 段 · 分段生成 · 失败重试 · 自动合并</p></div></div><button onClick={() => setGroupsOpen(prev => ({ ...prev, "长音频与输出": true }))}>调整设置<ChevronRight size={14} /></button></div></section>

        <aside className="parameter-panel"><div className="parameter-head"><div><p className="eyebrow">{engines[engine].name}</p><h2>完整推理参数</h2></div><button className="icon-button" title="恢复默认值" onClick={() => setParams(prev => ({ ...prev, [engine]: defaultsFor(engine) }))}><RotateCcw size={16} /></button></div><label className="parameter-search"><Search size={15} /><input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索参数名称或用途" />{search && <button onClick={() => setSearch("")}><X size={14} /></button>}</label><div className="parameter-scroll">{visibleGroups.map(group => <section className="parameter-group" key={group.title}><button className="group-trigger" onClick={() => setGroupsOpen(prev => ({ ...prev, [group.title]: !prev[group.title] }))}><div><strong>{group.title}</strong><span>{group.fields.length} 项 · {group.summary}</span></div>{groupsOpen[group.title] || search ? <ChevronDown size={17} /> : <ChevronRight size={17} />}</button>{(groupsOpen[group.title] || search) && <div className="group-fields">{group.fields.map(field => <FieldControl key={field.key} field={field} value={currentParams[field.key]} onChange={value => setParams(prev => ({ ...prev, [engine]: { ...prev[engine], [field.key]: value } }))} />)}</div>}</section>)}</div><div className="parameter-footer"><div><Info size={14} /><span>每项均附中文用途与调试说明</span></div><button className="primary-button full" onClick={submit} disabled={generating}><Zap size={16} />使用 {engines[engine].name} 生成</button></div></aside>
      </div>

      <section className={`queue-drawer ${queueOpen ? "is-open" : ""}`}><button className="queue-handle" onClick={() => setQueueOpen(v => !v)}><div><ListMusic size={17} /><strong>生成队列</strong><span>{jobs.filter(j => j.status !== "done").length} 个未完成</span></div>{queueOpen ? <ChevronDown size={17} /> : <ChevronRight size={17} />}</button>{queueOpen && <div className="job-list">{jobs.length === 0 ? <div className="empty-queue"><ListMusic size={24} /><div><strong>队列还是空的</strong><span>配置参数并生成后，真实任务会出现在这里。</span></div></div> : jobs.map(job => <div className="job-row" key={job.id}><button className={`job-play ${job.status}`} disabled={["queued", "done"].includes(job.status)} onClick={() => ["failed", "cancelled"].includes(job.status) ? retryJob(job.id) : job.status === "running" ? cancelJob(job.id) : undefined} title={job.status === "running" ? "取消任务" : ["failed", "cancelled"].includes(job.status) ? "重试任务" : statusValueLabel(job.status)}>{job.status === "running" ? <Pause size={15} /> : job.status === "done" ? <Play size={15} /> : ["failed", "cancelled"].includes(job.status) ? <RefreshCw size={15} /> : <Clock3 size={15} />}</button><div className="job-main"><div className="job-title"><strong>{job.title}</strong><span>{engines[job.engine].name}</span></div><div className="progress-line"><div className="progress-track"><i style={{ width: `${job.progress}%` }} /></div><span>{job.status === "done" ? job.duration : `${job.progress}%`}</span></div></div><div className="job-segments">{job.status === "running" && <Activity size={14} />}{job.segments}</div><div className={`job-status ${job.status}`}>{statusValueLabel(job.status)}</div></div>)}</div>}</section>
      </>}
    </main>
    {showOnboarding && <Onboarding onDone={finishOnboarding} onSetup={() => setActiveNav("settings")} />}
    {projectLibraryOpen && <ProjectLibrary apiUrl={apiUrl} currentProjectId={projectId} onClose={() => setProjectLibraryOpen(false)} onOpen={openProject} onRequestNew={requestNewProject} onDeletedCurrent={() => clearProject("当前项目已删除，编辑器已切换为空白项目。")}/>}
    {confirmNewProject && <div className="project-confirm-overlay new-project-confirm-overlay" role="presentation"><div className="project-confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="new-project-confirm-title"><span className="project-confirm-icon"><Plus size={24} /></span><h3 id="new-project-confirm-title">新建空白项目？</h3><p>当前编辑器内容会被清空。已经保存的项目仍保留在项目库中；尚未保存的修改无法恢复。</p><div><button className="secondary-button" onClick={() => setConfirmNewProject(false)}>继续编辑</button><button className="primary-button" onClick={newProject}>确认新建</button></div></div></div>}
  </div>;
}
