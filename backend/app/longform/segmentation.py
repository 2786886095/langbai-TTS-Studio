"""Offset-preserving, bounded segmentation; raw source is never stripped."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata

from .models import SegmentOptions

VERSION = "offset-v2.2"
HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002fa1f]")
BOUNDARY = set("。！？!?；;\n")


def digest(value) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def has_speech(text: str) -> bool:
    return any(unicodedata.category(c)[0] in "LN" for c in text)


def _safe_boundary(text: str, index: int) -> bool:
    if index <= 0 or index >= len(text):
        return True
    left, right = text[index - 1], text[index]
    if left.isdigit() and right.isdigit():
        return False
    if unicodedata.category(right).startswith("M") or right in "\ufe0e\ufe0f\u200d" or left == "\u200d" or 0x1F3FB <= ord(right) <= 0x1F3FF:
        return False
    if 0xDC00 <= ord(right) <= 0xDFFF and 0xD800 <= ord(left) <= 0xDBFF:
        return False
    return True


def _character_chunks(text, maximum):
    start = 0
    while start < len(text):
        end = min(len(text), start + maximum)
        while end > start and not _safe_boundary(text, end):
            end -= 1
        if end == start:
            raise ValueError("输入包含超过单段预算的不可分字符序列，请手动增加段落")
        yield text[start:end]
        start = end


def _breeze_parts(text, maximum, hard_limit=300):
    """Same 110/120-Han packing as the proven benchmark, plus bounded fallbacks.

    No source normalization is performed here. For ordinary Chinese sentences
    below the hard character cap this produces the benchmark's exact requests.
    """
    units = re.findall(r".+?(?:[。！？!?；;]+[”’」』]*\s*|$)", text, flags=re.S)
    assert "".join(units) == text
    prepared = []
    for unit in units:
        matches = list(HAN.finditer(unit))
        offset, han_offset = 0, 0
        while len(matches) - han_offset > maximum:
            cut = matches[han_offset + maximum].start()
            prepared.extend(_character_chunks(unit[offset:cut], hard_limit))
            offset, han_offset = cut, han_offset + maximum
        if offset < len(unit):
            prepared.extend(_character_chunks(unit[offset:], hard_limit))
    parts, current = [], ""
    target = min(110, maximum)
    for unit in prepared:
        combined = current + unit
        if current and (len(HAN.findall(combined)) > maximum or len(combined) > hard_limit):
            parts.append(current)
            current = ""
        current += unit
        count = len(HAN.findall(current))
        if count >= target or count == 0 and len(current) >= maximum:
            parts.append(current)
            current = ""
    if current:
        if parts and len(HAN.findall(parts[-1] + current)) <= maximum and len(parts[-1] + current) <= hard_limit:
            parts[-1] += current
        else:
            parts.append(current)
    return parts


def plan_text(text: str, engine: str, options: SegmentOptions | dict | None = None) -> dict:
    options = options if isinstance(options, SegmentOptions) else SegmentOptions.model_validate(options or {})
    if not text or not has_speech(text):
        raise ValueError("文稿需要至少一个可发音的文字或数字")
    if len(HAN.findall(text)) > 100000 or len(text) > 500000:
        raise ValueError("首版上限为 10 万汉字、50 万总字符，请按章节拆分")
    breeze = engine in {"breeze", "breeze_tts2", "breeze_tts_2"}
    strategy = options.strategy or ("breeze_han_v1" if breeze else "generic_v2")
    maximum = min(options.max_chars or (120 if breeze else 180), 120 if breeze and strategy == "breeze_han_v1" else 300 if breeze else 1000)
    target = min(110, maximum) if breeze else maximum
    # A very long numeric token cannot satisfy both the budget and no-number-split rule.
    hard_limit = 300 if breeze else maximum
    if re.search(r"\d{" + str(hard_limit + 1) + r",}", text):
        raise ValueError(f"连续数字超过 {hard_limit} 字符，无法安全分段；请加入停顿或改写")
    document_id = hashlib.sha256(text.encode("utf-8")).hexdigest()
    plan_id = digest([document_id, strategy, maximum, VERSION])
    items = []
    start = 0
    breeze_parts = iter(_breeze_parts(text, maximum)) if breeze and strategy == "breeze_han_v1" else None
    while start < len(text):
        if breeze_parts is not None:
            end = start + len(next(breeze_parts))
        else:
            end = _generic_end(text, start, maximum, target, strategy)
        raw = text[start:end]
        request = raw.strip()
        skipped = not has_speech(request)
        warnings = []
        ending = raw.rstrip().rstrip("”’」』")
        if end < len(text) and ending and ending[-1] not in BOUNDARY:
            warnings.append("达到预算，已在安全字符边界强制切分")
        items.append({"id": f"s{len(items):06d}", "index": len(items), "start": start, "end": end,
                      "text": raw, "requestText": "" if skipped else request,
                      "normalization": "trim_edges_v1", "status": "skipped" if skipped else "pending",
                      "warnings": warnings, "splitDepth": 0})
        start = end
    reconstructed = "".join(item["text"] for item in items)
    assert reconstructed == text and hashlib.sha256(reconstructed.encode("utf-8")).hexdigest() == document_id
    return {"documentId": document_id, "planId": plan_id, "strategy": strategy,
            "maxChars": maximum, "maxRequestCharacters": hard_limit, "sourceCharacters": len(text), "exactCoverage": True,
            "warnings": ["Breeze 非中文长文尚未验收，请先用短段试听"] if breeze and not HAN.search(text) else [],
            "totalSegments": sum(s["status"] != "skipped" for s in items), "segments": items}


def _generic_end(text, start, maximum, target, strategy):
    limit = min(len(text), start + maximum)
    preferred = min(len(text), start + target)
    candidates = [i for i in range(start + 1, limit + 1) if text[i - 1] in BOUNDARY and _safe_boundary(text, i)]
    # Prefer a full sentence near the target, but never exceed the hard budget.
    nearby = [i for i in candidates if i >= preferred]
    end = nearby[0] if nearby else (candidates[-1] if candidates else limit)
    if strategy == "paragraph":
        paragraphs = [i for i in candidates if text[i - 1] == "\n"]
        if paragraphs:
            end = paragraphs[-1]
    while end > start and not _safe_boundary(text, end):
        end -= 1
    if end == start:
        raise ValueError("输入包含超过单段预算的不可分字符序列，请手动增加段落")
    return end


def split_segment(segment: dict, full_text: str) -> list[dict] | None:
    start, end = segment["start"], segment["end"]
    if segment.get("splitDepth", 0) >= 2 or end - start < 4:
        return None
    middle = (start + end) // 2
    candidates = [i for i in range(start + 1, end) if _safe_boundary(full_text, i)]
    if not candidates:
        return None
    sentences = [i for i in candidates if full_text[i - 1] in BOUNDARY]
    cut = min(sentences or candidates, key=lambda i: abs(i - middle))
    result = []
    for number, (a, b) in enumerate(((start, cut), (cut, end))):
        raw = full_text[a:b]
        request = raw.strip()
        result.append({"id": f"{segment['id']}.{number}", "index": segment["index"], "start": a, "end": b,
                       "text": raw, "requestText": request if has_speech(request) else "",
                       "normalization": "trim_edges_v1", "status": "pending" if has_speech(request) else "skipped",
                       "warnings": ["上级片段验收失败，自动拆分"], "splitDepth": segment.get("splitDepth", 0) + 1})
    return result
