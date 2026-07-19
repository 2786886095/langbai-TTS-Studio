from __future__ import annotations

from pathlib import Path


def merge_wav_files(inputs: list[str | Path], output: str | Path, *, sample_rate: int,
                    silence_ms: int = 0) -> Path:
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
    silence_frames = round(sample_rate * silence_ms / 1000)
    estimated_frames = silence_frames * max(0, len(inputs) - 1)
    for item in inputs:
        info = sf.info(str(item))
        estimated_frames += round(info.frames * sample_rate / info.samplerate)
    estimated_pcm_bytes = estimated_frames * 2  # mono PCM-16
    container = "RF64" if estimated_pcm_bytes >= 0xFFFF0000 else "WAV"
    silence = np.zeros(min(silence_frames, sample_rate), dtype=np.float32)
    with sf.SoundFile(str(output_path), mode="w", samplerate=sample_rate, channels=1,
                      subtype="PCM_16", format=container) as destination:
        for index, item in enumerate(inputs):
            if index and silence_frames:
                remaining = silence_frames
                while remaining:
                    block = silence[:min(remaining, len(silence))]
                    destination.write(block)
                    remaining -= len(block)
            audio, source_rate = sf.read(str(item), dtype="float32", always_2d=True)
            mono = audio.mean(axis=1)
            if source_rate != sample_rate and len(mono):
                out_len = max(1, round(len(mono) * sample_rate / source_rate))
                old_x = np.linspace(0.0, 1.0, num=len(mono), endpoint=False)
                new_x = np.linspace(0.0, 1.0, num=out_len, endpoint=False)
                mono = np.interp(new_x, old_x, mono).astype(np.float32)
            destination.write(mono)
    return output_path


def _merge_pcm_wav_stdlib(inputs: list[str | Path], output: str | Path, *, sample_rate: int,
                          silence_ms: int) -> Path:
    """Dependency-free PCM fallback used by bootstrap and contract tests on Python <=3.12."""
    import audioop
    import wave

    silence = b"\x00\x00" * round(sample_rate * silence_ms / 1000)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(sample_rate)
        for index, item in enumerate(inputs):
            if index and silence:
                destination.writeframesraw(silence)
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
