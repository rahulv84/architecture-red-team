"""Local context retrieval: chunking + TF-IDF over standards, mappings and uploaded context documents.

No embeddings API and no vector database: a small pure-Python lexical index keeps retrieval
deterministic, auditable and offline.  Every chunk carries an ``origin`` so findings can
distinguish organisational standards from generally recommended practices.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from ..models.domain import ArchitectureArtifact
from ..parsers.types import ParsedDocument

_STOP = set(
    ["a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "by", "is", "are", "was", "were", "be", "been", "it", "its", "this", "that", "these", "those", "as", "at", "from", "must", "should", "may", "can", "will", "shall", "not", "no", "any", "all", "each", "per", "than", "then", "so", "such", "into", "over", "under", "also", "more", "most"]
)
_TOKEN = re.compile(r"[a-z0-9][a-z0-9\-_]+")


def _stem(tok: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(tok) > len(suffix) + 3 and tok.endswith(suffix):
            return tok[: -len(suffix)]
    return tok


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in _TOKEN.findall(text.lower()) if t not in _STOP]


class KnowledgeChunk(BaseModel):
    chunk_id: str
    source: str
    title: str
    section: str = ""
    text: str
    origin: str = "GENERAL_PRACTICE"  # ORGANIZATIONAL_STANDARD | CONTROL_MAPPING | GENERAL_PRACTICE | CONTEXT_DOCUMENT
    domains: list[str] = []
    score: float = 0.0
    artifact_id: str | None = None


class TfidfIndex:
    def __init__(self, chunks: list[KnowledgeChunk]) -> None:
        self.chunks = chunks
        self._tf: list[Counter[str]] = [Counter(tokenize(c.title + " " + c.section + " " + c.text)) for c in chunks]
        df: Counter[str] = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        n = max(1, len(chunks))
        self._idf = {t: math.log((1 + n) / (1 + d)) + 1.0 for t, d in df.items()}
        self._norms = [self._norm(tf) for tf in self._tf]

    def _weight(self, tf: Counter[str]) -> dict[str, float]:
        return {t: (1 + math.log(c)) * self._idf.get(t, 1.0) for t, c in tf.items()}

    def _norm(self, tf: Counter[str]) -> float:
        return math.sqrt(sum(w * w for w in self._weight(tf).values())) or 1.0

    def search(self, query: str, k: int = 6, origins: set[str] | None = None, domains: set[str] | None = None) -> list[KnowledgeChunk]:
        q_tf = Counter(tokenize(query))
        if not q_tf:
            return []
        q_w = self._weight(q_tf)
        q_norm = math.sqrt(sum(w * w for w in q_w.values())) or 1.0
        scored: list[tuple[float, int]] = []
        for i, (tf, norm) in enumerate(zip(self._tf, self._norms)):
            chunk = self.chunks[i]
            if origins and chunk.origin not in origins:
                continue
            dot = sum(q_w[t] * (1 + math.log(c)) * self._idf.get(t, 1.0) for t, c in tf.items() if t in q_w)
            if dot <= 0:
                continue
            score = dot / (norm * q_norm)
            if domains and chunk.domains and not (set(chunk.domains) & domains):
                score *= 0.6
            scored.append((score, i))
        scored.sort(key=lambda x: (-x[0], x[1]))
        out = []
        for score, i in scored[:k]:
            out.append(self.chunks[i].model_copy(update={"score": round(score, 4)}))
        return out


def _split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return {}, text
    try:
        return yaml.safe_load(m.group(1)) or {}, text[m.end():]
    except yaml.YAMLError:
        return {}, text[m.end():]


def chunk_markdown(source: str, title: str, text: str, origin: str, domains: list[str], max_chars: int = 900,
                   artifact_id: str | None = None) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    sections = re.split(r"(?m)^(#{1,4}\s+.*)$", text)
    heading = title
    buf = ""
    idx = 0

    def emit(h: str, body: str) -> None:
        nonlocal idx
        body = body.strip()
        if not body:
            return
        for start in range(0, len(body), max_chars):
            piece = body[start:start + max_chars]
            idx += 1
            chunks.append(KnowledgeChunk(
                chunk_id=f"KB-{re.sub(r'[^A-Za-z0-9]+', '-', source)[:40]}-{idx}", source=source, title=title,
                section=h.lstrip("# ").strip(), text=piece, origin=origin, domains=domains, artifact_id=artifact_id))

    for part in sections:
        if re.match(r"^#{1,4}\s+", part):
            emit(heading, buf)
            heading, buf = part, ""
        else:
            buf += part
    emit(heading, buf)
    return chunks


class ContextService:
    def __init__(self, knowledge_dir: Path) -> None:
        self.knowledge_dir = knowledge_dir

    # ---- knowledge base --------------------------------------------------------
    @lru_cache(maxsize=1)  # noqa: B019 - one service instance per process
    def base_chunks(self) -> tuple[KnowledgeChunk, ...]:
        chunks: list[KnowledgeChunk] = []
        for path in sorted((self.knowledge_dir / "standards").glob("*.md")):
            meta, body = _split_front_matter(path.read_text(encoding="utf-8"))
            chunks += chunk_markdown(path.name, str(meta.get("title", path.stem)), body,
                                     str(meta.get("origin", "GENERAL_PRACTICE")), list(meta.get("domains", [])))
        for path in sorted((self.knowledge_dir / "control_mappings").glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            fw = data.get("framework", path.stem)
            for ctl in data.get("controls", []):
                text = (f"{fw} control {ctl.get('id')} - {ctl.get('topic')}. {ctl.get('architecture_relevance', '')} "
                        f"Evidence needed: {', '.join(ctl.get('evidence_needed', []))}. Related checks: {', '.join(ctl.get('rule_ids', []))}. "
                        f"{data.get('disclaimer', '')}")
                chunks.append(KnowledgeChunk(
                    chunk_id=f"KB-{path.stem}-{re.sub(r'[^A-Za-z0-9]+', '', str(ctl.get('id')))}", source=path.name,
                    title=f"{fw} {ctl.get('id')}", section=str(ctl.get("topic", "")), text=text.strip(),
                    origin="CONTROL_MAPPING", domains=["compliance", "security"]))
        return tuple(chunks)

    @cache  # noqa: B019
    def rubric(self, agent_id: str) -> dict[str, Any]:
        path = self.knowledge_dir / "agent_rubrics" / f"{agent_id}.yaml"
        return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}

    @cache  # noqa: B019
    def scoring_rubric(self) -> dict[str, Any]:
        return yaml.safe_load((self.knowledge_dir / "agent_rubrics" / "scoring.yaml").read_text(encoding="utf-8"))

    @cache  # noqa: B019
    def control_mappings(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for path in sorted((self.knowledge_dir / "control_mappings").glob("*.yaml")):
            out[path.stem] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return out

    def controls_for_rules(self, rule_ids: set[str]) -> list[dict[str, str]]:
        """Control identifiers (all frameworks) related to the supplied rule IDs."""
        hits: list[dict[str, str]] = []
        for key, data in self.control_mappings().items():
            for ctl in data.get("controls", []):
                if rule_ids & set(ctl.get("rule_ids", [])):
                    hits.append({"framework": data.get("framework", key), "control_id": str(ctl.get("id")), "topic": str(ctl.get("topic"))})
        return hits

    # ---- per-review index ------------------------------------------------------
    def chunks_from_documents(self, docs: list[tuple[ArchitectureArtifact, ParsedDocument]]) -> list[KnowledgeChunk]:
        """User-supplied standards, ADRs and incident reports become retrievable context."""
        out: list[KnowledgeChunk] = []
        for art, parsed in docs:
            if art.role.value not in ("STANDARD", "ADR", "INCIDENT") or not parsed.text.strip():
                continue
            origin = "ORGANIZATIONAL_STANDARD" if art.role.value == "STANDARD" else "CONTEXT_DOCUMENT"
            out += chunk_markdown(art.filename, art.filename, parsed.text, origin, [], artifact_id=art.artifact_id)
        return out

    def build_index(self, extra: list[KnowledgeChunk] | None = None) -> TfidfIndex:
        return TfidfIndex(list(self.base_chunks()) + list(extra or []))
