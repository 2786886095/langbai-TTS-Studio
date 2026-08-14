from __future__ import annotations

import re

from .models import ParsedSpeakerLine


SPEAKER_LINE_PATTERN = re.compile(r"^\s*【([^】\r\n]{1,80})】\s*[：:]\s*(.*?)\s*$")


def parse_multi_speaker_script(script: str) -> tuple[list[ParsedSpeakerLine], list[int]]:
    """Parse one explicitly labelled speaker turn per non-empty physical line."""
    parsed: list[ParsedSpeakerLine] = []
    invalid: list[int] = []
    for line_number, raw_line in enumerate(script.splitlines(), start=1):
        if not raw_line.strip():
            continue
        match = SPEAKER_LINE_PATTERN.fullmatch(raw_line)
        if not match:
            invalid.append(line_number)
            continue
        speaker = match.group(1).strip()
        text = match.group(2).strip()
        if not speaker or not text:
            invalid.append(line_number)
            continue
        parsed.append(ParsedSpeakerLine(lineNumber=line_number, speaker=speaker, text=text))
    return parsed, invalid
