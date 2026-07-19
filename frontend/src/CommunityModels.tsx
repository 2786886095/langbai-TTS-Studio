import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, CheckCircle2, ChevronLeft, CloudDownload, ExternalLink,
  HardDrive, LibraryBig, LoaderCircle, PackageCheck, RefreshCw, Search, X,
} from "lucide-react";
import type { VoiceProfileDraft } from "./VoiceProfiles";

type CommunityInstalled = { name: string; category: string; language: string; version: string; installPath: string; gptWeightsPath: string; sovitsWeightsPath: string; referenceAudio?: string | null; promptText?: string; sourcePage?: string };
type CommunityModel = { name: string; category: string; language: string; version: string; sourcePage: string; licenseNotice: string; installed?: CommunityInstalled | null };
type DownloadJob = { id: string; modelName: string; status: "queued" | "downloading" | "extracting" | "completed" | "failed"; progress: number; message: string; error?: string | null; installedModel?: CommunityInstalled | null };

export function CommunityModels({ apiUrl, onCreateVoice, onBack }: { apiUrl: (path: string) => string; onCreateVoice: (draft: VoiceProfileDraft) => void; onBack: () => void }) {
  const [categories, setCategories] = useState<string[]>([]);
  const [languages, setLanguages] = useState<string[]>([]);
  const [category, setCategory] = useState("");
  const [language, setLanguage] = useState("");
  const [models, setModels] = useState<CommunityModel[]>([]);
  const [jobs, setJobs] = useState<DownloadJob[]>([]);
  const [accepted, setAccepted] = useState<Record<string, boolean>>({});
  const [query, setQuery] = useState("");
  const [visibleLimit, setVisibleLimit] = useState(48);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);

  const loadCategories = async () => {
    setLoading(true);
    try { const response = await fetch(apiUrl("/api/community-models/categories")); if (!response.ok) throw new Error(`HTTP ${response.status}`); const payload = await response.json() as { items?: string[] }; const rows = payload.items ?? []; setCategories(rows); setCategory(current => current || rows[0] || ""); }
    catch (error) { setMessage(`社区目录暂时不可用：${error instanceof Error ? error.message : "未知错误"}`); }
    finally { setLoading(false); }
  };
  useEffect(() => { void loadCategories(); }, []);
  useEffect(() => { if (!category) return; setLanguage(""); setModels([]); void (async () => { try { const response = await fetch(apiUrl(`/api/community-models/languages?category=${encodeURIComponent(category)}`)); if (!response.ok) throw new Error(`HTTP ${response.status}`); const payload = await response.json() as { items?: string[] }; const rows = payload.items ?? []; setLanguages(rows); setLanguage(rows[0] || ""); } catch (error) { setMessage(error instanceof Error ? error.message : "无法读取语言分类"); } })(); }, [category]);
  const refreshModels = async () => { if (!category || !language) return; setLoading(true); try { const response = await fetch(apiUrl(`/api/community-models?category=${encodeURIComponent(category)}&language=${encodeURIComponent(language)}`)); if (!response.ok) { const detail = await response.json().catch(() => null) as { detail?: string } | null; throw new Error(detail?.detail || `HTTP ${response.status}`); } const payload = await response.json() as { items?: CommunityModel[] }; setModels(payload.items ?? []); } catch (error) { setMessage(error instanceof Error ? error.message : "无法读取社区模型"); } finally { setLoading(false); } };
  useEffect(() => { void refreshModels(); }, [language]);

  useEffect(() => {
    let disposed = false; let timer = 0;
    const poll = async () => { try { const response = await fetch(apiUrl("/api/community-models/jobs")); if (response.ok) { const payload = await response.json() as { items?: DownloadJob[] }; if (!disposed) { const rows = payload.items ?? []; setJobs(rows); const active = rows.some(item => ["queued", "downloading", "extracting"].includes(item.status)); timer = window.setTimeout(poll, active ? 1500 : 15000); return; } } } catch { /* main notice already covers service health */ } if (!disposed) timer = window.setTimeout(poll, 15000); };
    void poll(); return () => { disposed = true; if (timer) window.clearTimeout(timer); };
  }, []);

  const install = async (model: CommunityModel) => {
    if (!accepted[model.name]) { setMessage("下载前请先确认已阅读该模型原帖中的作者许可与使用限制。"); return; }
    const response = await fetch(apiUrl("/api/community-models/install"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ category: model.category, language: model.language, modelName: model.name, version: model.version || "auto", licenseAccepted: true }) });
    if (!response.ok) { const detail = await response.json().catch(() => null) as { detail?: string } | null; setMessage(detail?.detail || "无法开始下载"); return; }
    const job = await response.json() as DownloadJob; setJobs(current => [job, ...current]); setMessage("下载任务已提交。软件只会导入权重、参考音频与说明文件。");
  };
  const createVoice = (model: CommunityInstalled) => {
    const promptLanguage: Record<string, string> = { 中文: "中文", 日语: "日文", 英语: "英文", 韩语: "韩文", 粤语: "粤语" };
    onCreateVoice({ engine: "gpt_sovits", name: model.name, description: `${model.category} · ${model.language} · 社区模型`, sourceModel: { name: model.name, sourcePage: model.sourcePage, category: model.category, language: model.language }, parameters: { gpt_weights_path: model.gptWeightsPath, sovits_weights_path: model.sovitsWeightsPath, ref_audio_path: model.referenceAudio ?? "", prompt_text: model.promptText ?? "", prompt_lang: promptLanguage[model.language] ?? "中文", version: model.version || "auto" } });
  };
  const matching = useMemo(() => models.filter(model => !query || model.name.toLowerCase().includes(query.toLowerCase())), [models, query]);
  const visible = useMemo(() => matching.slice(0, visibleLimit), [matching, visibleLimit]);
  useEffect(() => { setVisibleLimit(48); }, [category, language, query]);
  const jobFor = (name: string) => jobs.find(job => job.modelName === name);

  return <div className="library-page community-page">
    <header className="library-topbar"><div><button className="back-link" onClick={onBack}><ChevronLeft size={14} />返回创作台</button><p className="eyebrow">GPT-SoVITS 资源</p><h1>社区模型广场</h1><p>参考 GSVI 的分类浏览体验，下载后创建“权重 + 参考音频”角色声音。</p></div><a className="secondary-button" href="https://www.ai-hobbyist.com/forum.php?mod=forumdisplay&fid=138" target="_blank" rel="noreferrer">打开社区模型区 <ExternalLink size={14} /></a></header>
    {message && <div className="manager-notice"><AlertTriangle size={16} /><span>{message}</span><button onClick={() => setMessage("")}><X size={14} /></button></div>}
    <div className="community-safety"><AlertTriangle size={19} /><div><strong>目录没有提供逐模型许可证或原帖链接</strong><span>作者许可可能限制商用、二次分发或要求署名，请先在社区模型区按模型名查找并核对。软件只能校验可信下载域名和 ZIP 路径，并拒绝脚本、程序与其他未知文件，不能替作者授予使用权。</span></div></div>
    <section className="community-browser"><div className="community-filters"><label><span>作品分类</span><select value={category} onChange={event => setCategory(event.target.value)}>{categories.map(item => <option key={item}>{item}</option>)}</select></label><label><span>模型语言</span><select value={language} onChange={event => setLanguage(event.target.value)}>{languages.map(item => <option key={item}>{item}</option>)}</select></label><label className="community-search"><span>搜索角色</span><div><Search size={15} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="输入模型名称" /></div></label><button className="secondary-button" onClick={() => void refreshModels()}><RefreshCw size={15} />刷新目录</button></div>
      {loading ? <div className="community-empty"><LoaderCircle className="spin" size={25} /><strong>正在读取社区目录</strong></div> : visible.length === 0 ? <div className="community-empty"><LibraryBig size={27} /><strong>当前分类没有匹配模型</strong><span>尝试切换作品、语言或搜索词。</span></div> : <><div className="community-model-grid">{visible.map(model => { const job = jobFor(model.name); const installed = model.installed || job?.installedModel; const active = job && ["queued", "downloading", "extracting"].includes(job.status); return <article key={model.name} className="community-model-card"><div className="community-model-title"><span><HardDrive size={18} /></span><div><strong>{model.name}</strong><small>{model.category} · {model.language} · 自动识别版本</small></div>{installed && <i><PackageCheck size={14} />已安装</i>}</div><p>{model.licenseNotice}</p>{active && <div className="community-progress"><div><i style={{ width: `${Math.round((job?.progress ?? 0) * 100)}%` }} /></div><span>{job?.message} · {Math.round((job?.progress ?? 0) * 100)}%</span></div>}{job?.status === "failed" && <div className="community-error">{job.error || "下载失败"}</div>}<label className="community-license"><input type="checkbox" checked={Boolean(accepted[model.name])} onChange={event => setAccepted(current => ({ ...current, [model.name]: event.target.checked }))} /><span>我已在社区按名称核对该模型许可</span></label><div className="community-model-actions"><a href={model.sourcePage} target="_blank" rel="noreferrer">打开模型区 <ExternalLink size={12} /></a>{installed ? <button className="primary-button" onClick={() => createVoice(installed)}><CheckCircle2 size={15} />创建角色声音</button> : <button className="primary-button" disabled={Boolean(active) || !accepted[model.name]} onClick={() => void install(model)}>{active ? <LoaderCircle className="spin" size={15} /> : <CloudDownload size={15} />}{active ? "下载中" : "安全下载"}</button>}</div></article>; })}</div>{visible.length < matching.length && <div className="community-load-more"><span>已显示 {visible.length} / {matching.length} 个模型</span><button className="secondary-button" onClick={() => setVisibleLimit(current => current + 48)}>加载更多</button></div>}</>}
    </section>
    {jobs.length > 0 && <section className="community-jobs"><div><p className="eyebrow">下载任务</p><h2>最近进度</h2></div>{jobs.slice(0, 5).map(job => <article key={job.id}><span className={`community-job-state ${job.status}`}><HardDrive size={15} /></span><div><strong>{job.modelName}</strong><small>{job.error || job.message}</small></div><b>{job.status === "completed" ? "已完成" : job.status === "failed" ? "失败" : `${Math.round(job.progress * 100)}%`}</b>{job.status === "completed" && job.installedModel && <button className="secondary-button" onClick={() => createVoice(job.installedModel!)}>创建声音</button>}</article>)}</section>}
  </div>;
}
