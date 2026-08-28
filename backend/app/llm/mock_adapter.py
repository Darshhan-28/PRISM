"""MockAdapter — deterministic, offline, no network, for tests and default."""

import time
from typing import AsyncIterator

from backend.app.llm.adapter import validate_prompt, validate_system


class MockAdapter:
    provider: str = "mock"
    model: str = "mock-1"

    def __init__(self, canned: dict[str, str] | None = None):
        self.canned = canned or {}

    def generate(self, prompt: str, system: str | None = None, **kwargs) -> str:
        validate_prompt(prompt)
        validate_system(system)
        # canned substring match (first hit)
        for key, val in self.canned.items():
            if key in prompt or (system and key in system):
                return val
        # Planning request: return valid JSON plan
        if system and "industrial investigation planner" in system.lower():
            import json as _json
            import re as _re
            m = _re.search(r"Objective:\s*(.+)", prompt)
            obj = m.group(1).strip().split("\n")[0] if m else "Investigate"
            if len(obj) < 10:
                obj = "Investigate pressure in P-204 for demo"
            lower = obj.lower()
            steps = []
            if "pressure" in lower or "p-204" in lower:
                steps = [
                    {"step_no": 1, "tool": "search_documents", "input": {"query": "pressure 2.1 bar"}, "rationale": "find SOP pressure threshold"},
                    {"step_no": 2, "tool": "query_sensor_data", "input": {"equipment_id": "P-204"}, "rationale": "check sensor readings"},
                ]
            elif "valve" in lower:
                steps = [
                    {"step_no": 1, "tool": "search_documents", "input": {"query": "valve"}, "rationale": "find valve status"},
                    {"step_no": 2, "tool": "search_maintenance_logs", "input": {"equipment_id": "P-204", "keyword": "valve"}, "rationale": "check maintenance logs"},
                ]
            elif "vibration" in lower:
                steps = [
                    {"step_no": 1, "tool": "search_documents", "input": {"query": "vibration"}, "rationale": "find vibration reports"},
                    {"step_no": 2, "tool": "query_sensor_data", "input": {"equipment_id": "P-204"}, "rationale": "sensor data"},
                ]
            else:
                steps = [
                    {"step_no": 1, "tool": "search_documents", "input": {"query": obj[:50]}, "rationale": "find relevant documents"},
                ]
            return _json.dumps({"objective": obj, "steps": steps})
        # Grounded answer: if prompt contains Evidence block, cite its filenames deterministically
        if "<RETRIEVED_CHUNK" in prompt and "Evidence:" in prompt:
            import re as _re2
            files = _re2.findall(r"file=([^\s>]+)", prompt)
            seen = []
            for f in files:
                if f not in seen:
                    seen.append(f)
            if seen:
                cites = " ".join(f"[{f}]" for f in seen[:3])
                return f"Evidence shows pressure event at 4.8 bar exceeds SOP 2.1-3.4 bar threshold with sensor data {cites}"
        # heuristic for demo: if prompt mentions SOP/pressure
        if "SOP" in prompt or "pressure" in prompt.lower():
            return "According to SOP P-204, normal pressure is 2.1-3.4 bar. [MOCK, evidence: SOP_P-204.pdf]"
        # heuristic for demo: if prompt mentions SOP/pressure
        if "SOP" in prompt or "pressure" in prompt.lower():
            return "According to SOP P-204, normal pressure is 2.1-3.4 bar. [MOCK, evidence: SOP_P-204.pdf]"
        return f"[MOCK] echo: {prompt[:120]}"

    async def generate_stream(self, prompt: str, system: str | None = None, **kwargs) -> AsyncIterator[str]:
        text = self.generate(prompt, system, **kwargs)
        # yield in 2 chunks to simulate streaming without delay
        mid = max(1, len(text) // 2)
        yield text[:mid]
        yield text[mid:]

    def health_check(self) -> dict:
        return {"ok": True, "provider": self.provider, "model": self.model, "latency_ms": 0, "error": None}
