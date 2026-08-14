import numpy as np
import soundfile as sf

from app.audio import merge_wav_files, prepare_tts_segment
from app.workspace import ProjectCreate


def test_project_schema_has_no_application_text_length_cap():
    text = "长" * 2_000_001
    project = ProjectCreate(name="超长项目", engine="indextts2", text=text)
    assert len(project.text) == len(text)


def test_many_segments_are_streamed_into_one_audio_file(tmp_path):
    sample_rate = 16_000
    segments = []
    for index in range(80):
        path = tmp_path / f"{index:04d}.wav"
        sf.write(path, np.full(320, index / 100, dtype=np.float32), sample_rate)
        segments.append(path)

    output = merge_wav_files(segments, tmp_path / "merged.wav", sample_rate=sample_rate, silence_ms=5)
    info = sf.info(output)
    expected_frames = 80 * 320 + 79 * 80
    assert info.frames == expected_frames
    assert info.samplerate == sample_rate


def test_merge_supports_a_distinct_silence_duration_for_each_gap(tmp_path):
    sample_rate = 16_000
    segments = []
    for index in range(3):
        path = tmp_path / f"gap-{index}.wav"
        sf.write(path, np.full(160, 0.1, dtype=np.float32), sample_rate)
        segments.append(path)

    output = merge_wav_files(segments, tmp_path / "variable-gaps.wav", sample_rate=sample_rate, silence_ms=[100, 280])
    info = sf.info(output)
    assert info.frames == 3 * 160 + 1600 + 4480


def test_generated_segment_trims_only_abnormal_edge_silence(tmp_path):
    sample_rate = 16_000
    active = 0.15 * np.sin(2 * np.pi * 220 * np.arange(sample_rate * 2) / sample_rate)
    audio = np.concatenate([np.zeros(sample_rate // 2), active, np.zeros(sample_rate * 3)])
    path = tmp_path / "long-tail.wav"
    sf.write(path, audio, sample_rate)

    report = prepare_tts_segment(path, "这是一句用于测试静音裁剪的正常台词。", quality_preset="stable")

    assert report["trimmedLeadingMs"] >= 370
    assert report["trimmedTrailingMs"] >= 2700
    assert sf.info(path).duration < 2.4


def test_hq_resampler_suppresses_frequencies_above_target_nyquist(tmp_path):
    source_rate = 48_000
    target_rate = 32_000
    source = tmp_path / "above-nyquist.wav"
    tone = 0.8 * np.sin(2 * np.pi * 18_000 * np.arange(source_rate) / source_rate)
    sf.write(source, tone, source_rate)

    output = merge_wav_files([source], tmp_path / "resampled.wav", sample_rate=target_rate)
    audio, actual_rate = sf.read(output, dtype="float32")

    assert actual_rate == target_rate
    assert len(audio) == target_rate
    assert float(np.sqrt(np.mean(audio ** 2))) < 0.03
