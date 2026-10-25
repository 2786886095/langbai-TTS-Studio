"""Optional localhost-only Studio entry for the independently installed Gradio UI.

This module performs no network calls, CUDA initialization or model loading on
import. Windows-browser requests are intentional: WSL NAT cannot assume that a
Windows loopback service is reachable through Linux 127.0.0.1.
"""
from __future__ import annotations

import gc
import html
import json
import os
import sys
from urllib.parse import urlsplit

BRIDGE_VERSION = "1.0"
DEFAULT_STUDIO_ORIGIN = "http://127.0.0.1:18765"


def studio_origin() -> str:
    value = os.environ.get("LANGBAI_STUDIO_ORIGIN", DEFAULT_STUDIO_ORIGIN)
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username
            or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("Studio 桥接地址必须是 http://127.0.0.1:<端口>，不允许远程地址")
    port = parsed.port or 80
    if not 1 <= port <= 65535:
        raise ValueError("Studio 桥接端口无效")
    return f"http://127.0.0.1:{port}"


def browser_preflight_js() -> str:
    """A Gradio preprocessor preserves all inputs and rejects a known GPU owner.

    Offline Studio keeps the existing short-clip workflow. The Python-side free
    VRAM guard remains the fallback for direct API callers and network failures.
    """
    url = json.dumps(studio_origin() + "/api/gpu/lease")
    return """async (...args) => {
      let lease;
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 1600);
      try {
        const response = await fetch(URL_VALUE, {signal: controller.signal, cache: "no-store", credentials: "omit"});
        if (!response.ok) return args;
        lease = await response.json();
      } catch (_) {
        return args;
      } finally { clearTimeout(timer); }
      const owner = lease && lease.owner;
      const hasOwner = typeof owner === "string" ? owner.length > 0 : owner && Object.keys(owner).length > 0;
      const residents = Array.isArray(lease && lease.residents) ? lease.residents : [];
      const occupied = residents.some(item => item && (item.running || item.warmed || item.loaded));
      const banner = document.getElementById("langbai-gpu-guard");
      if (hasOwner || occupied) {
        const message = "Studio 正在使用显卡，或仍保留热模型。请先在 Studio 任务页暂停，再到引擎页释放模型后重试。网页不会自动结束其他服务。";
        if (banner) { banner.textContent = message; banner.style.display = "block"; banner.scrollIntoView({block: "nearest"}); }
        throw new Error(message);
      }
      if (banner) { banner.textContent = ""; banner.style.display = "none"; }
      return args;
    }""".replace("URL_VALUE", url)


def guard_free_vram(torch, performance_mode: str) -> None:
    """Reject a cold load that cannot fit; never change inference parameters."""
    minimum_gib = 9.0 if performance_mode == "accelerated" else 8.0
    free, _total = torch.cuda.mem_get_info(0)
    if free < minimum_gib * 1024 ** 3:
        raise RuntimeError(
            f"当前空闲显存约 {free / 1024 ** 3:.2f} GiB，低于本机冷加载预算 {minimum_gib:.1f} GiB。"
            "请在 Studio 引擎页释放热模型，或关闭其他显卡任务后重试；不会自动停止其他进程。"
        )


def runtime_status(engine) -> dict:
    """Read existing Python state only; do not import torch or load a model."""
    acquired = engine.lock.acquire(blocking=False)
    try:
        result = {
            "bridgeVersion": BRIDGE_VERSION, "source": "gradio", "pid": os.getpid(),
            "loaded": bool(engine.loaded), "busy": not acquired,
            "performanceMode": engine.performance_mode,
        }
        if acquired:
            result["model"] = engine.model_info()
            torch = sys.modules.get("torch")
            if torch is not None and torch.cuda.is_initialized():
                result["cudaAllocatedBytes"] = int(torch.cuda.memory_allocated(0))
                result["cudaReservedBytes"] = int(torch.cuda.memory_reserved(0))
        return result
    finally:
        if acquired:
            engine.lock.release()


def unload_runtime(engine) -> dict:
    """Explicitly release this UI's own runtime under its reentrant lock."""
    warnings = []
    with engine.lock:
        torch = sys.modules.get("torch")
        initialized = torch is not None and torch.cuda.is_initialized()
        if initialized:
            try:
                torch.cuda.synchronize()
            except Exception as exc:
                warnings.append(f"等待 CUDA 时出错：{exc}")
        engine.runtime = engine.model = engine.tokenizer = engine.audio_tokenizer = None
        engine.load_seconds = None
        engine.device_name = "尚未加载"
        engine.last_prefill_length = engine.last_cfg_scale = engine.last_model_loaded_for_request = None
        gc.collect()
        if initialized:
            try:
                torch.cuda.empty_cache()
            except Exception as exc:
                warnings.append(f"清理 CUDA 缓存时出错：{exc}")
        result = runtime_status(engine)  # RLock is deliberately reentrant.
        result.update(modelReferencesCleared=True, warnings=warnings)
        if result.get("cudaAllocatedBytes", 0):
            result["warnings"].append("仍有进程内张量分配；请以实际显存与进程状态为准，不代表全部显存已经释放。")
        return result


def attach_studio_tab(gr, engine, model_status):
    """Attach controls while the caller's existing gr.Blocks context is active."""
    origin = html.escape(studio_origin(), quote=True)
    with gr.Tab("长文本", id="langbai-longform"):
        gr.Markdown("## 长文配音与断点续跑\n\n由 Studio 统一分段、保存进度和合并；与桌面端同步。此页面不会再加载一份长文模型。")
        gr.HTML(
            '<p>任务服务未运行时，请从 Windows 启动 langbai 长文任务服务。'
            f'<a href="{origin}/longform" target="_blank" rel="noopener noreferrer">在新窗口打开长文工作台</a></p>'
            f'<iframe src="{origin}/longform?embed=1" title="Studio 长文配音工作台" '
            'style="width:100%;height:800px;min-height:640px;border:1px solid #dce3eb;border-radius:8px;background:white" '
            'sandbox="allow-same-origin allow-scripts allow-forms allow-downloads"></iframe>'
        )
        gr.Markdown("### 短句试听模型\n\n短句和 Studio 长文分别持有模型。使用长文前，可显式释放这里的短句模型；音频文件与采样设置保留。")
        with gr.Row():
            show_status = gr.Button("查看短句模型状态")
            unload_visible = gr.Button("释放短句试听模型")
        state = gr.JSON(label="短句模型状态")
        hidden_status = gr.Button("runtime_status", visible=False)
        hidden_unload = gr.Button("unload_model", visible=False)

        def status_endpoint():
            return runtime_status(engine)

        def unload_endpoint():
            return unload_runtime(engine)

        def unload_visible_endpoint():
            result = unload_runtime(engine)
            return result, "短句模型引用已清理；下次短句生成会重新加载。请以状态中的显存记录为准。"

        queue = {"concurrency_limit": 1, "concurrency_id": "breeze-gpu"}
        hidden_status.click(status_endpoint, inputs=[], outputs=state,
                            api_name="runtime_status", show_api=False, **queue)
        hidden_unload.click(unload_endpoint, inputs=[], outputs=state,
                            api_name="unload_model", show_api=False, **queue)
        show_status.click(status_endpoint, inputs=[], outputs=state, api_name=False, **queue)
        unload_visible.click(unload_visible_endpoint, inputs=[], outputs=[state, model_status], api_name=False, **queue)
