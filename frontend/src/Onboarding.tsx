import { useState } from "react";
import { ArrowRight, CheckCircle2, Cpu, FolderOpen, ShieldCheck, Sparkles, X } from "lucide-react";

const steps = [
  { icon: Sparkles, title: "欢迎使用 langbai TTS Studio", text: "统一管理四套本地语音引擎，包含 Breeze TTS 2 加速克隆。粘贴长文后可自动分段、合并与断点续跑。" },
  { icon: Cpu, title: "先确认本地引擎", text: "IndexTTS2、VoxCPM2 和 GPT-SoVITS 支持本地绑定或安装；Breeze 首版绑定已有 WSL 部署，默认使用加速版。" },
  { icon: ShieldCheck, title: "数据留在本机", text: "生成任务和输出音频默认保存在本地。下载模型前仍需阅读并接受各上游许可证。" },
];

export function Onboarding({ onDone, onSetup }: { onDone: () => void; onSetup: () => void }) {
  const [step, setStep] = useState(0);
  const current = steps[step];
  return <div className="onboarding-overlay"><section className="onboarding-dialog" role="dialog" aria-modal="true" aria-labelledby="onboarding-title"><div className="onboarding-brand"><img src="./icon.png" alt="" /><span><strong>langbai</strong><small>TTS Studio</small></span></div><button className="onboarding-close" onClick={onDone} aria-label="跳过引导"><X size={19} /></button><div className="onboarding-progress">{steps.map((_, index) => <i key={index} className={index <= step ? "active" : ""} />)}</div><span className="onboarding-hero-icon"><current.icon size={34} /></span><p className="eyebrow">首次启动 · {step + 1}/{steps.length}</p><h1 id="onboarding-title">{current.title}</h1><p>{current.text}</p>{step === 1 && <div className="onboarding-engine-list"><span><CheckCircle2 size={17} />IndexTTS2</span><span><CheckCircle2 size={17} />VoxCPM2</span><span><CheckCircle2 size={17} />GPT-SoVITS</span><span><Cpu size={17} />Breeze TTS 2 · 加速</span></div>}<div className="onboarding-actions"><button className="text-button" onClick={onDone}>稍后设置</button>{step < steps.length - 1 ? <button className="primary-button" onClick={() => setStep(value => value + 1)}>继续<ArrowRight size={17} /></button> : <button className="primary-button" onClick={() => { onDone(); onSetup(); }}><FolderOpen size={17} />检查引擎</button>}</div></section></div>;
}
