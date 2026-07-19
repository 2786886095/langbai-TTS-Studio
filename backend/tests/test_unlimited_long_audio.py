import numpy as np
import soundfile as sf

from app.audio import merge_wav_files
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
