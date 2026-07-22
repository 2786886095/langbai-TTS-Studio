import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  AlertTriangle, CheckCircle2, ChevronLeft, CloudDownload, ExternalLink,
  FileSearch2, FolderOpen, Github, HardDrive, LibraryBig, LoaderCircle,
  PackageCheck, RefreshCw, ScanSearch, Search, ShieldQuestion, X,
} from "lucide-react";
import type { VoiceProfileDraft } from "./VoiceProfiles";

type CommunityInstalled = { name: string; category: string; language: string; version: string; installPath: string; gptWeightsPath: string; sovitsWeightsPath: string; referenceAudio?: string | null; promptText?: string; sourcePage?: string };
type CommunityModel = { name: string; category: string; language: string; version: string; sourcePage: string; licenseNotice: string; installed?: CommunityInstalled | null };
type DownloadJob = { id: string; modelName: string; status: "queued" | "downloading" | "extracting" | "completed" | "failed"; progress: number; message: string; error?: string | null; installedModel?: CommunityInstalled | null };
type HuggingFaceModel = { id: string; name: string; platform: string; sourceType: "repository"; sourcePage: string; gptWeights: number; sovitsWeights: number; audioFiles: number; likes: number; downloads: number; lastModified?: string; license?: string | null; readyPair: boolean; licenseNotice: string };
type ExternalSource = { id: string; name: string; platform: string; sourceType: "repository" | "cloud"; sourcePage: string; summary: string; licenseNotice: string };
type ScanCandidate = { id: string; name: string; version: string; gptWeightsPath: string; sovitsWeightsPath: string; referenceAudio?: string | null; promptText?: string; folder: string; confidence: number; warnings: string[] };
type ScanResult = { roots: string[]; scannedFiles: number; gptWeights: number; sovitsWeights: number; audioFiles: number; items: ScanCandidate[] };
type SourceTab = "direct" | "huggingface" | "cloud" | "local";

const sourceTabs: Array<{ id: SourceTab; label: string; detail: string }> = [
  { id: "direct", label: "社区直链", detail: "软件内下载并安装" },
  { id: "huggingface", label: "Hugging Face", detail: "实时检索 260+ 仓库" },
  { id: "cloud", label: "网盘入口", detail: "百度・夸克・阿里云盘" },
  { id: "local", label: "智能识别", detail: "扫描已下载权重" },
];

export function CommunityModels({ apiUrl, onCreateVoice, onBack }: { apiUrl: (path: string) => string; onCreateVoice: (draft: VoiceProfileDraft) => void; onBack: () => void }) {
  const [activeSource, setActiveSource] = useState<SourceTab>("direct");
  const [categories, setCategories] = useState<string[]>([]);
  const [languages, setLanguages] = useState<string[]>([]);
  const [category, setCategory] = useState("");
  const [language, setLanguage] = useState("");
  const [models, setModels] = useState<CommunityModel[]>([]);
  const [huggingFaceModels, setHuggingFaceModels] = useState<HuggingFaceModel[]>([]);
  const [externalSources, setExternalSources] = useState<ExternalSource[]>([]);
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [jobs, setJobs] = useState<DownloadJob[]>([]);
  const [accepted, setAccepted] = useState<Record<string, boolean>>({});
  const [query, setQuery] = useState("");
  const [visibleLimit, setVisibleLimit] = useState(48);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);

  const loadCategories = async () => {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/community-models/categories"));
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json() as { items?: string[] };
      const rows = payload.items ?? [];
      setCategories(rows);
      setCategory(current => current || rows[0] || "");
    } catch (error) {
      setMessage(`社区目录暂时不可用：${error instanceof Error ? error.message : "未知错误"}`);
    } finally { setLoading(false); }
  };

  useEffect(() => { void loadCategories(); }, []);
  useEffect(() => {
    if (!category) return;
    setLanguage(""); setModels([]);
    void (async () => {
      try {
        const response = await fetch(apiUrl(`/api/community-models/languages?category=${encodeURIComponent(category)}`));
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json() as { items?: string[] };
        const rows = payload.items ?? [];
        setLanguages(rows); setLanguage(rows[0] || "");
      } catch (error) { setMessage(error instanceof Error ? error.message : "无法读取语言分类"); }
    })();
  }, [category]);

  const refreshModels = async () => {
    if (!category || !language) return;
    setLoading(true);
    try {
      const response = await fetch(apiUrl(`/api/community-models?category=${encodeURIComponent(category)}&language=${encodeURIComponent(language)}`));
      if (!response.ok) { const detail = await response.json().catch(() => null) as { detail?: string } | null; throw new Error(detail?.detail || `HTTP ${response.status}`); }
      const payload = await response.json() as { items?: CommunityModel[] };
      setModels(payload.items ?? []);
    } catch (error) { setMessage(error instanceof Error ? error.message : "无法读取社区模型"); }
    finally { setLoading(false); }
  };
  useEffect(() => { void refreshModels(); }, [language]);

  const refreshHuggingFace = async () => {
    setLoading(true);
    try {
      const search = query.trim() || "gpt-sovits";
      const response = await fetch(apiUrl(`/api/community-models/hugging-face?query=${encodeURIComponent(search)}&limit=100`));
      if (!response.ok) { const detail = await response.json().catch(() => null) as { detail?: string } | null; throw new Error(detail?.detail || `HTTP ${response.status}`); }
      const payload = await response.json() as { items?: HuggingFaceModel[] };
      setHuggingFaceModels(payload.items ?? []);
    } catch (error) { setMessage(error instanceof Error ? error.message : "无法检索 Hugging Face"); }
    finally { setLoading(false); }
  };

  const loadExternalSources = async () => {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/community-models/external-sources"));
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json() as { items?: ExternalSource[] };
      setExternalSources(payload.items ?? []);
    } catch (error) { setMessage(error instanceof Error ? error.message : "无法读取网盘入口"); }
    finally { setLoading(false); }
  };

  useEffect(() => {
    if (activeSource === "huggingface" && huggingFaceModels.length === 0) void refreshHuggingFace();
    if (activeSource === "cloud" && externalSources.length === 0) void loadExternalSources();
  }, [activeSource]);

  useEffect(() => {
    let disposed = false; let timer = 0;
    const poll = async () => {
      try {
        const response = await fetch(apiUrl("/api/community-models/jobs"));
        if (response.ok) {
          const payload = await response.json() as { items?: DownloadJob[] };
          if (!disposed) {
            const rows = payload.items ?? []; setJobs(rows);
            const active = rows.some(item => ["queued", "downloading", "extracting"].includes(item.status));
            timer = window.setTimeout(poll, active ? 1500 : 15000); return;
          }
        }
      } catch { /* service health is shown by the main workspace */ }
      if (!disposed) timer = window.setTimeout(poll, 15000);
    };
    void poll(); return () => { disposed = true; if (timer) window.clearTimeout(timer); };
  }, []);

  const openSource = async (url: string) => {
    if (window.langbaiDesktop?.openExternal) await window.langbaiDesktop.openExternal(url);
    else window.open(url, "_blank", "noopener,noreferrer");
  };

  const install = async (model: CommunityModel) => {
    if (!accepted[model.name]) { setMessage("下载前请先确认已阅读该模型原帖中的作者许可与使用限制。"); return; }
    const response = await fetch(apiUrl("/api/community-models/install"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ category: model.category, language: model.language, modelName: model.name, version: model.version || "auto", licenseAccepted: true }) });
    if (!response.ok) { const detail = await response.json().catch(() => null) as { detail?: string } | null; setMessage(detail?.detail || "无法开始下载"); return; }
    const job = await response.json() as DownloadJob; setJobs(current => [job, ...current]); setMessage("下载任务已提交。软件只会导入权重、参考音频与说明文件。");
  };

  const createVoice = (model: CommunityInstalled | ScanCandidate, source?: { category?: string; language?: string; page?: string }) => {
    const categoryName = "category" in model ? model.category : source?.category || "本地识别";
    const languageName = "language" in model ? model.language : source?.language || "自动识别";
    const sourcePage = "sourcePage" in model ? model.sourcePage : source?.page;
    const promptLanguage: Record<string, string> = { 中文: "中文", 日语: "日文", 英语: "英文", 韩语: "韩文", 粤语: "粤语" };
    onCreateVoice({ engine: "gpt_sovits", name: model.name, description: `${categoryName} · ${languageName} · GPT-SoVITS`, sourceModel: { name: model.name, sourcePage, category: categoryName, language: languageName }, parameters: { gpt_weights_path: model.gptWeightsPath, sovits_weights_path: model.sovitsWeightsPath, ref_audio_path: model.referenceAudio ?? "", prompt_text: model.promptText ?? "", prompt_lang: promptLanguage[languageName] ?? "中文", version: model.version || "auto" } });
  };

  const scanModels = async (paths: string[] = []) => {
    setLoading(true); setMessage("");
    try {
      const response = await fetch(apiUrl("/api/community-models/scan"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ paths }) });
      if (!response.ok) { const detail = await response.json().catch(() => null) as { detail?: string } | null; throw new Error(detail?.detail || `HTTP ${response.status}`); }
      const payload = await response.json() as ScanResult;
      setScanResult(payload);
      setMessage(payload.items.length ? `已识别 ${payload.items.length} 组 GPT + SoVITS 权重。` : "扫描完成，但没有找到可可靠配对的 GPT 与 SoVITS 权重。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "模型扫描失败"); }
    finally { setLoading(false); }
  };

  const chooseAndScan = async () => {
    const selected = await window.langbaiDesktop?.chooseDirectory?.();
    if (selected) await scanModels([selected]);
  };

  const matching = useMemo(() => models.filter(model => !query || model.name.toLowerCase().includes(query.toLowerCase())), [models, query]);
  const visible = useMemo(() => matching.slice(0, visibleLimit), [matching, visibleLimit]);
  const matchingSources = useMemo(() => externalSources.filter(source => !query || `${source.name} ${source.platform} ${source.summary}`.toLowerCase().includes(query.toLowerCase())), [externalSources, query]);
  useEffect(() => { setVisibleLimit(48); }, [category, language, query]);
  const jobFor = (name: string) => jobs.find(job => job.modelName === name);

  return <div className="library-page community-page">
    <header className="library-topbar"><div><button className="back-link" onClick={onBack}><ChevronLeft size={14} />返回创作台</button><p className="eyebrow">GPT-SoVITS 资源</p><h1>全网模型与本地识别</h1><p>直链在软件内安装；仓库和网盘保留原路径，下载完成后智能配对权重。</p></div><button className="secondary-button" onClick={() => setActiveSource("local")}><ScanSearch size={16} />识别已下载模型</button></header>
    {message && <div className="manager-notice"><AlertTriangle size={16} /><span>{message}</span><button onClick={() => setMessage("")}><X size={14} /></button></div>}
    <div className="community-source-tabs">{sourceTabs.map(tab => <button key={tab.id} className={activeSource === tab.id ? "active" : ""} onClick={() => { setActiveSource(tab.id); setQuery(""); }}><span>{tab.label}</span><small>{tab.detail}</small></button>)}</div>
    <div className="community-safety"><ShieldQuestion size={19} /><div><strong>来源可访问不等于获得授权</strong><span>直链、仓库和网盘会分别标注。许可未知的模型仍可导入，但软件不会将其标记为可商用；请保留原帖路径并自行判断使用范围。</span></div></div>

    {activeSource === "direct" && <section className="community-browser"><div className="community-filters"><label><span>作品分类</span><select value={category} onChange={event => setCategory(event.target.value)}>{categories.map(item => <option key={item}>{item}</option>)}</select></label><label><span>模型语言</span><select value={language} onChange={event => setLanguage(event.target.value)}>{languages.map(item => <option key={item}>{item}</option>)}</select></label><label className="community-search"><span>搜索角色</span><div><Search size={15} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="输入模型名称" /></div></label><button className="secondary-button" onClick={() => void refreshModels()}><RefreshCw size={15} />刷新目录</button></div>
      {loading ? <LoadingState label="正在读取社区目录" /> : visible.length === 0 ? <EmptyState label="当前分类没有匹配模型" /> : <><div className="community-model-grid">{visible.map(model => { const job = jobFor(model.name); const installed = model.installed || job?.installedModel; const active = job && ["queued", "downloading", "extracting"].includes(job.status); return <article key={`${model.category}-${model.language}-${model.name}`} className="community-model-card"><ModelTitle icon={<HardDrive size={18} />} title={model.name} detail={`${model.category} · ${model.language} · 软件内直链`} installed={Boolean(installed)} /><p>{model.licenseNotice}</p>{active && <Progress job={job} />}{job?.status === "failed" && <div className="community-error">{job.error || "下载失败"}</div>}<label className="community-license"><input type="checkbox" checked={Boolean(accepted[model.name])} onChange={event => setAccepted(current => ({ ...current, [model.name]: event.target.checked }))} /><span>我已核对该模型许可或愿意自行承担风险</span></label><div className="community-model-actions"><button className="text-link" onClick={() => void openSource(model.sourcePage)}>查看来源 <ExternalLink size={12} /></button>{installed ? <button className="primary-button" onClick={() => createVoice(installed)}><CheckCircle2 size={15} />创建角色声音</button> : <button className="primary-button" disabled={Boolean(active) || !accepted[model.name]} onClick={() => void install(model)}>{active ? <LoaderCircle className="spin" size={15} /> : <CloudDownload size={15} />}{active ? "下载中" : "软件内下载"}</button>}</div></article>; })}</div>{visible.length < matching.length && <div className="community-load-more"><span>已显示 {visible.length} / {matching.length} 个模型</span><button className="secondary-button" onClick={() => setVisibleLimit(current => current + 48)}>加载更多</button></div>}</>}
    </section>}

    {activeSource === "huggingface" && <section className="community-browser"><div className="community-discovery-bar"><label className="community-search"><span>搜索 Hugging Face 仓库</span><div><Search size={15} /><input value={query} onChange={event => setQuery(event.target.value)} onKeyDown={event => { if (event.key === "Enter") void refreshHuggingFace(); }} placeholder="例如 gpt-sovits、Genshin、Blue Archive" /></div></label><button className="primary-button" onClick={() => void refreshHuggingFace()}><Search size={15} />全网检索</button></div>
      {loading ? <LoadingState label="正在检索 Hugging Face" /> : huggingFaceModels.length === 0 ? <EmptyState label="没有找到相关仓库" /> : <div className="community-model-grid">{huggingFaceModels.map(model => <article key={model.id} className="community-model-card"><ModelTitle icon={<Github size={18} />} title={model.name} detail={`Hugging Face · ${model.readyPair ? "检测到完整权重对" : "需要手动核对文件"}`} /><div className="repository-metrics"><span>GPT {model.gptWeights}</span><span>SoVITS {model.sovitsWeights}</span><span>参考音频 {model.audioFiles}</span><span>★ {model.likes}</span></div><p>{model.licenseNotice}</p><div className="community-model-actions"><span className={`source-confidence ${model.readyPair ? "ready" : "unknown"}`}>{model.readyPair ? "可识别" : "待核对"}</span><button className="secondary-button" onClick={() => void openSource(model.sourcePage)}>打开仓库下载 <ExternalLink size={13} /></button></div></article>)}</div>}
    </section>}

    {activeSource === "cloud" && <section className="community-browser"><div className="community-discovery-bar"><label className="community-search"><span>筛选网盘与社区入口</span><div><Search size={15} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="作品、角色或网盘平台" /></div></label><button className="secondary-button" onClick={() => void loadExternalSources()}><RefreshCw size={15} />刷新入口</button></div>
      {loading ? <LoadingState label="正在读取来源目录" /> : <div className="community-model-grid">{matchingSources.map(source => <article key={source.id} className="community-model-card"><ModelTitle icon={<CloudDownload size={18} />} title={source.name} detail={`${source.platform} · 外部下载路径`} /><p>{source.summary}</p><div className="source-license-note"><AlertTriangle size={14} />{source.licenseNotice}</div><div className="community-model-actions"><span className="source-confidence unknown">许可待核对</span><button className="primary-button" onClick={() => void openSource(source.sourcePage)}>前往下载路径 <ExternalLink size={13} /></button></div></article>)}</div>}
    </section>}

    {activeSource === "local" && <section className="community-scan-panel"><div className="scan-hero"><span><ScanSearch size={27} /></span><div><p className="eyebrow">本地智能识别</p><h2>自动配对 GPT 与 SoVITS 权重</h2><p>识别 `.ckpt`、`.pth`、参考音频和模型版本；不会移动、修改或上传你的文件。</p></div><div><button className="secondary-button" onClick={() => void scanModels()} disabled={loading}><FileSearch2 size={16} />快速扫描下载目录</button><button className="primary-button" onClick={() => void chooseAndScan()} disabled={loading}><FolderOpen size={16} />选择文件夹扫描</button></div></div>
      {loading ? <LoadingState label="正在分析文件名与目录关系" /> : !scanResult ? <div className="scan-empty"><LibraryBig size={28} /><strong>等待扫描</strong><span>网盘或仓库下载完成后，选择保存目录即可开始识别。</span></div> : <><div className="scan-summary"><span><strong>{scanResult.items.length}</strong>组模型</span><span><strong>{scanResult.gptWeights}</strong>个 GPT 权重</span><span><strong>{scanResult.sovitsWeights}</strong>个 SoVITS 权重</span><span><strong>{scanResult.audioFiles}</strong>个音频</span></div>{scanResult.items.length === 0 ? <EmptyState label="没有找到可可靠配对的模型" /> : <div className="scan-result-list">{scanResult.items.map(candidate => <article key={candidate.id}><div className="scan-confidence"><strong>{Math.round(candidate.confidence * 100)}%</strong><span>匹配度</span></div><div className="scan-result-main"><strong>{candidate.name}</strong><small>{candidate.version === "auto" ? "自动识别版本" : candidate.version} · {candidate.folder}</small><div><span title={candidate.gptWeightsPath}>GPT：{candidate.gptWeightsPath}</span><span title={candidate.sovitsWeightsPath}>SoVITS：{candidate.sovitsWeightsPath}</span>{candidate.referenceAudio && <span title={candidate.referenceAudio}>参考：{candidate.referenceAudio}</span>}</div>{candidate.warnings.length > 0 && <p><AlertTriangle size={13} />{candidate.warnings.join("；")}</p>}</div><div className="scan-result-actions"><button className="secondary-button" onClick={() => void window.langbaiDesktop?.showItemInFolder?.(candidate.gptWeightsPath)}>显示文件</button><button className="primary-button" onClick={() => createVoice(candidate)}>创建角色声音</button></div></article>)}</div>}</>}
    </section>}

    {jobs.length > 0 && <section className="community-jobs"><div><p className="eyebrow">下载任务</p><h2>最近进度</h2></div>{jobs.slice(0, 5).map(job => <article key={job.id}><span className={`community-job-state ${job.status}`}><HardDrive size={15} /></span><div><strong>{job.modelName}</strong><small>{job.error || job.message}</small></div><b>{job.status === "completed" ? "已完成" : job.status === "failed" ? "失败" : `${Math.round(job.progress * 100)}%`}</b>{job.status === "completed" && job.installedModel && <button className="secondary-button" onClick={() => createVoice(job.installedModel!)}>创建声音</button>}</article>)}</section>}
  </div>;
}

function LoadingState({ label }: { label: string }) { return <div className="community-empty"><LoaderCircle className="spin" size={25} /><strong>{label}</strong></div>; }
function EmptyState({ label }: { label: string }) { return <div className="community-empty"><LibraryBig size={27} /><strong>{label}</strong><span>可以调整筛选条件，或下载后使用本地智能识别。</span></div>; }
function ModelTitle({ icon, title, detail, installed = false }: { icon: ReactNode; title: string; detail: string; installed?: boolean }) { return <div className="community-model-title"><span>{icon}</span><div><strong title={title}>{title}</strong><small>{detail}</small></div>{installed && <i><PackageCheck size={14} />已安装</i>}</div>; }
function Progress({ job }: { job?: DownloadJob }) { return <div className="community-progress"><div><i style={{ width: `${Math.round((job?.progress ?? 0) * 100)}%` }} /></div><span>{job?.message} · {Math.round((job?.progress ?? 0) * 100)}%</span></div>; }
