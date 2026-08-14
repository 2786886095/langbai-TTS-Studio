from __future__ import annotations

from pathlib import Path
from collections.abc import Sequence


class SegmentQualityError(RuntimeError):
    """Raised when a generated TTS fragment is unsafe to merge."""


QUALITY_RULES = {
    "stable": {"threshold_db": -48.0, "keep_leading_ms": 120, "keep_trailing_ms": 220},
    "balanced": {"threshold_db": -50.0, "keep_leading_ms": 150, "keep_trailing_ms": 260},
    "expressive": {"threshold_db": -52.0, "keep_leading_ms": 180, "keep_trailing_ms": 320},
}


def prepare_tts_segment(path: str | Path, text: str, *, quality_preset: str = "stable") -> dict:
    """Trim abnormal edge silence and reject unusable generated speech.

    Natural pauses inside the utterance are preserved. Only silence outside the
    first and last active sample is shortened, so labelled script lines remain
    independent while accidental multi-second tails cannot leak into the mix.
    """
    import math
    import numpy as np
    import soundfile as sf

    rules = QUALITY_RULES.get(quality_preset, QUALITY_RULES["stable"])
    audio, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    if not len(audio):
        raise SegmentQualityError("引擎返回了空音频")
    mono = audio.mean(axis=1).astype(np.float32, copy=False)
    threshold = 10 ** (float(rules["threshold_db"]) / 20)
    active = np.flatnonzero(np.abs(mono) >= threshold)
    if not len(active):
        raise SegmentQualityError("片段没有检测到有效声音")

    original_frames = len(mono)
    keep_leading = round(sample_rate * int(rules["keep_leading_ms"]) / 1000)
    keep_trailing = round(sample_rate * int(rules["keep_trailing_ms"]) / 1000)
    start = max(0, int(active[0]) - keep_leading)
    end = min(original_frames, int(active[-1]) + keep_trailing + 1)
    mono = mono[start:end]

    duration = len(mono) / sample_rate
    rms = float(np.sqrt(np.mean(np.square(mono, dtype=np.float64))))
    peak = float(np.max(np.abs(mono)))
    rms_db = 20 * math.log10(max(rms, 1e-12))
    peak_db = 20 * math.log10(max(peak, 1e-12))
    clipping_ratio = float(np.mean(np.abs(mono) >= 0.999))
    character_count = len("".join(text.split()))
    characters_per_second = character_count / max(duration, 1e-9)
    silence_ratio = float(np.mean(np.abs(mono) < threshold))

    problems: list[str] = []
    if duration < 0.06:
        problems.append("时长过短")
    if rms_db < -38:
        problems.append("有效响度过低")
    if clipping_ratio > 0.01:
        problems.append("削波比例过高")
    if character_count >= 8 and not 1.2 <= characters_per_second <= 14:
        problems.append("语音时长与文本长度明显不匹配")
    if duration >= 1 and silence_ratio > 0.75:
        problems.append("静音占比过高")
    if problems:
        raise SegmentQualityError("；".join(problems))

    trimmed_leading_ms = round(start * 1000 / sample_rate)
    trimmed_trailing_ms = round((original_frames - end) * 1000 / sample_rate)
    if start or end != original_frames or audio.shape[1] != 1:
        sf.write(str(path), mono, sample_rate, subtype="PCM_16")
    return {
        "preset": quality_preset,
        "durationSeconds": round(duration, 3),
        "rmsDb": round(rms_db, 2),
        "peakDb": round(peak_db, 2),
        "silenceRatio": round(silence_ratio, 4),
        "charactersPerSecond": round(characters_per_second, 3),
        "trimmedLeadingMs": trimmed_leading_ms,
        "trimmedTrailingMs": trimmed_trailing_ms,
    }


def _gap_milliseconds(count: int, silence_ms: int | Sequence[int]) -> list[int]:
    gap_count = max(0, count - 1)
    if isinstance(silence_ms, int):
        if silence_ms < 0:
            raise ValueError("silence_ms must be non-negative")
        return [silence_ms] * gap_count
    gaps = [int(value) for value in silence_ms]
    if len(gaps) != gap_count or any(value < 0 for value in gaps):
        raise ValueError("silence_ms sequence must contain one non-negative value per audio gap")
    return gaps


def merge_wav_files(inputs: list[str | Path], output: str | Path, *, sample_rate: int,
                    silence_ms: int | Sequence[int] = 0) -> Path:
    """Stream, mono-mix, resample and concatenate WAV files as PCM-16.

    The output is written segment-by-segment instead of collecting the complete
    program in memory. Files predicted to exceed the classic WAV 4 GiB limit
    use the RF64 container while keeping the familiar ``.wav`` extension.
    """
    if not inputs:
        raise ValueError("at least one input WAV is required")
    try:
        import numpy as np
        import soundfile as sf
    except ModuleNotFoundError:
        return _merge_pcm_wav_stdlib(inputs, output, sample_rate=sample_rate, silence_ms=silence_ms)

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    gap_frames = [round(sample_rate * value / 1000) for value in _gap_milliseconds(len(inputs), silence_ms)]
    estimated_frames = sum(gap_frames)
    for item in inputs:
        info = sf.info(str(item))
        estimated_frames += round(info.frames * sample_rate / info.samplerate)
    estimated_pcm_bytes = estimated_frames * 2  # mono PCM-16
    container = "RF64" if estimated_pcm_bytes >= 0xFFFF0000 else "WAV"
    silence = np.zeros(min(max(gap_frames, default=0), sample_rate), dtype=np.float32)
    with sf.SoundFile(str(output_path), mode="w", samplerate=sample_rate, channels=1,
                      subtype="PCM_16", format=container) as destination:
        for index, item in enumerate(inputs):
            if index and gap_frames[index - 1]:
                remaining = gap_frames[index - 1]
                while remaining:
                    block = silence[:min(remaining, len(silence))]
                    destination.write(block)
                    remaining -= len(block)
            audio, source_rate = sf.read(str(item), dtype="float32", always_2d=True)
            mono = audio.mean(axis=1)
            if source_rate != sample_rate and len(mono):
                import soxr
                mono = soxr.resample(mono, source_rate, sample_rate, quality="HQ").astype(np.float32)
            destination.write(mono)
    return output_path


def _merge_pcm_wav_stdlib(inputs: list[str | Path], output: str | Path, *, sample_rate: int,
                          silence_ms: int | Sequence[int]) -> Path:
    """Dependency-free PCM fallback used by bootstrap and contract tests on Python <=3.12."""
    import audioop
    import wave

    gaps = [b"\x00\x00" * round(sample_rate * value / 1000) for value in _gap_milliseconds(len(inputs), silence_ms)]
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(sample_rate)
        for index, item in enumerate(inputs):
            if index and gaps[index - 1]:
                destination.writeframesraw(gaps[index - 1])
            with wave.open(str(item), "rb") as source:
                channels = source.getnchannels()
                width = source.getsampwidth()
                source_rate = source.getframerate()
                state = None
                while True:
                    frames = source.readframes(65_536)
                    if not frames:
                        break
                    if channels == 2:
                        frames = audioop.tomono(frames, width, 0.5, 0.5)
                    elif channels != 1:
                        raise RuntimeError("多于双声道或浮点 WAV 需要安装 soundfile/numpy")
                    if width != 2:
                        frames = audioop.lin2lin(frames, width, 2)
                    if source_rate != sample_rate:
                        frames, state = audioop.ratecv(frames, 2, 1, source_rate, sample_rate, state)
                    destination.writeframesraw(frames)
    return output_path
