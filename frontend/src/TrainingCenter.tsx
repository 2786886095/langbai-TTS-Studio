import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, BookOpen, CheckCircle2, ChevronDown, ChevronLeft, Circle, CircleStop,
  Cpu, FileJson2, FolderOpen, Gauge, GraduationCap, LoaderCircle, Play,
  RefreshCw, RotateCw, Save, SlidersHorizontal, Sparkles,
} from "lucide-react";

type Capability = {
  available: boolean; detail: string; modes: string[]; projectPath?: string; pythonPath?: string;
  pretrainedPath?: string; defaultOutputDir?: string;
};

type TrainingTask = {
  id: string; name: string; engine: "voxcpm"; mode: "lora" | "sft";
  status: "queued" | "running" | "stopping" | "completed" | "failed" | "cancelled" | "interrupted";
  pid?: number | null; progress: number; currentStep: number; maxSteps: number; samples: number;
  outputDir: string; logPath: string; command: string[]; logLines: string[]; error?: string | null;
};

type FormState = {
  name: string; mode: "lora" | "sft"; pretrainedPath: string; trainManifest: string;
  valManifest: string; outputDir: string; batchSize: number; gradAccumSteps: number;
  maxSteps: number; saveInterval: number; validInterval: number; learningRate: number;
  warmupSteps: number; numWorkers: number; maxBatchTokens: number; maxGradNorm: number;
  loraRank: number; loraAlpha: number; loraDropout: number;
  enableLm: boolean; enableDit: boolean; enableProj: boolean;
};

const initialForm: FormState = {
  name: "VoxCPM2 角色训练", mode: "lora", pretrainedPath: "", trainManifest: "", valManifest: "",
  outputDir: "", batchSize: 2, gradAccumSteps: 8, maxSteps: 1000, saveInterval: 500,
  validInterval: 500, learningRate: 0.0001, warmupSteps: 100, numWorkers: 4,
  maxBatchTokens: 8192, maxGradNorm: 1, loraRank: 32, loraAlpha: 32,
  loraDropout: 0, enableLm: true, enableDit: true, enableProj: false,
};

const statusLabel: Record<TrainingTask["status"], string> = {
  queued: "准备中", running: "训练中", stopping: "正在停止", completed: "已完成",
  failed: "失败", cancelled: "已停止", interrupted: "被中断",
};

export function TrainingCenter({ apiUrl, onBack }: { apiUrl: (path: string) => string; onBack: () => void }) {
  const [capability, setCapability] = useState<Capability | null>(null);
  const [form, setForm] = useState<FormState>(initialForm);
  const [tasks, setTasks] = useState<TrainingTask[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const logRef = useRef<HTMLPreElement>(null);

  const load = async () => {
    try {
      const [capabilityResponse, tasksResponse] = await Promise.all([
        fetch(apiUrl("/api/training/capabilities")), fetch(apiUrl("/api/training/tasks")),
      ]);
      if (!capabilityResponse.ok || !tasksResponse.ok) throw new Error("训练服务暂时不可用");
      const capabilityPayload = await capabilityResponse.json() as { voxcpm?: Capability };
      const tasksPayload = await tasksResponse.json() as { items?: TrainingTask[] };
      const nextCapability = capabilityPayload.voxcpm ?? null;
      setCapability(nextCapability);
      if (nextCapability) setForm(current => ({
        ...current,
        pretrainedPath: current.pretrainedPath || nextCapability.pretrainedPath || "",
        outputDir: current.outputDir || nextCapability.defaultOutputDir || "",
      }));
      const rows = tasksPayload.items ?? [];
      setTasks(rows);
      setSelectedId(current => current || rows[0]?.id || "");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "无法读取训练状态");
    }
  };

  useEffect(() => {
    let disposed = false; let timer = 0;
    const poll = async () => { await load(); if (!disposed) timer = window.setTimeout(poll, document.hidden ? 15000 : 2000); };
    void poll();
    return () => { disposed = true; if (timer) window.clearTimeout(timer); };
  }, []);

  const selected = useMemo(() => tasks.find(item => item.id === selectedId) ?? tasks[0], [tasks, selectedId]);
  const activeTask = tasks.find(item => ["queued", "running", "stopping"].includes(item.status));
  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [selected?.logLines]);

  const patch = <K extends keyof FormState>(key: K, value: FormState[K]) => setForm(current => ({ ...current, [key]: value }));
  const chooseDirectory = async (key: "pretrainedPath" | "outputDir") => {
    const selectedPath = await window.langbaiDesktop?.chooseDirectory?.();
    if (selectedPath) patch(key, selectedPath);
  };
  const chooseManifest = async (key: "trainManifest" | "valManifest") => {
    const selectedPath = await window.langbaiDesktop?.chooseFile({ filters: [{ name: "VoxCPM 数据清单", extensions: ["jsonl", "json"] }] });
    if (selectedPath) patch(key, selectedPath);
  };

  const startTraining = async () => {
    setBusy("start"); setMessage("");
    try {
      const response = await fetch(apiUrl("/api/training/tasks"), {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(form),
      });
      const payload = await response.json().catch(() => null) as TrainingTask & { detail?: string } | null;
      if (!response.ok) throw new Error(payload?.detail || `HTTP ${response.status}`);
      if (payload) setSelectedId(payload.id);
      setMessage("训练任务已启动，关闭页面不会停止训练。退出软件时会进行保护确认。");
      await load();
    } catch (error) { setMessage(error instanceof Error ? error.message : "训练启动失败"); }
    finally { setBusy(""); }
  };

  const taskAction = async (task: TrainingTask, action: "stop" | "resume") => {
    setBusy(`${action}:${task.id}`); setMessage("");
    try {
      const response = await fetch(apiUrl(`/api/training/tasks/${task.id}/${action}`), { method: "POST" });
      const payload = await response.json().catch(() => null) as { detail?: string } | null;
      if (!response.ok) throw new Error(payload?.detail || `HTTP ${response.status}`);
      setMessage(action === "stop" ? "正在请求安全停止，官方训练器会尽量保存当前检查点。" : "已从最近检查点继续训练。");
      await load();
    } catch (error) { setMessage(error instanceof Error ? error.message : "操作失败"); }
    finally { setBusy(""); }
  };

  return <div className="library-page training-page">
    <header className="library-topbar"><div><button className="back-link" onClick={onBack}><ChevronLeft size={14} />返回选择训练引擎</button><p className="eyebrow">本地模型训练</p><h1>VoxCPM2 训练中心</h1><p>从数据清单到 LoRA 或全量 SFT，训练进度、日志和检查点都保存在本地。</p></div><button className="secondary-button" onClick={() => void load()}><RefreshCw size={15} />刷新状态</button></header>

    {message && <div className={`manager-notice ${/(失败|不存在|无法|占用|缺少)/.test(message) ? "warning" : ""}`}><AlertTriangle size={16} /><span>{message}</span></div>}

    <div className="training-flow-strip"><span className="active"><b>01</b><i><strong>准备数据</strong><small>JSONL 音频与文本</small></i></span><span><b>02</b><i><strong>选择训练策略</strong><small>LoRA 或全量 SFT</small></i></span><span><b>03</b><i><strong>观察并导出</strong><small>日志、检查点与恢复</small></i></span></div>

    <div className="training-workspace">
      <section className="training-config-panel">
        <div className="training-panel-title"><div><p className="eyebrow">训练方案</p><h2>创建 VoxCPM2 训练任务</h2></div><span className={capability?.available ? "ready" : "warning"}>{capability?.available ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}{capability?.available ? "官方入口已检测" : "需要检查路径"}</span></div>
        {capability && <p className="training-capability-note">{capability.detail}</p>}

        <div className="training-mode-grid">
          <button className={form.mode === "lora" ? "active" : ""} onClick={() => { patch("mode", "lora"); patch("learningRate", 0.0001); }}><span><Sparkles size={20} /></span><strong>LoRA 微调</strong><small>显存占用更低，适合角色音色和快速迭代</small><i>推荐</i></button>
          <button className={form.mode === "sft" ? "active" : ""} onClick={() => { patch("mode", "sft"); patch("learningRate", 0.00001); }}><span><Cpu size={20} /></span><strong>全量 SFT</strong><small>更新完整模型参数，需要更多显存和训练时间</small></button>
        </div>

        <div className="training-form-grid">
          <label><span>训练名称</span><input value={form.name} onChange={event => patch("name", event.target.value)} /></label>
          <label className="training-path-field"><span>基础模型目录</span><button onClick={() => void chooseDirectory("pretrainedPath")}><FolderOpen size={15} /><b title={form.pretrainedPath}>{form.pretrainedPath || "选择包含 config.json 的 VoxCPM2 模型"}</b></button></label>
          <label className="training-path-field"><span>训练数据清单 <em>必填</em></span><button onClick={() => void chooseManifest("trainManifest")}><FileJson2 size={15} /><b title={form.trainManifest}>{form.trainManifest || "选择 train.jsonl"}</b></button><small>每行必须包含 `audio` 与 `text`，音频建议累计 5–10 分钟以上。</small></label>
          <label className="training-path-field"><span>验证数据清单 <em>可选</em></span><button onClick={() => void chooseManifest("valManifest")}><FileJson2 size={15} /><b title={form.valManifest}>{form.valManifest || "不使用独立验证集"}</b></button></label>
          <label className="training-path-field"><span>检查点保存目录</span><button onClick={() => void chooseDirectory("outputDir")}><Save size={15} /><b title={form.outputDir}>{form.outputDir || "选择输出目录"}</b></button></label>
        </div>

        <div className="training-parameter-grid">
          <NumberField label="批大小" value={form.batchSize} min={1} step={1} onChange={value => patch("batchSize", value)} help="单步送入显卡的样本数；显存不足时调小。" />
          <NumberField label="梯度累积" value={form.gradAccumSteps} min={1} step={1} onChange={value => patch("gradAccumSteps", value)} help={`有效批大小：${form.batchSize * form.gradAccumSteps}`} />
          <NumberField label="训练步数" value={form.maxSteps} min={1} step={100} onChange={value => patch("maxSteps", value)} help="达到该步数后自动结束。" />
          <NumberField label="保存间隔" value={form.saveInterval} min={1} step={100} onChange={value => patch("saveInterval", value)} help="每隔多少步写入可恢复检查点。" />
          <NumberField label="学习率" value={form.learningRate} min={0.000001} step={0.000001} onChange={value => patch("learningRate", value)} help={form.mode === "lora" ? "LoRA 官方默认 0.0001。" : "全量 SFT 官方默认 0.00001。"} />
        </div>

        <button className={`training-advanced-trigger ${advanced ? "open" : ""}`} onClick={() => setAdvanced(value => !value)}><span><SlidersHorizontal size={16} /><strong>高级训练参数</strong><small>显存、收敛与 LoRA 模块</small></span><ChevronDown size={17} /></button>
        {advanced && <div className="training-advanced-grid">
          <NumberField label="预热步数" value={form.warmupSteps} min={0} step={10} onChange={value => patch("warmupSteps", value)} help="逐步提升学习率，降低训练初期震荡。" />
          <NumberField label="数据线程" value={form.numWorkers} min={0} step={1} onChange={value => patch("numWorkers", value)} help="读取音频的并行线程数。" />
          <NumberField label="最大批 Token" value={form.maxBatchTokens} min={0} step={512} onChange={value => patch("maxBatchTokens", value)} help="过滤过长样本，降低显存溢出概率。" />
          <NumberField label="梯度裁剪" value={form.maxGradNorm} min={0} step={0.1} onChange={value => patch("maxGradNorm", value)} help="限制梯度幅度；0 表示关闭。" />
          {form.mode === "lora" && <><NumberField label="LoRA Rank" value={form.loraRank} min={1} step={8} onChange={value => patch("loraRank", value)} help="容量越大，参数与显存占用越高。" /><NumberField label="LoRA Alpha" value={form.loraAlpha} min={1} step={8} onChange={value => patch("loraAlpha", value)} help="LoRA 更新强度缩放。" /><NumberField label="LoRA Dropout" value={form.loraDropout} min={0} step={0.01} onChange={value => patch("loraDropout", value)} help="小数据集可适度增加以抑制过拟合。" /><div className="training-module-toggles"><strong>训练模块</strong><Toggle label="语言模型 LM" checked={form.enableLm} onChange={value => patch("enableLm", value)} /><Toggle label="声学 DiT" checked={form.enableDit} onChange={value => patch("enableDit", value)} /><Toggle label="投影层" checked={form.enableProj} onChange={value => patch("enableProj", value)} /></div></>}
        </div>}

        <div className="training-launch-bar"><div><Gauge size={18} /><span><strong>{form.mode === "lora" ? "LoRA 微调" : "全量 SFT"}</strong><small>{form.maxSteps} 步 · 有效批大小 {form.batchSize * form.gradAccumSteps} · 本地保存</small></span></div><button className="primary-button" disabled={Boolean(activeTask) || busy === "start" || !form.trainManifest || !form.pretrainedPath || !form.outputDir} onClick={() => void startTraining()}>{busy === "start" ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />}{activeTask ? "已有训练运行" : "开始训练"}</button></div>
      </section>

      <aside className="training-monitor-panel">
        <div className="training-monitor-head"><div><p className="eyebrow">实时任务</p><h2>训练进度与日志</h2></div><GraduationCap size={23} /></div>
        <div className="training-task-list">{tasks.length ? tasks.map(task => <button key={task.id} className={selected?.id === task.id ? "active" : ""} onClick={() => setSelectedId(task.id)}><Circle className={`training-task-dot ${task.status}`} size={9} fill="currentColor" /><i><strong>{task.name}</strong><small>{task.mode === "lora" ? "LoRA" : "全量 SFT"} · {statusLabel[task.status]}</small></i><b>{Math.round((task.progress || 0) * 100)}%</b></button>) : <div className="training-empty"><GraduationCap size={26} /><strong>还没有训练任务</strong><span>配置数据和参数后，训练日志会显示在这里。</span></div>}</div>
        {selected && <div className="training-selected">
          <div className="training-progress-summary"><div><strong>{statusLabel[selected.status]}</strong><span>{selected.currentStep} / {selected.maxSteps} 步 · {selected.samples} 个样本{selected.pid ? ` · PID ${selected.pid}` : ""}</span></div><b>{Math.round((selected.progress || 0) * 100)}%</b><div><i style={{ width: `${Math.round((selected.progress || 0) * 100)}%` }} /></div></div>
          {selected.error && <div className="training-error"><AlertTriangle size={14} />{selected.error}</div>}
          <div className="training-log-window"><header><Circle size={8} fill="#d97a61" color="#d97a61" /><Circle size={8} fill="#ddb25b" color="#ddb25b" /><Circle size={8} fill="#59b584" color="#59b584" /><strong>VoxCPM2 / train.log</strong></header><pre ref={logRef}>{selected.logLines?.length ? selected.logLines.join("\n") : "等待训练器输出日志…"}</pre></div>
          <div className="training-task-actions"><button className="secondary-button" onClick={() => window.langbaiDesktop?.showItemInFolder?.(selected.outputDir)}><FolderOpen size={15} />打开检查点</button>{["queued", "running", "stopping"].includes(selected.status) ? <button className="secondary-button danger" disabled={selected.status === "stopping" || busy === `stop:${selected.id}`} onClick={() => void taskAction(selected, "stop")}><CircleStop size={15} />安全停止</button> : ["failed", "cancelled", "interrupted"].includes(selected.status) && <button className="primary-button" disabled={Boolean(activeTask) || busy === `resume:${selected.id}`} onClick={() => void taskAction(selected, "resume")}><RotateCw size={15} />继续训练</button>}</div>
        </div>}
        <div className="training-guide-note"><BookOpen size={15} /><span><strong>训练不会上传数据</strong><small>数据、日志和权重只保存在你选择的本地目录。关闭软件时若训练仍运行，会先询问是否终止。</small></span></div>
      </aside>
    </div>
  </div>;
}

function NumberField({ label, value, min, step, help, onChange }: { label: string; value: number; min: number; step: number; help: string; onChange: (value: number) => void }) {
  return <label className="training-number-field"><span>{label}</span><input type="number" value={value} min={min} step={step} onChange={event => onChange(Number(event.target.value))} /><small>{help}</small></label>;
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return <label><span>{label}</span><button type="button" className={`switch ${checked ? "is-on" : ""}`} role="switch" aria-checked={checked} onClick={() => onChange(!checked)}><span /></button></label>;
}
