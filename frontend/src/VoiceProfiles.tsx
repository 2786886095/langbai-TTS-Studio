import { useEffect, useMemo, useState } from "react";
import {
  AudioLines, ChevronLeft, FolderOpen, Library, LoaderCircle, Pencil,
  Plus, Save, Search, Trash2, UserRoundCheck, X,
} from "lucide-react";
import { engines, type EngineId } from "./parameterSchemas";

export type VoiceProfile = {
  id: string;
  name: string;
  engine: EngineId;
  description: string;
  parameters: Record<string, unknown>;
  sourceModel?: Record<string, unknown> | null;
  updatedAt?: string;
};

export type VoiceProfileDraft = {
  engine: EngineId;
  name?: string;
  description?: string;
  parameters: Record<string, unknown>;
  sourceModel?: Record<string, unknown> | null;
};

type Props = {
  apiUrl: (path: string) => string;
  draft?: VoiceProfileDraft | null;
  onDraftConsumed?: () => void;
  onUse: (profile: VoiceProfile) => void;
  onBack: () => void;
};

const engineOrder: EngineId[] = ["indextts2", "voxcpm", "gpt_sovits"];

function pickFile(kind: "audio" | "gpt" | "sovits") {
  const filters = kind === "audio"
    ? [{ name: "音频文件", extensions: ["wav", "mp3", "flac", "ogg", "m4a"] }]
    : kind === "gpt"
      ? [{ name: "GPT 权重", extensions: ["ckpt"] }]
      : [{ name: "SoVITS 权重", extensions: ["pth"] }];
  return window.langbaiDesktop?.chooseFile({ filters });
}

function PathField({ label, value, kind, onChange, help }: { label: string; value: unknown; kind: "audio" | "gpt" | "sovits"; onChange: (value: string) => void; help: string }) {
  return <label className="voice-form-field voice-path-field"><span>{label}</span><button type="button" onClick={async () => { const path = await pickFile(kind); if (path) onChange(path); }}><FolderOpen size={15} /><b title={String(value || "")}>{value ? String(value) : "选择本地文件"}</b></button><small>{help}</small></label>;
}

function VoiceFields({ engine, values, onChange }: { engine: EngineId; values: Record<string, unknown>; onChange: (key: string, value: unknown) => void }) {
  if (engine === "indextts2") return <>
    <PathField label="音色参考音频" value={values.spk_audio_prompt} kind="audio" onChange={value => onChange("spk_audio_prompt", value)} help="这个角色的核心音色来源；建议使用干净的单人语音。" />
    <PathField label="情感参考音频（可选）" value={values.emo_audio_prompt} kind="audio" onChange={value => onChange("emo_audio_prompt", value)} help="只保存表达方式，不会替换音色参考。" />
    <label className="voice-form-field"><span>情感权重</span><input type="number" min="0" max="1" step="0.05" value={Number(values.emo_alpha ?? 0.65)} onChange={event => onChange("emo_alpha", Number(event.target.value))} /><small>数值越高，情感提示影响越明显。</small></label>
  </>;
  if (engine === "voxcpm") return <>
    <label className="voice-form-field"><span>声音模式</span><select value={String(values.mode ?? "可控音色克隆")} onChange={event => onChange("mode", event.target.value)}><option>可控音色克隆</option><option>极致克隆</option><option>音色设计</option><option>普通合成</option></select><small>克隆模式使用参考音频；音色设计使用文字指令。</small></label>
    <PathField label="音色参考音频" value={values.reference_wav_path} kind="audio" onChange={value => onChange("reference_wav_path", value)} help="可控克隆与极致克隆必填。" />
    <label className="voice-form-field"><span>音色 / 风格指令</span><textarea value={String(values.voice_instruction ?? "")} onChange={event => onChange("voice_instruction", event.target.value)} placeholder="例如：年轻、清亮、温柔的中文女声" /><small>音色设计时它是主要依据，克隆时用于控制表达。</small></label>
  </>;
  return <>
    <div className="voice-pair-note"><AudioLines size={17} /><span><strong>成对角色权重</strong><small>GPT 与 SoVITS 权重必须来自同一个角色包；参考音频和文本也会一起保存。</small></span></div>
    <PathField label="GPT 权重（.ckpt）" value={values.gpt_weights_path} kind="gpt" onChange={value => onChange("gpt_weights_path", value)} help="负责语义与韵律建模。" />
    <PathField label="SoVITS 权重（.pth）" value={values.sovits_weights_path} kind="sovits" onChange={value => onChange("sovits_weights_path", value)} help="负责角色声学音色。" />
    <label className="voice-form-field"><span>模型版本</span><select value={String(values.version ?? "auto")} onChange={event => onChange("version", event.target.value)}><option value="auto">自动检测</option><option>v2</option><option>v3</option><option>v4</option><option>v2Pro</option><option>v2ProPlus</option></select><small>无法确认社区权重版本时保持自动检测。</small></label>
    <PathField label="参考音频" value={values.ref_audio_path} kind="audio" onChange={value => onChange("ref_audio_path", value)} help="建议 3–10 秒、清晰、无背景音乐。" />
    <label className="voice-form-field"><span>参考音频精确文本</span><textarea value={String(values.prompt_text ?? "")} onChange={event => onChange("prompt_text", event.target.value)} placeholder="逐字填写参考音频内容" /><small>错字或漏字会直接影响克隆相似度。</small></label>
    <label className="voice-form-field"><span>参考语言</span><select value={String(values.prompt_lang ?? "中文")} onChange={event => onChange("prompt_lang", event.target.value)}><option>中文</option><option>英文</option><option>日文</option><option>韩文</option><option>粤语</option></select><small>应与参考音频实际语言一致。</small></label>
  </>;
}

export function VoiceProfiles({ apiUrl, draft, onDraftConsumed, onUse, onBack }: Props) {
  const [items, setItems] = useState<VoiceProfile[]>([]);
  const [filter, setFilter] = useState<EngineId | "all">("all");
  const [query, setQuery] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [engine, setEngine] = useState<EngineId>("indextts2");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [parameters, setParameters] = useState<Record<string, unknown>>({});
  const [sourceModel, setSourceModel] = useState<Record<string, unknown> | null>(null);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const refresh = async () => {
    try {
      const response = await fetch(apiUrl("/api/voice-profiles"));
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json() as { items?: VoiceProfile[] };
      setItems(payload.items ?? []);
    } catch (error) { setMessage(`无法读取角色声音：${error instanceof Error ? error.message : "未知错误"}`); }
    finally { setLoading(false); }
  };

  useEffect(() => { void refresh(); }, []);
  useEffect(() => {
    if (!draft) return;
    setEngine(draft.engine); setName(draft.name ?? ""); setDescription(draft.description ?? "");
    setParameters({ ...draft.parameters }); setSourceModel(draft.sourceModel ?? null);
    setEditingId(null); setFormOpen(true); setFilter(draft.engine); onDraftConsumed?.();
  }, [draft]);

  const visible = useMemo(() => items.filter(item => (filter === "all" || item.engine === filter) && (!query || `${item.name} ${item.description}`.toLowerCase().includes(query.toLowerCase()))), [items, filter, query]);
  const beginCreate = (target: EngineId = filter === "all" ? "indextts2" : filter) => { setEditingId(null); setEngine(target); setName(""); setDescription(""); setParameters({ mode: "可控音色克隆", version: "auto", prompt_lang: "中文" }); setSourceModel(null); setFormOpen(true); setMessage(""); };
  const beginEdit = (item: VoiceProfile) => { setEditingId(item.id); setEngine(item.engine); setName(item.name); setDescription(item.description ?? ""); setParameters({ ...item.parameters }); setSourceModel(item.sourceModel ?? null); setFormOpen(true); setMessage(""); };

  const save = async () => {
    if (!name.trim()) { setMessage("请填写角色声音名称。"); return; }
    setSaving(true); setMessage("");
    try {
      const body = editingId ? { name: name.trim(), description, parameters, sourceModel } : { name: name.trim(), engine, description, parameters, sourceModel };
      const response = await fetch(apiUrl(editingId ? `/api/voice-profiles/${editingId}` : "/api/voice-profiles"), { method: editingId ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!response.ok) { const details = await response.json().catch(() => null) as { detail?: string | Array<{ msg?: string }> } | null; throw new Error(Array.isArray(details?.detail) ? details.detail.map(item => item.msg).join("；") : details?.detail || `HTTP ${response.status}`); }
      setFormOpen(false); setMessage(editingId ? "角色声音已更新。" : "角色声音已独立保存。"); await refresh();
    } catch (error) { setMessage(error instanceof Error ? error.message : "保存失败"); }
    finally { setSaving(false); }
  };

  const remove = async (item: VoiceProfile) => {
    if (!window.confirm(`删除角色声音“${item.name}”？只会删除资料库配置，不会删除权重和参考音频。`)) return;
    const response = await fetch(apiUrl(`/api/voice-profiles/${item.id}`), { method: "DELETE" });
    if (response.ok) { setMessage("角色声音配置已删除，本地模型文件未改动。"); await refresh(); }
    else setMessage("删除失败，请稍后重试。");
  };

  return <div className="library-page voice-library-page">
    <header className="library-topbar"><div><button className="back-link" onClick={onBack}><ChevronLeft size={14} />返回创作台</button><p className="eyebrow">角色资产</p><h1>角色声音资料库</h1><p>每个引擎独立保存声音配置；切换角色时不会改动其他引擎的参数。</p></div><button className="primary-button" onClick={() => beginCreate()}><Plus size={16} />新建角色声音</button></header>
    {message && <div className="manager-notice"><Library size={16} /><span>{message}</span><button onClick={() => setMessage("")}><X size={14} /></button></div>}
    <div className="voice-toolbar"><div className="voice-engine-tabs"><button className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>全部 <b>{items.length}</b></button>{engineOrder.map(id => <button key={id} className={filter === id ? "active" : ""} onClick={() => setFilter(id)}>{engines[id].name} <b>{items.filter(item => item.engine === id).length}</b></button>)}</div><label><Search size={15} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索角色声音" /></label></div>
    {loading ? <div className="voice-empty"><LoaderCircle className="spin" size={24} /><strong>正在读取角色声音</strong></div> : visible.length === 0 ? <div className="voice-empty"><UserRoundCheck size={28} /><strong>还没有匹配的角色声音</strong><span>从当前引擎的参考音频或 GPT‑SoVITS 权重组合新建一个。</span><button className="secondary-button" onClick={() => beginCreate()}>新建角色声音</button></div> : <div className="voice-card-grid">{visible.map(item => <article className="voice-card" key={item.id}><div className="voice-card-head"><span className="voice-avatar"><AudioLines size={20} /></span><span className="voice-engine-chip">{engines[item.engine].name}</span></div><h2>{item.name}</h2><p>{item.description || (item.engine === "gpt_sovits" ? "双权重与参考音频组合" : "参考音频声音角色")}</p><div className="voice-card-meta">{item.engine === "gpt_sovits" ? <><span>GPT + SoVITS 成对权重</span><span>{String(item.parameters.version ?? "未标版本")}</span></> : <span title={String(item.parameters.spk_audio_prompt || item.parameters.reference_wav_path || "")}>{item.engine === "indextts2" ? "音色参考音频" : String(item.parameters.mode ?? "声音配置")}</span>}</div><div className="voice-card-actions"><button className="primary-button" onClick={() => onUse(item)}><UserRoundCheck size={15} />用于创作台</button><button className="icon-button" onClick={() => beginEdit(item)} title="编辑"><Pencil size={15} /></button><button className="icon-button danger" onClick={() => void remove(item)} title="删除"><Trash2 size={15} /></button></div></article>)}</div>}
    {formOpen && <div className="voice-editor-overlay"><section className="voice-editor" role="dialog" aria-modal="true" aria-labelledby="voice-editor-title"><header><div><p className="eyebrow">{editingId ? "编辑角色" : "新建角色"}</p><h2 id="voice-editor-title">{editingId ? name : "保存一套可复用的声音"}</h2></div><button className="icon-button" onClick={() => setFormOpen(false)}><X size={17} /></button></header><div className="voice-editor-scroll"><label className="voice-form-field"><span>使用引擎</span><select value={engine} disabled={Boolean(editingId)} onChange={event => { setEngine(event.target.value as EngineId); setParameters({ mode: "可控音色克隆", version: "auto", prompt_lang: "中文" }); }}><option value="indextts2">IndexTTS 2 · 参考音频</option><option value="voxcpm">VoxCPM 2 · 参考音频 / 音色设计</option><option value="gpt_sovits">GPT-SoVITS · 双权重 + 参考音频</option></select><small>角色保存后引擎不可更改，避免配置混用。</small></label><label className="voice-form-field"><span>角色声音名称</span><input value={name} onChange={event => setName(event.target.value)} placeholder="例如：芙宁娜 · 中文自然" /><small>建议包含角色、语言或风格，便于以后搜索。</small></label><label className="voice-form-field"><span>备注（可选）</span><textarea value={description} onChange={event => setDescription(event.target.value)} placeholder="记录适合的内容类型、情绪或来源" /></label>{sourceModel && <div className="voice-source-note"><Library size={16} /><span><strong>来自社区模型</strong><small>{String(sourceModel.name ?? "已安装模型")} · 保存时会保留来源信息。</small></span></div>}<VoiceFields engine={engine} values={parameters} onChange={(key, value) => setParameters(current => ({ ...current, [key]: value }))} /></div><footer><button className="secondary-button" onClick={() => setFormOpen(false)}>取消</button><button className="primary-button" onClick={() => void save()} disabled={saving}>{saving ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}{saving ? "正在保存" : "保存角色声音"}</button></footer></section></div>}
  </div>;
}
