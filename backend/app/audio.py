from __future__ import annotations

from pathlib import Path
from collections.abc import Sequence


class SegmentQualityError(RuntimeError):
    """Raised when a generated TTS fragment is unsafe to merge."""


class LongSilenceError(SegmentQualityError):
    """Raised when speech resumes after an abnormally long silent interval."""


QUALITY_RULES = {
    "stable": {
        "threshold_db": -48.0, "keep_leading_ms": 120, "keep_trailing_ms": 220,
        "max_internal_silence_ms": 1800, "repaired_silence_ms": 650,
    },
    "balanced": {
        "threshold_db": -50.0, "keep_leading_ms": 150, "keep_trailing_ms": 260,
        "max_internal_silence_ms": 2200, "repaired_silence_ms": 800,
    },
    "expressive": {
        "threshold_db": -52.0, "keep_leading_ms": 180, "keep_trailing_ms": 320,
        "max_internal_silence_ms": 3000, "repaired_silence_ms": 1200,
    },
}


def _quiet_run_durations_ms(audio, sample_rate: int, threshold: float, *, frame_ms: int = 20) -> list[int]:
    import numpy as np

    frame_size = max(1, round(sample_rate * frame_ms / 1000))
    quiet: list[bool] = []
    for start in range(0, len(audio), frame_size):
        frame = audio[start:start + frame_size]
        rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64)))) if len(frame) else 0.0
        quiet.append(rms < threshold)
    runs: list[int] = []
    start_index: int | None = None
    for index, is_quiet in enumerate([*quiet, False]):
        if is_quiet and start_index is None:
            start_index = index
        elif not is_quiet and start_index is not None:
            runs.append((index - start_index) * frame_ms)
            start_index = None
    return runs


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
    quiet_runs_ms = _quiet_run_durations_ms(mono, sample_rate, threshold)
    max_internal_silence_ms = max(quiet_runs_ms, default=0)

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
    if max_internal_silence_ms > int(rules["max_internal_silence_ms"]):
        raise LongSilenceError(
            f"片段包含 {max_internal_silence_ms}ms 异常长静音，"
            f"上限为 {rules['max_internal_silence_ms']}ms"
        )
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
        "maxInternalSilenceMs": max_internal_silence_ms,
        "charactersPerSecond": round(characters_per_second, 3),
        "trimmedLeadingMs": trimmed_leading_ms,
        "trimmedTrailingMs": trimmed_trailing_ms,
    }


def silence_policy(quality_preset: str = "stable") -> dict:
    return dict(QUALITY_RULES.get(quality_preset, QUALITY_RULES["stable"]))


def compress_long_silences(source: str | Path, output: str | Path, *, threshold_db: float = -50.0,
                            trigger_ms: int = 1800, keep_ms: int = 650,
                            frame_ms: int = 20) -> dict:
    """Stream a WAV file while shortening only continuous abnormal silence.

    Short pauses are copied sample-for-sample. Once a quiet run exceeds
    ``trigger_ms`` it is replaced by ``keep_ms`` of digital silence. Memory use
    stays bounded by the trigger window, so this is safe for multi-hour audio.
    """
    import numpy as np
    import soundfile as sf

    source_path = Path(source).resolve()
    output_path = Path(output).resolve()
    if source_path == output_path:
        raise ValueError("长静音压缩必须写入不同的临时文件")
    if trigger_ms <= keep_ms or keep_ms < 0 or frame_ms <= 0:
        raise ValueError("静音压缩参数无效")

    info = sf.info(str(source_path))
    frame_size = max(1, round(info.samplerate * frame_ms / 1000))
    trigger_frames = round(info.samplerate * trigger_ms / 1000)
    keep_frames = round(info.samplerate * keep_ms / 1000)
    threshold = 10 ** (threshold_db / 20)
    estimated_pcm_bytes = info.frames * info.channels * 2
    container = "RF64" if estimated_pcm_bytes >= 0xFFFF0000 else "WAV"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pending: list[np.ndarray] = []
    pending_frames = 0
    compressing = False
    compressed_count = 0
    removed_frames = 0
    longest_frames = 0

    def flush_quiet(destination) -> None:
        nonlocal pending, pending_frames, compressing, compressed_count, removed_frames, longest_frames
        if not pending_frames:
            return
        longest_frames = max(longest_frames, pending_frames)
        if compressing:
            destination.write(np.zeros((keep_frames, info.channels), dtype=np.float32))
            compressed_count += 1
            removed_frames += max(0, pending_frames - keep_frames)
        else:
            for buffered in pending:
                destination.write(buffered)
        pending = []
        pending_frames = 0
        compressing = False

    with sf.SoundFile(str(source_path)) as source_file, sf.SoundFile(
        str(output_path), mode="w", samplerate=info.samplerate, channels=info.channels,
        subtype="PCM_16", format=container,
    ) as destination:
        while True:
            block = source_file.read(frame_size, dtype="float32", always_2d=True)
            if not len(block):
                break
            mono = block.mean(axis=1)
            rms = float(np.sqrt(np.mean(np.square(mono, dtype=np.float64))))
            if rms < threshold:
                pending_frames += len(block)
                if not compressing:
                    pending.append(block)
                    if pending_frames > trigger_frames:
                        compressing = True
                        pending = []
                continue
            flush_quiet(destination)
            destination.write(block)
        flush_quiet(destination)

    return {
        "compressedSilenceCount": compressed_count,
        "removedSilenceMs": round(removed_frames * 1000 / info.samplerate),
        "longestSilenceMs": round(longest_frames * 1000 / info.samplerate),
        "originalDurationSeconds": round(info.duration, 3),
        "outputDurationSeconds": round(sf.info(str(output_path)).duration, 3),
        "triggerMs": trigger_ms,
        "keptMs": keep_ms,
        "thresholdDb": threshold_db,
    }


def repair_tts_long_silence(path: str | Path, *, quality_preset: str = "stable") -> dict:
    target = Path(path)
    temporary = target.with_name(f"{target.stem}.silence-repair{target.suffix}")
    rules = silence_policy(quality_preset)
    try:
        report = compress_long_silences(
            target, temporary, threshold_db=float(rules["threshold_db"]),
            trigger_ms=int(rules["max_internal_silence_ms"]),
            keep_ms=int(rules["repaired_silence_ms"]),
        )
        if report["compressedSilenceCount"]:
            temporary.replace(target)
        return report
    finally:
        if temporary.exists():
            temporary.unlink()


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
