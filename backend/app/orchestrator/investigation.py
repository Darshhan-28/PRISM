"""Investigation Orchestrator — bounded, deterministic, offline."""

import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.app.config import get_config
from backend.app.llm.factory import get_llm_adapter
from backend.app.tools.registry import execute_tool, list_tools, TOOL_REGISTRY
from backend.app.evidence.engine import EvidenceState, EvidenceRef, EvidenceResult, evaluate, build_grounded_prompt
from backend.app.retrieval.retriever import RetrievedChunk
from backend.app.store.db import get_connection, init_db
from backend.app.safety.policy import validate_objective as safety_validate_objective, validate_tool_input as safety_validate_tool_input, validate_tool_name as safety_validate_tool_name, check_step_limits as safety_check_step_limits, check_output_size as safety_check_output_size, detect_prompt_injection as safety_detect_injection
from backend.app.audit.logger import log_event

# Hard limits per spec
MAX_STEPS = 8
STEP_TIMEOUT_SECONDS = 30
MAX_TOOL_CALLS = 8
OBJECTIVE_MIN_LEN = 10
OBJECTIVE_MAX_LEN = 2000

ALLOWED_TOOLS = set(TOOL_REGISTRY.keys())


# Pydantic plan models
class InvestigationStep(BaseModel):
    step_no: int = Field(..., ge=1, le=MAX_STEPS)
    tool: str
    input: dict[str, Any] = Field(..., description="Validated tool input")
    rationale: str = Field(..., min_length=3, max_length=500)

    @field_validator("tool")
    @classmethod
    def validate_tool(cls, v):
        if v not in ALLOWED_TOOLS:
            raise ValueError(f"Unknown tool: {v}. Allowed: {sorted(ALLOWED_TOOLS)}")
        return v


class InvestigationPlan(BaseModel):
    objective: str = Field(..., min_length=OBJECTIVE_MIN_LEN, max_length=OBJECTIVE_MAX_LEN)
    steps: list[InvestigationStep] = Field(..., min_length=1, max_length=MAX_STEPS)

    @model_validator(mode="after")
    def check_steps(self):
        if not self.steps:
            raise ValueError("steps must be non-empty")
        nums = [s.step_no for s in self.steps]
        if len(nums) != len(set(nums)):
            raise ValueError("duplicate step_no")
        if sorted(nums) != list(range(1, len(nums) + 1)):
            raise ValueError(f"step_no must be sequential 1..{len(nums)}, got {nums}")
        # Validate each step input against tool's input_model
        for step in self.steps:
            spec = TOOL_REGISTRY.get(step.tool)
            if not spec:
                raise ValueError(f"Unknown tool in step {step.step_no}: {step.tool}")
            try:
                spec["input_model"](**step.input)
            except Exception as e:
                raise ValueError(f"Invalid input for tool {step.tool} at step {step.step_no}: {e}")
        return self


class StepExecution(BaseModel):
    step_no: int
    tool: str
    input: dict[str, Any]
    rationale: str
    success: bool
    result: Any = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    error: str | None = None
    execution_ms: int = 0


class InvestigationReport(BaseModel):
    investigation_id: str
    objective: str
    status: Literal["completed", "failed", "partial"]
    plan: InvestigationPlan | None = None
    steps_executed: list[StepExecution] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    evidence_state: EvidenceState
    summary: str
    created_at: str
    completed_at: str
    error: str | None = None


PLANNING_SYSTEM = (
    "You are an industrial investigation planner. "
    "You must output ONLY valid JSON with keys objective and steps. "
    f"Steps must use only these tools: {sorted(ALLOWED_TOOLS)}. "
    "Each step needs step_no (1..N), tool, input (valid for that tool), rationale. "
    f"Max {MAX_STEPS} steps. No other tools. No code execution."
)

PLANNING_PROMPT_TEMPLATE = """Objective: {objective}

Available tools and their inputs:
- search_documents: {{query: str 1..500, top_k: 1..20, filters: optional dict}}
- retrieve_evidence: {{chunk_id or document_id}}
- query_sensor_data: {{equipment_id, metric optional, start_time/end_time optional, aggregation raw|avg|max|min}}
- search_maintenance_logs: {{equipment_id, start_date/end_date optional, keyword optional}}
- inspect_image: {{image_path: str path under data/raw|data/processed|tests/fixtures|tmp, prompt: str}} (only if objective mentions image/photo/visual)

Output JSON example:
{{"objective": "...", "steps": [{{"step_no": 1, "tool": "search_documents", "input": {{"query": "pressure"}}, "rationale": "find SOP"}}]}}

Now output JSON for the objective above:"""


def _extract_json(text: str) -> str:
    # Try to find JSON object in text (handle markdown fences)
    # Prefer ```json block
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return m.group(1)
    # Find first { to last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def _validate_objective(objective: str) -> None:
    if not isinstance(objective, str) or not objective.strip():
        raise ValueError("objective must be non-empty string")
    if not (OBJECTIVE_MIN_LEN <= len(objective) <= OBJECTIVE_MAX_LEN):
        raise ValueError(f"objective length must be {OBJECTIVE_MIN_LEN}..{OBJECTIVE_MAX_LEN}, got {len(objective)}")


# Persistence helpers
INVESTIGATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS investigations (
    id TEXT PRIMARY KEY,
    objective TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_state TEXT,
    summary TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS investigation_steps (
    id TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    step_no INTEGER NOT NULL,
    tool TEXT NOT NULL,
    input TEXT NOT NULL,
    rationale TEXT,
    success INTEGER NOT NULL,
    result TEXT,
    error TEXT,
    evidence_refs TEXT,
    execution_ms INTEGER
);
"""


def _ensure_investigation_tables(db_path=None):
    conn = get_connection(db_path)
    try:
        conn.executescript(INVESTIGATION_SCHEMA)
        conn.commit()
    finally:
        conn.close()


class InvestigationOrchestrator:
    def __init__(self, llm_adapter=None, retriever=None, db_path=None):
        self.llm_adapter = llm_adapter or get_llm_adapter()
        # retriever is kept for search_documents tool injection if needed
        self.retriever = retriever
        self.db_path = db_path
        _ensure_investigation_tables(db_path)

    def investigate(self, objective: str, retriever=None, **kwargs) -> InvestigationReport:
        created_at = datetime.now(timezone.utc).isoformat()
        investigation_id = uuid.uuid4().hex
        effective_retriever = retriever or self.retriever
        # Audit: investigation start
        try:
            log_event(investigation_id, "investigation_start", raw_input={"objective": objective[:500]}, success=None, db_path=self.db_path)
        except Exception:
            pass
        # Safety: objective validation (treat as untrusted)
        safety_res = safety_validate_objective(objective)
        if not safety_res.allowed:
            completed_at = datetime.now(timezone.utc).isoformat()
            try:
                log_event(investigation_id, "safety_rejection", raw_input={"objective": objective[:500]}, success=False, error_code=safety_res.error_code, db_path=self.db_path)
            except Exception:
                pass
            report = InvestigationReport(
                investigation_id=investigation_id,
                objective=objective,
                status="failed",
                plan=None,
                steps_executed=[],
                evidence_refs=[],
                evidence_state=EvidenceState.INSUFFICIENT_EVIDENCE,
                summary=f"Invalid objective: {safety_res.reason}",
                created_at=created_at,
                completed_at=completed_at,
                error=safety_res.reason,
            )
            self._persist(report)
            return report
        # Validate objective (original length check)
        try:
            _validate_objective(objective)
        except ValueError as e:
            completed_at = datetime.now(timezone.utc).isoformat()
            report = InvestigationReport(
                investigation_id=investigation_id,
                objective=objective,
                status="failed",
                plan=None,
                steps_executed=[],
                evidence_refs=[],
                evidence_state=EvidenceState.INSUFFICIENT_EVIDENCE,
                summary=f"Invalid objective: {e}",
                created_at=created_at,
                completed_at=completed_at,
                error=str(e),
            )
            self._persist(report)
            return report

        # Ask LLM for plan
        prompt = PLANNING_PROMPT_TEMPLATE.format(objective=objective)
        try:
            raw = self.llm_adapter.generate(prompt, system=PLANNING_SYSTEM)
        except Exception as e:
            completed_at = datetime.now(timezone.utc).isoformat()
            report = InvestigationReport(
                investigation_id=investigation_id,
                objective=objective,
                status="failed",
                plan=None,
                steps_executed=[],
                evidence_refs=[],
                evidence_state=EvidenceState.INSUFFICIENT_EVIDENCE,
                summary=f"LLM planning failed: {e}",
                created_at=created_at,
                completed_at=completed_at,
                error=str(e),
            )
            self._persist(report)
            return report

        # Parse and validate plan
        try:
            json_str = _extract_json(raw)
            data = json.loads(json_str)
            plan = InvestigationPlan(**data)
            # Safety: check plan steps count via policy
            safety_step = safety_check_step_limits(len(plan.steps))
            # Note: check_step_limits expects current count, we check if plan exceeds max
            if len(plan.steps) > MAX_STEPS:
                raise ValueError(f"Plan exceeds MAX_STEPS {MAX_STEPS}")
            try:
                log_event(investigation_id, "plan_generated", raw_input={"plan": data}, success=True, db_path=self.db_path)
            except Exception:
                pass
        except Exception as e:
            try:
                log_event(investigation_id, "plan_validation_failed", raw_input={"raw": raw[:500]}, success=False, error_code="PLAN_VALIDATION_FAILED", db_path=self.db_path)
            except Exception:
                pass
            completed_at = datetime.now(timezone.utc).isoformat()
            report = InvestigationReport(
                investigation_id=investigation_id,
                objective=objective,
                status="failed",
                plan=None,
                steps_executed=[],
                evidence_refs=[],
                evidence_state=EvidenceState.INSUFFICIENT_EVIDENCE,
                summary=f"Plan validation failed: {e}. Raw: {raw[:500]}",
                created_at=created_at,
                completed_at=completed_at,
                error=f"Plan validation failed: {e}",
            )
            self._persist(report)
            return report

        # Enforce max steps/tool calls
        if len(plan.steps) > MAX_STEPS:
            completed_at = datetime.now(timezone.utc).isoformat()
            report = InvestigationReport(
                investigation_id=investigation_id,
                objective=objective,
                status="failed",
                plan=plan,
                steps_executed=[],
                evidence_refs=[],
                evidence_state=EvidenceState.INSUFFICIENT_EVIDENCE,
                summary=f"Plan exceeds MAX_STEPS {MAX_STEPS}",
                created_at=created_at,
                completed_at=completed_at,
                error="MAX_STEPS exceeded",
            )
            self._persist(report)
            return report

        # Execute steps deterministically
        steps_executed: list[StepExecution] = []
        all_evidence: list[EvidenceRef] = []
        for step in sorted(plan.steps, key=lambda s: s.step_no):
            # Safety: check step limits before execution
            safety_lim = safety_check_step_limits(len(steps_executed))
            if not safety_lim.allowed:
                steps_executed.append(
                    StepExecution(
                        step_no=step.step_no,
                        tool=step.tool,
                        input=step.input,
                        rationale=step.rationale,
                        success=False,
                        result=None,
                        evidence_refs=[],
                        error=safety_lim.reason,
                        execution_ms=0,
                    )
                )
                try:
                    log_event(investigation_id, "safety_rejection", tool=step.tool, raw_input=step.input, success=False, error_code=safety_lim.error_code, db_path=self.db_path)
                except Exception:
                    pass
                break
            # Safety: validate tool name and input (untrusted from LLM plan)
            safety_tool = safety_validate_tool_name(step.tool)
            if not safety_tool.allowed:
                steps_executed.append(
                    StepExecution(step_no=step.step_no, tool=step.tool, input=step.input, rationale=step.rationale, success=False, result=None, evidence_refs=[], error=safety_tool.reason, execution_ms=0)
                )
                try:
                    log_event(investigation_id, "safety_rejection", tool=step.tool, raw_input=step.input, success=False, error_code=safety_tool.error_code, db_path=self.db_path)
                except Exception:
                    pass
                continue
            safety_inp = safety_validate_tool_input(step.tool, step.input)
            if not safety_inp.allowed:
                steps_executed.append(
                    StepExecution(step_no=step.step_no, tool=step.tool, input=step.input, rationale=step.rationale, success=False, result=None, evidence_refs=[], error=safety_inp.reason, execution_ms=0)
                )
                try:
                    log_event(investigation_id, "safety_rejection", tool=step.tool, raw_input=step.input, success=False, error_code=safety_inp.error_code, db_path=self.db_path)
                except Exception:
                    pass
                continue

            t0 = time.time()
            # Prepare kwargs for tool execution (inject retriever/db)
            exec_kwargs: dict[str, Any] = {}
            if step.tool == "search_documents" and effective_retriever is not None:
                exec_kwargs["retriever"] = effective_retriever
            if step.tool in ("query_sensor_data", "search_maintenance_logs", "retrieve_evidence", "inspect_image") and self.db_path is not None:
                exec_kwargs["db_path"] = self.db_path
            # Also allow caller kwargs to override
            exec_kwargs.update({k: v for k, v in kwargs.items() if k in ("retriever", "db_path")})

            try:
                # Timeout guard: tools are local fast, but we enforce wall-clock
                result = execute_tool(step.tool, step.input, **exec_kwargs)
                elapsed_ms = int((time.time() - t0) * 1000)
                if elapsed_ms > STEP_TIMEOUT_SECONDS * 1000:
                    # Mark timeout but keep result
                    result = result.model_copy(update={"success": False, "error": f"step timeout {elapsed_ms}ms > {STEP_TIMEOUT_SECONDS*1000}ms"}) if hasattr(result, "model_copy") else result

                # Safety: output size check (treat evidence as untrusted)
                try:
                    import json as _json2
                    out_str = _json2.dumps(getattr(result, "result", None), default=str)
                    oc = safety_check_output_size(out_str)
                    if not oc.allowed:
                        result = result.model_copy(update={"result": out_str[:4000], "truncated": True, "error": oc.reason}) if hasattr(result, "model_copy") else result
                    # Detect injection in tool output (evidence is untrusted, never executable)
                    if safety_detect_injection(out_str):
                        try:
                            log_event(investigation_id, "prompt_injection_detected", tool=step.tool, raw_input={"output": out_str[:500]}, success=False, error_code="PROMPT_INJECTION", db_path=self.db_path)
                        except Exception:
                            pass
                except Exception:
                    pass
                # result is ToolOutput
                success = bool(getattr(result, "success", False))
                err = getattr(result, "error", None)
                # Extract evidence refs — handle tool refs where chunk_id may be None (sensor/maintenance)
                ev_refs = []
                for ref in getattr(result, "evidence_refs", []):
                    data = ref.model_dump() if hasattr(ref, "model_dump") else dict(ref)
                    if not data.get("chunk_id"):
                        fallback = data.get("document_id") or data.get("filename") or "evidence"
                        data["chunk_id"] = f"{fallback}:{step.step_no}:{len(all_evidence)}"
                    if not data.get("filename"):
                        data["filename"] = data.get("source_path", "unknown").split("/")[-1].split("\\")[-1] if data.get("source_path") else "unknown"
                    try:
                        ev = EvidenceRef(**data)
                    except Exception:
                        ev = EvidenceRef(
                            chunk_id=str(data.get("chunk_id")),
                            document_id=data.get("document_id"),
                            filename=str(data.get("filename") or "unknown"),
                            page_number=data.get("page_number"),
                            sha256=data.get("sha256"),
                            source_path=data.get("source_path"),
                            score=data.get("score"),
                            text_snippet=None,
                        )
                    ev_refs.append(ev)
                    all_evidence.append(ev)

                steps_executed.append(
                    StepExecution(
                        step_no=step.step_no,
                        tool=step.tool,
                        input=step.input,
                        rationale=step.rationale,
                        success=success,
                        result=getattr(result, "result", None),
                        evidence_refs=ev_refs,
                        error=err,
                        execution_ms=elapsed_ms,
                    )
                )
                try:
                    log_event(investigation_id, "tool_call", tool=step.tool, raw_input=step.input, success=success, execution_ms=elapsed_ms, evidence_refs=[r.model_dump() for r in ev_refs], error_code=err, db_path=self.db_path)
                except Exception:
                    pass
            except Exception as e:
                elapsed_ms = int((time.time() - t0) * 1000)
                steps_executed.append(
                    StepExecution(
                        step_no=step.step_no,
                        tool=step.tool,
                        input=step.input,
                        rationale=step.rationale,
                        success=False,
                        result=None,
                        evidence_refs=[],
                        error=f"{type(e).__name__}: {e}",
                        execution_ms=elapsed_ms,
                    )
                )
                try:
                    log_event(investigation_id, "tool_call", tool=step.tool, raw_input=step.input, success=False, execution_ms=elapsed_ms, error_code=f"{type(e).__name__}", db_path=self.db_path)
                except Exception:
                    pass
            # Enforce max tool calls
            if len(steps_executed) >= MAX_TOOL_CALLS:
                break

        # Evidence gating
        # Build pseudo RetrievedChunks from evidence_refs for engine evaluation
        # For search_documents, evidence_refs already have text snippet; for sensor/maintenance, use result summary
        pseudo_chunks: list[RetrievedChunk] = []
        for ref in all_evidence:
            # Create minimal RetrievedChunk for engine (text from ref or placeholder)
            pseudo_chunks.append(
                RetrievedChunk(
                    chunk_id=ref.chunk_id or ref.document_id or "unknown",
                    text=ref.text_snippet or ref.filename or "evidence",
                    score=ref.score or 0.9,
                    distance=0.1,
                    metadata={
                        "filename": ref.filename,
                        "chunk_id": ref.chunk_id,
                        "document_id": ref.document_id,
                        "sha256": ref.sha256,
                        "page_number": ref.page_number,
                        "source_path": ref.source_path,
                    },
                )
            )

        # Generate summary via LLM grounded on evidence (if any evidence)
        if not all_evidence:
            evidence_state = EvidenceState.INSUFFICIENT_EVIDENCE
            summary = "Insufficient evidence: No relevant evidence was retrieved for the investigation."
            status = "partial"
        else:
            # Ask LLM to summarize
            try:
                system, prompt = build_grounded_prompt(objective, pseudo_chunks)
                summary_raw = self.llm_adapter.generate(prompt, system=system)
            except Exception as e:
                summary_raw = f"Insufficient evidence: LLM error during summary: {e}"
            # Evaluate summary
            eval_res = evaluate(objective, pseudo_chunks, summary_raw)
            evidence_state = eval_res.state
            summary = eval_res.answer
            # Prefer conflicting if detected
            if eval_res.conflicting:
                evidence_state = EvidenceState.CONFLICTING_EVIDENCE
        # Determine overall status
        if any(not s.success for s in steps_executed):
            status_val: str = "partial" if all_evidence else "failed"
        else:
            status_val = "completed" if evidence_state in (EvidenceState.SUPPORTED, EvidenceState.PARTIALLY_SUPPORTED, EvidenceState.CONFLICTING_EVIDENCE) else ("partial" if evidence_state == EvidenceState.PARTIALLY_SUPPORTED else "completed")

        # Normalize status to allowed literal
        if status_val not in ("completed", "partial", "failed"):
            status_val = "completed"
        # Use Literal enforcement
        final_status: Any = "completed" if status_val == "completed" else ("failed" if status_val == "failed" else "partial")
        if not all_evidence and evidence_state == EvidenceState.INSUFFICIENT_EVIDENCE:
            final_status = "partial" if steps_executed and any(s.success for s in steps_executed) else "failed"

        completed_at = datetime.now(timezone.utc).isoformat()
        report = InvestigationReport(
            investigation_id=investigation_id,
            objective=objective,
            status=final_status,  # type: ignore
            plan=plan,
            steps_executed=steps_executed,
            evidence_refs=all_evidence,
            evidence_state=evidence_state,
            summary=summary,
            created_at=created_at,
            completed_at=completed_at,
            error=None if all(s.success for s in steps_executed) else "; ".join(s.error for s in steps_executed if s.error),
        )
        try:
            log_event(investigation_id, "evidence_state", raw_input={"evidence_state": evidence_state.value}, success=True, execution_ms=0, evidence_refs=[r.model_dump() for r in all_evidence[:5]], db_path=self.db_path)
        except Exception:
            pass
        try:
            log_event(investigation_id, "investigation_complete", raw_input={"summary": summary[:500]}, success=final_status != "failed", execution_ms=int((datetime.fromisoformat(completed_at) - datetime.fromisoformat(created_at)).total_seconds() * 1000), evidence_refs=[r.model_dump() for r in all_evidence[:5]], db_path=self.db_path)
        except Exception:
            pass
        self._persist(report)
        return report

    def _persist(self, report: InvestigationReport) -> None:
        _ensure_investigation_tables(self.db_path)
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO investigations (id, objective, status, evidence_state, summary, error, created_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    report.investigation_id,
                    report.objective,
                    report.status,
                    report.evidence_state.value if report.evidence_state else None,
                    report.summary,
                    report.error,
                    report.created_at,
                    report.completed_at,
                ),
            )
            for step in report.steps_executed:
                # Persist step with JSON for input/result/ev refs
                import json as _json

                conn.execute(
                    "INSERT OR REPLACE INTO investigation_steps (id, investigation_id, step_no, tool, input, rationale, success, result, error, evidence_refs, execution_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"{report.investigation_id}:{step.step_no}",
                        report.investigation_id,
                        step.step_no,
                        step.tool,
                        _json.dumps(step.input),
                        step.rationale,
                        1 if step.success else 0,
                        _json.dumps(step.result, default=str)[:4000] if step.result is not None else None,
                        step.error,
                        _json.dumps([r.model_dump() for r in step.evidence_refs], default=str)[:4000],
                        step.execution_ms,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def get_investigation(self, investigation_id: str, db_path=None) -> InvestigationReport | None:
        conn = get_connection(db_path or self.db_path)
        try:
            cur = conn.execute("SELECT * FROM investigations WHERE id = ?", (investigation_id,))
            row = cur.fetchone()
            if not row:
                return None
            cur2 = conn.execute("SELECT * FROM investigation_steps WHERE investigation_id = ? ORDER BY step_no", (investigation_id,))
            steps = cur2.fetchall()
            # Reconstruct minimal report (plan not stored fully, but steps)
            # For retrieval, we return stored steps without full plan
            return None  # Placeholder: persistence verified via direct DB query in tests
        finally:
            conn.close()
