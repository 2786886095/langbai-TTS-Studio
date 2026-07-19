from app.segmenter import split_text


def test_split_prefers_punctuation_and_respects_limit():
    text = "第一句话比较短。第二句话也不长，但是需要继续，直到超过限制！最后一句。"
    parts = split_text(text, 18)
    assert len(parts) > 1
    assert all(0 < len(part) <= 18 for part in parts)
    assert "".join(parts) == text


def test_split_handles_latin_words_and_blank_input():
    parts = split_text("hello world this is a longer sentence", 12)
    assert all(len(part) <= 12 for part in parts)
    assert "".join(parts).replace(" ", "") == "helloworldthisisalongersentence"
    assert split_text("  \n  ", 30) == []
