"""
Huginn Research Memory — Persistent vector store for accumulated research knowledge.

Accumulates findings, citations, and raw page content across research sessions.
Enables semantic search over everything the agent has ever learned.

Uses ChromaDB with local persistence (no external server needed).
Embeddings are handled by Chroma's default all-MiniLM-L6-v2 model
(downloaded once on first use, ~80MB).

Usage:
    from huginn.memory import ResearchMemory
    memory = ResearchMemory(data_dir="/path/to/huginn/data")

    # After research:
    await memory.store_research(report)

    # Query accumulated knowledge:
    results = await memory.query("Kubernetes vs Docker Swarm")
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


try:
    import chromadb
    CHROMA_AVAILABLE = True
except ImportError:
    CHROMA_AVAILABLE = False
    logger.warning("chromadb not installed. Research memory disabled. pip install chromadb")


def _get_embedding_function():
    """Get the best available embedding function."""
    if not CHROMA_AVAILABLE:
        return None
    try:
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
        return DefaultEmbeddingFunction()
    except Exception as e:
        logger.warning("Could not load default embedding function: %s", e)
        return None


class ResearchMemory:
    """
    Persistent vector memory for research findings and citations.

    Stores:
    - Individual findings (claim + confidence)
    - Citations (url + quote + relevance)
    - Raw page content snippets
    - Full research reports

    All stored with metadata for filtering and retrieval.
    """

    def __init__(
        self,
        data_dir: Optional[str] = None,
        collection_name: str = "huginn_research",
    ):
        self.data_dir = data_dir or os.path.expanduser("~/.huginn/vector_db")
        self.collection_name = collection_name
        self._client: Optional[Any] = None
        self._collection: Optional[Any] = None
        self._embedding_fn = None
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """Lazy initialization of ChromaDB."""
        if self._initialized:
            return
        self._initialized = True

        if not CHROMA_AVAILABLE:
            logger.warning("ChromaDB not available — research memory will not persist")
            return

        self._embedding_fn = _get_embedding_function()
        if self._embedding_fn is None:
            logger.warning("No embedding function available — research memory disabled")
            return

        try:
            self._client = chromadb.PersistentClient(path=self.data_dir)
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                embedding_function=self._embedding_fn,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(
                "ResearchMemory initialized: %s items in collection",
                self._collection.count() if self._collection.count() is not None else 0
            )
        except Exception as e:
            logger.error("Failed to initialize ChromaDB: %s", e)
            self._client = None
            self._collection = None

    @property
    def available(self) -> bool:
        """Whether the memory store is operational."""
        self._ensure_initialized()
        return self._collection is not None and self._embedding_fn is not None

    @property
    def count(self) -> int:
        """Number of items in memory."""
        if not self.available:
            return 0
        try:
            return self._collection.count() or 0
        except Exception:
            return 0

    async def store_finding(
        self,
        finding: Any,
        report_id: Optional[str] = None,
        topic: Optional[str] = None,
    ) -> None:
        """Store a single Finding in vector memory."""
        if not self.available:
            return
        if finding is None:
            return

        # Extract text to embed
        claim = getattr(finding, "claim", str(finding))
        if not claim:
            return

        cid = f"finding_{hash(claim) & 0xFFFFFFFF:08x}"
        relevance = getattr(finding, "confidence", 0.5)
        citations = getattr(finding, "supporting_citations", [])
        source_urls = [c.url for c in citations if hasattr(c, "url")]

        meta = {
            "type": "finding",
            "topic": topic or "",
            "claim": claim[:1000],
            "confidence": relevance,
            "sources": json.dumps(source_urls[:5]),
            "report_id": report_id or "",
            "stored_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            self._collection.add(
                ids=[cid],
                documents=[claim[:2000]],  # Embed the claim text
                metadatas=[meta],
            )
        except Exception as e:
            logger.warning("Failed to store finding: %s", e)

    async def store_citation(
        self,
        citation: Any,
        report_id: Optional[str] = None,
    ) -> None:
        """Store a single Citation in vector memory."""
        if not self.available:
            return
        if citation is None:
            return

        quote = getattr(citation, "quote", str(citation))
        url = getattr(citation, "url", "")
        if not quote:
            return

        cid = f"cite_{hash(url + quote) & 0xFFFFFFFF:08x}"
        relevance = getattr(citation, "relevance_score", 0.5)

        meta = {
            "type": "citation",
            "url": url,
            "title": getattr(citation, "title", "")[:200],
            "domain": getattr(citation, "domain", ""),
            "quote": quote[:1000],
            "relevance": relevance,
            "report_id": report_id or "",
            "stored_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            self._collection.add(
                ids=[cid],
                documents=[quote[:2000]],
                metadatas=[meta],
            )
        except Exception as e:
            logger.warning("Failed to store citation: %s", e)

    async def store_snippet(
        self,
        text: str,
        url: str,
        title: str = "",
        topic: str = "",
        report_id: Optional[str] = None,
    ) -> None:
        """Store a raw text snippet from a page."""
        if not self.available:
            return
        if not text or not text.strip():
            return

        # Chunk to ~1000 chars for better embedding quality
        chunks = self._chunk_text(text.strip(), chunk_size=1000, overlap=100)
        for i, chunk in enumerate(chunks):
            cid = f"snippet_{hash(url) & 0xFFFFFFFF:08x}_{i}"
            meta = {
                "type": "snippet",
                "url": url,
                "title": title[:200],
                "topic": topic,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "report_id": report_id or "",
                "stored_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                self._collection.add(
                    ids=[cid],
                    documents=[chunk],
                    metadatas=[meta],
                )
            except Exception as e:
                logger.warning("Failed to store snippet chunk %d: %s", i, e)

    async def store_research(
        self,
        report: Any,
        report_id: Optional[str] = None,
    ) -> None:
        """Store all findings and citations from a ResearchReport."""
        if not self.available:
            return
        if report is None:
            return

        rid = report_id or f"report_{hash(str(report.query)) & 0xFFFFFFFF:08x}_{int(datetime.now(timezone.utc).timestamp())}"
        logger.info("Storing research report %s in memory (%d findings, %d citations)",
                    rid, len(getattr(report, "findings", [])), len(getattr(report, "citations", [])))

        # Store findings
        for finding in getattr(report, "findings", []):
            await self.store_finding(finding, report_id=rid, topic=getattr(report, "query", ""))

        # Store citations
        for citation in getattr(report, "citations", []):
            await self.store_citation(citation, report_id=rid)

        # Store report summary as a document
        summary = getattr(report, "summary", "")
        if summary:
            full_text = f"Research: {getattr(report, 'query', '')}\n\nSummary: {summary}\n\n"
            full_text += f"Findings: {len(getattr(report, 'findings', []))}\n"
            full_text += f"Citations: {len(getattr(report, 'citations', []))}\n"
            full_text += f"Confidence: {getattr(report, 'confidence', 0)}\n"

            try:
                self._collection.add(
                    ids=[f"report_summary_{rid}"],
                    documents=[full_text[:4000]],
                    metadatas=[{
                        "type": "report_summary",
                        "query": getattr(report, "query", "")[:200],
                        "report_id": rid,
                        "findings_count": len(getattr(report, "findings", [])),
                        "citations_count": len(getattr(report, "citations", [])),
                        "confidence": getattr(report, "confidence", 0),
                        "stored_at": datetime.now(timezone.utc).isoformat(),
                    }],
                )
            except Exception as e:
                logger.warning("Failed to store report summary: %s", e)

    async def query(
        self,
        query_text: str,
        n_results: int = 5,
        min_relevance: float = 0.0,
        filter_type: Optional[str] = None,
        report_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Semantic search over accumulated research knowledge.

        Args:
            query_text: Natural language query
            n_results: Max results to return
            min_relevance: Minimum relevance score (0-1, Chroma cosine distance)
            filter_type: Only return results of this type (finding, citation, snippet, report_summary)
            report_id: Only return results from this specific report

        Returns:
            List of result dicts with keys: text, metadata, distance, id
        """
        if not self.available:
            return []

        where_filter: Optional[Dict[str, Any]] = None
        conditions = []
        if filter_type:
            conditions.append({"type": filter_type})
        if report_id:
            conditions.append({"report_id": report_id})
        if conditions:
            if len(conditions) == 1:
                where_filter = conditions[0]
            else:
                where_filter = {"$and": conditions}

        try:
            results = self._collection.query(
                query_texts=[query_text],
                n_results=n_results,
                where=where_filter,
            )
        except Exception as e:
            logger.warning("Memory query failed: %s", e)
            return []

        if not results or not results.get("ids") or not results["ids"][0]:
            return []

        output: List[Dict[str, Any]] = []
        ids = results["ids"][0]
        documents = results["documents"][0]
        distances = results["distances"][0] if results.get("distances") else [None] * len(ids)
        metadatas = results["metadatas"][0] if results.get("metadatas") else [{}] * len(ids)

        for i, cid in enumerate(ids):
            distance = distances[i] if distances[i] is not None else 1.0
            relevance = max(0.0, 1.0 - distance) if distance is not None else 0.5
            if relevance < min_relevance:
                continue

            output.append({
                "id": cid,
                "text": documents[i] if i < len(documents) else "",
                "metadata": metadatas[i] if i < len(metadatas) else {},
                "distance": distance,
                "relevance": relevance,
            })

        return output

    async def get_related_topics(
        self,
        topic: str,
        n_results: int = 10,
    ) -> List[str]:
        """Find topics related to the given topic from accumulated research."""
        results = await self.query(topic, n_results=n_results, filter_type="finding")
        topics: set[str] = set()
        for r in results:
            meta = r.get("metadata", {})
            t = meta.get("topic", "")
            if t and t.lower() != topic.lower():
                topics.add(t)
        return sorted(topics)

    async def get_all_reports(self) -> List[Dict[str, Any]]:
        """Return summaries of all stored research reports."""
        if not self.available:
            return []
        try:
            results = self._collection.get(
                where={"type": "report_summary"},
                include=["metadatas"],
            )
            reports = []
            for i, cid in enumerate(results.get("ids", [])):
                meta = results["metadatas"][i] if i < len(results.get("metadatas", [])) else {}
                reports.append({
                    "id": cid,
                    "query": meta.get("query", ""),
                    "report_id": meta.get("report_id", ""),
                    "confidence": meta.get("confidence", 0),
                    "findings": meta.get("findings_count", 0),
                    "citations": meta.get("citations_count", 0),
                    "stored_at": meta.get("stored_at", ""),
                })
            return sorted(reports, key=lambda x: x["stored_at"], reverse=True)
        except Exception as e:
            logger.warning("Failed to list reports: %s", e)
            return []

    async def delete_report(self, report_id: str) -> None:
        """Remove all items associated with a specific report."""
        if not self.available:
            return
        try:
            results = self._collection.get(where={"report_id": report_id})
            ids = results.get("ids", [])
            if ids:
                self._collection.delete(ids=ids)
                logger.info("Deleted %d items from report %s", len(ids), report_id)
        except Exception as e:
            logger.warning("Failed to delete report: %s", e)

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 1000, overlap: int = 100) -> List[str]:
        """Split text into overlapping chunks for embedding."""
        chunks = []
        start = 0
        text_len = len(text)
        if text_len == 0:
            return chunks
        # Clamp overlap to avoid negative starts
        overlap = min(overlap, chunk_size // 2)
        while start < text_len:
            end = min(start + chunk_size, text_len)
            # Try to break at sentence boundary (only if not at end of text)
            if end < text_len:
                for boundary in ["\n\n", ". ", "\n"]:
                    pos = text.rfind(boundary, start, end)
                    if pos > start + chunk_size // 2:
                        end = pos + len(boundary)
                        break
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            start = end - overlap
            if start <= 0 or start >= end or end >= text_len:
                break
        # If we broke early but haven't appended final tail, do so
        if start > 0 and start < text_len:
            tail = text[start:].strip()
            if tail and (not chunks or tail != chunks[-1]):
                chunks.append(tail)
        return chunks
