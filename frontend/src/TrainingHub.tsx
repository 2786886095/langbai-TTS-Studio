import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  ArrowRight, BookOpenCheck, CheckCircle2, ChevronLeft, Circle, CircleStop,
  Database, ExternalLink, FileAudio, GraduationCap, LoaderCircle, MonitorPlay,
  Play, RefreshCw, Sparkles, SquareTerminal, Waves,
} from "lucide-react";
import { TrainingCenter } from "./TrainingCenter";

type Capability = {
  available: boolean; detail: string; projectPath?: string; sourcePath?: string; pythonPath?: string;
  running?: boolean; reachable?: boolean; pid?: number | null; url?: string; logLines?: string[];
};

type Capabilities = { voxcpm?: Capability; gptSovits?: Capability };
type EngineChoice = "gpt_sovits" | "voxcpm" | null;

const tutorials = [
  {
    engine: "GPT-SoVITS", title: "V2 新版模型训练实操", source: "B站社区教程 · 已核验可访问",
    detail: "从训练数据到本地训练，适合作为第一次完整跟练。",
    url: "https://www.bilibili.com/video/BV1RxmKYKE5c/",
  },
  {
    engine: "GPT-SoVITS", title: "官方项目与中文使用指南", source: "RVC-Boss / GPT-SoVITS",
    detail: "核对当前版本的数据工具、预训练模型和训练入口。",
    url: "https://github.com/RVC-Boss/GPT-SoVITS",
  },
  {
    engine: "VoxCPM2", title: "官方 LoRA / 全量微调指南", source: "OpenBMB 官方文档",
    detail: "包含数据格式、显存估算、参数解释、恢复训练与常见问题。",
    url: "https://voxcpm.readthedocs.io/zh-cn/latest/finetuning/finetune.html",
  },
  {
    engine: "VoxCPM2", title: "在 B 站查找最新实操视频", source: "B站搜索入口 · 结果随平台更新",
    detail: "目前未核验到 OpenBMB 官方指定的 VoxCPM2 训练视频，因此不冒充官方推荐。",
    url: "https://search.bilibili.com/all?keyword=VoxCPM2%20%E5%BE%AE%E8%B0%83%20%E8%AE%AD%E7%BB%83",
  },
];

export function TrainingHub({ apiUrl, onExit }: { apiUrl: (path: string) => string; onExit: () => void }) {
  const [engine, setEngine] = useState<EngineChoice>(null);
  const [capabilities, setCapabilities] = useState<Capabilities>({});

  const load = async () => {
    const response = await fetch(apiUrl("/api/training/capabilities"));
    if (response.ok) setCapabilities(await response.json() as Capabilities);
  };
  useEffect(() => { void load(); }, []);

  if (engine === "voxcpm") return <TrainingCenter apiUrl={apiUrl} onBack={() => setEngine(null)} />;
  if (engine === "gpt_sovits") return <GptSovitsTraining apiUrl={apiUrl} onBack={() => setEngine(null)} />;

  return <div className="library-page training-hub-page">
    <header className="library-topbar">
      <div><button className="back-link" onClick={onExit}><ChevronLeft size={14} />返回创作台</button><p className="eyebrow">本地模型训练</p><h1>选择要训练的语音引擎</h1><p>两个引擎的数据格式和训练方式不同。先选引擎，再按对应流程准备数据。</p></div>
      <button className="secondary-button" onClick={() => void load()}><RefreshCw size={15} />重新检测</button>
    </header>

    <main className="training-hub-content">
      <section className="training-choice-grid">
        <EngineTrainingCard
          title="GPT-SoVITS" kicker="双模型角色音色训练" icon={<Waves size={25} />}
          status={capabilities.gptSovits}
          summary="使用官方工作台完成数据切分、ASR 标注、SoVITS 声学模型训练和 GPT 语义模型训练。"
          features={["官方完整数据处理链", "分别产出 .pth 与 .ckpt", "软件内打开本地训练工作台"]}
          action="进入 GPT-SoVITS 训练" onClick={() => setEngine("gpt_sovits")}
        />
        <EngineTrainingCard
          title="VoxCPM2" kicker="LoRA / 全量 SFT" icon={<Sparkles size={25} />}
          status={capabilities.voxcpm}
          summary="使用 JSONL 音频文本清单，在软件内创建、监控、停止和恢复训练任务。"
          features={["LoRA 参数高效微调", "全量 SFT 深度适配", "日志、进度与检查点恢复"]}
          action="进入 VoxCPM2 训练" onClick={() => setEngine("voxcpm")}
        />
      </section>

      <section className="training-onboarding-card">
        <div className="section-heading"><div><p className="eyebrow">第一次训练</p><h2>先按四步准备，再启动显卡任务</h2></div><BookOpenCheck size={24} /></div>
        <div className="training-onboarding-steps">
          <article><b>01</b><span><strong>整理原始音频</strong><small>清晰、单人、少混响，先去掉明显噪声与长静音。</small></span></article>
          <article><b>02</b><span><strong>切片并校对文本</strong><small>音频与转写必须一致；错误标注通常比参数问题更伤效果。</small></span></article>
          <article><b>03</b><span><strong>先做短程试训</strong><small>用较少步数确认数据、显存和日志正常，再开始正式训练。</small></span></article>
          <article><b>04</b><span><strong>固定测试句验收</strong><small>比较相同参考音频与文本，确认音色、咬字和稳定性后保存角色。</small></span></article>
        </div>
      </section>

      <TutorialLibrary />
    </main>
  </div>;
}

function EngineTrainingCard({ title, kicker, icon, status, summary, features, action, onClick }: {
  title: string; kicker: string; icon: ReactNode; status?: Capability; summary: string;
  features: string[]; action: string; onClick: () => void;
}) {
  return <article className="training-choice-card">
    <div className="training-choice-head"><span>{icon}</span><div><p>{kicker}</p><h2>{title}</h2></div><i className={status?.available ? "ready" : "warning"}><Circle size={8} fill="currentColor" />{status?.available ? "本地已就绪" : "需要配置"}</i></div>
    <p className="training-choice-summary">{summary}</p>
    <ul>{features.map(feature => <li key={feature}><CheckCircle2 size={15} />{feature}</li>)}</ul>
    <div className="training-choice-path" title={status?.projectPath}><SquareTerminal size={15} /><span>{status?.detail || "正在检测本地训练入口…"}</span></div>
    <button className="primary-button" onClick={onClick}>{action}<ArrowRight size={16} /></button>
  </article>;
}

function GptSovitsTraining({ apiUrl, onBack }: { apiUrl: (path: string) => string; onBack: () => void }) {
  const [status, setStatus] = useState<Capability | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const load = async () => {
    try {
      const response = await fetch(apiUrl("/api/training/gpt-sovits/workbench"));
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setStatus(await response.json() as Capability);
    } catch (error) { setMessage(error instanceof Error ? error.message : "无法读取训练工作台状态"); }
  };
  useEffect(() => {
    let disposed = false; let timer = 0;
    const poll = async () => { await load(); if (!disposed) timer = window.setTimeout(poll, status?.running && !status.reachable ? 1000 : 3000); };
    void poll();
    return () => { disposed = true; if (timer) window.clearTimeout(timer); };
  }, [status?.running, status?.reachable]);

  const action = async (name: "start" | "stop") => {
    setBusy(true); setMessage("");
    try {
      const response = await fetch(apiUrl(`/api/training/gpt-sovits/workbench/${name}`), { method: "POST" });
      const payload = await response.json().catch(() => null) as Capability & { detail?: string } | null;
      if (!response.ok) throw new Error(payload?.detail || `HTTP ${response.status}`);
      if (payload) setStatus(payload);
      setMessage(name === "start" ? "官方训练工作台正在本机启动，首次加载依赖时可能需要一些时间。" : "已请求关闭 GPT-SoVITS 训练工作台。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "操作失败"); }
    finally { setBusy(false); }
  };

  const guide = useMemo(() => [
    ["01", "音频工具", "先做人声分离、降噪与切片，保留干净单人语音。"],
    ["02", "ASR 与人工校对", "生成标注后逐句核对文字；不要让错字和漏字进入训练。"],
    ["03", "SoVITS 训练", "训练声学模型，权重保存为 SoVITS_weights 下的 .pth。"],
    ["04", "GPT 训练", "训练语义模型，权重保存为 GPT_weights 下的 .ckpt。"],
    ["05", "创建角色声音", "训练完成后到角色声音页绑定 .pth、.ckpt、参考音频和原文。"],
  ], []);

  return <div className="library-page gpt-training-page">
    <header className="library-topbar"><div><button className="back-link" onClick={onBack}><ChevronLeft size={14} />返回选择训练引擎</button><p className="eyebrow">GPT-SoVITS 训练</p><h1>官方训练工作台</h1><p>软件负责检测、启动和终止本地工作台；具体数据处理与训练仍由你绑定的 GPT-SoVITS 官方界面执行。</p></div><button className="secondary-button" onClick={() => void load()}><RefreshCw size={15} />刷新状态</button></header>
    {message && <div className="manager-notice"><MonitorPlay size={16} /><span>{message}</span></div>}

    <main className="gpt-training-content">
      <section className="gpt-workbench-status">
        <div><span className={status?.available ? "ready" : "warning"}><Circle size={8} fill="currentColor" />{status?.available ? "本地训练入口已识别" : "本地入口不可用"}</span><h2>{status?.running ? (status.reachable ? "工作台已启动" : "正在加载工作台") : "工作台尚未启动"}</h2><p>{status?.detail || "正在检测本地 GPT-SoVITS…"}</p><small title={status?.projectPath}>{status?.projectPath || "未识别项目目录"}{status?.pid ? ` · PID ${status.pid}` : ""}</small></div>
        <div className="gpt-workbench-actions">
          {status?.running ? <button className="secondary-button danger" disabled={busy} onClick={() => void action("stop")}><CircleStop size={16} />关闭工作台</button> : <button className="primary-button" disabled={busy || !status?.available} onClick={() => void action("start")}>{busy ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />}启动训练工作台</button>}
          {status?.url && <button className="secondary-button" disabled={!status.reachable} onClick={() => void window.langbaiDesktop?.openExternal?.(status.url!)}><ExternalLink size={15} />浏览器打开</button>}
        </div>
      </section>

      <section className="gpt-training-guide"><div className="section-heading"><div><p className="eyebrow">训练指引</p><h2>GPT-SoVITS 的五个必要阶段</h2></div><GraduationCap size={24} /></div><div>{guide.map(([number, title, detail]) => <article key={number}><b>{number}</b><span><strong>{title}</strong><small>{detail}</small></span></article>)}</div></section>

      {status?.reachable && status.url ? <section className="embedded-training-workbench"><header><div><p className="eyebrow">软件内工作台</p><h2>GPT-SoVITS / {status.url}</h2></div><span><Circle size={8} fill="currentColor" />本地连接</span></header><iframe title="GPT-SoVITS 官方训练工作台" src={status.url} /></section> : <section className="gpt-workbench-placeholder"><Database size={28} /><strong>{status?.running ? "正在等待本地 WebUI 就绪" : "启动后将在这里显示官方训练界面"}</strong><span>所有音频、标注与训练权重都保存在你的本地 GPT-SoVITS 目录。</span></section>}

      <TutorialLibrary compact />
    </main>
  </div>;
}

function TutorialLibrary({ compact = false }: { compact?: boolean }) {
  return <section className={`training-tutorials ${compact ? "compact" : ""}`}>
    <div className="section-heading"><div><p className="eyebrow">学习资料</p><h2>训练文档与 B 站视频</h2></div><FileAudio size={23} /></div>
    <div>{tutorials.map(item => <button key={item.url} onClick={() => void window.langbaiDesktop?.openExternal?.(item.url)}><i>{item.engine}</i><strong>{item.title}</strong><span>{item.detail}</span><small>{item.source}<ExternalLink size={13} /></small></button>)}</div>
  </section>;
}
