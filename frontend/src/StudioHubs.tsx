import { useEffect, useState, type ReactNode } from "react";
import { AlertCircle, AudioLines, Check, Cpu, ExternalLink, FolderOpen, Plus, RefreshCw, Save, ShieldCheck, SquareTerminal, Trash2, X, Zap } from "lucide-react";
import { CommunityModels } from "./CommunityModels";
import { EngineManager } from "./EngineManager";
import { RuntimeConsole } from "./RuntimeConsole";
import { VoiceProfiles, type VoiceProfile, type VoiceProfileDraft } from "./VoiceProfiles";
import { serviceRequest, serviceUrl } from "./serviceClient";
import "./studio-hubs.css";

export type BreezeVoice = { id: string; name: string; engine: "breeze"; parameters: Record<string, unknown>; source?: string; license?: string; consent?: boolean | string };
function HubFrame({ title, description, tabs, activeTab, onTab, children }: { title: string; description: string; tabs: Array<{ id: string; name: string }>; activeTab: string; onTab: (id: string) => void; children: ReactNode }) {
  return <div className="studio-hub"><header className="hub-header"><div><p className="eyebrow">本地语音工作空间</p><h1>{title}</h1><p>{description}</p></div></header><nav className="hub-tabs" aria-label={`${title}分类`}>{tabs.map(tab => <button key={tab.id} onClick={() => onTab(tab.id)} aria-current={activeTab === tab.id ? "page" : undefined}>{tab.name}</button>)}</nav><div className="hub-content">{children}</div></div>;
}

export function VoiceHub({ apiUrl, draft, onDraftConsumed, onUse, onUseBreeze, onBack, initialTab = "local" }: { apiUrl: (path: string) => string; draft: VoiceProfileDraft | null; onDraftConsumed: () => void; onUse: (voice: VoiceProfile) => void; onUseBreeze: (voice: BreezeVoice) => void; onBack: () => void; initialTab?: string }) {
  const [tab, setTab] = useState(initialTab);
  const [communityDraft, setCommunityDraft] = useState<VoiceProfileDraft | null>(null);
  return <HubFrame title="声音" description="声音资料按引擎独立管理，社区模型保留来源与许可信息。" tabs={[{ id: "local", name: "三引擎声音资料" }, { id: "breeze", name: "Breeze 声音" }, { id: "community", name: "GPT 社区模型" }]} activeTab={tab} onTab={setTab}>
    {tab === "breeze" ? <BreezeVoiceLibrary onUse={onUseBreeze} /> : tab === "community" ? <CommunityModels apiUrl={apiUrl} onBack={onBack} onCreateVoice={value => { setCommunityDraft(value); setTab("local"); }} /> : <VoiceProfiles apiUrl={apiUrl} draft={communityDraft ?? draft} onDraftConsumed={() => { setCommunityDraft(null); onDraftConsumed(); }} onUse={onUse} onBack={onBack} />}
  </HubFrame>;
}

function BreezeVoiceLibrary({ onUse }: { onUse: (voice: BreezeVoice) => void }) {
  const [items, setItems] = useState<BreezeVoice[]>([]);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<BreezeVoice | null>(null);
  const [remove, setRemove] = useState<BreezeVoice | null>(null);
  const [busy, setBusy] = useState(false);
  const refresh = () => serviceRequest<{ items: BreezeVoice[] }>("/api/breeze/voices").then(data => setItems(data.items)).catch(reason => setError(String(reason.message ?? reason)));
  useEffect(() => { void refresh(); }, []);
  const blank = (): BreezeVoice => ({ id: "", name: "", engine: "breeze", parameters: { ref_audio_path: "", ref_text: "" }, source: "", license: "", consent: false });
  const save = async () => {
    if (!editing) return;
    setBusy(true); setError("");
    try { const { id } = editing; const body = { name: editing.name, parameters: editing.parameters, source: editing.source ?? "", license: editing.license ?? "", consent: editing.consent ?? false }; await serviceRequest(`/api/breeze/voices${id ? `/${id}` : ""}`, { method: id ? "PUT" : "POST", body }); setEditing(null); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "保存失败"); }
    finally { setBusy(false); }
  };
  const setParam = (key: string, value: string) => setEditing(previous => previous ? { ...previous, parameters: { ...previous.parameters, [key]: value } } : previous);
  return <section className="breeze-voices"><div className="hub-section-heading"><div><h2>Breeze 声音资料</h2><p>参考音频与准确文字成对保存，仅用于 CFG 1 加速克隆。</p></div><button className="primary-button" onClick={() => setEditing(blank())}><Plus size={16} />新建声音资料</button></div>{error && <div className="lf-message error" role="alert"><AlertCircle size={16} /><span>{error}</span></div>}<div className="breeze-voice-grid">{items.length ? items.map(voice => <article className="breeze-voice-card" key={voice.id}><div><span className="hub-engine-symbol"><AudioLines size={21} /></span><strong>{voice.name}</strong><span className="lf-state">Breeze · 加速</span></div><p>{String(voice.parameters.ref_text ?? "未填写参考文字")}</p><dl><dt>来源</dt><dd>{voice.source || "未记录"}</dd><dt>权利说明</dt><dd>{voice.license || "未记录"}</dd><dt>同意情况</dt><dd>{voice.consent === true ? "用户已记录确认" : typeof voice.consent === "string" && voice.consent.trim() ? `未核实 · ${voice.consent}` : "未记录确认"}</dd></dl><div className="breeze-card-actions"><button className="primary-button" onClick={() => onUse(voice)}><Check size={15} />用于创作</button><button className="secondary-button" onClick={() => setEditing(voice)}>编辑</button><button className="icon-button" aria-label={`删除声音 ${voice.name}`} onClick={() => setRemove(voice)}><Trash2 size={16} /></button></div></article>) : <div className="lf-empty"><AudioLines size={32} /><strong>还没有 Breeze 声音资料</strong><p>添加自己的参考音频和准确文字，或在创作页保存临时声音。</p></div>}</div>{editing && <div className="project-confirm-overlay"><section className="hub-voice-dialog" role="dialog" aria-modal="true" aria-labelledby="breeze-voice-title"><header><h2 id="breeze-voice-title">{editing.id ? "编辑" : "新建"} Breeze 声音</h2><button className="icon-button" aria-label="关闭声音编辑" onClick={() => setEditing(null)}><X size={17} /></button></header><div className="hub-form"><label>声音名称<input value={editing.name} onChange={e => setEditing(v => v ? { ...v, name: e.target.value } : v)} /></label><label>参考音频路径<div className="lf-inline"><input value={String(editing.parameters.ref_audio_path ?? "")} onChange={e => setParam("ref_audio_path", e.target.value)} placeholder="任务服务所在电脑的绝对路径" /><button className="secondary-button" onClick={async () => { const file = await window.langbaiDesktop?.chooseFile({ filters: [{ name: "参考音频", extensions: ["wav", "flac", "mp3"] }] }); if (file) setParam("ref_audio_path", file); }} disabled={!window.langbaiDesktop}><FolderOpen size={15} /></button></div></label><label>准确参考文字<textarea value={String(editing.parameters.ref_text ?? "")} onChange={e => setParam("ref_text", e.target.value)} rows={3} /></label><label>音频来源<input value={editing.source ?? ""} onChange={e => setEditing(v => v ? { ...v, source: e.target.value } : v)} /></label><label>权利或许可说明<input value={editing.license ?? ""} onChange={e => setEditing(v => v ? { ...v, license: e.target.value } : v)} /></label><label className="lf-check"><input type="checkbox" checked={editing.consent === true} onChange={e => setEditing(v => v ? { ...v, consent: e.target.checked } : v)} />已取得所需权利与同意</label>{typeof editing.consent === "string" && editing.consent.trim() && <small className="hub-consent-note">原始说明：{editing.consent}。尚未作为已获同意的确认。</small>}</div><footer><button className="secondary-button" onClick={() => setEditing(null)}>取消</button><button className="primary-button" disabled={busy} onClick={() => void save()}><Save size={15} />保存资料</button></footer></section></div>}{remove && <div className="project-confirm-overlay"><section className="project-confirm-dialog" role="alertdialog" aria-modal="true"><h3>删除声音资料“{remove.name}”？</h3><p>只删除资料记录，原始参考音频保留。</p><div><button className="secondary-button" onClick={() => setRemove(null)}>取消</button><button className="danger-button" onClick={async () => { try { await serviceRequest(`/api/breeze/voices/${remove.id}`, { method: "DELETE" }); setRemove(null); await refresh(); } catch (reason) { setError(String(reason)); } }}>删除资料</button></div></section></div>}</section>;
}

export function EngineHub({ apiUrl, onBack, density, onDensityChange, initialTab = "engines" }: { apiUrl: (path: string) => string; onBack: () => void; density: "comfortable" | "compact"; onDensityChange: () => void; initialTab?: string }) {
  const [tab, setTab] = useState(initialTab);
  return <HubFrame title="引擎" description="管理本地安装、WSL 加速绑定和运行日志。每次只让一个重度任务使用显卡。" tabs={[{ id: "engines", name: "三引擎安装与绑定" }, { id: "breeze", name: "Breeze · WSL" }, { id: "runtime", name: "运行终端" }]} activeTab={tab} onTab={setTab}>{tab === "runtime" ? <RuntimeConsole apiUrl={apiUrl} onBack={onBack} /> : tab === "breeze" ? <BreezeEnginePanel /> : <EngineManager onBack={onBack} density={density} onDensityChange={onDensityChange} viewMode="engines" />}</HubFrame>;
}

function BreezeEnginePanel() {
  const [config, setConfig] = useState({ distribution: "Ubuntu", sourcePath: "F:\\AI\\Breeze-TTS-2", pythonPath: "/home/langbai666/.venvs/breeze-tts-2/bin/python", modelPath: "F:\\AI\\Breeze-TTS-2\\checkpoints\\Breeze-TTS-2" });
  const [status, setStatus] = useState<Record<string, unknown>>({});
  const [health, setHealth] = useState<Record<string, unknown>>({});
  const [webRuntime, setWebRuntime] = useState<Record<string, unknown> | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [message, setMessage] = useState("");
  const [hasError, setHasError] = useState(false);
  const [busy, setBusy] = useState(false);
  const refresh = async () => {
    try {
      const result = await serviceRequest<{ binding?: Record<string, unknown>; defaults?: Record<string, unknown>; status?: Record<string, unknown> }>("/api/breeze/binding");
      const binding = result.binding ?? result.defaults ?? {};
      setConfig(previous => ({ ...previous, ...Object.fromEntries(Object.keys(previous).filter(key => typeof binding[key] === "string").map(key => [key, String(binding[key])])) }));
      setStatus(result.status ?? {});
      if (binding.licenseAccepted === true && binding.licenseVersion === "Breeze TTS 2 Model License v1.1 (2026-09-01)") setAccepted(true);
      setHealth(await serviceRequest<Record<string, unknown>>("/health"));
    } catch (reason) { setHasError(true); setMessage(reason instanceof Error ? reason.message : "状态读取失败"); }
  };
  useEffect(() => { void refresh(); }, []);
  const save = async () => { setBusy(true); setMessage(""); setHasError(false); try { await serviceRequest("/api/breeze/binding", { method: "PUT", body: { ...config, confirm: true, licenseAccepted: accepted } }); setMessage("绑定已保存。生成前会核验模型、源码和加速补丁。"); await refresh(); } catch (reason) { setHasError(true); setMessage(reason instanceof Error ? reason.message : "绑定失败"); } finally { setBusy(false); } };
  const webAction = async (unload: boolean) => {
    setBusy(true); setMessage(""); setHasError(false);
    try {
      const result = await serviceRequest<Record<string, unknown>>(unload ? "/api/breeze/web-unload" : "/api/breeze/web-runtime", unload ? { method: "POST" } : {});
      setWebRuntime(unload ? { ...result, available: true } : result);
      if (unload) setMessage("已清理独立网页的模型引用；请查看状态和实际显存，任务服务未被停止。");
    } catch (reason) { setHasError(true); setMessage(reason instanceof Error ? reason.message : "网页模型操作失败"); }
    finally { setBusy(false); }
  };
  const unloadStudio = async () => {
    setBusy(true); setMessage(""); setHasError(false);
    try { await serviceRequest("/api/breeze/unload", { method: "POST" }); setMessage("Studio 已确认自己的 Breeze worker 退出。独立网页和其他引擎未被结束。"); await refresh(); }
    catch (reason) { setHasError(true); setMessage(reason instanceof Error ? reason.message : "释放模型失败，请先暂停活动任务。"); }
    finally { setBusy(false); }
  };
  return <div className="breeze-engine-panel"><div className="hub-engine-overview"><span className="hub-engine-symbol"><Zap size={25} /></span><div><h2>Breeze TTS 2</h2><p>已验证加速配置 · 参考音频克隆 · CFG 1 · 24kHz</p></div><span className={`install-state ${status.available ? "installed" : "missing"}`}>{status.available ? "已就绪" : "待配置"}</span><button className="secondary-button" onClick={() => void refresh()}><RefreshCw size={15} />检测</button></div>{message && <div className={`lf-message ${hasError ? "error" : "success"}`} role={hasError ? "alert" : "status"}>{message}</div>}<div className="hub-engine-layout"><section className="hub-card"><div className="hub-section-heading"><div><h3>只读绑定已有 WSL 安装</h3><p>{String(status.detail ?? "绑定不会修改你的源码、权重或独立网页安装。")}</p></div><Cpu size={20} /></div><div className="hub-form">{Object.entries({ distribution: "WSL 发行版", sourcePath: "Windows 源码目录", pythonPath: "WSL Python 可执行文件", modelPath: "Windows 模型目录" }).map(([key, label]) => <label key={key}>{label}<input value={config[key as keyof typeof config]} onChange={e => setConfig(v => ({ ...v, [key]: e.target.value }))} /></label>)}<label className="lf-check"><input type="checkbox" checked={accepted} onChange={e => setAccepted(e.target.checked)} />我已阅读并接受模型权重的研究、非商业使用范围</label><button className="primary-button" onClick={() => void save()} disabled={busy || !accepted}><Save size={15} />保存并核验绑定</button></div></section><div><section className="hub-card"><div className="hub-section-heading"><div><h3>显卡与模型释放</h3><p>先暂停长文任务，再显式释放对应进程的热模型。</p></div><Cpu size={18} /></div><button className="secondary-button" disabled={busy} onClick={() => void unloadStudio()}>释放 Studio 热模型</button><hr /><p className="hub-runtime-description">独立 Breeze 网页试听模型：{webRuntime ? webRuntime.available ? webRuntime.loaded ? "已加载" : "未加载" : "不可用 / 尚未更新协作桥" : "尚未查询"}</p>{typeof webRuntime?.detail === "string" && <p className="hub-runtime-description">{String(webRuntime.detail)}</p>}{Array.isArray(webRuntime?.warnings) && <p className="hub-runtime-description">{webRuntime.warnings.map(String).join("；")}</p>}<div className="hub-runtime-actions"><button className="secondary-button" disabled={busy} onClick={() => void webAction(false)}>查看网页模型状态</button><button className="secondary-button" disabled={busy || webRuntime?.available !== true || webRuntime?.loaded !== true} onClick={() => void webAction(true)}>释放网页试听模型</button></div><small className="hub-runtime-description">只会在点击按钮后卸载；网页未运行或旧版不支持时会说明原因。</small></section><section className="hub-card"><div className="hub-section-heading"><div><h3>共享长文网页</h3><p>关闭桌面窗口后仍可按退出时的选择继续任务。</p></div><ExternalLink size={18} /></div><dl className="hub-status-list"><dt>服务版本</dt><dd>{String(health.version ?? "无记录")}</dd><dt>绑定模式</dt><dd>WSL · accelerated</dd><dt>任务状态</dt><dd>与桌面端共用</dd></dl><a className="secondary-button" href={serviceUrl("/longform")} target="_blank" rel="noreferrer"><ExternalLink size={15} />打开长文网页</a></section><section className="hub-card hub-license-card"><ShieldCheck size={20} /><h3>模型与素材许可</h3><p>Breeze 权重的许可独立于 Studio 的 MIT 代码许可。商用需上游书面授权；安装包不包含权重和角色原音。</p><p>当前只开放已验证的加速参考克隆，其他模式将经过单独验证后提供。</p><a href="https://huggingface.co/BreezeBlue/Breeze-TTS-2/blob/3e28c5151381a722f1d8661b4118c298caa77aa4/LICENSE" target="_blank" rel="noreferrer">查看模型许可 v1.1 <ExternalLink size={12} /></a></section></div></div></div>;
}
