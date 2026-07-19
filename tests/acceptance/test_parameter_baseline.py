from __future__ import annotations

import re


CJK_RE = re.compile(r"[\u3400-\u9fff]")


EXPECTED_ENGINES = {"indextts2", "voxcpm", "gpt_sovits"}

MINIMUM_REQUIRED = {
    "indextts2": {
        "text",
        "speaker_audio",
        "emotion_mode",
        "emotion_audio",
        "emotion_weight",
        "emotion_vector",
        "emotion_text",
        "emotion_random",
        "interval_silence",
        "do_sample",
        "top_p",
        "top_k",
        "temperature",
        "length_penalty",
        "num_beams",
        "repetition_penalty",
        "max_mel_tokens",
        "max_text_tokens_per_segment",
    },
    "voxcpm": {
        "text",
        "control_instruction",
        "prompt_wav_path",
        "prompt_text",
        "reference_wav_path",
        "cfg_value",
        "inference_timesteps",
        "min_len",
        "max_len",
        "normalize",
        "denoise",
        "retry_badcase",
        "retry_badcase_max_times",
        "retry_badcase_ratio_threshold",
        "streaming",
        "seed",
    },
    "gpt_sovits": {
        "text",
        "text_lang",
        "ref_audio_path",
        "aux_ref_audio_paths",
        "prompt_lang",
        "prompt_text",
        "top_k",
        "top_p",
        "temperature",
        "text_split_method",
        "batch_size",
        "batch_threshold",
        "split_bucket",
        "speed_factor",
        "fragment_interval",
        "seed",
        "media_type",
        "streaming_mode",
        "parallel_infer",
        "repetition_penalty",
        "sample_steps",
        "super_sampling",
        "overlap_length",
        "min_chunk_length",
    },
}


def test_exactly_three_supported_engines(parameter_baseline: dict) -> None:
    assert set(parameter_baseline["engines"]) == EXPECTED_ENGINES


def test_required_native_parameter_surface_is_locked(parameter_baseline: dict) -> None:
    for engine, required in MINIMUM_REQUIRED.items():
        actual = set(parameter_baseline["engines"][engine]["required_surface"])
        missing = sorted(required - actual)
        assert not missing, f"{engine} baseline is missing native parameters: {missing}"


def test_every_parameter_has_real_chinese_help(parameter_baseline: dict) -> None:
    for engine, spec in parameter_baseline["engines"].items():
        parameters = spec["parameters"]
        names = [item["name"] for item in parameters]
        assert len(names) == len(set(names)), f"{engine} has duplicate parameter names"
        for item in parameters:
            label = item.get("label_zh", "")
            help_text = item.get("help_zh", "")
            assert CJK_RE.search(label), f"{engine}.{item['name']} has no Chinese label"
            assert len(help_text) >= 12 and CJK_RE.search(help_text), (
                f"{engine}.{item['name']} needs a meaningful Chinese usage description"
            )
            assert item.get("type"), f"{engine}.{item['name']} has no type"
            assert item.get("category") in {"input", "generation", "runtime", "output"}


def test_required_surface_entries_exist_in_parameter_catalog(parameter_baseline: dict) -> None:
    for engine, spec in parameter_baseline["engines"].items():
        names = {item["name"] for item in spec["parameters"]}
        missing = sorted(set(spec["required_surface"]) - names)
        assert not missing, f"{engine} required surface has no parameter metadata: {missing}"


def test_baseline_records_auditable_upstream_sources(parameter_baseline: dict) -> None:
    for engine, spec in parameter_baseline["engines"].items():
        assert re.fullmatch(r"[0-9a-f]{40}", spec["upstream_commit"]), engine
        assert spec["source_files"], f"{engine} has no source file evidence"
