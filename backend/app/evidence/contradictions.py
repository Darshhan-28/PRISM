"""Contradiction Engine — deterministic, offline, no LLM."""

import re
import hashlib
from typing import Any
from pydantic import BaseModel, Field

from backend.app.evidence.engine import EvidenceState, EvidenceRef
from backend.app.retrieval.retriever import RetrievedChunk


class Contradiction(BaseModel):
    id: str
    evidence_refs: list[EvidenceRef]
    conflicting_terms: tuple[str, str] | None = None
    metric: str | None = None
    values: list[str] | None = None
    explanation: str
    severity: str = "medium"


class ContradictionReport(BaseModel):
    has_contradictions: bool
    contradictions: list[Contradiction] = Field(default_factory=list)
    evidence_state: EvidenceState
    explanation: str


# Deterministic term pairs
TERM_PAIRS: list[tuple[str, str]] = [
    ("normal", "exceeds"),
    ("within limit", "above limit"),
    ("operational", "failed"),
    ("healthy", "fault"),
    ("present", "absent"),
    ("replaced", "pending"),
]

# For numeric detection
NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(bar|psi|%|rpm|°c|c|mm|m|kpa)?", re.IGNORECASE)
EQUIP_RE = re.compile(r"P-\d+", re.IGNORECASE)
METRIC_KEYWORDS = ["pressure", "temperature", "vibration", "level", "flow"]


def _get_text(item: Any) -> str:
    if isinstance(item, RetrievedChunk):
        return item.text or ""
    if isinstance(item, EvidenceRef):
        return item.text_snippet or item.filename or ""
    if isinstance(item, dict):
        return str(item.get("text") or item.get("text_snippet") or item.get("action") or "")
    return str(item or "")


def _get_ref(item: Any, idx: int) -> EvidenceRef:
    if isinstance(item, EvidenceRef):
        return item
    if isinstance(item, RetrievedChunk):
        meta = item.metadata or {}
        return EvidenceRef(
            chunk_id=item.chunk_id,
            document_id=meta.get("document_id") or item.chunk_id.split(":")[0],
            filename=meta.get("filename") or "unknown",
            page_number=meta.get("page_number") if meta.get("page_number") != -1 else None,
            sha256=meta.get("sha256"),
            source_path=meta.get("source_path"),
            score=item.score,
            text_snippet=(item.text[:120] if item.text else None),
        )
    if isinstance(item, dict):
        return EvidenceRef(
            chunk_id=item.get("chunk_id") or item.get("id") or f"dict:{idx}",
            document_id=item.get("document_id") or item.get("doc_id"),
            filename=item.get("filename") or item.get("source_file") or "unknown",
            page_number=item.get("page_number"),
            sha256=item.get("sha256"),
            source_path=item.get("source_path") or item.get("source_file"),
            score=item.get("score"),
            text_snippet=str(item.get("text") or item.get("value") or "")[:120],
        )
    return EvidenceRef(chunk_id=f"unknown:{idx}", filename="unknown", text_snippet=str(item)[:120])


def _normalize(text: str) -> str:
    return text.lower()


def _contains_term(text: str, term: str) -> bool:
    # phrase aware: for single word use word boundary, for phrase use substring
    if " " in term:
        return term.lower() in text.lower()
    return bool(re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE))


def _extract_numbers(text: str) -> list[tuple[str, str | None]]:
    return [(m.group(1), (m.group(2) or "").lower() or None) for m in NUM_RE.finditer(text)]


def _same_context(a: str, b: str) -> bool:
    # Check if same equipment or same metric keyword
    equip_a = set(m.lower() for m in EQUIP_RE.findall(a))
    equip_b = set(m.lower() for m in EQUIP_RE.findall(b))
    if equip_a and equip_b and equip_a & equip_b:
        return True
    metric_a = {k for k in METRIC_KEYWORDS if k in a.lower()}
    metric_b = {k for k in METRIC_KEYWORDS if k in b.lower()}
    if metric_a and metric_b and metric_a & metric_b:
        return True
    # If neither has context, consider same if both are short and share unit
    return False


def find_contradictions(evidence: list[Any] | None) -> ContradictionReport:
    if not evidence:
        return ContradictionReport(
            has_contradictions=False,
            contradictions=[],
            evidence_state=EvidenceState.INSUFFICIENT_EVIDENCE,
            explanation="No evidence provided",
        )

    # Filter malformed: skip non-string text items? Keep but text will be ""
    # Deduplicate by chunk_id/text
    seen: set[str] = set()
    deduped: list[Any] = []
    for idx, item in enumerate(evidence):
        txt = _get_text(item)
        ref = _get_ref(item, idx)
        key = ref.chunk_id or txt
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    if len(deduped) < 2:
        return ContradictionReport(
            has_contradictions=False,
            contradictions=[],
            evidence_state=EvidenceState.INSUFFICIENT_EVIDENCE if not deduped else EvidenceState.SUPPORTED,
            explanation="Not enough distinct evidence to compare",
        )

    contradictions: list[Contradiction] = []
    n = len(deduped)

    # Term pair checks
    for i in range(n):
        for j in range(i + 1, n):
            ti = _get_text(deduped[i])
            tj = _get_text(deduped[j])
            if not ti or not tj:
                continue
            # Unrelated evidence: skip if no shared context and terms are unrelated? For now check all pairs
            for a, b in TERM_PAIRS:
                has_a_i = _contains_term(ti, a)
                has_b_i = _contains_term(ti, b)
                has_a_j = _contains_term(tj, a)
                has_b_j = _contains_term(tj, b)
                # One has a, other has b (different items)
                if (has_a_i and has_b_j) or (has_b_i and has_a_j):
                    # Optionally require same context for numeric-like? For term pairs, require not unrelated
                    # If both contain both terms, not a conflict (same item has both)
                    # Already ensured different items, so flag
                    ref_i = _get_ref(deduped[i], i)
                    ref_j = _get_ref(deduped[j], j)
                    # deterministic order-independent id
                    ids_sorted = sorted([ref_i.chunk_id, ref_j.chunk_id])
                    terms_sorted = tuple(sorted([a, b]))
                    cid = hashlib.sha256(f"{ids_sorted[0]}:{ids_sorted[1]}:{terms_sorted[0]}:{terms_sorted[1]}".encode()).hexdigest()[:12]
                    # Avoid duplicate contradiction ids
                    if any(c.id == cid for c in contradictions):
                        continue
                    contradictions.append(
                        Contradiction(
                            id=cid,
                            evidence_refs=[ref_i, ref_j],
                            conflicting_terms=(a, b),
                            explanation=f"Conflicting terms '{a}' vs '{b}' found in separate evidence items",
                            severity="high",
                        )
                    )

    # Numeric conflicts: same equipment/metric/unit but different values
    for i in range(n):
        for j in range(i + 1, n):
            ti = _get_text(deduped[i])
            tj = _get_text(deduped[j])
            nums_i = _extract_numbers(ti)
            nums_j = _extract_numbers(tj)
            if not nums_i or not nums_j:
                continue
            # Check same context (equipment or metric)
            if not _same_context(ti, tj):
                # For numeric, require same context to avoid unrelated numeric conflicts
                continue
            # Compare each number with same unit
            for val_i, unit_i in nums_i:
                for val_j, unit_j in nums_j:
                    if unit_i != unit_j:
                        continue
                    try:
                        f_i = float(val_i)
                        f_j = float(val_j)
                    except ValueError:
                        continue
                    if f_i == f_j:
                        continue
                    # Consider conflicting if difference > 5% or absolute difference > 0.5 for bar
                    # Deterministic threshold: absolute diff > 0.5 or relative > 0.05
                    diff = abs(f_i - f_j)
                    rel = diff / max(abs(f_i), abs(f_j), 1e-9)
                    if diff > 0.5 or rel > 0.05:
                        ref_i = _get_ref(deduped[i], i)
                        ref_j = _get_ref(deduped[j], j)
                        ids_sorted = sorted([ref_i.chunk_id, ref_j.chunk_id])
                        vals_sorted = sorted([val_i, val_j])
                        cid = hashlib.sha256(f"num:{ids_sorted[0]}:{ids_sorted[1]}:{vals_sorted[0]}:{vals_sorted[1]}:{unit_i}".encode()).hexdigest()[:12]
                        if any(c.id == cid for c in contradictions):
                            continue
                        metric = unit_i or "numeric"
                        # Try to infer metric keyword
                        for kw in METRIC_KEYWORDS:
                            if kw in ti.lower() or kw in tj.lower():
                                metric = kw
                                break
                        contradictions.append(
                            Contradiction(
                                id=cid,
                                evidence_refs=[ref_i, ref_j],
                                metric=metric,
                                values=[f"{val_i} {unit_i or ''}".strip(), f"{val_j} {unit_j or ''}".strip()],
                                explanation=f"Conflicting numeric values for {metric}: {val_i} vs {val_j}",
                                severity="high",
                            )
                        )
                        # Only one numeric contradiction per pair to avoid explosion
                        break
                else:
                    continue
                break

    # Deterministic sort
    contradictions.sort(key=lambda c: c.id)

    has = len(contradictions) > 0
    return ContradictionReport(
        has_contradictions=has,
        contradictions=contradictions,
        evidence_state=EvidenceState.CONFLICTING_EVIDENCE if has else EvidenceState.SUPPORTED,
        explanation=f"Found {len(contradictions)} contradiction(s)" if has else "No contradictions found",
    )


def evaluate_with_contradictions(query: str, retrieved: list[RetrievedChunk], answer: str) -> tuple[Any, ContradictionReport]:
    """Helper to integrate with existing evidence engine: run evaluate + contradictions."""
    from backend.app.evidence.engine import evaluate

    result = evaluate(query, retrieved, answer)
    # Convert retrieved to evidence list for contradiction check
    report = find_contradictions(retrieved)
    if report.has_contradictions:
        # Upgrade state to conflicting if not already
        result.state = EvidenceState.CONFLICTING_EVIDENCE
        result.conflicting = True
    return result, report
