from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


MODEL_SUFFIXES = {".ckpt", ".pth"}
AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}
IGNORED_PARTS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", "pretrained_models",
    "uvr5_weights", "chinese-hubert-base", "chinese-roberta-wwm-ext-large",
}
IGNORED_WEIGHT_MARKERS = (
    "s2g2333k", "s2d2333k", "s2g488k", "s2d488k", "s2gv3", "s2gv4",
    "vocoder", "hubert", "roberta", "eres2net", "rmvpe", "pretrained",
)


def _normalize_stem(path: Path) -> str:
    value = path.stem.casefold()
    value = re.sub(r"(?i)gpt|sovits|so-vits|weights?|model|finetune|trained", " ", value)
    value = re.sub(r"(?i)(?:^|[_\- ])(?:e|s|epoch|step)\d+(?:$|[_\- ])", " ", value)
    value = re.sub(r"(?i)(?:^|[_\- ])v(?:1|2|3|4)(?:pro(?:plus)?)?(?:$|[_\- ])", " ", value)
    value = re.sub(r"[^\w\u3400-\u9fff]+", "", value, flags=re.UNICODE)
    return value or path.stem.casefold()


def _infer_version(*paths: Path) -> str:
    value = " ".join(str(path).casefold() for path in paths)
    if re.search(r"v2\s*pro\s*plus|v2proplus", value):
        return "v2ProPlus"
    if re.search(r"v2\s*pro|v2pro", value):
        return "v2Pro"
    for version in ("v4", "v3", "v2", "v1"):
        if re.search(rf"(?:^|[^a-z0-9]){version}(?:[^a-z0-9]|$)", value):
            return version
    return "auto"


def _common_root_score(left: Path, right: Path) -> float:
    left_parts = [part.casefold() for part in left.parent.parts]
    right_parts = [part.casefold() for part in right.parent.parts]
    common = 0
    for lpart, rpart in zip(left_parts, right_parts):
        if lpart != rpart:
            break
        common += 1
    if left.parent == right.parent:
        return 0.28
    if left.parent.parent == right.parent.parent:
        return 0.22
    return min(0.18, common * 0.018)


def _pair_score(gpt: Path, sovits: Path) -> float:
    left = _normalize_stem(gpt)
    right = _normalize_stem(sovits)
    similarity = SequenceMatcher(None, left, right).ratio()
    containment = 0.22 if left in right or right in left else 0.0
    return similarity * 0.7 + containment + _common_root_score(gpt, sovits)


def _display_name(gpt: Path, sovits: Path) -> str:
    candidates = [_normalize_stem(gpt), _normalize_stem(sovits)]
    chosen = max(candidates, key=len).strip("_-. ")
    if len(chosen) >= 2:
        return chosen
    value = re.sub(r"(?i)(gpt|sovits|weights?|model)", "", gpt.stem).strip("_-. ")
    return value or gpt.parent.name or "未命名角色"


def _iter_files(root: Path, *, max_files: int = 30_000) -> list[Path]:
    rows: list[Path] = []
    pending = [root]
    while pending and len(rows) < max_files:
        current = pending.pop()
        try:
            children = list(current.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir():
                if child.name.casefold() not in IGNORED_PARTS:
                    pending.append(child)
            elif child.suffix.casefold() in MODEL_SUFFIXES | AUDIO_SUFFIXES:
                rows.append(child)
                if len(rows) >= max_files:
                    break
    return rows


def _is_candidate_weight(path: Path) -> bool:
    lowered = str(path).casefold()
    return not any(marker in lowered for marker in IGNORED_WEIGHT_MARKERS)


def _best_reference(name: str, gpt: Path, sovits: Path, audio_files: list[Path]) -> Path | None:
    if not audio_files:
        return None
    normalized_name = re.sub(r"[^\w\u3400-\u9fff]+", "", name.casefold())
    scored: list[tuple[float, Path]] = []
    for audio in audio_files:
        stem = re.sub(r"[^\w\u3400-\u9fff]+", "", audio.stem.casefold())
        score = SequenceMatcher(None, normalized_name, stem[: max(len(normalized_name), 1)]).ratio() * 0.55
        score += _common_root_score(gpt, audio) * 0.7 + _common_root_score(sovits, audio) * 0.7
        if normalized_name and normalized_name in stem:
            score += 0.35
        scored.append((score, audio))
    score, selected = max(scored, key=lambda item: item[0])
    return selected if score >= 0.32 or len(audio_files) == 1 else None


def _prompt_from_audio(name: str, audio: Path | None) -> str:
    if audio is None:
        return ""
    value = audio.stem
    for separator in ("-", "—", "_", "：", ":"):
        if separator in value:
            prefix, remainder = value.split(separator, 1)
            if name.casefold() in prefix.casefold() or len(prefix) <= 12:
                return remainder.strip()
    return ""


def scan_gpt_sovits_models(paths: list[str | Path]) -> dict[str, Any]:
    roots: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser().resolve(strict=False)
        if path.is_dir() and path not in roots:
            roots.append(path)
    if not roots:
        raise ValueError("没有可扫描的文件夹")

    files: list[Path] = []
    for root in roots:
        files.extend(_iter_files(root))
    gpt_files = sorted((path for path in files if path.suffix.casefold() == ".ckpt" and _is_candidate_weight(path)), key=str)
    sovits_files = sorted((path for path in files if path.suffix.casefold() == ".pth" and _is_candidate_weight(path)), key=str)
    audio_files = [path for path in files if path.suffix.casefold() in AUDIO_SUFFIXES]

    candidates: list[dict[str, Any]] = []
    used_sovits: set[Path] = set()
    for gpt in gpt_files:
        ranked = sorted(((_pair_score(gpt, sovits), sovits) for sovits in sovits_files if sovits not in used_sovits), reverse=True, key=lambda item: item[0])
        if not ranked:
            continue
        score, sovits = ranked[0]
        if score < 0.43 and not (len(gpt_files) == 1 and len(sovits_files) == 1):
            continue
        used_sovits.add(sovits)
        name = _display_name(gpt, sovits)
        reference = _best_reference(name, gpt, sovits, audio_files)
        confidence = min(0.99, 0.58 + score * 0.38 + (0.04 if reference else 0.0))
        warnings: list[str] = []
        if reference is None:
            warnings.append("未自动找到参考音频，创建声音后需要手动补充")
        if _infer_version(gpt, sovits) == "auto":
            warnings.append("未从文件名识别模型版本，将交给 GPT-SoVITS 自动判断")
        candidates.append({
            "id": hashlib.sha256(f"{gpt}\n{sovits}".encode("utf-8")).hexdigest()[:24],
            "name": name,
            "version": _infer_version(gpt, sovits),
            "gptWeightsPath": str(gpt),
            "sovitsWeightsPath": str(sovits),
            "referenceAudio": str(reference) if reference else None,
            "promptText": _prompt_from_audio(name, reference),
            "folder": str(Path(Path(gpt).parent).parent if gpt.parent.name.casefold() in {"gpt", "gpt_weights"} else gpt.parent),
            "confidence": round(confidence, 3),
            "warnings": warnings,
        })

    return {
        "roots": [str(root) for root in roots],
        "scannedFiles": len(files),
        "gptWeights": len(gpt_files),
        "sovitsWeights": len(sovits_files),
        "audioFiles": len(audio_files),
        "items": sorted(candidates, key=lambda item: (-item["confidence"], item["name"])),
    }
