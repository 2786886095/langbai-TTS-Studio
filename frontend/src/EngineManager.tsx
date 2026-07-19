import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, Check, CheckCircle2, ChevronDown, ChevronRight, Circle,
  CloudDownload, Code2, Cpu, Download, ExternalLink, FileArchive, FileCode2, FolderOpen, Gauge,
  HardDrive, ListRestart, LoaderCircle, Pause, Play, RefreshCw, RotateCcw,
  ScrollText, Square, TerminalSquare, X,
} from "lucide-react";
import { engines, type EngineId } from "./parameterSchemas";

type ComponentState = { installed: boolean; state: string; detail?: string; path?: string; version?: string };
type ModelAsset = { id: string; name: string; size?: string; bytes?: number; source?: string; sourceUrl?: string; license?: string; licenseUrl?: string; required?: boolean; installed?: boolean; state?: string };
type RuntimeLicense = { id: string; name: string; license: string; licenseUrl?: string; sourcePage?: string };
type InstallEngine = { id: EngineId; name: string; installPath: string; license?: string; licenseUrl?: string; source: ComponentState; environment: ComponentState; modelsState: ComponentState; models: ModelAsset[]; requiredTools: RuntimeLicense[]; requiredRuntimeLicenses: RuntimeLicense[]; origin: "managed" | "bound" };
type Transfer = { id: string; engine: EngineId; label: string; kind: string; state: string; progress: number; speed?: string; downloaded?: string; total?: string; error?: string; log?: string[] };
type GlobalSettings = { revision: number; defaultEngine: EngineId; outputDirectory: string | null; autoRevealOutput: boolean; updateChannel: "stable" | "beta" };

const engineOrder: EngineId[] = ["indextts2", "voxcpm", "gpt_sovits"];
const apiBase = new URLSearchParams(window.location.search).get("backendUrl") ?? "";
const apiUrl = (path: string) => `${apiBase}${path}`;
const emptyComponent = (): ComponentState => ({ installed: false, state: "unknown", detail: "等待后端检测" });
function friendlyError(reason: unknown, fallback: string) {
  const message = reason instanceof Error ? reason.message : "";
  if (/Failed to fetch|NetworkError|fetch failed/i.test(message)) {
    return "无法连接本地后端，请确认应用服务已启动后重试。";
  }
  return message || fallback;
}
function textState(raw: unknown) { return typeof raw === "string" ? raw : "unknown"; }
function formatBytes(value: unknown) { const bytes = Number(value ?? 0); if (!bytes) return undefined; const units = ["B", "KB", "MB", "GB", "TB"]; const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024))); return `${(bytes / 1024 ** index).toFixed(index >= 3 ? 1 : 0)} ${units[index]}`; }
function asRows(raw: unknown, preferredKeys: string[]) {
  if (Array.isArray(raw)) return raw;
  if (!raw || typeof raw !== "object") return [];
  const record = raw as Record<string, unknown>;
  for (const key of preferredKeys) if (Array.isArray(record[key])) return record[key] as unknown[];
  return Object.entries(record).filter(([, value]) => value && typeof value === "object" && !Array.isArray(value)).map(([id, value]) => ({ id, ...(value as Record<string, unknown>) }));
}
function normalizeComponent(raw: unknown, fallbackPath?: string): ComponentState {
  const item = typeof raw === "object" && raw ? raw as Record<string, unknown> : {};
  const state = textState(item.state ?? item.status);
  const installed = Boolean(item.installed ?? item.available ?? ["ready", "installed", "completed"].includes(state));
  return { installed, state, detail: String(item.detail ?? item.message ?? (installed ? "已安装" : "未安装")), path: String(item.path ?? item.projectPath ?? item.pythonPath ?? fallbackPath ?? ""), version: item.version ? String(item.version) : undefined };
}
function normalizeModel(raw: unknown, index: number): ModelAsset {
  const item = typeof raw === "object" && raw ? raw as Record<string, unknown> : {};
  const bytes = Number(item.bytes ?? item.sizeBytes ?? item.estimated_download_bytes ?? 0) || undefined;
  const source = item.source ?? item.url ?? (item.repo_id ? `${item.provider ?? "model"}: ${item.repo_id}` : undefined);
  return { id: String(item.id ?? item.name ?? `model-${index}`), name: String(item.name ?? item.label ?? `模型 ${index + 1}`), size: item.size ? String(item.size) : item.sizeText ? String(item.sizeText) : formatBytes(bytes), bytes, source: source ? String(source) : undefined, sourceUrl: item.source_url ? String(item.source_url) : item.repo_id ? `https://huggingface.co/${item.repo_id}` : undefined, license: item.license ? String(item.license) : item.licenseNotice ? String(item.licenseNotice) : undefined, licenseUrl: item.license_url ? String(item.license_url) : undefined, required: Boolean(item.required ?? true), installed: Boolean(item.installed ?? item.available), state: textState(item.state ?? item.status) };
}
function normalizeRuntimeLicense(raw: unknown, index: number): RuntimeLicense {
  const item = typeof raw === "object" && raw ? raw as Record<string, unknown> : {};
  return {
    id: String(item.id ?? `runtime-${index}`),
    name: String(item.name ?? item.id ?? `运行组件 ${index + 1}`),
    license: String(item.license ?? "未提供许可证名称"),
    licenseUrl: item.license_url || item.licenseUrl ? String(item.license_url ?? item.licenseUrl) : undefined,
    sourcePage: item.source_page || item.sourcePage ? String(item.source_page ?? item.sourcePage) : undefined,
  };
}
function normalizeEngine(raw: unknown, fallbackId: EngineId): InstallEngine {
  const item = typeof raw === "object" && raw ? raw as Record<string, unknown> : {};
  const id = engineOrder.includes(String(item.id ?? item.engine) as EngineId) ? String(item.id ?? item.engine) as EngineId : fallbackId;
  const installPath = String(item.installRoot ?? item.install_root ?? item.installPath ?? item.install_path ?? item.projectPath ?? item.project_path ?? "");
  const source = normalizeComponent(item.source ?? { installed: item.sourceInstalled ?? item.source_installed ?? item.installed, state: item.sourceState ?? item.source_state, detail: item.sourceDetail ?? item.source_detail, path: item.sourcePath ?? item.source_path }, installPath);
  const environment = normalizeComponent(item.environment ?? item.python ?? { installed: item.environmentInstalled ?? item.environment_installed ?? item.installed, state: item.environmentState ?? item.environment_state, path: item.envPath ?? item.env_path ?? item.pythonPath ?? item.python_path });
  const modelRows = Array.isArray(item.models) ? item.models.map(normalizeModel) : [];
  const modelInstalled = modelRows.some(model => model.installed);
  const requiredTools = Array.isArray(item.required_tools) ? item.required_tools.map(normalizeRuntimeLicense) : [];
  const requiredRuntimeLicenses = Array.isArray(item.required_runtime_licenses) ? item.required_runtime_licenses.map(normalizeRuntimeLicense) : [];
  return { id, name: String(item.name ?? engines[id].name), installPath, license: item.code_license ? String(item.code_license) : undefined, licenseUrl: item.code_license_url ? String(item.code_license_url) : undefined, source, environment, modelsState: normalizeComponent(item.modelsState ?? { installed: modelInstalled, state: modelInstalled ? "partial" : "missing", detail: modelInstalled ? "部分模型可用" : "未检测到模型" }), models: modelRows, requiredTools, requiredRuntimeLicenses, origin: item.origin === "bound" ? "bound" : "managed" };
}
function normalizeTransfer(raw: unknown, index: number): Transfer {
  const item = typeof raw === "object" && raw ? raw as Record<string, unknown> : {};
  const rawProgress = Number(item.progress ?? item.ratio ?? 0);
  return { id: String(item.id ?? item.taskId ?? `transfer-${index}`), engine: engineOrder.includes(String(item.engine ?? item.engineId) as EngineId) ? String(item.engine ?? item.engineId) as EngineId : "indextts2", label: String(item.label ?? item.name ?? item.message ?? item.phase ?? "安装任务"), kind: String(item.kind ?? item.type ?? "download"), state: String(item.state ?? item.status ?? "queued"), progress: Math.max(0, Math.min(100, rawProgress <= 1 ? rawProgress * 100 : rawProgress)), speed: item.speed ? String(item.speed) : item.speedText ? String(item.speedText) : item.speed_bps ? `${formatBytes(item.speed_bps)}/s` : undefined, downloaded: item.downloaded ? String(item.downloaded) : formatBytes(item.bytes_downloaded), total: item.total ? String(item.total) : formatBytes(item.bytes_total), error: item.error ? String(item.error) : undefined, log: Array.isArray(item.log) ? item.log.map(String) : Array.isArray(item.logs) ? item.logs.map(String) : Array.isArray(item.log_tail) ? item.log_tail.map(String) : undefined };
}

function StateBadge({ value }: { value: ComponentState }) {
  return <span className={`install-state ${value.installed ? "installed" : value.state === "checking" ? "checking" : "missing"}`}>{value.installed ? <CheckCircle2 size={13} /> : value.state === "checking" ? <LoaderCircle className="spin" size={13} /> : <Circle size={13} />}{value.state === "bound" ? "已绑定" : value.installed ? "已安装" : value.state === "checking" ? "检测中" : "未安装"}</span>;
}

export function EngineManager({ onBack, density, onDensityChange }: { onBack: () => void; density: "comfortable" | "compact"; onDensityChange: () => void }) {
  const [items, setItems] = useState<InstallEngine[]>(() => engineOrder.map(id => normalizeEngine({}, id)));
  const [transfers, setTransfers] = useState<Transfer[]>([]);
  const [selectedEngine, setSelectedEngine] = useState<EngineId>("indextts2");
  const [selectedModels, setSelectedModels] = useState<Record<EngineId, Set<string>>>(() => ({ indextts2: new Set(), voxcpm: new Set(), gpt_sovits: new Set() }));
  const [sourceLicenseAccepted, setSourceLicenseAccepted] = useState<Record<EngineId, boolean>>({ indextts2: false, voxcpm: false, gpt_sovits: false });
  const [runtimeLicensesAccepted, setRuntimeLicensesAccepted] = useState<Record<EngineId, Set<string>>>(() => ({ indextts2: new Set(), voxcpm: new Set(), gpt_sovits: new Set() }));
  const [modelLicensesAccepted, setModelLicensesAccepted] = useState<Record<EngineId, boolean>>({ indextts2: false, voxcpm: false, gpt_sovits: false });
  const [devices, setDevices] = useState<Record<EngineId, "CPU" | "CU126" | "CU128">>({ indextts2: "CU128", voxcpm: "CU128", gpt_sovits: "CU128" });
  const [expandedLog, setExpandedLog] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [updateStatus, setUpdateStatus] = useState("尚未检查 GitHub Releases");
  const [updateReady, setUpdateReady] = useState(false);
  const [updateDownloaded, setUpdateDownloaded] = useState(false);
  const [settings, setSettings] = useState<GlobalSettings>({ revision: 0, defaultEngine: "indextts2", outputDirectory: null, autoRevealOutput: false, updateChannel: "stable" });
  const [diagnosticsStatus, setDiagnosticsStatus] = useState("尚未导出诊断包");
  const selected = items.find(item => item.id === selectedEngine) ?? items[0];
  const requiredRuntimeIds = useMemo(() => [...selected.requiredRuntimeLicenses, ...selected.requiredTools].map(item => item.id), [selected]);
  const setupLicensesAccepted = sourceLicenseAccepted[selectedEngine] && requiredRuntimeIds.every(id => runtimeLicensesAccepted[selectedEngine].has(id));
  const activeTransfers = useMemo(() => transfers.filter(item => ["queued", "running", "paused", "failed"].includes(item.state)), [transfers]);

  const refresh = async () => {
    try {
      const [response, catalogResponse, downloadsResponse] = await Promise.all([fetch(apiUrl("/api/installations")), fetch(apiUrl("/api/installer/catalog")), fetch(apiUrl("/api/downloads"))]);
      if (!response.ok) throw new Error(`安装状态接口返回 HTTP ${response.status}`);
      const raw = await response.json();
      const catalogRaw = catalogResponse.ok ? await catalogResponse.json() : [];
      const downloadRaw = downloadsResponse.ok ? await downloadsResponse.json() : [];
      const engineRows = asRows(raw, ["engines", "installations"]);
      const byId = new Map(engineRows.map((row: Record<string, unknown>) => [String(row.id ?? row.engine), row]));
      const catalogRows = asRows(catalogRaw, ["engines", "catalog"]);
      const catalogById = new Map(catalogRows.map((row: Record<string, unknown>) => [String(row.id ?? row.engine), row]));
      setItems(engineOrder.map(id => {
        const installed = byId.get(id) as Record<string, unknown> | undefined;
        const catalog = catalogById.get(id) as Record<string, unknown> | undefined;
        const catalogModels = Array.isArray(catalog?.models) ? catalog.models as Record<string, unknown>[] : [];
        const installedModels = Array.isArray(installed?.models) ? installed.models as Record<string, unknown>[] : [];
        const installedById = new Map(installedModels.map(model => [String(model.id), model]));
        const models = catalogModels.map(model => ({ ...model, ...(installedById.get(String(model.id)) ?? {}) }));
        return normalizeEngine({ ...(catalog ?? {}), ...(installed ?? {}), models: models.length ? models : installedModels }, id);
      }));
      const transferRows = asRows(downloadRaw, ["downloads", "jobs"]);
      setTransfers(transferRows.map(normalizeTransfer)); setMessage("");
    } catch (error) { setMessage(friendlyError(error, "无法读取安装状态")); }
    finally { setLoading(false); }
  };
  const scanLocal = async (silent = false) => {
    setLoading(true);
    try {
      const runScan = async (roots?: string[]) => {
        const response = await fetch(apiUrl("/api/installations/scan-local"), { method: "POST", headers: { "Content-Type": "application/json" }, body: roots ? JSON.stringify({ roots, maxDepth: 2 }) : undefined });
        if (!response.ok) throw new Error(await response.text() || `HTTP ${response.status}`);
        return response.json() as Promise<{ found?: number; errors?: { engine?: string; error?: string }[] }>;
      };
      let result = await runScan();
      if (!silent && !result.found) {
        const root = await window.langbaiDesktop?.chooseDirectory?.();
        if (root) result = await runScan([root]);
      }
      await refresh();
      if (!silent) setMessage(result.found ? `已扫描并绑定 ${result.found} 个现有本地引擎，无需重复安装。` : result.errors?.length ? `扫描完成，但部分路径不可用：${result.errors.map(item => item.error).join("；")}` : "未发现可直接使用的本地引擎，可继续使用软件内安装。" );
    } catch (error) {
      if (!silent) setMessage(friendlyError(error, "快速扫描失败"));
    } finally { setLoading(false); }
  };
  useEffect(() => { void scanLocal(true); }, []);
  useEffect(() => { const timer = window.setInterval(() => { if (!document.hidden) void refresh(); }, activeTransfers.length ? 2000 : 10000); return () => window.clearInterval(timer); }, [activeTransfers.length]);
  const refreshSettings = async () => {
    const response = await fetch(apiUrl("/api/settings"));
    if (!response.ok) throw new Error(`设置接口返回 HTTP ${response.status}`);
    const raw = await response.json() as GlobalSettings;
    setSettings(raw);
  };
  useEffect(() => { void refreshSettings().catch(error => setMessage(friendlyError(error, "无法读取全局设置"))); }, []);
  useEffect(() => { const unsubscribe = window.langbaiDesktop?.onUpdateEvent?.(event => {
    const item = event && typeof event === "object" ? event as Record<string, unknown> : {};
    const state = String(item.state ?? item.type ?? item.status ?? event);
    const progress = item.progress && typeof item.progress === "object" ? item.progress as { percent?: number } : {};
    const info = item.info && typeof item.info === "object" ? item.info as { version?: string } : {};
    const labels: Record<string, string> = {
      checking: "正在连接 GitHub 检查更新…",
      current: "当前已是最新版",
      available: `发现新版本${info.version ? ` ${info.version}` : ""}`,
      downloading: `正在下载更新${Number.isFinite(progress.percent) ? ` · ${Math.round(progress.percent!)}%` : "…"}`,
      downloaded: "更新已下载，可重启安装",
      error: String(item.message ?? "更新检查失败，请稍后重试"),
    };
    setUpdateStatus(labels[state] ?? String(item.message ?? state));
    if (["available", "update-available"].includes(state)) setUpdateReady(true);
    if (["downloaded", "update-downloaded"].includes(state)) setUpdateDownloaded(true);
  }); return () => { if (typeof unsubscribe === "function") unsubscribe(); }; }, []);
  const checkUpdate = async () => { setUpdateStatus("正在连接 GitHub 检查更新…"); try {
    const result = await window.langbaiDesktop?.checkForUpdates?.(settings.updateChannel) as { supported?: boolean; reason?: string } | undefined;
    if (result?.supported === false) setUpdateStatus(result.reason || "当前环境不支持自动更新");
  } catch (error) { setUpdateStatus(friendlyError(error, "检查更新失败")); } };
  const downloadUpdate = async () => { setUpdateStatus("正在下载更新…"); try { await window.langbaiDesktop?.downloadUpdate?.(); } catch (error) { setUpdateStatus(error instanceof Error ? error.message : "下载更新失败"); } };
  const installUpdate = async () => { try { await window.langbaiDesktop?.installUpdate?.(); } catch (error) { setUpdateStatus(error instanceof Error ? error.message : "安装更新失败"); } };
  const patchSettings = async (changes: Partial<Omit<GlobalSettings, "revision">>) => {
    const response = await fetch(apiUrl("/api/settings"), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expectedRevision: settings.revision, ...changes }) });
    if (response.status === 409) { await refreshSettings(); throw new Error("设置已在其他窗口修改，已刷新为最新值，请重新操作。"); }
    if (!response.ok) throw new Error(await response.text() || `HTTP ${response.status}`);
    setSettings(await response.json() as GlobalSettings);
  };
  const chooseOutputDirectory = async () => {
    const path = await window.langbaiDesktop?.chooseDirectory?.();
    if (!path) return;
    try { await patchSettings({ outputDirectory: path }); setMessage("默认输出目录已保存。"); } catch (error) { setMessage(error instanceof Error ? error.message : "无法保存输出目录"); }
  };
  const exportDiagnostics = async () => {
    setDiagnosticsStatus("正在生成诊断包…");
    try {
      const response = await fetch(apiUrl("/api/diagnostics/exports"), { method: "POST" });
      if (!response.ok) throw new Error(await response.text() || `HTTP ${response.status}`);
      const result = await response.json() as { path?: string; sizeBytes?: number };
      setDiagnosticsStatus(`已导出${result.sizeBytes ? ` · ${formatBytes(result.sizeBytes)}` : ""}`);
      if (result.path) await window.langbaiDesktop?.showItemInFolder?.(result.path);
    } catch (error) { setDiagnosticsStatus(error instanceof Error ? error.message : "诊断导出失败"); }
  };

  const choosePath = async () => {
    const path = await window.langbaiDesktop?.chooseDirectory?.();
    if (!path) return;
    setItems(current => current.map(item => item.id === selectedEngine ? { ...item, installPath: path } : item));
  };
  const startSetup = async () => {
    if (!setupLicensesAccepted) { setMessage("请分别阅读并接受项目源码、CPython 与安装工具的许可证。"); return; }
    try {
      const response = await fetch(apiUrl(`/api/installations/${selectedEngine}/setup`), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ installRoot: selected.installPath || undefined, acceptLicense: true, acceptPythonLicense: true, acceptedToolLicenses: selected.requiredTools.map(tool => tool.id), device: devices[selectedEngine] }) });
      if (!response.ok) throw new Error(await response.text() || `HTTP ${response.status}`);
      setMessage("安装任务已提交，进度将由后端持续更新。"); await refresh();
    } catch (error) { setMessage(error instanceof Error ? error.message : "无法启动安装"); }
  };
  const startModels = async () => {
    const modelIds = [...selectedModels[selectedEngine]];
    if (!modelIds.length) { setMessage("请先勾选要下载的模型权重。"); return; }
    if (!modelLicensesAccepted[selectedEngine]) { setMessage("请先阅读并接受所选模型权重各自的许可证。"); return; }
    try {
      const responses = await Promise.all(modelIds.map(modelId => fetch(apiUrl(`/api/installations/${selectedEngine}/models`), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ installRoot: selected.installPath || undefined, modelId, acceptLicense: true }) })));
      const failed = responses.find(response => !response.ok);
      if (failed) throw new Error(await failed.text() || `HTTP ${failed.status}`);
      setMessage("模型下载已提交，完成状态以后端校验为准。"); await refresh();
    } catch (error) { setMessage(error instanceof Error ? error.message : "无法启动模型下载"); }
  };
  const transferAction = async (id: string, action: "pause" | "resume" | "cancel" | "retry") => {
    try { const response = await fetch(apiUrl(`/api/downloads/${id}/${action}`), { method: "POST" }); if (!response.ok) throw new Error(await response.text() || `HTTP ${response.status}`); await refresh(); }
    catch (error) { setMessage(error instanceof Error ? error.message : `${action} 操作失败`); }
  };
  const toggleModel = (id: string) => setSelectedModels(current => { const next = new Set(current[selectedEngine]); next.has(id) ? next.delete(id) : next.add(id); return { ...current, [selectedEngine]: next }; });
  const toggleRuntimeLicense = (id: string, accepted: boolean) => setRuntimeLicensesAccepted(current => {
    const next = new Set(current[selectedEngine]);
    accepted ? next.add(id) : next.delete(id);
    return { ...current, [selectedEngine]: next };
  });

  return <div className="manager-page">
    <header className="manager-topbar"><div><button className="back-link" onClick={onBack}><ChevronRight size={14} />返回创作台</button><p className="eyebrow">设置与路径</p><h1>引擎管理</h1><p>先扫描并绑定已有程序；没有本地引擎时，再安装源码、独立 Python 环境和模型权重。</p></div><button className="secondary-button" onClick={() => void scanLocal(false)} disabled={loading}><RefreshCw className={loading ? "spin" : ""} size={16} />快速扫描本地程序</button></header>
    {message && <div className="manager-notice"><AlertTriangle size={16} /><span>{message}</span><button onClick={() => setMessage("")} aria-label="关闭提示"><X size={14} /></button></div>}
    <div className="manager-body">
      <aside className="manager-engines"><div className="manager-section-title"><strong>本地语音引擎</strong><span>3 个项目</span></div>{items.map(item => <button key={item.id} className={selectedEngine === item.id ? "active" : ""} onClick={() => setSelectedEngine(item.id)}><span className="manager-engine-icon"><Cpu size={19} /></span><span><strong>{item.name}</strong><small>{item.origin === "bound" ? "本地程序已绑定" : item.source.installed && item.environment.installed ? "源码与环境已就绪" : "需要配置"}</small></span><ChevronRight size={16} /></button>)}</aside>
      <main className="manager-content">
        <section className="preferences-panel"><div className="panel-title"><div><p className="eyebrow">界面、存储与更新</p><h2>桌面偏好</h2><p>窗口操作由 Electron 执行；工作区设置通过本地后端持久化。</p></div></div><div className="preference-grid"><article><span className="preference-icon"><Gauge size={19} /></span><div><strong>信息密度</strong><small>舒适模式便于阅读；紧凑模式显示更多参数。</small></div><button className="secondary-button" onClick={onDensityChange}>{density === "comfortable" ? "切换紧凑" : "切换舒适"}</button></article><article className="update-preference"><span className="preference-icon"><RefreshCw size={19} /></span><div><strong>软件更新</strong><small>{updateStatus}</small></div><div>{updateDownloaded ? <button className="primary-button" onClick={installUpdate}>重启安装</button> : updateReady ? <button className="primary-button" onClick={downloadUpdate}><Download size={16} />下载更新</button> : <button className="secondary-button" onClick={checkUpdate}>检查更新</button>}</div></article><article><span className="preference-icon"><FileArchive size={19} /></span><div><strong>诊断包</strong><small>{diagnosticsStatus}</small></div><button className="secondary-button" onClick={exportDiagnostics}>导出诊断</button></article></div><div className="workspace-preferences"><div><span><strong>默认输出目录</strong><small title={settings.outputDirectory ?? undefined}>{settings.outputDirectory || "跟随任务默认目录"}</small></span><button className="secondary-button" onClick={chooseOutputDirectory}>选择目录</button></div><label><span><strong>默认引擎</strong><small>新项目优先使用的本地引擎。</small></span><select value={settings.defaultEngine} onChange={event => void patchSettings({ defaultEngine: event.target.value as EngineId }).catch(error => setMessage(error instanceof Error ? error.message : "保存失败"))}>{engineOrder.map(id => <option value={id} key={id}>{engines[id].name}</option>)}</select></label><label><span><strong>更新通道</strong><small>稳定版优先可靠性；测试版更早获得功能。</small></span><select value={settings.updateChannel} onChange={event => void patchSettings({ updateChannel: event.target.value as "stable" | "beta" }).catch(error => setMessage(error instanceof Error ? error.message : "保存失败"))}><option value="stable">稳定版</option><option value="beta">测试版</option></select></label><label className="preference-toggle"><span><strong>完成后显示文件</strong><small>任务结束时自动定位输出音频。</small></span><button className={`switch ${settings.autoRevealOutput ? "is-on" : ""}`} role="switch" aria-checked={settings.autoRevealOutput} onClick={() => void patchSettings({ autoRevealOutput: !settings.autoRevealOutput }).catch(error => setMessage(error instanceof Error ? error.message : "保存失败"))}><span /></button></label></div></section>
        <section className="install-overview">
          <div className="install-heading"><div><p className="eyebrow">{selected.id}</p><h2>{selected.name}</h2><p>源码、环境与模型相互独立检测；已存在的本地文件不会被重复标记为完成。</p></div><div className="install-score"><strong>{[selected.source, selected.environment, selected.modelsState].filter(state => state.installed).length}/3</strong><span>组件就绪</span></div></div>
          <div className={`install-path ${selected.origin === "bound" ? "is-bound" : ""}`}><div><FolderOpen size={17} /><span><small>{selected.origin === "bound" ? "当前绑定的本地程序" : "安装目录"}</small><strong>{selected.origin === "bound" ? selected.source.path : selected.installPath || "尚未选择安装目录"}</strong></span></div><button className="secondary-button" onClick={selected.origin === "bound" ? () => void scanLocal(false) : choosePath}>{selected.origin === "bound" ? "重新扫描" : "选择目录"}</button></div>
          <div className="component-grid">{[{ icon: Code2, label: "项目源码", data: selected.source }, { icon: TerminalSquare, label: "Python 环境", data: selected.environment }, { icon: HardDrive, label: "模型权重", data: selected.modelsState }].map(component => <article key={component.label}><div className="component-icon"><component.icon size={18} /></div><div><span>{component.label}</span><strong>{component.data.version || component.data.detail || component.data.state}</strong>{component.data.path && <small title={component.data.path}>{component.data.path}</small>}</div><StateBadge value={component.data} /></article>)}</div>
          {selected.origin === "bound" ? <div className="existing-engine-callout"><CheckCircle2 size={20} /><span><strong>已直接使用现有本地程序</strong><small>软件只保存这些路径的引用，不会移动、覆盖或重复下载源码与模型；生成任务已切换到这套本地环境。</small></span></div> : <><div className="runtime-license-list">
            <label className="license-accept"><span className="license-control"><input type="checkbox" checked={sourceLicenseAccepted[selectedEngine]} onChange={event => setSourceLicenseAccepted(current => ({ ...current, [selectedEngine]: event.target.checked }))} /><span className={`license-checkbox ${sourceLicenseAccepted[selectedEngine] ? "checked" : ""}`} aria-hidden="true">{sourceLicenseAccepted[selectedEngine] && <Check size={16} />}</span></span><span><strong>接受 {selected.name} 项目源码许可证</strong><small>{selected.license || "上游项目许可证"}。{selected.licenseUrl && <a href={selected.licenseUrl} target="_blank" rel="noreferrer">查看官方条款 <ExternalLink size={11} /></a>}</small></span></label>
            {[...selected.requiredRuntimeLicenses, ...selected.requiredTools].map(runtime => { const accepted = runtimeLicensesAccepted[selectedEngine].has(runtime.id); return <label className="license-accept" key={runtime.id}><span className="license-control"><input type="checkbox" checked={accepted} onChange={event => toggleRuntimeLicense(runtime.id, event.target.checked)} /><span className={`license-checkbox ${accepted ? "checked" : ""}`} aria-hidden="true">{accepted && <Check size={16} />}</span></span><span><strong>接受 {runtime.name} 许可证</strong><small>{runtime.license}。{runtime.licenseUrl && <a href={runtime.licenseUrl} target="_blank" rel="noreferrer">查看官方条款 <ExternalLink size={11} /></a>}{runtime.sourcePage && <> · <a href={runtime.sourcePage} target="_blank" rel="noreferrer">官方来源 <ExternalLink size={11} /></a></>}</small></span></label>; })}
          </div>
          <div className="setup-callout"><div><FileCode2 size={18} /><span><strong>自动安装源码与运行环境</strong><small>下载固定版本官方源码和工具，校验 SHA-256，并创建隔离 Python 环境；无需系统 Git、Python、uv 或 FFmpeg。模型权重不会在此步骤下载。</small></span></div><label className="device-choice"><span>计算环境</span><select value={devices[selectedEngine]} onChange={event => setDevices(current => ({ ...current, [selectedEngine]: event.target.value as "CPU" | "CU126" | "CU128" }))}><option value="CU128">CUDA 12.8</option><option value="CU126">CUDA 12.6</option><option value="CPU">仅 CPU</option></select></label><button className="primary-button" onClick={startSetup} disabled={!selected.installPath || !setupLicensesAccepted}><CloudDownload size={16} />一键安装</button></div>
          </>}
        </section>

        {selected.origin !== "bound" && <section className="models-panel"><div className="panel-title"><div><p className="eyebrow">独立下载</p><h2>基础模型权重</h2><p>勾选需要的官方基础资产后下载。角色声音与 GPT-SoVITS 社区模型在各自的资料库中管理。</p></div><button className="primary-button" onClick={startModels} disabled={!selectedModels[selectedEngine].size || !modelLicensesAccepted[selectedEngine]}><CloudDownload size={16} />下载已选（{selectedModels[selectedEngine].size}）</button></div><div className="model-table"><div className="model-table-head"><span>选择 / 模型</span><span>体积</span><span>来源与许可证</span><span>状态</span></div>{selected.models.length === 0 ? <div className="model-empty"><HardDrive size={24} /><div><strong>后端尚未返回模型清单</strong><span>请检查本地服务与安装目录后重新检测。</span></div></div> : selected.models.map(model => <div className="model-row" key={model.id}><button className={`model-check ${selectedModels[selectedEngine].has(model.id) ? "checked" : ""}`} onClick={() => toggleModel(model.id)} aria-label={`选择 ${model.name}`}>{selectedModels[selectedEngine].has(model.id) && <Check size={14} />}</button><div className="model-name"><strong>{model.name}</strong><span>{model.required ? "核心必需" : "可选组件"}</span></div><div className="model-size"><HardDrive size={14} />{model.size || (model.bytes ? `${(model.bytes / 1024 ** 3).toFixed(1)} GB` : "后端未提供体积")}</div><div className="model-source"><span>{model.sourceUrl ? <a href={model.sourceUrl} target="_blank" rel="noreferrer">{model.source || "官方模型页"} <ExternalLink size={11} /></a> : model.source || "后端未提供官方来源"}</span><small><ScrollText size={12} />{model.licenseUrl ? <a href={model.licenseUrl} target="_blank" rel="noreferrer">{model.license || "官方许可证"}</a> : model.license || "下载前请阅读上游许可证"}</small></div><span className={`model-status ${model.installed ? "installed" : "missing"}`}>{model.installed ? "已校验" : model.state === "running" ? "下载中" : "未下载"}</span></div>)}</div><label className="license-accept model-license-accept"><span className="license-control"><input type="checkbox" checked={modelLicensesAccepted[selectedEngine]} onChange={event => setModelLicensesAccepted(current => ({ ...current, [selectedEngine]: event.target.checked }))} /><span className={`license-checkbox ${modelLicensesAccepted[selectedEngine] ? "checked" : ""}`} aria-hidden="true">{modelLicensesAccepted[selectedEngine] && <Check size={16} />}</span></span><span><strong>我已阅读并接受所选模型各自的许可证</strong><small>模型许可可能与源码许可不同；只有明确勾选后才会开始下载。</small></span></label></section>}

        <section className="transfers-panel"><div className="panel-title compact"><div><p className="eyebrow">实时任务</p><h2>安装与下载</h2></div><span className="transfer-count">{activeTransfers.length} 个活动任务</span></div>{transfers.length === 0 ? <div className="transfer-empty"><Gauge size={25} /><div><strong>暂无安装任务</strong><span>开始安装或模型下载后，真实进度、速度与日志会显示在这里。</span></div></div> : <div className="transfer-list">{transfers.map(transfer => <article className={`transfer-item ${transfer.state}`} key={transfer.id}><div className="transfer-top"><div className="transfer-kind">{transfer.kind === "model" ? <HardDrive size={16} /> : <CloudDownload size={16} />}</div><div className="transfer-meta"><div><strong>{transfer.label}</strong><span>{engines[transfer.engine].name}</span></div><small>{transfer.downloaded && transfer.total ? `${transfer.downloaded} / ${transfer.total}` : transfer.state}{transfer.speed ? ` · ${transfer.speed}` : ""}</small></div><div className="transfer-actions">{transfer.state === "running" && <button onClick={() => transferAction(transfer.id, "pause")} title="暂停"><Pause size={15} /></button>}{transfer.state === "paused" && <button onClick={() => transferAction(transfer.id, "resume")} title="继续"><Play size={15} /></button>}{transfer.state === "failed" && <button onClick={() => transferAction(transfer.id, "retry")} title="重试"><RotateCcw size={15} /></button>}{["queued", "running", "paused"].includes(transfer.state) && <button onClick={() => transferAction(transfer.id, "cancel")} title="取消"><Square size={14} /></button>}<button onClick={() => setExpandedLog(expandedLog === transfer.id ? null : transfer.id)} title="查看日志">{expandedLog === transfer.id ? <ChevronDown size={15} /> : <ChevronRight size={15} />}</button></div></div><div className="transfer-progress"><i style={{ width: `${transfer.progress}%` }} /></div><div className="transfer-foot"><span>{Math.round(transfer.progress)}%</span>{transfer.error && <strong>{transfer.error}</strong>}</div>{expandedLog === transfer.id && <pre className="transfer-log">{transfer.log?.length ? transfer.log.join("\n") : transfer.error || "后端尚未返回日志。"}</pre>}</article>)}</div>}</section>
      </main>
    </div>
  </div>;
}
