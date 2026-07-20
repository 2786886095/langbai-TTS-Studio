"""Persistent JSON-lines worker, launched with the selected engine's own Python."""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine_runtime import has_override, resolve_torch_device


PROTOCOL_OUT = sys.stdout
sys.stdout = sys.stderr  # third-party model logging must not corrupt JSON-RPC stdout
ENGINE = os.environ["LANGBAI_ENGINE"]
PROJECT = Path(os.environ["LANGBAI_PROJECT_PATH"]).resolve()
RUNTIME_ROOT = Path(os.environ.get("LANGBAI_RUNTIME_ROOT", str(PROJECT))).resolve()
sys.path.insert(0, str(PROJECT))
if ENGINE == "voxcpm":
    sys.path.insert(0, str(PROJECT / "src"))

_model = None
_model_key = None


def emit(payload: dict) -> None:
    PROTOCOL_OUT.write(json.dumps(payload, ensure_ascii=False) + "\n")
    PROTOCOL_OUT.flush()


def runtime_subset(parameters: dict, keys: tuple[str, ...]) -> tuple:
    return tuple((key, json.dumps(parameters.get(key), sort_keys=True, ensure_ascii=False)) for key in keys)


def index_synthesize(text: str, output: Path, p: dict) -> None:
    global _model, _model_key
    from indextts.infer_v2 import IndexTTS2
    runtime_keys = ("model_dir", "device", "use_fp16", "use_cuda_kernel", "use_deepspeed", "use_accel", "use_torch_compile")
    key = runtime_subset(p, runtime_keys)
    if _model is None or key != _model_key:
        model_dir = Path(p.get("model_dir") or PROJECT / "checkpoints").resolve()
        _model = IndexTTS2(
            cfg_path=str(model_dir / "config.yaml"), model_dir=str(model_dir),
            device=p.get("device"), use_fp16=bool(p.get("use_fp16", True)),
            use_cuda_kernel=p.get("use_cuda_kernel"), use_deepspeed=bool(p.get("use_deepspeed", False)),
            use_accel=bool(p.get("use_accel", False)), use_torch_compile=bool(p.get("use_torch_compile", False)),
        )
        _model_key = key
    mode = p.get("emotion_mode")
    if not mode:
        mode = "vector" if p.get("emotion_vector") is not None else ("text" if p.get("emotion_text") else ("random" if p.get("use_random") else "audio"))
    vector = p.get("emotion_vector") if mode == "vector" else None
    if vector is not None and len(vector) != 8:
        raise ValueError("IndexTTS2 emotion_vector 必须正好包含 8 个数值")
    converters = {
        "do_sample": bool,
        "top_p": float,
        "top_k": int,
        "temperature": float,
        "length_penalty": float,
        "num_beams": int,
        "repetition_penalty": float,
        "max_mel_tokens": int,
    }
    kwargs = {
        name: converter(p[name])
        for name, converter in converters.items()
        if p.get(name) is not None
    }
    if mode == "text" and not p.get("emotion_text"):
        raise ValueError("情感模式为 text 时必须填写 emotion_text")
    if mode == "vector" and vector is None:
        raise ValueError("情感模式为 vector 时必须填写 emotion_vector")
    result = _model.infer_generator(
        spk_audio_prompt=p.get("speaker_audio"), text=text, output_path=str(output),
        emo_audio_prompt=p.get("emotion_audio") if mode == "audio" else None,
        emo_alpha=float(p.get("emotion_alpha", 1.0)), emo_vector=vector,
        use_emo_text=mode == "text", emo_text=p.get("emotion_text") if mode == "text" else None,
        use_random=mode == "random", interval_silence=int(p.get("interval_silence", 200)),
        max_text_tokens_per_segment=int(p.get("max_text_tokens_per_segment", 120)),
        stream_return=bool(p.get("stream_return", False)),
        quick_streaming_tokens=int(p.get("quick_streaming_tokens", 0)), **kwargs,
    )
    list(result)
    if not output.is_file():
        raise RuntimeError("IndexTTS2 未返回音频")


def voxcpm_synthesize(text: str, output: Path, p: dict) -> None:
    global _model, _model_key
    from voxcpm import VoxCPM
    from voxcpm.core import LoRAConfig
    runtime_keys = ("model_path", "hf_model_id", "cache_dir", "local_files_only", "device",
                    "enable_denoiser", "zipenhancer_model_id", "optimize", "lora_weights_path",
                    "lora_r", "lora_alpha", "lora_dropout", "lora_disable_lm", "lora_disable_dit", "lora_enable_proj")
    key = runtime_subset(p, runtime_keys)
    if _model is None or key != _model_key:
        lora_config = None
        if p.get("lora_weights_path"):
            lora_config = LoRAConfig(
                r=int(p.get("lora_r", 32)), alpha=int(p.get("lora_alpha", 16)),
                dropout=float(p.get("lora_dropout", 0.0)), enable_lm=not bool(p.get("lora_disable_lm", False)),
                enable_dit=not bool(p.get("lora_disable_dit", False)), enable_proj=bool(p.get("lora_enable_proj", False)),
            )
        common = dict(
            optimize=bool(p.get("optimize", True)), device=p.get("device") or "auto",
            lora_weights_path=p.get("lora_weights_path"), lora_config=lora_config,
        )
        if p.get("model_path"):
            _model = VoxCPM(
                voxcpm_model_path=p["model_path"],
                zipenhancer_model_path=p.get("zipenhancer_model_id") if p.get("enable_denoiser", True) else None,
                enable_denoiser=bool(p.get("enable_denoiser", True)), **common,
            )
        else:
            cache_dir = p.get("cache_dir")
            # huggingface_hub accepts its cache root (the directory that
            # directly contains models--*). Older VoxCPM setups often expose
            # the parent directory and keep the actual cache in ``hub``.
            if cache_dir:
                cache_path = Path(cache_dir)
                repo_cache = "models--" + (p.get("hf_model_id") or "openbmb/VoxCPM2").replace("/", "--")
                if not (cache_path / repo_cache).exists() and (cache_path / "hub" / repo_cache).exists():
                    cache_dir = str(cache_path / "hub")
            _model = VoxCPM.from_pretrained(
                hf_model_id=p.get("hf_model_id") or "openbmb/VoxCPM2",
                load_denoiser=bool(p.get("enable_denoiser", True)),
                zipenhancer_model_id=p.get("zipenhancer_model_id") or "iic/speech_zipenhancer_ans_multiloss_16k_base",
                cache_dir=cache_dir, local_files_only=bool(p.get("local_files_only", True)), **common,
            )
        _model_key = key
    control = (p.get("control") or "").strip()
    final_text = f"({control}){text}" if control else text
    method = _model.generate_streaming if p.get("streaming", False) else _model.generate
    audio = method(
        text=final_text, prompt_wav_path=p.get("prompt_audio"), prompt_text=p.get("prompt_text"),
        reference_wav_path=p.get("reference_audio"), cfg_value=float(p.get("cfg_value", 2.0)),
        inference_timesteps=int(p.get("inference_timesteps", 10)), min_len=int(p.get("min_len", 2)),
        max_len=int(p.get("max_len", 4096)), normalize=bool(p.get("normalize", False)),
        denoise=bool(p.get("denoise", False)), retry_badcase=bool(p.get("retry_badcase", True)),
        retry_badcase_max_times=int(p.get("retry_badcase_max_times", 3)),
        retry_badcase_ratio_threshold=float(p.get("retry_badcase_ratio_threshold", 6.0)), seed=p.get("seed"),
    )
    if p.get("streaming", False):
        import numpy as np
        audio = np.concatenate(list(audio))
    import soundfile as sf
    sf.write(str(output), audio, _model.tts_model.sample_rate)


def gpt_sovits_synthesize(text: str, output: Path, p: dict) -> None:
    global _model, _model_key
    config_path = Path(p.get("tts_config_path") or RUNTIME_ROOT / "GPT_SoVITS/configs/tts_infer.yaml").resolve()
    # Weight paths in upstream YAML are relative to the distribution root.
    runtime_root = config_path.parents[2]
    os.chdir(runtime_root)
    sys.path.insert(0, str(PROJECT / "GPT_SoVITS"))
    from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config
    runtime_keys = ("tts_config_path", "device", "is_half", "t2s_weights_path", "vits_weights_path",
                    "bert_base_path", "cnhuhbert_base_path")
    key = runtime_subset(p, runtime_keys)
    if _model is None or key != _model_key:
        config = TTS_Config(str(config_path))
        for name in ("device", "is_half", "t2s_weights_path", "vits_weights_path", "bert_base_path", "cnhuhbert_base_path"):
            if has_override(p.get(name)):
                setattr(config, name, p[name])
        config.device = resolve_torch_device(config.device)
        if config.device == "cpu":
            config.is_half = False
        config.update_configs()
        _model = TTS(config)
        _model_key = key
    mode = p.get("streaming_mode", 0)
    mode = int(mode) if not isinstance(mode, bool) else int(mode)
    request = {
        "text": text, "text_lang": p.get("text_language", "auto"),
        "ref_audio_path": p.get("reference_audio"), "aux_ref_audio_paths": p.get("aux_reference_audios") or [],
        "prompt_text": p.get("prompt_text", ""), "prompt_lang": p.get("prompt_language", "auto"),
        "top_k": int(p.get("top_k", 15)), "top_p": float(p.get("top_p", 1.0)),
        "temperature": float(p.get("temperature", 1.0)), "text_split_method": p.get("text_split_method", "cut5"),
        "batch_size": int(p.get("batch_size", 1)), "batch_threshold": float(p.get("batch_threshold", 0.75)),
        "split_bucket": bool(p.get("split_bucket", True)), "speed_factor": float(p.get("speed_factor", 1.0)),
        "fragment_interval": float(p.get("fragment_interval", 0.3)), "seed": int(p.get("seed", -1)),
        "parallel_infer": bool(p.get("parallel_infer", True)),
        "repetition_penalty": float(p.get("repetition_penalty", 1.35)),
        "sample_steps": int(p.get("sample_steps", 32)), "super_sampling": bool(p.get("super_sampling", False)),
        "streaming_mode": mode in (2, 3), "return_fragment": bool(p.get("return_fragment", mode == 1)),
        "fixed_length_chunk": bool(p.get("fixed_length_chunk", mode == 3)), "overlap_length": int(p.get("overlap_length", 2)),
        "min_chunk_length": int(p.get("min_chunk_length", 16)),
    }
    outputs = list(_model.run(request))
    if not outputs:
        raise RuntimeError("GPT-SoVITS 未返回音频")
    import numpy as np
    import soundfile as sf
    sample_rate = outputs[0][0]
    audio = np.concatenate([item[1] for item in outputs])
    sf.write(str(output), audio, sample_rate)


HANDLERS = {"indextts2": index_synthesize, "voxcpm": voxcpm_synthesize, "gpt_sovits": gpt_sovits_synthesize}


def main() -> None:
    if ENGINE not in HANDLERS:
        emit({"ready": False, "error": f"unknown engine: {ENGINE}"})
        return
    emit({"ready": True, "engine": ENGINE})
    for line in sys.stdin:
        try:
            # PowerShell may prefix redirected UTF-8 input with a BOM. The
            # desktop adapter does not, but accepting it makes the worker CLI
            # and diagnostics reliable on Windows too.
            request = json.loads(line.lstrip("\ufeff"))
            if request.get("action") == "shutdown":
                return
            if request.get("action") != "synthesize":
                raise ValueError("unknown action")
            output = Path(request["output_path"])
            output.parent.mkdir(parents=True, exist_ok=True)
            HANDLERS[ENGINE](request["text"], output, request.get("parameters") or {})
            emit({"id": request.get("id"), "ok": True, "output_path": str(output)})
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            emit({"id": request.get("id") if "request" in locals() else None, "ok": False,
                  "error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    main()
