# PRODUCT_SPEC.md — SIH26117 Sovereign On-Premise Agentic AI Workbench

## 1. Target Users

| User | Context | Need |
|------|---------|------|
| **Reliability / Maintenance Engineer** | Refinery, manufacturing plant, energy facility | Diagnose anomalies, correlate sensor + log + SOP evidence without leaking data off-premise |
| **Safety / Operations Engineer** | SOP-governed environment | Verify whether a proposed action violates SOP; require evidence + audit trail |
| **Plant Manager / Auditor** | Confidential industrial site | Replayable investigation report that traces every conclusion to source documents |
| **SIH Judges / Demo Evaluators** | Offline demo on a laptop | See differentiation vs. generic RAG in < 5 minutes via a flagship investigation scenario |

Non-user: general consumer chatbot audience. This is an industrial workbench, not a writing assistant.

## 2. Main Problem

Industrial sites possess sensitive, high-volume operational records (maintenance logs, inspection reports, SOPs, sensor exports, equipment photographs) that **cannot be sent to cloud AI services** for confidentiality and sovereignty reasons. Existing generic RAG/chatbot tools:

- Depend on cloud inference (data exfiltration risk).
- Answer without verifiable grounding (hallucination risk — unacceptable for shutdown/safety advice).
- Hide contradictions between sources instead of flagging them.
- Provide no audit/replay trail for accountability.
- Lack industrial investigation workflows (evidence graph, tool-based verification).

Result: engineers either under-use AI or use it unsafely.

## 3. Product Vision

A **sovereign, on-premise, evidence-gated, agentic workbench** that runs fully on a plant-owned machine (or the SIH demo laptop) and turns a natural-language investigation objective into a **structured, auditable, evidence-backed report** with explicit support/conflict signals — using only local open-weight models, local documents, local vector store, and deterministic local tools.

The LLM is a reasoning/orchestration component. Deterministic code owns retrieval, tool execution, evidence gating, and audit.

## 4. Core Workflows

### 4.1 Ingest & Index (Offline)
1. User drops files into `data/raw/` or uploads via Workbench UI (PDF, CSV, JSON, images — allowlist enforced).
2. System validates, parses, chunks, embeds locally, upserts to local vector store + structured store.
3. UI shows ingest status, doc count, chunk count, embedding health.

### 4.2 Ask (Evidence-Backed Q&A — Phases 5–6)
1. User asks a factual question (e.g., "What is the SOP pressure limit for Pump P-204?").
2. System retrieves top-k chunks locally, generates a grounded draft via local LLM, evidence-gates it, and returns answer + evidenceState + evidenceRefs.
3. If insufficient/conflicting evidence, the system says so explicitly.

### 4.3 Investigate (Investigation Mode — Phases 7–9) — Flagship Workflow
1. User starts an investigation: objective (e.g., "Investigate the abnormal pressure event in Pump P-204 on 2026-08-15").
2. Orchestrator plans steps (which evidence to gather, which tools to call).
3. System executes tools deterministically: `search_documents`, `query_sensor_data`, `search_maintenance_logs`, `compare_sources`, etc.
4. Evidence Engine and Contradiction Engine run; Evidence Graph is built.
5. System produces a structured report: summary, timeline, findings per evidence source, conflicts flagged, overall evidence state, SOP/policy check, and recommended next steps (guarded by disclaimers for safety-critical advice).
6. Full audit trail is persisted and replayable.

### 4.4 Review & Replay
- Evidence Graph view (nodes/edges), Audit log view, and per-claim grounding allow an auditor to trace every sentence to source chunks.

## 5. MVP (Minimum Viable Prototype — Phases 2–6, Judge-Visible)

Must run on the specified laptop with internet disabled at demo time (after setup):

- [ ] Local ingestion: PDF + CSV/JSON → parse → chunk → local embeddings → local vector store (Chroma/sqlite-vec) + SQLite.
- [ ] Local retrieval + local LLM (replaceable adapter; benchmarked small quantized model) → evidence-backed answer with `SUPPORTED | PARTIALLY_SUPPORTED | INSUFFICIENT_EVIDENCE | CONFLICTING_EVIDENCE` badge and citations (doc/page/chunk).
- [ ] Single orchestrator + at least 3 deterministic tools (`search_documents`, `query_sensor_data`, `search_maintenance_logs`).
- [ ] Audit log persisted and viewable.
- [ ] Flagship demo scenario runnable end-to-end.
- [ ] Synthetic/public data only — no real confidential data in repo.

## 6. Advanced Features (Phases 7–12)

| Feature | Description | Phase |
|---------|-------------|-------|
| Investigation Mode | Objective → plan → tool calls → structured report | 7 |
| Evidence Graph | Nodes (Incident/Equipment/Sensor/Maintenance/SOP/Finding) + edges (supports/contradicts/references) | 8 |
| Contradiction Detection | Rule + embedding + LLM-assisted flagging; `CONFLICTING_EVIDENCE` state | 9 |
| Multimodal Input | Equipment photos, scanned docs, P&ID diagrams via `VisionAdapter` (stub in Phase 1) | 10 |
| Safety/Policy Validation | SOP threshold checks; safety-critical advice gated + disclaimer + audit | 11 |
| Evidence-Gated Responses | No confident claim without evidence; explicit insufficient/conflicting states | 5, 11 |
| Audit/Replay Trail | Append-only log; deterministic replay from logged inputs | 11 |
| Judge-Grade UI/Demo | Polished Workbench + Investigation + Graph + Audit views; one-click demo seed | 12 |

## 7. Non-Goals (Explicitly Out of Scope)

- Cloud LLM as default or required path. Cloud may only appear as an explicitly flagged non-default adapter for benchmarking.
- Kubernetes, microservices, blockchain.
- Multi-tenant SaaS, user management at scale (single-workstation assumption; basic local auth only if needed).
- GPU-dependent architecture or assumption of 24 GB VRAM / CUDA.
- Real-time streaming sensor ingestion at industrial scale (batch CSV/JSON import is sufficient for prototype).
- Claiming security, accuracy, or compliance guarantees that are not implemented and tested (see `docs/SECURITY.md`).
- Storing or committing real confidential MRPL/industrial data (synthetic/public data only in `data/`).

## 8. Demo Scenarios

### Flagship Scenario (Primary — Must Be Judge-Ready)

**Title:** Investigate abnormal pump pressure using maintenance logs, sensor data, and SOP documents

**Synthetic data (all under `data/raw/` samples):**
- `SOP_P-204.pdf` — SOP stating normal pressure 2.1–3.4 bar, inspection interval 30 days, shutdown procedure §4.2.
- `maintenance_log_P-204.csv` — entries: valve replacement 2026-08-12, seal inspection 2026-08-10.
- `inspection_report_P-204_2026-08-14.pdf` — states "Valve replacement pending — seal wear observed."
- `sensor_P-204_2026-08-15.csv` — pressure readings: 08:00 2.3 bar, 12:00 3.1 bar, 14:30 4.8 bar (abnormal spike), 15:00 4.9 bar.
- `incident_note_2026-08-15.json` — operator note: abnormal vibration at 14:35.

**Investigation objective (user input):**
> "Investigate the abnormal pressure event in Pump P-204 on 2026-08-15."

**Expected system behavior:**
1. Retrieves SOP threshold (2.1–3.4 bar) + sensor spike (4.8–4.9 bar) + maintenance/inspection conflict.
2. Flags contradiction: maintenance log says valve replaced 08-12 vs. inspection report says replacement pending 08-14.
3. Evidence Graph: `Incident(2026-08-15 spike) → Equipment(P-204) → SensorEvent(4.8 bar) + MaintenanceLog(valve replaced?) + InspectionReport(pending) + SOP(threshold) → Finding(INCONCLUSIVE — conflicting maintenance evidence; SOP threshold exceeded; recommend physical verification, do not assume valve state)`.
4. Returns evidence state: `CONFLICTING_EVIDENCE` + `PARTIALLY_SUPPORTED` for the pressure exceedance itself (supported by sensor + SOP) and `CONFLICTING_EVIDENCE` for valve status.
5. Safety/policy layer: notes SOP §4.2 shutdown procedure not to be executed on AI advice alone; requires human verification.
6. Audit trail records every step for replay.

**Why this differentiates:** generic RAG would silently pick one source and confidently assert valve status; this workbench surfaces the conflict and gates the conclusion.

### Secondary Scenario (Evidence-Gated Safety)

> "Can we restart Pump P-204 now?"

System must refuse to give an ungrounded "yes"; it must check SOP, sensor state, maintenance status, and if evidence is insufficient/conflicting, return `INSUFFICIENT_EVIDENCE` or `CONFLICTING_EVIDENCE` with disclaimer and required human checks.

## 9. Success Criteria

| Criterion | How Measured |
|-----------|--------------|
| **Sovereignty** | Demo runs with Wi-Fi disabled after setup; no cloud API call in core path (verified via network log / adapter config `ALLOW_CLOUD_ADAPTER=false`) |
| **Evidence grounding** | Every factual sentence in report cites at least one retrieved chunk; evidence state badge shown; no citation-free factual claim passes Evidence Engine |
| **Contradiction awareness** | Flagship scenario correctly flags the maintenance vs. inspection conflict as `CONFLICTING_EVIDENCE` with both sources cited |
| **Auditability** | Full investigation replayable from `audit_log` / JSONL; judge can inspect query → tools → evidence → response |
| **Hardware fit** | Runs on 16 GB / Iris Xe laptop without OOM; quantized small model (1.5–3B) benchmarked; memory budget documented |
| **Model replaceability** | Swap `LLM_ADAPTER` / `LLM_MODEL` via config without code change; tests use `MockAdapter` |
| **Industrial relevance** | SOP/policy checks present; safety-critical advice gated with disclaimer |
| **No fake claims** | Security and accuracy boundaries documented in `docs/SECURITY.md`; no untested guarantees in UI or docs |

## 10. Constraints & Assumptions

- Synthetic/public data only for development; treat all ingested docs as untrusted (see `docs/SECURITY.md`).
- Model choice deferred until benchmarking on the target laptop; quantized CPU inference assumed.
- Single-workstation deployment; no multi-user concurrency beyond demo use.
- Vision/multimodal is Phase 10 — architecture supports it via stub, but MVP does not require it.

## 11. Next Step

Await Phase 1 doc approval, then proceed to Phase 2 (local document ingestion) and seed synthetic flagship data under `data/raw/`.

---

**Last updated:** 2026-08-27 — Phase 1. No runtime code yet; this spec is the build contract.
