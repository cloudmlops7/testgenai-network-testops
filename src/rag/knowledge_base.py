import hashlib
import os
import re
import time
from pathlib import Path
from typing import Dict, List

import chromadb
import ollama


class KnowledgeBase:
    """Local persistent RAG store plus lightweight RFC/document intelligence."""

    NORMATIVE_KEYWORDS = [
        "MUST NOT", "SHALL NOT", "SHOULD NOT",
        "MUST", "SHALL", "SHOULD", "REQUIRED", "RECOMMENDED", "MAY", "OPTIONAL",
    ]

    def __init__(self, db_path: str = "./data/chroma", collection_name: str = "network_knowledge",
                 embedding_model: str = "embeddinggemma"):
        self.embedding_model = embedding_model
        Path(db_path).mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=db_path)
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"description": "TestGenAI network protocol and requirements knowledge"},
        )

    @staticmethod
    def _doc_id(name: str, data: bytes) -> str:
        return hashlib.sha256(name.encode("utf-8") + b"\0" + data).hexdigest()[:16]

    @staticmethod
    def _extract_text(name: str, data: bytes) -> str:
        suffix = Path(name).suffix.lower()
        if suffix == ".pdf":
            from pypdf import PdfReader
            import io
            reader = PdfReader(io.BytesIO(data))
            pages = []
            for i, page in enumerate(reader.pages, 1):
                txt = page.extract_text() or ""
                pages.append(f"[Page {i}]\n{txt}")
            return "\n\n".join(pages)
        if suffix == ".docx":
            from docx import Document
            import io
            doc = Document(io.BytesIO(data))
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        return data.decode("utf-8", errors="ignore")

    @staticmethod
    def _split_sections(text: str) -> List[Dict[str, str]]:
        lines = text.splitlines()
        sections: List[Dict[str, str]] = []
        current = "General"
        buf: List[str] = []
        heading_re = re.compile(r"^(?:\s*(?:\d+(?:\.\d+)*|[A-Z][A-Za-z0-9_-]{0,30})[.)]?\s+)?[A-Z][^\n]{2,100}$")
        for line in lines:
            clean = line.strip()
            if clean and (clean.startswith("#") or (len(clean) <= 100 and heading_re.match(clean) and not clean.endswith("."))):
                if buf:
                    sections.append({"section": current, "text": "\n".join(buf).strip()})
                    buf = []
                current = clean.lstrip("# ").strip()
            else:
                buf.append(line)
        if buf:
            sections.append({"section": current, "text": "\n".join(buf).strip()})
        return [x for x in sections if x["text"]]

    @staticmethod
    def _chunk(section_text: str, chunk_size: int = 1400, overlap: int = 200) -> List[str]:
        text = re.sub(r"\n{3,}", "\n\n", section_text).strip()
        if len(text) <= chunk_size:
            return [text] if text else []
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            if end < len(text):
                cut = max(text.rfind("\n\n", start, end), text.rfind(". ", start, end))
                if cut > start + chunk_size // 2:
                    end = cut + (2 if text[cut:cut+2] == ". " else 0)
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(text):
                break
            start = max(end - overlap, start + 1)
        return chunks

    def ingest(self, name: str, data: bytes) -> Dict[str, object]:
        text = self._extract_text(name, data)
        if not text.strip():
            raise ValueError(f"No extractable text found in {name}.")
        doc_id = self._doc_id(name, data)
        try:
            self.collection.delete(where={"doc_id": doc_id})
        except Exception:
            pass

        records = []
        for section in self._split_sections(text):
            for chunk_no, chunk in enumerate(self._chunk(section["text"]), 1):
                records.append((chunk_no, section["section"], chunk))
        if not records:
            raise ValueError(f"No usable chunks found in {name}.")

        # Do not send a whole large RFC to Ollama in one embedding request.
        # On local Windows/CPU setups this can make Ollama's tokenizer worker
        # crash/restart, which surfaces as a 400 containing a random local
        # /tokenize port (for example 127.0.0.1:54582).
        batch_size = max(1, int(os.getenv("TESTGENAI_EMBED_BATCH", "8")))
        retries = max(1, int(os.getenv("TESTGENAI_EMBED_RETRIES", "3")))
        delay = float(os.getenv("TESTGENAI_EMBED_RETRY_DELAY", "1.5"))

        ids = [f"{doc_id}-{i:04d}" for i in range(len(records))]
        documents = [r[2] for r in records]
        metadatas = [
            {"doc_id": doc_id, "source": name, "section": r[1], "chunk": r[0]}
            for r in records
        ]

        for start in range(0, len(records), batch_size):
            end = min(start + batch_size, len(records))
            batch_texts = documents[start:end]
            batch_ids = ids[start:end]
            batch_metas = metadatas[start:end]
            embeddings = None
            last_error = None
            for attempt in range(1, retries + 1):
                try:
                    response = ollama.embed(model=self.embedding_model, input=batch_texts)
                    embeddings = response["embeddings"] if isinstance(response, dict) else response.embeddings
                    if len(embeddings) != len(batch_texts):
                        raise RuntimeError(f"Embedding count mismatch: expected {len(batch_texts)}, got {len(embeddings)}")
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < retries:
                        time.sleep(delay * attempt)
            if embeddings is None:
                raise RuntimeError(
                    f"Ollama embedding failed for {name}, chunks {start + 1}-{end}. "
                    f"Model={self.embedding_model}. Last error: {last_error}"
                ) from last_error
            self.collection.add(ids=batch_ids, documents=batch_texts, embeddings=embeddings, metadatas=batch_metas)

        return {"doc_id": doc_id, "source": name, "chunks": len(records), "batch_size": batch_size}

    def retrieve(self, query: str, top_k: int = 5) -> List[Dict[str, object]]:
        if self.collection.count() == 0:
            return []
        response = ollama.embed(model=self.embedding_model, input=query)
        embeddings = response["embeddings"] if isinstance(response, dict) else response.embeddings
        if not embeddings:
            raise RuntimeError(f"Ollama returned no embedding for the retrieval query using {self.embedding_model}.")
        embedding = embeddings[0]
        result = self.collection.query(query_embeddings=[embedding], n_results=min(top_k, self.collection.count()))
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        return [
            {
                "text": d,
                "metadata": metas[i] if i < len(metas) and metas[i] else {},
                "distance": distances[i] if i < len(distances) else None,
            }
            for i, d in enumerate(docs)
        ]

    def list_sources(self) -> List[Dict[str, object]]:
        if self.collection.count() == 0:
            return []
        data = self.collection.get(include=["metadatas"])
        seen = {}
        for m in data.get("metadatas", []):
            if m:
                seen[m.get("doc_id")] = {"doc_id": m.get("doc_id"), "source": m.get("source")}
        return list(seen.values())

    def build_context(self, query: str, top_k: int = 5) -> tuple[str, List[str]]:
        hits = self.retrieve(query, top_k=top_k)
        blocks = []
        refs = []
        for idx, hit in enumerate(hits, 1):
            meta = hit["metadata"]
            ref = f"{meta.get('source', 'unknown')} | {meta.get('section', 'General')} | chunk {meta.get('chunk', '?')}"
            refs.append(ref)
            blocks.append(f"[SOURCE-{idx}] {ref}\n{hit['text']}")
        return "\n\n".join(blocks), refs

    @classmethod
    def _normative_sentences(cls, text: str) -> List[tuple[str, str]]:
        """Return (keyword, sentence) pairs without inventing or rewriting source text."""
        normalized = re.sub(r"\s+", " ", text).strip()
        # Keep sentence boundaries conservative. RFC prose often uses bullets and semicolons.
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z\[])|\n+", normalized)
        results = []
        for sentence in sentences:
            s = sentence.strip(" -•\t")
            if not s:
                continue
            upper = s.upper()
            matches = []
            for keyword in cls.NORMATIVE_KEYWORDS:
                if re.search(rf"\b{re.escape(keyword)}\b", upper):
                    matches.append(keyword)
            if matches:
                # Prefer the strongest/longest compound keyword.
                keyword = sorted(matches, key=len, reverse=True)[0]
                results.append((keyword, s))
        return results

    def analyze_documents(self, max_requirements: int = 60, sources: List[str] | None = None) -> Dict[str, object]:
        """Extract source-grounded normative statements from indexed documents.

        This is intentionally deterministic: the first RFC intelligence layer does not
        let an LLM rewrite normative requirements. Later phases can add an LLM synthesis
        layer on top of these source statements.
        """
        if self.collection.count() == 0:
            return {"documents": [], "requirements": [], "counts": {}, "protocols": []}

        data = self.collection.get(include=["documents", "metadatas"])
        docs = data.get("documents", [])
        metas = data.get("metadatas", [])
        selected = {str(x) for x in (sources or []) if str(x).strip()}
        source_names = []
        seen_source = set()
        extracted = []
        seen_text = set()

        for text, meta in zip(docs, metas):
            meta = meta or {}
            source = str(meta.get("source", "unknown"))
            if selected and source not in selected:
                continue
            section = str(meta.get("section", "General"))
            chunk = str(meta.get("chunk", "?"))
            if source not in seen_source:
                source_names.append(source)
                seen_source.add(source)
            for keyword, statement in self._normative_sentences(text or ""):
                key = re.sub(r"\s+", " ", statement.lower()).strip()
                dedup = (source, key)
                if dedup in seen_text:
                    continue
                seen_text.add(dedup)
                extracted.append({
                    "keyword": keyword,
                    "statement": statement,
                    "source": source,
                    "section": section,
                    "chunk": chunk,
                    "evidence_ref": f"{source} | {section} | chunk {chunk}",
                })
                if len(extracted) >= max_requirements:
                    break
            if len(extracted) >= max_requirements:
                break

        # Stable IDs after extraction so re-runs are reproducible.
        requirements = []
        per_source = {}
        for item in extracted:
            stem = re.sub(r"[^A-Za-z0-9]+", "-", Path(item["source"]).stem).strip("-").upper() or "DOC"
            per_source[stem] = per_source.get(stem, 0) + 1
            req_id = f"REQ-{stem}-{per_source[stem]:03d}"
            requirements.append({"id": req_id, **item})

        counts = {}
        for r in requirements:
            counts[r["keyword"]] = counts.get(r["keyword"], 0) + 1

        protocols = []
        # Protocol detection inspects selected document content. Generic transport/network
        # terms are retained as related technologies but are not preferred as the primary protocol.
        selected_docs = [str(x or "") for x, meta in zip(docs, metas) if (not selected or str((meta or {}).get("source", "unknown")) in selected)]
        joined = " ".join(source_names + selected_docs).lower()
        candidates = ["BGP", "OSPF", "DHCP", "VLAN", "STP", "MPLS", "GPON", "XGS-PON", "ISIS", "TCP", "IP"]
        scores = {}
        for p in candidates:
            term = p.lower()
            scores[p] = len(re.findall(rf"\b{re.escape(term)}\b", joined))
        protocols = [p for p in candidates if scores[p] > 0]
        protocols.sort(key=lambda p: (-scores[p], candidates.index(p)))
        primary_candidates = [p for p in protocols if p not in {"TCP", "IP"}]
        primary_protocol = primary_candidates[0] if primary_candidates else (protocols[0] if protocols else "")
        related_protocols = [p for p in protocols if p != primary_protocol]
        return {"documents": source_names, "requirements": requirements, "counts": counts, "protocols": protocols, "primary_protocol": primary_protocol, "related_protocols": related_protocols}
