import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.llm.orchestrator import chunk_text, estimate_tokens  # noqa: E402


def test_short_text_single_chunk():
    text = "This is a short paragraph."
    chunks = chunk_text(text, max_tokens=1000)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_long_text_splits_on_paragraphs():
    para = "Sentence one. Sentence two. Sentence three. " * 50  # ~2250 chars
    text = "\n\n".join([para] * 5)
    chunks = chunk_text(text, max_tokens=300)  # ~1200 char budget
    assert len(chunks) > 1
    for c in chunks:
        assert estimate_tokens(c) <= 300 * 1.5  # allow overlap slack


def test_no_chunk_exceeds_budget_by_much():
    text = ("word " * 5000)
    chunks = chunk_text(text, max_tokens=500)
    max_chars = 500 * 4
    for c in chunks:
        # overlap adds some slack but shouldn't blow past ~1.3x budget
        assert len(c) <= max_chars * 1.5


if __name__ == "__main__":
    test_short_text_single_chunk()
    test_long_text_splits_on_paragraphs()
    test_no_chunk_exceeds_budget_by_much()
    print("All chunking tests passed.")
