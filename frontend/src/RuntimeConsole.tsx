import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, ChevronLeft, Circle, CircleStop, Copy, ExternalLink,
  LoaderCircle, Maximize2, Play, RefreshCw, RotateCw, SquareTerminal, X,
} from "lucide-react";
import { engines, type EngineId } from "./parameterSchemas";

type RuntimeEngine = {
  id: EngineId; available: boolean; state: string; detail: string; running: boolean;
  pid?: number | null; command: string[]; cwd: string; logPath: string; logLines: string[];
};

const allEngineIds: EngineId[] = ["indextts2", "voxcpm", "gpt_sovits"];

export function RuntimeConsole({ apiUrl, onBack }: { apiUrl: (path: string) => string; onBack: () => void }) {
  const [items, setItems] = useState<RuntimeEngine[]>([]);
  const [openWindows, setOpenWindows] = useState<EngineId[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState("");

  const refresh = async () => {
    try {
      const response = await fetch(apiUrl("/api/runtime/engines?lines=300"));
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json() as { items?: RuntimeEngine[] };
      setItems(payload.items ?? []);
    } catch (error) { setMessage(error instanceof Error ? error.message : "无法读取运行状态"); }
  };

  useEffect(() => {
    let disposed = false; let timer = 0;
    const poll = async () => { await refresh(); if (!disposed) timer = window.setTimeout(poll, document.hidden ? 15000 : 1800); };
    void poll();
    return () => { disposed = true; if (timer) window.clearTimeout(timer); };
  }, []);

  const rows = useMemo(() => allEngineIds.map(id => items.find(item => item.id === id) ?? {
    id, available: false, state: "loading", detail: "正在读取本地引擎状态", running: false,
    pid: null, command: [], cwd: "", logPath: "", logLines: [],
  }), [items]);

  const action = async (engineId: EngineId, name: "start" | "stop" | "restart") => {
    const actionId = `${engineId}:${name}`;
    setBusy(actionId); setMessage("");
    try {
      const response = await fetch(apiUrl(`/api/runtime/engines/${engineId}/${name}`), { method: "POST" });
      if (!response.ok) {
        const detail = await response.json().catch(() => null) as { detail?: string } | null;
        throw new Error(detail?.detail || `HTTP ${response.status}`);
      }
      setMessage(name === "start" ? `${engines[engineId].name} 已启动。` : name === "stop" ? `${engines[engineId].name} 已停止。` : `${engines[engineId].name} 已重新启动。`);
      await refresh();
    } catch (error) { setMessage(error instanceof Error ? error.message : "引擎操作失败"); }
    finally { setBusy(""); }
  };

  const openConsole = (id: EngineId) => setOpenWindows(current => current.includes(id) ? current : [...current, id]);
  const closeConsole = (id: EngineId) => setOpenWindows(current => current.filter(item => item !== id));

  return <div className="library-page runtime-page">
    <header className="library-topbar"><div><button className="back-link" onClick={onBack}><ChevronLeft size={14} />返回创作台</button><p className="eyebrow">本地运行观察</p><h1>三个独立引擎终端</h1><p>每个引擎拥有自己的软件内 CMD 窗口、启动控制和实时日志；打开窗口不会自动占用显存。</p></div><div className="runtime-header-actions"><button className="secondary-button" onClick={() => allEngineIds.forEach(openConsole)}><Maximize2 size={15} />打开全部终端</button><button className="secondary-button" onClick={() => void refresh()}><RefreshCw size={15} />刷新状态</button></div></header>
    {message && <div className={`manager-notice ${/(失败|无法|不存在|不可用)/.test(message) ? "warning" : ""}`}><AlertTriangle size={16} /><span>{message}</span></div>}

    <section className="runtime-launch-grid">{rows.map(item => <article key={item.id} className={item.running ? "running" : ""}>
      <div className="runtime-launch-title"><span><SquareTerminal size={21} /></span><div><p className="eyebrow">{item.id === "indextts2" ? "INDEXTTS 2" : item.id === "voxcpm" ? "VOXCPM 2" : "GPT-SOVITS"}</p><h2>{engines[item.id].name}</h2></div><i className={item.running ? "running" : item.available ? "idle" : "offline"}>{item.running ? "运行中" : item.available ? "待机" : "不可用"}</i></div>
      <p>{item.detail}</p>
      <dl><div><dt>进程</dt><dd>{item.pid ? `PID ${item.pid}` : "尚未启动"}</dd></div><div><dt>终端</dt><dd>{openWindows.includes(item.id) ? "窗口已打开" : "窗口已关闭"}</dd></div></dl>
      <div className="runtime-launch-actions"><button className="secondary-button" onClick={() => openConsole(item.id)}><ExternalLink size={15} />打开 CMD 窗口</button>{item.running ? <button className="secondary-button danger" disabled={Boolean(busy)} onClick={() => void action(item.id, "stop")}><CircleStop size={15} />停止</button> : <button className="primary-button" disabled={!item.available || Boolean(busy)} onClick={() => void action(item.id, "start")}>{busy === `${item.id}:start` ? <LoaderCircle className="spin" size={15} /> : <Play size={15} />}启动</button>}</div>
    </article>)}</section>

    <section className={`runtime-window-desktop windows-${openWindows.length}`}>
      {openWindows.length === 0 ? <div className="runtime-desktop-empty"><SquareTerminal size={34} /><strong>尚未打开 CMD 窗口</strong><span>从上方选择一个引擎。窗口只展示软件管理的命令与日志，不接受任意系统命令。</span></div> : openWindows.map(id => {
        const item = rows.find(row => row.id === id)!;
        return <EngineConsoleWindow key={id} item={item} busy={busy} onAction={action} onClose={() => closeConsole(id)} />;
      })}
    </section>
  </div>;
}

function EngineConsoleWindow({ item, busy, onAction, onClose }: {
  item: RuntimeEngine; busy: string;
  onAction: (id: EngineId, action: "start" | "stop" | "restart") => Promise<void>;
  onClose: () => void;
}) {
  const [follow, setFollow] = useState(true);
  const terminalRef = useRef<HTMLPreElement>(null);
  useEffect(() => { if (follow && terminalRef.current) terminalRef.current.scrollTop = terminalRef.current.scrollHeight; }, [item.logLines, follow]);
  const command = item.command?.map(part => /\s/.test(part) ? `"${part}"` : part).join(" ") || "等待引擎配置";
  const logs = item.logLines?.length ? item.logLines.join("\n") : "当前没有运行日志。点击启动后，真实输出会显示在这里。";

  return <article className="runtime-cmd-window">
    <header><div className="runtime-window-lights" aria-hidden="true"><Circle size={10} fill="#d97a61" color="#d97a61" /><Circle size={10} fill="#ddb25b" color="#ddb25b" /><Circle size={10} fill="#59b584" color="#59b584" /></div><strong>{engines[item.id].name} · CMD</strong><span className={item.running ? "running" : ""}>{item.running ? `运行中 · PID ${item.pid}` : "待机"}</span><button onClick={onClose} aria-label={`关闭 ${engines[item.id].name} 终端窗口`}><X size={15} /></button></header>
    <div className="runtime-cmd-meta"><code title={command}>{command}</code><button onClick={() => void navigator.clipboard.writeText(command)}><Copy size={13} />复制命令</button></div>
    <pre ref={terminalRef}>{logs}</pre>
    <footer><label><input type="checkbox" checked={follow} onChange={event => setFollow(event.target.checked)} />自动跟随日志</label><div>{item.running ? <><button className="runtime-window-button" disabled={Boolean(busy)} onClick={() => void onAction(item.id, "restart")}>{busy === `${item.id}:restart` ? <LoaderCircle className="spin" size={14} /> : <RotateCw size={14} />}重启</button><button className="runtime-window-button danger" disabled={Boolean(busy)} onClick={() => void onAction(item.id, "stop")}><CircleStop size={14} />停止模型</button></> : <button className="runtime-window-button primary" disabled={!item.available || Boolean(busy)} onClick={() => void onAction(item.id, "start")}><Play size={14} />启动模型</button>}</div></footer>
  </article>;
}
