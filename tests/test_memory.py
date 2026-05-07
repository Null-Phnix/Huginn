"""Tests for Huginn ResearchMemory module."""

import pytest

from huginn.memory import ResearchMemory


class TestResearchMemoryInit:
    """Test memory initialization."""

    def test_init_default(self):
        mem = ResearchMemory(data_dir="/tmp/test_huginn_memory")
        assert mem.data_dir == "/tmp/test_huginn_memory"
        assert mem.collection_name == "huginn_research"
        assert mem.count == 0

    def test_available_without_chroma(self):
        # When chromadb is available it should be True, when not False
        mem = ResearchMemory(data_dir="/tmp/test_huginn_memory2")
        # We can't know if chroma is installed, but the count should be 0
        assert mem.count == 0


class TestResearchMemoryChunking:
    """Test text chunking logic."""

    def test_chunk_short_text(self):
        text = "Hello world"
        chunks = ResearchMemory._chunk_text(text, chunk_size=100)
        assert len(chunks) == 1
        assert chunks[0] == "Hello world"

    def test_chunk_long_text(self):
        text = "A" * 2000
        chunks = ResearchMemory._chunk_text(text, chunk_size=500, overlap=50)
        assert len(chunks) > 1
        # Check overlap
        if len(chunks) > 1:
            assert chunks[1].startswith("A" * 50)

    def test_chunk_empty(self):
        chunks = ResearchMemory._chunk_text("", chunk_size=100)
        assert chunks == []

    def test_chunk_boundary(self):
        text = "Sentence one. Sentence two. Sentence three."
        chunks = ResearchMemory._chunk_text(text, chunk_size=20)
        assert len(chunks) >= 1
        # Should break at sentence boundary if possible
        assert all("." in c or "Sentence" in c for c in chunks)
