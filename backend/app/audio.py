from __future__ import annotations

from pathlib import Path


def merge_wav_files(inputs: list[str | Path], output: str | Path, *, sample_rate: int,
                    silence_ms: int = 0) -> Path:
    """Decode, mono-mix, resample and concatenate WAV files as PCM-16."""
    if not inputs:
        raise ValueError("at least one input WAV is required")
    try:
        import numpy as np
        import soundfile as sf
    except ModuleNotFoundError:
        return _merge_pcm_wav_stdlib(inputs, output, sample_rate=sample_rate, silence_ms=silence_ms)

    chunks = []
    for item in inputs:
        audio, source_rate = sf.read(str(item), dtype="float32", always_2d=True)
        mono = audio.mean(axis=1)
        if source_rate != sample_rate and len(mono):
            out_len = max(1, round(len(mono) * sample_rate / source_rate))
            old_x = np.linspace(0.0, 1.0, num=len(mono), endpoint=False)
            new_x = np.linspace(0.0, 1.0, num=out_len, endpoint=False)
            mono = np.interp(new_x, old_x, mono).astype(np.float32)
        chunks.append(mono)
    silence = np.zeros(round(sample_rate * silence_ms / 1000), dtype=np.float32)
    merged_parts = []
    for index, chunk in enumerate(chunks):
        if index and len(silence):
            merged_parts.append(silence)
        merged_parts.append(chunk)
    merged = np.concatenate(merged_parts) if merged_parts else np.zeros(0, dtype=np.float32)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_path), merged, sample_rate, subtype="PCM_16")
    return output_path


def _merge_pcm_wav_stdlib(inputs: list[str | Path], output: str | Path, *, sample_rate: int,
                          silence_ms: int) -> Path:
    """Dependency-free PCM fallback used by bootstrap and contract tests on Python <=3.12."""
    import audioop
    import wave

    chunks: list[bytes] = []
    for item in inputs:
        with wave.open(str(item), "rb") as source:
            channels = source.getnchannels()
            width = source.getsampwidth()
            source_rate = source.getframerate()
            frames = source.readframes(source.getnframes())
        if channels == 2:
            frames = audioop.tomono(frames, width, 0.5, 0.5)
        elif channels != 1:
            raise RuntimeError("多于双声道或浮点 WAV 需要安装 soundfile/numpy")
        if width != 2:
            frames = audioop.lin2lin(frames, width, 2)
            width = 2
        if source_rate != sample_rate:
            frames, _ = audioop.ratecv(frames, width, 1, source_rate, sample_rate, None)
        chunks.append(frames)
    silence = b"\x00\x00" * round(sample_rate * silence_ms / 1000)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(sample_rate)
        destination.writeframes(silence.join(chunks))
    return output_path
