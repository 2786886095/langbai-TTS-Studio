import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity, AlertCircle, AudioLines, BellRing, Check, ChevronDown, ChevronRight,
  Clock3, Cpu, Download, FileAudio, FileText, FolderOpen, History, Info,
  ListMusic, LoaderCircle, Menu, Play, Plus, RefreshCw, RotateCcw,
  Save, Search, Settings, SlidersHorizontal, Sparkles, Square, Upload, WandSparkles,
  X, Zap, UserRoundCog, Store, SquareTerminal, GraduationCap,
} from "lucide-react";
import { defaultsFor, engines, gptSovitsDefaultsFor, parameterGroups, resolveGptSovitsVersion, type EngineId, type Field } from "./parameterSchemas";
import { EngineManager } from "./EngineManager";
import { WorkspacePage } from "./WorkspacePages";
import { Onboarding } from "./Onboarding";
import { ProjectLibrary, type ProjectRecord } from "./ProjectLibrary";
import { VoiceProfiles, type VoiceProfile, type VoiceProfileDraft } from "./VoiceProfiles";
import { CommunityModels } from "./CommunityModels";
import { RuntimeConsole } from "./RuntimeConsole";
import { TrainingHub } from "./TrainingHub";

type Job = { id: string; title: string; engine: EngineId; progress: number; status: "running" | "queued" | "failed" | "cancelled" | "done"; segments: string; duration?: string; outputPath?: string };
type ApiEngineStatus = { id?: string; name?: string; state?: string; available?: boolean };
type AppSettings = { defaultEngine?: string; autoRevealOutput?: boolean; updateChannel?: "stable" | "beta" };
type UpdateEvent = { state: "checking" | "available" | "current" | "downloading" | "downloaded" | "error"; info?: { version?: string }; progress?: { percent?: number; bytesPerSecond?: number }; message?: string };
type ParameterPreset = { id: string; name: string; engine: EngineId; parameters: Record<string, unknown>; updatedAt: string };

declare global {
  interface Window {
    langbaiDesktop?: {
      chooseFile: (options?: { filters?: Array<{ name: string; extensions: string[] }> }) => Promise<string | null>;
      chooseDirectory?: () => Promise<string | null>;
      openExternal?: (targetUrl: string) => Promise<unknown>;
      showItemInFolder?: (targetPath: string) => Promise<unknown>;
      readTextFile?: () => Promise<string | { content?: string; text?: string; path?: string } | null>;
      getAudioUrl?: (targetPath: string) => Promise<string>;
      exportAudio?: (targetPath: string) => Promise<{ path: string; name: string } | null>;
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
const parameterPresetStorageKey = "langbai-parameter-presets-v1";
const initialText = "声音不只是信息的载体，它也承载情绪、节奏与想象。\n\n在 langbai TTS Studio 中，你可以为每个任务选择最适合的本地语音引擎，细致调整音色与表达，并将长篇文本稳定地转换成完整音频。";

function titleFromText(value: string, limit = 36) {
  const collapsed = value.replace(/\s+/g, " ").trim().replace(/^[，。！？、；：,.!?;:\-—_\s]+/, "");
  if (!collapsed) return "语音项目";
  const sentence = collapsed.split(/[。！？!?\n]/, 1)[0]?.trim() || collapsed;
  return sentence.slice(0, limit).replace(/[，。！？、；：,.!?;:\s\-—_]+$/, "") || "语音项目";
}

function loadParameterPresets(): ParameterPreset[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(parameterPresetStorageKey) ?? "[]") as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item): item is ParameterPreset => Boolean(item && typeof item === "object" && isEngineId((item as ParameterPreset).engine) && typeof (item as ParameterPreset).id === "string" && typeof (item as ParameterPreset).name === "string" && typeof (item as ParameterPreset).updatedAt === "string" && (item as ParameterPreset).parameters && typeof (item as ParameterPreset).parameters === "object"));
  } catch { return []; }
}

function FieldControl({ field, value, onChange }: { field: Field; value: unknown; onChange: (value: unknown) => void }) {
  const id = `field-${field.key}`;
  if (field.type === "toggle") return <div className="field-row field-toggle"><div><label htmlFor={id}>{field.label}</label><p>{field.help}</p></div><button id={id} type="button" className={`switch ${value ? "is-on" : ""}`} role="switch" aria-checked={Boolean(value)} onClick={() => onChange(!value)}><span /></button></div>;
  if (field.type === "range") return <div className="field-row"><div className="field-label"><label htmlFor={id}>{field.label}</label><output>{String(value)}{field.unit ? ` ${field.unit}` : ""}</output></div><input id={id} className="range" type="range" min={field.min} max={field.max} step={field.step} value={Number(value)} onChange={e => onChange(Number(e.target.value))} /><p>{field.help}</p></div>;
  if (field.type === "select") return <div className="field-row"><label htmlFor={id}>{field.label}</label><div className="select-wrap"><select id={id} value={String(value)} onChange={e => onChange(e.target.value)}>{field.options?.map(option => <option key={option}>{option}</option>)}</select><ChevronDown size={15} /></div><p>{field.help}</p></div>;
  if (field.type === "file") {
    const filter = field.fileKind === "gpt-weight" ? { name: "GPT 权重", extensions: ["ckpt"] } : field.fileKind === "sovits-weight" ? { name: "SoVITS 权重", extensions: ["pth"] } : field.fileKind === "yaml" ? { name: "YAML 配置", extensions: ["yaml", "yml"] } : field.fileKind === "lora" ? { name: "LoRA 权重", extensions: ["safetensors", "pt", "pth", "bin"] } : { name: "音频文件", extensions: ["wav", "mp3", "flac", "ogg", "m4a"] };
    const placeholder = field.fileKind === "directory" ? "选择本地目录…" : field.fileKind === "gpt-weight" ? "选择 .ckpt 权重…" : field.fileKind === "sovits-weight" ? "选择 .pth 权重…" : field.fileKind === "yaml" ? "选择 YAML 配置…" : field.fileKind === "lora" ? "选择 LoRA 权重…" : "选择本地音频…";
    return <div className="field-row"><label htmlFor={id}>{field.label}</label><button id={id} type="button" className="file-picker" onClick={async () => { const selected = field.fileKind === "directory" ? await window.langbaiDesktop?.chooseDirectory?.() : await window.langbaiDesktop?.chooseFile({ filters: [filter] }); if (selected) onChange(selected); }}><FolderOpen size={15} /><span>{value ? String(value) : placeholder}</span></button><p>{field.help}</p></div>;
  }
  if (field.type === "textarea") return <div className="field-row"><label htmlFor={id}>{field.label}</label><textarea id={id} className="small-textarea" value={String(value)} onChange={e => onChange(e.target.value)} /><p>{field.help}</p></div>;
  return <div className="field-row"><label htmlFor={id}>{field.label}</label><div className="number-wrap"><input id={id} type={field.type === "number" ? "number" : "text"} min={field.min} max={field.max} step={field.step} value={String(value)} onChange={e => onChange(field.type === "number" ? Number(e.target.value) : e.target.value)} />{field.unit && <span>{field.unit}</span>}</div><p>{field.help}</p></div>;
}

function AppLogo() { return <div className="app-logo"><img src="./icon.png" alt="" /><div><strong>langbai</strong><span>TTS Studio</span></div></div>; }

function normalizeJob(raw: Record<string, unknown>): Job {
  const engine = (["indextts2", "voxcpm", "gpt_sovits"].includes(String(raw.engine)) ? raw.engine : "indextts2") as EngineId;
  const statusValue = String(raw.status ?? "queued");
  const output = raw.output && typeof raw.output === "object" ? raw.output as Record<string, unknown> : {};
  return {
    id: String(raw.id ?? crypto.randomUUID()), title: String(raw.title ?? "未命名任务"), engine,
    progress: Math.round(Number(raw.progress ?? 0) * 100), status: statusValue === "completed" ? "done" : (["running", "queued", "failed", "cancelled", "done"].includes(statusValue) ? statusValue : "queued") as Job["status"],
    segments: Array.isArray(raw.segments) ? `${raw.segments.filter(segment => typeof segment === "object" && segment && (segment as { status?: string }).status === "completed").length} / ${raw.segments.length} 段` : String(raw.segment_progress ?? "等待中"), duration: raw.duration ? String(raw.duration) : undefined,
    outputPath: output.path ? String(output.path) : raw.outputPath ? String(raw.outputPath) : raw.output_path ? String(raw.output_path) : undefined,
  };
}
function statusValueLabel(status: Job["status"]) { return status === "running" ? "生成中" : status === "queued" ? "排队" : status === "done" ? "已完成" : status === "cancelled" ? "已取消" : "失败"; }

function applyGptSovitsVersionTransition(previous: Record<string, unknown>, next: Record<string, unknown>) {
  const previousDefaults = gptSovitsDefaultsFor(previous);
  const nextDefaults = gptSovitsDefaultsFor(next);
  const usedPreviousDefault = Number(previous.sample_steps ?? previousDefaults.sample_steps) === previousDefaults.sample_steps;
  const result = { ...next };
  if (usedPreviousDefault && previousDefaults.version !== nextDefaults.version) result.sample_steps = nextDefaults.sample_steps;
  if (nextDefaults.version !== "v3") result.super_sampling = false;
  return result;
}

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
    if (saved.t2s_weights_path !== undefined) restored.gpt_weights_path = saved.t2s_weights_path ?? "";
    if (saved.vits_weights_path !== undefined) restored.sovits_weights_path = saved.vits_weights_path ?? "";
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
    if (saved.sample_steps === undefined) restored.sample_steps = gptSovitsDefaultsFor(restored).sample_steps;
    if (resolveGptSovitsVersion(restored) !== "v3") restored.super_sampling = false;
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
      quick_streaming_tokens: clean.quick_streaming_tokens, model_dir: clean.model_dir || null,
      device: clean.device || null, use_fp16: clean.use_fp16, use_cuda_kernel: clean.use_cuda_kernel,
      use_deepspeed: clean.use_deepspeed, use_accel: clean.use_accel, use_torch_compile: clean.use_torch_compile,
      do_sample: clean.do_sample, top_p: clean.top_p, top_k: clean.top_k, temperature: clean.temperature,
      length_penalty: clean.length_penalty, num_beams: clean.num_beams, repetition_penalty: clean.repetition_penalty, max_mel_tokens: clean.max_mel_tokens,
    };
  }
  if (engine === "voxcpm") {
    const { mode: _mode, voice_instruction, reference_wav_path, prompt_wav_path, ...rest } = clean;
    return { ...rest, control: voice_instruction || null, reference_audio: reference_wav_path || null, prompt_audio: prompt_wav_path || null };
  }
  const versionDefaults = gptSovitsDefaultsFor(clean);
  const { gpt_weights_path, sovits_weights_path, ref_audio_path, aux_ref_audio_paths, prompt_lang, text_lang, is_half, ...rest } = clean;
  return {
    ...rest, t2s_weights_path: gpt_weights_path, vits_weights_path: sovits_weights_path,
    reference_audio: ref_audio_path, aux_reference_audios: aux_ref_audio_paths ? [aux_ref_audio_paths] : null,
    prompt_language: languageCodes[String(prompt_lang)] ?? "auto", text_language: languageCodes[String(text_lang)] ?? "auto",
    is_half: is_half === "跟随配置" ? null : is_half === "开启",
    text_split_method: String(rest.text_split_method).split("｜")[0], streaming_mode: Number(String(rest.streaming_mode).split("｜")[0]),
    sample_steps_auto: Number(rest.sample_steps) === versionDefaults.sample_steps,
  };
}

export function App() {
  const [engine, setEngine] = useState<EngineId>("indextts2");
  const [params, setParams] = useState<Record<EngineId, Record<string, unknown>>>(() => ({ indextts2: defaultsFor("indextts2"), voxcpm: defaultsFor("voxcpm"), gpt_sovits: defaultsFor("gpt_sovits") }));
  const [text, setText] = useState(initialText);
  const [groupsOpen, setGroupsOpen] = useState<Record<string, boolean>>({ "音色与情感": true, "音色模式": true, "角色模型与参考": true });
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
  const [projectName, setProjectName] = useState(() => titleFromText(initialText));
  const [projectDescription, setProjectDescription] = useState("");
  const [projectLibraryOpen, setProjectLibraryOpen] = useState(false);
  const [confirmNewProject, setConfirmNewProject] = useState(false);
  const [cancelTarget, setCancelTarget] = useState<Job | null>(null);
  const [previewAudio, setPreviewAudio] = useState<{ url: string; title: string } | null>(null);
  const [savingProject, setSavingProject] = useState(false);
  const [voiceProfiles, setVoiceProfiles] = useState<VoiceProfile[]>([]);
  const [selectedVoiceId, setSelectedVoiceId] = useState<Record<EngineId, string>>({ indextts2: "", voxcpm: "", gpt_sovits: "" });
  const [voiceDraft, setVoiceDraft] = useState<VoiceProfileDraft | null>(null);
  const [parameterDrawerOpen, setParameterDrawerOpen] = useState(false);
  const [parameterPresets, setParameterPresets] = useState<ParameterPreset[]>(loadParameterPresets);
  const [selectedParameterPreset, setSelectedParameterPreset] = useState<Record<EngineId, string>>({ indextts2: "", voxcpm: "", gpt_sovits: "" });
  const [parameterPresetDialogOpen, setParameterPresetDialogOpen] = useState(false);
  const [parameterPresetDraftName, setParameterPresetDraftName] = useState("");
  const [updateEvent, setUpdateEvent] = useState<UpdateEvent | null>(null);
  const [updateDismissed, setUpdateDismissed] = useState(false);
  const updateChannelRef = useRef<"stable" | "beta">("stable");
  const autoRevealOutputRef = useRef(false);
  const previousJobStatusRef = useRef(new Map<string, string>());
  const revealedJobIdsRef = useRef(new Set<string>());
  const refreshInFlightRef = useRef(false);
  const currentParams = params[engine];
  const activeGeneration = useMemo(() => jobs.find(job => job.status === "running") ?? jobs.find(job => job.status === "queued") ?? null, [jobs]);
  const sentenceCount = useMemo(() => text.split(/[。！？\n]+/).filter(Boolean).length, [text]);
  const effectiveProjectName = projectName.trim() && !["未命名语音项目", "未命名语音任务"].includes(projectName.trim()) ? projectName.trim() : titleFromText(text);
  const updateProjectText = (nextText: string) => {
    const previousAutoTitle = titleFromText(text);
    setText(nextText);
    if (!projectName.trim() || projectName === previousAutoTitle || ["未命名语音项目", "未命名语音任务"].includes(projectName.trim())) setProjectName(titleFromText(nextText));
  };
  const visibleGroups = useMemo(() => parameterGroups[engine].map(group => ({ ...group, fields: group.fields.filter(field => !search || `${field.label}${field.help}${field.key}`.toLowerCase().includes(search.toLowerCase())) })).filter(group => !search || group.fields.length), [engine, search]);
  const currentVoiceProfiles = useMemo(() => voiceProfiles.filter(profile => profile.engine === engine), [voiceProfiles, engine]);
  const currentParameterPresets = useMemo(() => parameterPresets.filter(preset => preset.engine === engine).sort((left, right) => right.updatedAt.localeCompare(left.updatedAt)), [parameterPresets, engine]);

  useEffect(() => { localStorage.setItem(parameterPresetStorageKey, JSON.stringify(parameterPresets)); }, [parameterPresets]);

  const refreshVoiceProfiles = async () => {
    try {
      const response = await fetch(apiUrl("/api/voice-profiles"));
      if (!response.ok) return;
      const payload = await response.json() as { items?: VoiceProfile[] };
      setVoiceProfiles(payload.items ?? []);
    } catch { /* the creation page will show a detailed error if the service is unavailable */ }
  };

  const useVoiceProfile = (profile: VoiceProfile) => {
    setEngine(profile.engine);
    setParams(current => {
      const merged = { ...current[profile.engine], ...profile.parameters };
      return { ...current, [profile.engine]: profile.engine === "gpt_sovits" ? applyGptSovitsVersionTransition(current[profile.engine], merged) : merged };
    });
    setSelectedVoiceId(current => ({ ...current, [profile.engine]: profile.id }));
    setGroupsOpen(current => ({ ...current, [parameterGroups[profile.engine][0].title]: true }));
    setActiveNav("创作台");
    setNotice(`已应用角色声音“${profile.name}”。`);
    void refreshVoiceProfiles();
  };

  const applyParameterPreset = (presetId: string) => {
    setSelectedParameterPreset(current => ({ ...current, [engine]: presetId }));
    const preset = parameterPresets.find(item => item.id === presetId && item.engine === engine);
    if (!preset) return;
    const restored = { ...defaultsFor(engine, preset.parameters), ...preset.parameters };
    if (engine === "gpt_sovits" && resolveGptSovitsVersion(restored) !== "v3") restored.super_sampling = false;
    setParams(current => ({ ...current, [engine]: restored }));
    setNotice(`已应用参数预设“${preset.name}”。`);
  };

  const saveParameterPreset = () => {
    const selected = currentParameterPresets.find(item => item.id === selectedParameterPreset[engine]);
    setParameterPresetDraftName(selected?.name ?? `${engines[engine].name} 参数 ${currentParameterPresets.length + 1}`);
    setParameterPresetDialogOpen(true);
  };

  const confirmParameterPresetSave = () => {
    const selected = currentParameterPresets.find(item => item.id === selectedParameterPreset[engine]);
    const name = parameterPresetDraftName.trim();
    if (!name) { setNotice("参数预设名称不能为空。"); return; }
    const now = new Date().toISOString();
    const id = selected?.id ?? crypto.randomUUID();
    const preset: ParameterPreset = { id, name, engine, parameters: { ...currentParams }, updatedAt: now };
    setParameterPresets(current => [preset, ...current.filter(item => item.id !== id)]);
    setSelectedParameterPreset(current => ({ ...current, [engine]: id }));
    setParameterPresetDialogOpen(false);
    setNotice(selected ? `参数预设“${name}”已更新。` : `参数预设“${name}”已保存。`);
  };

  const deleteParameterPreset = () => {
    const id = selectedParameterPreset[engine];
    const preset = currentParameterPresets.find(item => item.id === id);
    if (!preset || !window.confirm(`删除参数预设“${preset.name}”？`)) return;
    setParameterPresets(current => current.filter(item => item.id !== id));
    setSelectedParameterPreset(current => ({ ...current, [engine]: "" }));
    setNotice(`参数预设“${preset.name}”已删除。`);
  };

  const saveCurrentVoice = () => {
    const voiceKeys = engine === "indextts2"
      ? ["spk_audio_prompt", "emo_audio_prompt", "emo_control", "emo_alpha", "emo_text"]
      : engine === "voxcpm"
        ? ["mode", "reference_wav_path", "prompt_wav_path", "prompt_text", "voice_instruction", "denoise"]
        : ["gpt_weights_path", "sovits_weights_path", "version", "ref_audio_path", "prompt_text", "prompt_lang"];
    setVoiceDraft({ engine, parameters: Object.fromEntries(voiceKeys.map(key => [key, currentParams[key]])) });
    setActiveNav("voices");
  };

  const resolveJobOutput = async (jobId: string, fallbackPath?: string) => {
    const response = await fetch(apiUrl(`/api/jobs/${jobId}/output`));
    if (!response.ok) throw new Error(`无法读取输出文件（HTTP ${response.status}）`);
    const payload = await response.json() as { output?: { path?: string }; openContract?: { open?: { path?: string }; reveal?: { path?: string } } };
    return {
      openPath: payload.openContract?.open?.path ?? payload.output?.path ?? fallbackPath,
      revealPath: payload.openContract?.reveal?.path ?? payload.output?.path ?? fallbackPath,
    };
  };

  const revealCompletedOutput = async (jobId: string, fallbackPath?: string) => {
    try {
      const output = await resolveJobOutput(jobId, fallbackPath);
      if (!output.revealPath) throw new Error("输出文件路径为空");
      if (!window.langbaiDesktop?.showItemInFolder) throw new Error("当前桌面端不支持打开文件位置");
      await window.langbaiDesktop.showItemInFolder(output.revealPath);
    } catch (reason) {
      setNotice(`音频已完成，但无法定位输出文件：${reason instanceof Error ? reason.message : "未知错误"}`);
    }
  };

  const playCompletedOutput = async (job: Job) => {
    try {
      const output = await resolveJobOutput(job.id, job.outputPath);
      if (!output.openPath) throw new Error("输出文件路径为空");
      if (!window.langbaiDesktop?.getAudioUrl) throw new Error("当前桌面端不支持本地音频试听");
      const url = await window.langbaiDesktop.getAudioUrl(output.openPath);
      if (!url) throw new Error("桌面端未返回可播放地址");
      setPreviewAudio({ url, title: job.title });
    } catch (reason) {
      setNotice(`无法试听生成音频：${reason instanceof Error ? reason.message : "未知错误"}`);
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
          updateChannelRef.current = settings.updateChannel === "beta" ? "beta" : "stable";
        }
      } catch { /* settings are optional while the local service is starting */ }
      if (disposed) return;
      schedule(await refreshApi());
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    void initialize();
    return () => { disposed = true; document.removeEventListener("visibilitychange", onVisibilityChange); if (timer) window.clearTimeout(timer); };
  }, []);
  useEffect(() => { void refreshVoiceProfiles(); }, []);
  useEffect(() => {
    const unsubscribe = window.langbaiDesktop?.onUpdateEvent?.(payload => {
      const event = payload as UpdateEvent;
      setUpdateEvent(event);
      if (["available", "downloading", "downloaded"].includes(event.state)) setUpdateDismissed(false);
    });
    const timer = window.setTimeout(() => {
      void window.langbaiDesktop?.checkForUpdates?.(updateChannelRef.current).catch(() => undefined);
    }, 5000);
    return () => { window.clearTimeout(timer); if (typeof unsubscribe === "function") unsubscribe(); };
  }, []);
  useEffect(() => {
    if (!parameterDrawerOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") setParameterDrawerOpen(false); };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [parameterDrawerOpen]);

  const openParameterGroup = (title?: string) => {
    if (title) setGroupsOpen(current => ({ ...current, [title]: true }));
    setParameterDrawerOpen(true);
    if (title) window.setTimeout(() => document.getElementById(`parameter-group-${parameterGroups[engine].findIndex(group => group.title === title)}`)?.scrollIntoView({ block: "start", behavior: "smooth" }), 240);
  };
  const downloadAvailableUpdate = async () => {
    try { await window.langbaiDesktop?.downloadUpdate?.(); }
    catch (error) { setUpdateEvent({ state: "error", message: error instanceof Error ? error.message : "下载更新失败" }); }
  };
  const installAvailableUpdate = () => { void window.langbaiDesktop?.installUpdate?.(); };

  const submit = async () => {
    if (!text.trim()) { setNotice("请先输入需要生成的文本。"); return; }
    if (engineStatus[engine] !== true) { setNotice(`${engines[engine].name} 尚未就绪。请先在“设置与路径”中检查或绑定本地引擎。`); return; }
    const focusRequiredField = (key: string, group: string, message: string) => {
      setGroupsOpen(current => ({ ...current, [group]: true }));
      setParameterDrawerOpen(true);
      setSearch("");
      setNotice(message);
      window.setTimeout(() => {
        const field = document.getElementById(`field-${key}`);
        field?.scrollIntoView({ block: "center", behavior: "smooth" });
        field?.focus();
      }, 240);
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
    if (engine === "gpt_sovits") {
      const required: Array<[string, string]> = [
        ["gpt_weights_path", "请先选择角色的 GPT（.ckpt）权重。"],
        ["sovits_weights_path", "请先选择同一角色的 SoVITS（.pth）权重。"],
        ["ref_audio_path", "请先选择 GPT-SoVITS 的主参考音频。"],
        ["prompt_text", "请填写与参考音频逐字对应的文本。"],
      ];
      const missing = required.find(([key]) => !String(currentParams[key] ?? "").trim());
      if (missing) { focusRequiredField(missing[0], "角色模型与参考", missing[1]); return; }
    }
    setGenerating(true); setNotice("");
    let reachedBackend = false;
    try {
      const sampleRate = Number(String(currentParams.sample_rate ?? "44100").replace(/\D/g, "")) || 44100;
      const longAudio = { maxChars: Number(currentParams.segment_chars ?? 180), maxRetries: Number(currentParams.retry_count ?? 2), keepSegments: Boolean(currentParams.keep_segments), targetSampleRate: sampleRate, silenceMs: Number(currentParams.segment_pause ?? 280) };
      const response = await fetch(apiUrl("/api/jobs"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: effectiveProjectName, engine, text, params: toApiParams(engine, currentParams), longAudio }) });
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
  const cancelJob = async (id: string) => {
    try {
      const response = await fetch(apiUrl(`/api/jobs/${id}/cancel`), { method: "POST" });
      if (!response.ok) throw new Error(await response.text());
      setCancelTarget(null);
      setNotice("已请求取消生成；当前模型推理正在终止，已完成分段会保留。");
      await refreshApi();
    } catch { setNotice("取消失败：后端服务未连接。"); }
  };
  const saveProject = async () => {
    if (savingProject) return;
    const sampleRate = Number(String(currentParams.sample_rate ?? "44100").replace(/\D/g, "")) || 44100;
    const payload = { name: effectiveProjectName, description: projectDescription, engine, text, params: currentParams, longAudio: { maxChars: Number(currentParams.segment_chars ?? 180), maxRetries: Number(currentParams.retry_count ?? 2), keepSegments: Boolean(currentParams.keep_segments), targetSampleRate: sampleRate, silenceMs: Number(currentParams.segment_pause ?? 280) } };
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
    setProjectName("");
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
    setProjectName(project.name && !["未命名语音项目", "未命名语音任务"].includes(project.name) ? project.name : titleFromText(project.text ?? ""));
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
  const importText = async () => { const result = await window.langbaiDesktop?.readTextFile?.(); if (!result) return; const content = typeof result === "string" ? result : result.content ?? result.text ?? ""; if (content) { updateProjectText(content); setNotice("文本已导入。"); } };
  useEffect(() => { const unsubscribe = window.langbaiDesktop?.onCommand?.(command => { if (command === "save-project") void saveProject(); else if (command === "new-project") requestNewProject(); else if (command === "open-settings") setActiveNav("settings"); else if (command === "generate") void submit(); }); return () => { if (typeof unsubscribe === "function") unsubscribe(); }; });

  const changeDensity = () => setDensity(current => { const next = current === "comfortable" ? "compact" : "comfortable"; localStorage.setItem("langbai-density", next); return next; });
  const finishOnboarding = () => { localStorage.setItem("langbai-onboarding-complete", "1"); setShowOnboarding(false); };

  return <div className={`app-shell density-${density}`}>
    <aside className={`sidebar ${sideOpen ? "is-open" : ""}`}>
      <div className="sidebar-head"><AppLogo /><button className="icon-button sidebar-close" onClick={() => setSideOpen(false)} aria-label="关闭导航"><X size={18} /></button></div>
      <nav>{[{ name: "创作台", icon: WandSparkles }, { name: "任务队列", icon: ListMusic }, { name: "角色声音", id: "voices", icon: UserRoundCog }, { name: "GPT 模型广场", id: "community", icon: Store }, { name: "模型训练", id: "training", icon: GraduationCap }, { name: "运行终端", id: "runtime", icon: SquareTerminal }, { name: "音频库", icon: FileAudio }, { name: "历史记录", icon: History }].map(item => { const id = item.id ?? item.name; return <button key={id} className={activeNav === id ? "active" : ""} onClick={() => setActiveNav(id)}><item.icon size={18} /><span>{item.name}</span>{item.name === "任务队列" && <b>{jobs.filter(j => ["running", "queued"].includes(j.status)).length}</b>}</button>; })}</nav>
      <div className="sidebar-spacer" />
      <div className="engine-health"><div className="health-title"><Cpu size={16} /><span>本地引擎</span><button onClick={refreshApi} aria-label="刷新状态"><RefreshCw size={14} /></button></div>{(Object.keys(engines) as EngineId[]).map(id => <div className="health-row" key={id}><i className={engineStatus[id] ? "online" : ""} /><span>{engines[id].name}</span><small>{engineStatus[id] === null ? "检测中" : engineStatus[id] ? "就绪" : "待连接"}</small></div>)}</div>
      <button className="density-toggle" onClick={changeDensity}><span>{density === "comfortable" ? "舒适密度" : "紧凑密度"}</span><b>{density === "comfortable" ? "A" : "A−"}</b></button>
      <button className={`sidebar-settings ${activeNav === "settings" ? "active" : ""}`} onClick={() => setActiveNav("settings")}><Settings size={17} /><span>设置与路径</span></button>
    </aside>

    <main className="workspace">
      {activeNav === "settings" ? <EngineManager onBack={() => setActiveNav("创作台")} density={density} onDensityChange={changeDensity} /> : activeNav === "voices" ? <VoiceProfiles apiUrl={apiUrl} draft={voiceDraft} onDraftConsumed={() => setVoiceDraft(null)} onUse={useVoiceProfile} onBack={() => setActiveNav("创作台")} /> : activeNav === "community" ? <CommunityModels apiUrl={apiUrl} onBack={() => setActiveNav("创作台")} onCreateVoice={draft => { setVoiceDraft(draft); setActiveNav("voices"); }} /> : activeNav === "training" ? <TrainingHub apiUrl={apiUrl} onExit={() => setActiveNav("创作台")} /> : activeNav === "runtime" ? <RuntimeConsole apiUrl={apiUrl} onBack={() => setActiveNav("创作台")} /> : activeNav !== "创作台" ? <WorkspacePage kind={activeNav === "任务队列" ? "queue" : activeNav === "音频库" ? "library" : "history"} onCreate={() => setActiveNav("创作台")} /> : <>
      <header className="topbar"><div className="title-row"><button className="icon-button mobile-menu" onClick={() => setSideOpen(true)} aria-label="打开导航"><Menu size={19} /></button><div><p className="eyebrow">语音创作工作台</p><h1>把长文本变成可控的声音</h1></div></div><div className="top-actions"><button className={`connection ${connected ? "ok" : "warn"}`} onClick={refreshApi}><i />{connected === null ? "正在检测服务" : connected ? "后端已连接" : "后端未连接"}</button><button className="secondary-button project-library-entry" onClick={() => setProjectLibraryOpen(true)}><FolderOpen size={17} />打开方案</button><button className="secondary-button project-new-entry" onClick={requestNewProject}><Plus size={17} />新建</button><button className="secondary-button" onClick={() => void saveProject()} disabled={savingProject}>{savingProject ? <LoaderCircle className="spin" size={17} /> : <Save size={17} />}{savingProject ? "正在保存" : "保存方案"}</button>{activeGeneration && <button className="danger-button generation-cancel-button" onClick={() => setCancelTarget(activeGeneration)}><Square size={15} fill="currentColor" />取消生成</button>}<button className="primary-button" onClick={submit} disabled={generating}>{generating ? <RefreshCw className="spin" size={17} /> : <Sparkles size={17} />}{generating ? "正在提交" : "生成音频"}</button></div></header>

      <section className="engine-strip"><div className="section-label"><span>01</span><div><strong>选择引擎</strong><small>每个任务使用一个本地模型</small></div></div><div className="engine-options">{(Object.keys(engines) as EngineId[]).map(id => <button key={id} className={`engine-option ${engine === id ? "selected" : ""}`} onClick={() => setEngine(id)} style={{ "--engine-accent": engines[id].accent } as React.CSSProperties}><div className="engine-icon"><AudioLines size={20} /></div><div><strong>{engines[id].name}</strong><span>{engines[id].description}</span></div><div className="engine-check">{engine === id && <Check size={14} />}</div></button>)}</div></section>
      <section className="voice-quickbar"><div><span className="voice-quickbar-icon"><UserRoundCog size={18} /></span><span><strong>角色声音</strong><small>{engine === "gpt_sovits" ? "选择已配对的 GPT + SoVITS 权重与参考音频" : "选择该引擎保存的参考声音"}</small></span></div><div className="voice-quickbar-actions"><select value={selectedVoiceId[engine]} onChange={event => { const profile = currentVoiceProfiles.find(item => item.id === event.target.value); if (profile) useVoiceProfile(profile); else setSelectedVoiceId(current => ({ ...current, [engine]: "" })); }}><option value="">使用当前临时配置</option>{currentVoiceProfiles.map(profile => <option value={profile.id} key={profile.id}>{profile.name}</option>)}</select><button className="secondary-button" onClick={saveCurrentVoice}><Save size={15} />保存当前声音</button><button className="secondary-button" onClick={() => setActiveNav("voices")}>管理资料库</button>{engine === "gpt_sovits" && <button className="secondary-button" onClick={() => setActiveNav("community")}><Store size={15} />模型广场</button>}</div></section>
      {notice && <div className={`notice ${/(请|失败|尚未|未提交|无法|缺少|必须|拒绝)/.test(notice) ? "warning" : "success"}`}><AlertCircle size={16} /><span>{notice}</span><button onClick={() => setNotice("")}><X size={15} /></button></div>}

      <div className="studio-grid">
        <section className="editor-panel"><div className="panel-heading"><div className="section-label compact"><span>02</span><div><strong>输入内容</strong><small>自动识别段落与标点</small></div></div><div className="editor-actions"><button onClick={importText}><Upload size={15} />导入 TXT</button><button onClick={async () => { const clip = await navigator.clipboard.readText(); if (clip) updateProjectText(clip); }}><FileText size={15} />粘贴纯文本</button><button className="parameter-entry" onClick={() => openParameterGroup()}><SlidersHorizontal size={16} />推理参数<span>{parameterGroups[engine].reduce((total, group) => total + group.fields.length, 0)}</span></button></div></div><div className="document-title"><input aria-label="任务名称" value={projectName} onChange={event => setProjectName(event.target.value)} placeholder={titleFromText(text)} /><span>{projectId ? "已保存项目" : "未保存"}</span></div><textarea className="script-editor" aria-label="要生成的文本" value={text} onChange={e => updateProjectText(e.target.value)} placeholder="输入或粘贴需要生成的长文本…" /><div className="editor-footer"><div><span>{text.replace(/\s/g, "").length} 字</span><span>{sentenceCount} 个句段</span><span>预计 {Math.max(1, Math.ceil(text.length / 250))} 分钟</span></div></div><div className="segment-preview"><div><span className="preview-icon"><FileAudio size={17} /></span><div><strong>长音频分段预览 · 无软件时长上限</strong><p>约 {Math.max(1, Math.ceil(text.length / Number(currentParams.segment_chars ?? 180)))} 段 · 分段落盘 · 断点续作 · 流式合并（受本机磁盘与模型稳定性限制）</p></div></div><button onClick={() => openParameterGroup("长音频与输出")}>调整设置<ChevronRight size={14} /></button></div></section>
      </div>

      <div className={`parameter-drawer-layer ${parameterDrawerOpen ? "is-open" : ""}`} aria-hidden={!parameterDrawerOpen}><button className="parameter-scrim" aria-label="关闭推理参数" onClick={() => setParameterDrawerOpen(false)} /><aside className="parameter-panel parameter-drawer" role="dialog" aria-modal="true" aria-labelledby="parameter-drawer-title"><div className="parameter-drawer-head"><div><p className="eyebrow">{engines[engine].name}</p><h2 id="parameter-drawer-title">推理参数</h2><span>{parameterGroups[engine].reduce((total, group) => total + group.fields.length, 0)} 项设置 · 修改立即保存到当前方案</span></div><button className="icon-button" aria-label="关闭推理参数" onClick={() => setParameterDrawerOpen(false)}><X size={18} /></button></div><div className="parameter-drawer-tools"><div className="parameter-preset-bar"><label><span>参数预设</span><select value={selectedParameterPreset[engine]} onChange={event => applyParameterPreset(event.target.value)}><option value="">当前临时参数</option>{currentParameterPresets.map(preset => <option key={preset.id} value={preset.id}>{preset.name}</option>)}</select></label><button className="secondary-button" onClick={saveParameterPreset}><Save size={15} />{selectedParameterPreset[engine] ? "更新预设" : "保存为预设"}</button>{selectedParameterPreset[engine] && <button className="secondary-button preset-delete" onClick={deleteParameterPreset}><X size={15} />删除</button>}</div><label className="parameter-search"><Search size={15} /><input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索参数名称或用途" />{search && <button onClick={() => setSearch("")}><X size={14} /></button>}</label><div className="parameter-jump-list">{parameterGroups[engine].map((group, index) => <button key={group.title} onClick={() => { setGroupsOpen(prev => ({ ...prev, [group.title]: true })); document.getElementById(`parameter-group-${index}`)?.scrollIntoView({ block: "start", behavior: "smooth" }); }}>{group.title}<span>{group.fields.length}</span></button>)}</div></div><div className="parameter-scroll">{visibleGroups.map(group => { const index = parameterGroups[engine].findIndex(item => item.title === group.title); return <section className="parameter-group" id={`parameter-group-${index}`} key={group.title}><button className="group-trigger" onClick={() => setGroupsOpen(prev => ({ ...prev, [group.title]: !prev[group.title] }))}><div><strong>{group.title}</strong><span>{group.fields.length} 项 · {group.summary}</span></div>{groupsOpen[group.title] || search ? <ChevronDown size={17} /> : <ChevronRight size={17} />}</button>{(groupsOpen[group.title] || search) && <div className="group-fields">{group.fields.map(field => <FieldControl key={field.key} field={field} value={currentParams[field.key]} onChange={value => setParams(prev => { const next = { ...prev[engine], [field.key]: value }; return { ...prev, [engine]: engine === "gpt_sovits" ? applyGptSovitsVersionTransition(prev[engine], next) : next }; })} />)}</div>}</section>; })}</div><div className="parameter-footer drawer-footer"><div><Info size={14} /><span>每项均附中文用途与调试说明</span></div><span><button className="secondary-button" onClick={() => setParams(prev => ({ ...prev, [engine]: defaultsFor(engine, prev[engine]) }))}><RotateCcw size={15} />恢复当前版本默认</button><button className="secondary-button" onClick={() => setParameterDrawerOpen(false)}>完成设置</button><button className="primary-button" onClick={submit} disabled={generating}><Zap size={16} />生成音频</button></span></div></aside></div>

      {previewAudio && <section className="studio-audio-player" aria-label="生成音频试听"><div><span className="studio-player-icon"><AudioLines size={18} /></span><span><strong>正在试听</strong><small>{previewAudio.title}</small></span></div><audio src={previewAudio.url} controls autoPlay /><button className="icon-button" onClick={() => setPreviewAudio(null)} aria-label="关闭试听"><X size={17} /></button></section>}
      <section className={`queue-drawer ${queueOpen ? "is-open" : ""}`}>
        <button className="queue-handle" onClick={() => setQueueOpen(v => !v)}><div><ListMusic size={17} /><strong>生成队列</strong><span>{jobs.filter(j => j.status !== "done").length} 个未完成</span></div>{queueOpen ? <ChevronDown size={17} /> : <ChevronRight size={17} />}</button>
        {queueOpen && <div className="job-list">{jobs.length === 0 ? <div className="empty-queue"><ListMusic size={24} /><div><strong>队列还是空的</strong><span>配置参数并生成后，真实任务会出现在这里。</span></div></div> : jobs.map(job => <div className="job-row" key={job.id}>
          <button className={`job-play ${job.status}`} onClick={() => job.status === "done" ? void playCompletedOutput(job) : ["failed", "cancelled"].includes(job.status) ? void retryJob(job.id) : ["running", "queued"].includes(job.status) ? setCancelTarget(job) : undefined} title={job.status === "done" ? "试听音频" : ["running", "queued"].includes(job.status) ? "取消任务" : ["failed", "cancelled"].includes(job.status) ? "重试任务" : statusValueLabel(job.status)}>{["running", "queued"].includes(job.status) ? <Square size={13} fill="currentColor" /> : job.status === "done" ? <Play size={15} fill="currentColor" /> : ["failed", "cancelled"].includes(job.status) ? <RefreshCw size={15} /> : <Clock3 size={15} />}</button>
          <div className="job-main"><div className="job-title"><strong>{job.title}</strong><span>{engines[job.engine].name}</span></div><div className="progress-line"><div className="progress-track"><i style={{ width: `${job.progress}%` }} /></div><span>{job.status === "done" ? job.duration : `${job.progress}%`}</span></div></div>
          <div className="job-segments">{job.status === "running" && <Activity size={14} />}{job.segments}</div>
          {job.status === "done" ? <div className="job-result-actions"><button onClick={() => void playCompletedOutput(job)}><Play size={14} fill="currentColor" />试听</button><button onClick={() => void revealCompletedOutput(job.id, job.outputPath)}><FolderOpen size={14} />打开位置</button></div> : <div className={`job-status ${job.status}`}>{statusValueLabel(job.status)}</div>}
        </div>)}</div>}
      </section>
      </>}
    </main>
    {updateEvent && !updateDismissed && ["available", "downloading", "downloaded", "error"].includes(updateEvent.state) && <aside className={`global-update-notice ${updateEvent.state}`} role="status" aria-live="polite"><span className="global-update-icon">{updateEvent.state === "downloading" ? <Download size={21} /> : <BellRing size={21} />}</span><div><p className="eyebrow">软件更新</p><strong>{updateEvent.state === "available" ? `发现新版本 ${updateEvent.info?.version ?? ""}` : updateEvent.state === "downloading" ? `正在下载 ${Math.round(updateEvent.progress?.percent ?? 0)}%` : updateEvent.state === "downloaded" ? `新版本 ${updateEvent.info?.version ?? ""} 已准备好` : "更新检查遇到问题"}</strong><small>{updateEvent.state === "available" ? "可直接在软件内下载，当前任务不会被中断。" : updateEvent.state === "downloading" ? "下载完成后会提示重启安装。" : updateEvent.state === "downloaded" ? "重启软件即可完成更新。" : updateEvent.message || "可稍后在设置中重新检查。"}</small>{updateEvent.state === "downloading" && <div className="global-update-progress"><i style={{ width: `${Math.round(updateEvent.progress?.percent ?? 0)}%` }} /></div>}</div><div className="global-update-actions">{updateEvent.state === "available" && <button className="primary-button" onClick={() => void downloadAvailableUpdate()}>立即下载</button>}{updateEvent.state === "downloaded" && <button className="primary-button" onClick={installAvailableUpdate}>重启安装</button>}{updateEvent.state !== "downloading" && <button className="icon-button" aria-label="稍后提醒" onClick={() => setUpdateDismissed(true)}><X size={16} /></button>}</div></aside>}
    {showOnboarding && <Onboarding onDone={finishOnboarding} onSetup={() => setActiveNav("settings")} />}
    {projectLibraryOpen && <ProjectLibrary apiUrl={apiUrl} currentProjectId={projectId} onClose={() => setProjectLibraryOpen(false)} onOpen={openProject} onRequestNew={requestNewProject} onDeletedCurrent={() => clearProject("当前项目已删除，编辑器已切换为空白项目。")}/>}
    {parameterPresetDialogOpen && <div className="project-confirm-overlay parameter-preset-dialog-overlay" role="presentation"><div className="project-confirm-dialog parameter-preset-dialog" role="dialog" aria-modal="true" aria-labelledby="parameter-preset-dialog-title"><span className="project-confirm-icon"><SlidersHorizontal size={22} /></span><h3 id="parameter-preset-dialog-title">保存参数预设</h3><p>预设仅属于 {engines[engine].name}，会保存当前全部推理、模型路径和长音频参数。</p><input autoFocus value={parameterPresetDraftName} onChange={event => setParameterPresetDraftName(event.target.value)} onKeyDown={event => { if (event.key === "Enter") confirmParameterPresetSave(); }} placeholder="例如：胡桃 v4 高质量" aria-label="参数预设名称" /><div><button className="secondary-button" onClick={() => setParameterPresetDialogOpen(false)}>取消</button><button className="primary-button" onClick={confirmParameterPresetSave}><Save size={15} />确认保存</button></div></div></div>}
    {cancelTarget && <div className="project-confirm-overlay generation-cancel-overlay" role="presentation"><div className="project-confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="generation-cancel-title"><span className="project-confirm-icon danger"><Square size={20} fill="currentColor" /></span><h3 id="generation-cancel-title">取消这次配音生成？</h3><p>“{cancelTarget.title}”将立即停止当前模型推理。已经完成的长音频分段会保留，之后可以从断点重试。</p><div><button className="secondary-button" onClick={() => setCancelTarget(null)}>继续生成</button><button className="danger-button" onClick={() => void cancelJob(cancelTarget.id)}>确认取消</button></div></div></div>}
    {confirmNewProject && <div className="project-confirm-overlay new-project-confirm-overlay" role="presentation"><div className="project-confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="new-project-confirm-title"><span className="project-confirm-icon"><Plus size={24} /></span><h3 id="new-project-confirm-title">新建空白项目？</h3><p>当前编辑器内容会被清空。已经保存的项目仍保留在项目库中；尚未保存的修改无法恢复。</p><div><button className="secondary-button" onClick={() => setConfirmNewProject(false)}>继续编辑</button><button className="primary-button" onClick={newProject}>确认新建</button></div></div></div>}
  </div>;
}
