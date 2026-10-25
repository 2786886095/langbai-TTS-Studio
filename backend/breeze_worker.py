"""Studio-owned WSL worker. Imports the bound upstream installation read-only.

The module can be imported without Torch/CUDA for transport and ownership tests.
Only the private duplicated stdout pipe carries RPC; upstream output goes to stderr.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

PROTOCOL_PREFIX = "@@LANGBAI_BREEZE@@"
PROTOCOL_VERSION = 1
SOURCE_COMMIT = "6eba901d1845353802652c708a4740fdfc5baa75"
MODEL_REVISION = "3e28c5151381a722f1d8661b4118c298caa77aa4"
KV_PATCH_SHA256 = "9b27b1fefc3d867abf9ac477f03e3a5ecc2c030a06fe4f18a4e56a3c1abe3ac6"
WEIGHT_HASHES = {
    "model-00001-of-00002.safetensors": "abf813781256e10cbe81f2dbb415f897556225d4dfa0282d67aa8ea164e114a9",
    "model-00002-of-00002.safetensors": "36aa73b1a11361e1774db90d9c63c63303b294c022de112aa51904d940edcef1",
    "audio_tokenizer/model.safetensors": "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258",
}
FAST_FLAGS = {"text_encoder": False, "backbone_prefill": False,
              "backbone_decode": True, "depth_decoder": True, "codec": False}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def process_identity(pid: int, token: str | None = None) -> dict:
    proc = Path("/proc") / str(pid)
    # The command name can contain spaces/parentheses; field 22 is after ') '.
    stat = (proc / "stat").read_text().rsplit(") ", 1)[1].split()
    cmdline = (proc / "cmdline").read_bytes()
    result = {"pid": pid, "state": stat[0], "start_time": stat[19], "pgid": int(stat[2]), "sid": int(stat[3]),
              "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
              "cmdline_sha256": hashlib.sha256(cmdline).hexdigest(),
              "exe": str((proc / "exe").resolve()), "uid": proc.stat().st_uid}
    if token is not None:
        # A zombie has already released CUDA/resources and its cmdline is empty.
        # The controller additionally checks its original start time before using this evidence.
        if token.encode() not in cmdline.split(b"\x00") and stat[0] != "Z":
            raise ValueError("实例 token 不在进程启动参数中")
        result["token"] = token
    return result


def identity_matches(expected: dict, actual: dict) -> bool:
    keys = ("pid", "start_time", "pgid", "sid", "boot_id", "cmdline_sha256", "exe", "uid", "token")
    return all(expected.get(key) is not None and expected.get(key) == actual.get(key) for key in keys)


def safe_output(root: Path, target: Path) -> Path:
    root = root.absolute()
    target = target.absolute()
    # Refuse symlinks before resolving them, including parents of the trusted root.
    for item in (target, *target.parents):
        if item.is_symlink():
            raise ValueError("任务输出路径不允许符号链接")
    resolved_root, resolved = root.resolve(), target.resolve()
    if not resolved.is_relative_to(resolved_root) or resolved.parent != resolved_root:
        raise ValueError("输出必须位于受信任的 staging 根目录")
    if not re.fullmatch(r"[0-9a-f]{64}\.[0-9a-f]{32}\.wav\.part", resolved.name):
        raise ValueError("输出名称必须含渲染 key 和独立 attempt ID")
    return resolved


def validate_parameters(text: str, params: dict) -> dict:
    if not text.strip() or len(text) > 300:
        raise ValueError("Breeze 单段文字必须为 1–300 字符，请先自动分段")
    if float(params.get("cfg_scale", 1)) != 1 or str(params.get("instruction") or "").strip():
        raise ValueError("accelerated 首版仅支持 CFG=1 参考声音克隆，声音描述须留空")
    if params.get("performance_mode", "accelerated") != "accelerated":
        raise ValueError("当前 worker 只提供已验证 accelerated 模式")
    ref = str(params.get("ref_audio_path") or params.get("reference_audio") or params.get("ref_audio") or "")
    ref_text = str(params.get("ref_text") or params.get("prompt_text") or "").strip()
    if not ref or not ref_text or not Path(ref).is_absolute() or not Path(ref).is_file():
        raise ValueError("请提供可读取的参考音频及准确参考文字")
    settings = {"seed": int(params.get("seed", 42)), "cfg_scale": 1.0,
                "temperature": float(params.get("temperature", .9)), "top_k": int(params.get("top_k", 50)),
                "top_p": float(params.get("top_p", 1)), "repetition_penalty": float(params.get("repetition_penalty", 1.1)),
                "max_new_tokens": int(params.get("max_new_tokens", 600)),
                "ref_audio_path": ref, "ref_text": ref_text}
    if not all(math.isfinite(settings[k]) for k in ("temperature", "top_p", "repetition_penalty")):
        raise ValueError("采样参数必须为有限数值")
    if not (.1 <= settings["temperature"] <= 2 and 0 < settings["top_p"] <= 1
            and 0 <= settings["top_k"] <= 1024 and .1 <= settings["repetition_penalty"] <= 2
            and 1 <= settings["max_new_tokens"] <= 1500):
        raise ValueError("Breeze 采样参数超出已支持范围")
    return settings


def verify_install(source: Path, model: Path, *, verify_weights: bool = True) -> dict:
    actual_commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True, timeout=15).strip()
    if actual_commit != SOURCE_COMMIT:
        raise ValueError(f"Breeze 源码提交不匹配：{actual_commit}")
    patch = source / "models/stream_runtime/stream/kv_cache.py"
    if sha256_file(patch) != KV_PATCH_SHA256:
        raise ValueError("Breeze KV 修复 sha 不匹配；现有安装不会被自动修改")
    for item in ("config.json", "audio_tokenizer/config.json", "LICENSE"):
        if not (model / item).is_file():
            raise ValueError(f"Breeze 模型文件缺失：{item}")
    if verify_weights:
        for relative, expected in WEIGHT_HASHES.items():
            if sha256_file(model / relative) != expected:
                raise ValueError(f"Breeze 权重 sha 不匹配：{relative}")
    return {"source_commit": actual_commit, "model_revision": MODEL_REVISION,
            "patch_sha256": KV_PATCH_SHA256, "weights_verified": verify_weights}


class Runtime:
    def __init__(self, source: Path, model_path: Path, phase):
        self.source, self.model_path, self.phase = source, model_path, phase
        self.runtime = self.model = self.tokenizer = self.audio_tokenizer = None
        self.prefill = None
        self.load_seconds = None
        self.warmed = False

    def load(self):
        if self.runtime is not None:
            return
        self.phase("loading")
        sys.path.insert(0, str(self.source))
        import torch
        from breeze_infer.runtime import load_runtime, update_generation_config_for_breeze
        from models.fast_streaming import FastBreezeStreamingRuntime, FastStreamingConfig
        torch.set_num_threads(4)
        if not torch.cuda.is_available():
            raise RuntimeError("WSL 中未检测到 CUDA，请检查 NVIDIA 驱动和 PyTorch")
        # CUDA's free memory is authoritative; an occupied Gradio port alone isn't.
        free, _ = torch.cuda.mem_get_info()
        if free < 8.2 * 1024**3:
            raise RuntimeError("显卡空闲显存不足 8.2 GiB；可能有网页试听或其他程序占用，请先释放后继续")
        started = time.perf_counter()
        self.tokenizer, self.model, self.audio_tokenizer = load_runtime(
            self.model_path, device="cuda:0", attn_implementation="eager")
        update_generation_config_for_breeze(self.model)
        config = FastStreamingConfig(max_new_tokens=600, max_seq_len=2048, fast_all=None,
                                     fast_backbone_decode=True, fast_depth_decoder=True,
                                     temperature=.9, top_k=50, top_p=1., repetition_penalty=1.1)
        self.runtime = FastBreezeStreamingRuntime(self.model, self.audio_tokenizer, config, tokenizer=self.tokenizer)
        actual = {key: bool(getattr(self.runtime, "_fast_" + key)) for key in FAST_FLAGS}
        if actual != FAST_FLAGS:
            raise RuntimeError(f"加速配置未生效：{actual}")
        ensure_graphs = self.runtime._ensure_graphs

        def cfg1_graphs(batch_size, guidance_scale, *, depth_bucket_sizes=None):
            if batch_size != 1 or guidance_scale != 1.0:
                raise ValueError("只允许 CFG=1 单分支")
            return ensure_graphs(batch_size, guidance_scale, depth_bucket_sizes=[1])

        self.runtime._ensure_graphs = cfg1_graphs
        build_branch = self.runtime._build_branch_batch

        def checked_branch(inputs):
            branch = build_branch(inputs)
            self.prefill = int(branch.inputs_embeds.shape[1])
            budget = self.runtime.config.max_seq_len - self.prefill - 1
            if budget < self.runtime.config.max_new_tokens:
                raise ValueError(f"上下文预算 {budget} 少于生成上限 {self.runtime.config.max_new_tokens}，请缩短分段或参考")
            return branch

        self.runtime._build_branch_batch = checked_branch
        torch.cuda.synchronize()
        self.load_seconds = time.perf_counter() - started

    def synthesize(self, text: str, target: Path, settings: dict, cancel: threading.Event) -> dict:
        import numpy as np
        import soundfile as sf
        loaded = self.runtime is not None
        self.load()
        import torch
        from breeze_infer.runtime import set_all_seeds
        from breeze_infer.templates import get_template, prepare_inputs, select_template_name
        if cancel.is_set():
            raise InterruptedError("用户已停止当前分段")
        self.runtime.config = replace(self.runtime.config, **{k: settings[k] for k in (
            "max_new_tokens", "temperature", "top_k", "top_p", "repetition_penalty")})
        request = {"id": target.name, "text": text.strip(), "speaker": "S0",
                   "ref_audio_path": settings["ref_audio_path"], "ref_text": settings["ref_text"]}
        self.phase("generating" if self.warmed else "warming")
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
        started, frames, chunks, first_chunk = time.perf_counter(), 0, [], None
        self.prefill = None
        set_all_seeds(settings["seed"])
        with torch.inference_mode():
            inputs = prepare_inputs(self.tokenizer, self.audio_tokenizer, self.model, [request],
                                    get_template(select_template_name(request)), guidance_scale=1.,
                                    guidance_scale_ref=None, guidance_scale_ins=None)
            for chunk in self.runtime.iter_audio_chunks(inputs, request_id=request["id"], seed=settings["seed"]):
                if cancel.is_set():
                    raise InterruptedError("用户已停止当前分段")
                if first_chunk is None:
                    first_chunk = time.perf_counter() - started
                chunks.append(np.asarray(chunk.audio, dtype=np.float32).reshape(-1))
                frames += int(chunk.codec_frames)
                self.phase("generating", frames)
        torch.cuda.synchronize()
        if cancel.is_set():
            raise InterruptedError("用户已停止当前分段")
        if not chunks:
            raise RuntimeError("Breeze 没有生成音频")
        audio = np.concatenate(chunks)
        if not audio.size or not np.isfinite(audio).all():
            raise RuntimeError("Breeze 生成空音频或非法数值")
        # Exclusive open prevents attempt overwrite; sf receives a handle because .part isn't a format.
        with target.open("xb") as stream:
            sf.write(stream, audio, self.runtime.sample_rate, format="WAV", subtype="PCM_16")
            stream.flush()
            os.fsync(stream.fileno())
        elapsed = time.perf_counter() - started
        context_budget = self.runtime.config.max_seq_len - int(self.prefill) - 1
        self.warmed = True
        return {"metadata_version": 3, "path": str(target), "text": text.strip(), **settings,
                "performance_mode": "accelerated", "fast_all": None, "fast_flags": FAST_FLAGS,
                "depth_bucket_sizes": list(self.runtime._depth_decoder_graph.bucket_sizes),
                "sample_rate": self.runtime.sample_rate, "channels": 1, "subtype": "PCM_16",
                "duration_seconds": len(audio) / self.runtime.sample_rate, "elapsed_seconds": elapsed,
                "first_chunk_seconds": first_chunk, "codec_frames": frames, "prefill_length": self.prefill,
                "max_seq_len": 2048, "context_budget_frames": context_budget,
                "possibly_truncated": frames >= min(settings["max_new_tokens"], context_budget),
                "frame_limited": frames >= settings["max_new_tokens"], "context_limited": frames >= context_budget,
                "model_loaded_for_request": not loaded,
                "model_load_seconds_this_request": self.load_seconds if not loaded else 0.,
                "peak_vram_allocated_gib": torch.cuda.max_memory_allocated() / 1024**3,
                "peak_vram_reserved_gib": torch.cuda.max_memory_reserved() / 1024**3,
                "audio_sha256": sha256_file(target), "bytes": target.stat().st_size,
                "ref_audio_sha256": sha256_file(Path(settings["ref_audio_path"]))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--task-root", required=True)
    parser.add_argument("--instance-token", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{32}", args.instance_token):
        raise ValueError("无效实例 token")
    try:
        os.setsid()
    except PermissionError:
        if os.getsid(0) != os.getpid() or os.getpgrp() != os.getpid():
            raise
    # Isolate upstream's native stdout before importing any inference code.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    emit_lock = threading.Lock()
    def emit(data):
        with emit_lock:
            protocol.write(PROTOCOL_PREFIX + json.dumps(data, ensure_ascii=False) + "\n")
            protocol.flush()
    source, model, root = Path(args.source).resolve(), Path(args.model).resolve(), Path(args.task_root).absolute()
    root.mkdir(parents=True, exist_ok=True)
    safe_output(root, root / ("0" * 64 + "." + "0" * 32 + ".wav.part"))
    cancel, shutdown = threading.Event(), threading.Event()
    requests = queue.Queue()
    phase = {"name": "validating", "frames": 0}
    def set_phase(name, codec_frames=0):
        changed = phase["name"] != name
        phase["name"] = name
        phase["frames"] = codec_frames
        if changed:
            emit({"event": "phase", "phase": name, "codecFrames": codec_frames})
    def heartbeat():
        while not shutdown.wait(5):
            emit({"event": "heartbeat", "phase": phase["name"], "codecFrames": phase["frames"], "instanceToken": args.instance_token})
    def read_commands():
        for line in sys.stdin:
            try:
                item = json.loads(line)
                if item.get("op") in {"cancel", "shutdown"}:
                    cancel.set()
                if item.get("op") == "shutdown":
                    shutdown.set()
                if item.get("op") == "synthesize":
                    cancel.clear()
                requests.put(item)
            except (ValueError, TypeError):
                emit({"event": "error", "error": "无效协议消息"})
        cancel.set()
        shutdown.set()
        requests.put({"op": "shutdown"})
        # A stalled native call cannot observe the Python cooperative cancel flag.
        threading.Thread(target=lambda: (time.sleep(30), os._exit(70)), daemon=True).start()
    threading.Thread(target=heartbeat, daemon=True).start()
    threading.Thread(target=read_commands, daemon=True).start()
    try:
        identity = process_identity(os.getpid(), args.instance_token)
        # Register ownership before any expensive file reads so cancellation is safe even during validation.
        emit({"event": "identity", "identity": identity})
        install = verify_install(source, model)
        emit({"event": "hello", "protocolVersion": PROTOCOL_VERSION, "identity": identity,
              **install, "performance_mode": "accelerated", "fast_flags": FAST_FLAGS,
              "depth_bucket_sizes": [1], "max_seq_len": 2048})
        runtime = Runtime(source, model, set_phase)
        set_phase("idle")
        while not shutdown.is_set():
            request = requests.get()
            request_id = request.get("id")
            if request.get("op") == "shutdown":
                break
            if request.get("op") == "cancel":
                continue
            try:
                if request.get("op") != "synthesize":
                    raise ValueError("不支持的 worker 操作")
                target = safe_output(root, Path(request["output"]))
                settings = validate_parameters(request["text"], request.get("params", {}))
                metadata = runtime.synthesize(request["text"], target, settings, cancel)
                emit({"id": request_id, "ok": True, "metadata": {**metadata, **install,
                      "worker_instance": args.instance_token,
                      "worker_instance_id": args.instance_token}})
            except Exception as exc:
                emit({"id": request_id, "ok": False, "error": str(exc), "errorType": type(exc).__name__})
            finally:
                set_phase("idle")
    except Exception as exc:
        emit({"event": "fatal", "error": str(exc)})
        raise
    finally:
        shutdown.set()
        emit({"event": "exiting", "instanceToken": args.instance_token})


if __name__ == "__main__":
    for key, value in {"PYTHONDONTWRITEBYTECODE": "1", "TOKENIZERS_PARALLELISM": "false",
                       "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                       "OMP_NUM_THREADS": "4", "MKL_NUM_THREADS": "4", "TORCHINDUCTOR_COMPILE_THREADS": "2",
                       "MAX_JOBS": "2"}.items():
        os.environ[key] = value
    main()
