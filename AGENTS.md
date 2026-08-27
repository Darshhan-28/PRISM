# AGENTS.md — Persistent Engineering Instructions — SIH26117

> **Read this file FIRST in every session. The repository is the source of truth, not chat history.**

## 1. Project Purpose

**SIH26117 — Sovereign On-Premise Agentic AI Workbench using Open-Weight Multimodal LLMs for Confidential Industrial Work**

This is NOT a generic chatbot or RAG demo. It is an **investigative workbench** for confidential industrial settings (refineries, manufacturing, energy) where:

- All inference must be local / on-premise (no cloud AI APIs for core operation).
- Documents and sensor data are confidential and never leave the premises.
- Answers must be **evidence-backed, auditable, and contradiction-aware**.
- The LLM is a reasoning/orchestration component, NOT an unrestricted authority.

Target differentiators: Investigation Mode, Evidence Graph, Contradiction Detection, Evidence-Gated Responses, and full Audit/Replay Trail.

Full product definition: `docs/PRODUCT_SPEC.md`
Full architecture: `docs/ARCHITECTURE.md`
Agent/tool design: `docs/AGENT_SPEC.md`
Security model: `docs/SECURITY.md`

## 2. Current Architecture (Status: Phase 1 — Documentation & Scaffolding)

```
SIH26117/
├── backend/        # FastAPI (planned) — API, orchestrator, tools, engines
├── frontend/       # Lightweight web UI (planned) — Workbench + Investigation view
├── models/         # Local model weights / Ollama registry (gitignored when large)
├── data/           # Synthetic/public documents only — NEVER real confidential data
│   ├── raw/        # Original source files (PDF, CSV, JSON, images)
│   ├── processed/  # Parsed + chunked artifacts
│   └── vector_store/ # Local vector DB files (e.g., Chroma/SQLite)
├── tests/          # Unit + integration tests
└── docs/           # Architecture & spec docs (source of truth)
    ├── ARCHITECTURE.md
    ├── PRODUCT_SPEC.md
    ├── AGENT_SPEC.md
    └── SECURITY.md
```

**Phase roadmap (incremental, do not skip):**

1. Architecture + instructions (THIS PHASE)
2. Local document ingestion
3. Local embeddings + retrieval
4. Local LLM integration (via replaceable adapter)
5. Evidence-backed answering
6. Agent/tool architecture
7. Investigation Mode
8. Evidence Graph
9. Contradiction Detection
10. Multimodal input
11. Safety + auditability
12. Judge-grade UI/demo

Current phase: **Phase 2 — Local Document Ingestion (In Progress).** Phase 1 docs approved. Implementing `backend/app/ingestion/` + `backend/app/retrieval/` (adapter protocols) + `backend/app/store/` + `data/` scaffolding.

### Hardware Constraint (Non-Negotiable)

Development and demo machine:

- Windows 11 Home, Intel 12th-gen mobile, 16 GB RAM, Intel Iris Xe, NO NVIDIA GPU, 512 GB NVMe
- Python 3.13.2, Node 24.19.0, Git; Docker/Ollama not yet installed

Implications:
- Keep services lightweight; no Kubernetes, no microservices, no multi-LLM concurrency by default.
- Do NOT assume CUDA, 24GB VRAM, or cloud inference.
- Support quantized models (e.g., Q4_K_M) and CPU inference.
- Model layer must be fully replaceable (adapter pattern) — model choice is deferred until benchmarking.
- Benchmark before committing to a model size.

## 3. Existing Directory Structure — Rules

- **DO NOT** delete, rename, or move `backend/`, `frontend/`, `models/`, `data/`, `tests/`, `docs/`.
- Do NOT introduce a second top-level architecture or microservices/Kubernetes/blockchain/cloud AI APIs.
- You MAY create files/subdirectories INSIDE those directories when justified (e.g., `backend/app/`, `data/raw/`).
- Before any structural change: explain WHY it is necessary in the PR/session summary and update `docs/ARCHITECTURE.md`.

## 4. Coding Principles

1. **Small modules, clear interfaces.** No giant files (>400 lines needs justification). One responsibility per module.
2. **Type safety where practical:** Python type hints, Pydantic models for API/tool schemas, TypeScript for frontend.
3. **Meaningful errors & logging:** Structured logging (JSON optional), no silent failures, propagate error context.
4. **Model-agnostic design:** All LLM/vision/embedding calls go through an adapter (`backend/app/llm/`). No direct `openai.*` or provider-specific code in business logic.
5. **Local-first:** Offline-capable by default. No network call in core retrieval/inference path.
6. **No hardcoded secrets.** Use `.env` (gitignored) + example file. Never commit keys/tokens.
7. **No unnecessary dependencies.** Prefer stdlib / lightweight libs. Justify each addition (size, RAM, license).
8. **No fake security claims.** Document assumptions and boundaries explicitly.
9. **No unnecessary abstractions.** Prefer concrete, testable code over framework-heavy patterns.
10. **Inspect before changing:** Read target files and relevant docs before editing. Do not recreate existing functionality.
11. **Explain major changes:** Any change to architecture, data flow, or tool contracts requires a short rationale in the commit/PR and an update to `docs/`.

## 5. Security Principles

- Sovereign/on-prem is an **architectural constraint**, not a marketing claim. See `docs/SECURITY.md`.
- Core operation must work with internet disabled: local model, local docs, local DB, local vector store, local tools.
- Implement explicit boundaries: input validation, path traversal guards, file-type allowlists, prompt-injection defenses, output evidence-gating.
- Treat all ingested documents as untrusted (malicious content, injection payloads).
- LLM output is **never** treated as fact — it must be gated by evidence states: `SUPPORTED | PARTIALLY_SUPPORTED | INSUFFICIENT_EVIDENCE | CONFLICTING_EVIDENCE`.
- Safety-critical responses (SOP violations, shutdown advice) require evidence + explicit disclaimer + audit log.

## 6. Model-Agnostic & Local-First Requirements

- **No cloud AI APIs for core operation.** No OpenAI, Anthropic, Gemini, etc. in the default path. Cloud may only appear as an optional, explicitly flagged, non-default adapter for benchmarking — never required.
- **Adapter interface** (planned: `backend/app/llm/adapter.py`):
  ```python
  class LLMAdapter(Protocol):
      def generate(self, prompt: str, system: str | None, **kwargs) -> str: ...
      async def generate_stream(self, ...): ...
      def embed(self, texts: list[str]) -> list[list[float]]: ...  # or separate EmbeddingAdapter
  ```
- Implementations: `OllamaAdapter` (primary for dev), `LlamaCppAdapter` (alternative), `MockAdapter` (tests). Swapped via config/env, not code changes.
- Keep the model layer replaceable to benchmark small open-weight models later (e.g., Qwen2.5 1.5B/3B, Phi-3 mini, Gemma 2 2B, Llama 3.2 1B/3B — quantized).
- Same principle for vision: `VisionAdapter` stub in Phase 1, implemented in Phase 10.

## 7. File Modification Rules

1. Always `Read` the file and surrounding context before `Edit`.
2. Derive `oldString` from actual file content (match indentation exactly).
3. Keep edits minimal — change only what the task requires.
4. Do not modify unrelated files.
5. After editing, verify: read the edited region, run relevant tests/linters if present, and check `git status`/`git diff`.
6. Never hallucinate APIs, config keys, or file paths. If unsure, `Glob`/`Grep` the codebase or ask the user.

## 8. Testing Requirements

- Tests live in `tests/` mirror `backend/` structure (e.g., `tests/test_evidence_engine.py`).
- Required for: chunking, retrieval, evidence-gating, contradiction detection, tool validation, audit logging.
- Run `pytest` (or project test command) after logic changes. Do not claim tests pass without running them.
- For LLM-dependent logic, use `MockAdapter` / fixtures — tests must not require a running model or network.

## 9. Documentation Requirements

- `docs/` is the source of truth. After any significant task: update the relevant doc, record architecture decisions (ADR-style short entry), completed features, known issues, and next step.
- Keep docs concise and factual. No marketing language.
- Use `file_path:line_number` references when pointing to code.
- Diagrams: ASCII or Mermaid (if frontend supports it) — keep them renderable without external tools.

## 10. Session Continuity Rule

**Never assume previous chat/session context exists.**

At session start:
1. Read `AGENTS.md` (this file).
2. Read `docs/ARCHITECTURE.md`, `docs/PRODUCT_SPEC.md`, `docs/AGENT_SPEC.md`, `docs/SECURITY.md`.
3. Inspect current codebase: `Read` on `D:\SIH26117` + `Glob` for relevant files + `git status`/`git log --oneline -10`/`git diff`.
4. Determine what has already been implemented before writing new code.
5. Do not recreate existing functionality or rewrite architecture for convenience.
6. Do not change architecture without user approval — STOP and explain first.

At session end / after a significant task:
- Update relevant documentation.
- Record: architectural decisions, completed features, known issues, next recommended step.
- Ensure `git status` is clean or changes are clearly described.

## 11. Verification Requirement

- After any code modification: verify via execution where reasonable — run the changed module, run tests, or run a small `python -c` sanity check via `bash`.
- Evidence before synthesis — inspect files yourself; do not trust "already verified" claims from prior messages.
- If findings contradict a previous claim, state the discrepancy explicitly.

## 12. Working Rules for Agents

- You are an **implementation agent**, not the product decision-maker.
- If a major architectural change seems needed: **STOP and explain before implementing.**
- If requirements are ambiguous: **STOP and ask** (use the `question` tool or plain text). Do not silently invent requirements.
- Prefer proposal + confirmation over unilateral restructuring.
- Keep responses short and factual; avoid superlatives and emotional validation.

## 13. Prohibited Actions

- Cloud AI APIs in core path, Kubernetes, blockchain, microservices without justification.
- Deleting/renaming top-level directories.
- Installing packages / Ollama / models without explicit user instruction (Phase 1 is docs-only).
- Creating `frontend`/`backend` implementations before Phase 2 is approved.
- Committing secrets, large model weights, or real confidential data.
- Claiming security or accuracy guarantees that are not implemented and tested.

## 14. Commit & PR Hygiene

- Inspect `git status`, `git diff`, `git log --oneline -10` before committing.
- Stage only intended files; never commit secrets.
- Write concise, conventional commit messages.
- Do not amend failed commits — create a new commit after fixing.
- Use `gh` for GitHub operations; return PR URL when done.

---

**Last updated:** 2026-08-27 — Phase 2 complete (local ingestion: validator, parsers, chunker, pipeline, MockEmbedder, ChromaStore, CLI, 6 synthetic samples, 33 tests). Next: Phase 3 — Local embeddings + retrieval (real embedder benchmarking).
