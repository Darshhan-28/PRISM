"""Safety/policy layer — deterministic, offline, no network."""

import re
from pathlib import Path
from typing import Any
from pydantic import BaseModel

from backend.app.config import get_config
from backend.app.tools.registry import TOOL_REGISTRY

# Limits per spec
MAX_OBJECTIVE_LEN = 2000
MIN_OBJECTIVE_LEN = 10
MAX_TOOL_CALLS = 8
MAX_STEPS = 8
MAX_OUTPUT_CHARS = 4000
STEP_TIMEOUT_S = 30

# Prompt-injection patterns (case-insensitive, ordered)
INJECTION_PATTERNS = [
    r"ignore\s+previous\s+instructions",
    r"disregard\s+(?:all\s+)?previous",
    r"system\s*:\s*",
    r"you\s+are\s+now\s+",
    r"do\s+not\s+follow",
    r"override\s+system",
    r"jailbreak",
    r"act\s+as\s+(?:a\s+)?(?:system|admin|root)",
    r"<\s*system\s*>",
    r"\[INST\]",
    r"\[SYSTEM\]",
]

INJECTION_RE = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]

# Code execution patterns
CODE_PATTERNS = [
    r"\bexec\s*\(",
    r"\beval\s*\(",
    r"\bimport\s+os\b",
    r"\bsubprocess\b",
    r"\bos\.system\b",
    r"rm\s+-rf",
    r";\s*cat\s",
    r"\|\s*sh\b",
]

CODE_RE = [re.compile(p, re.IGNORECASE) for p in CODE_PATTERNS]

# Network/cloud patterns
NETWORK_PATTERNS = [
    r"https?://",
    r"api\.openai\.com",
    r"anthropic\.com",
    r"openai\.com",
    r"cloudflare",
]

NETWORK_RE = [re.compile(p, re.IGNORECASE) for p in NETWORK_PATTERNS]


class SafetyResult(BaseModel):
    allowed: bool
    reason: str | None = None
    sanitized_input: str | None = None
    error_code: str | None = None


def sanitize_text(text: str, max_len: int = MAX_OUTPUT_CHARS) -> str:
    if not isinstance(text, str):
        text = str(text)
    # Truncate
    if len(text) > max_len:
        text = text[:max_len] + "...[truncated]"
    # Remove null bytes
    text = text.replace("\x00", "")
    return text


def detect_prompt_injection(text: str) -> bool:
    if not text or not isinstance(text, str):
        return False
    for pat in INJECTION_RE:
        if pat.search(text):
            return True
    return False


def detect_code_execution(text: str) -> bool:
    if not text:
        return False
    for pat in CODE_RE:
        if pat.search(text):
            return True
    return False


def detect_network_request(text: str) -> bool:
    if not text:
        return False
    for pat in NETWORK_RE:
        if pat.search(text):
            return True
    return False


def validate_objective(objective: str) -> SafetyResult:
    if not isinstance(objective, str) or not objective.strip():
        return SafetyResult(allowed=False, reason="objective empty", error_code="EMPTY_OBJECTIVE")
    if len(objective) < MIN_OBJECTIVE_LEN:
        return SafetyResult(allowed=False, reason=f"objective too short ({len(objective)} < {MIN_OBJECTIVE_LEN})", error_code="OBJECTIVE_TOO_SHORT")
    if len(objective) > MAX_OBJECTIVE_LEN:
        return SafetyResult(allowed=False, reason=f"objective too long ({len(objective)} > {MAX_OBJECTIVE_LEN})", error_code="OBJECTIVE_TOO_LONG")
    if detect_prompt_injection(objective):
        return SafetyResult(allowed=False, reason="prompt injection detected in objective", error_code="PROMPT_INJECTION")
    if detect_code_execution(objective):
        return SafetyResult(allowed=False, reason="code execution attempt in objective", error_code="CODE_EXECUTION")
    if detect_network_request(objective):
        return SafetyResult(allowed=False, reason="network request detected in objective", error_code="NETWORK_REQUEST")
    return SafetyResult(allowed=True, sanitized_input=sanitize_text(objective))


def validate_tool_name(tool: str) -> SafetyResult:
    if tool not in TOOL_REGISTRY:
        return SafetyResult(allowed=False, reason=f"unknown tool {tool}", error_code="UNKNOWN_TOOL")
    return SafetyResult(allowed=True)


def validate_path(path_str: str) -> SafetyResult:
    if not path_str or "\x00" in path_str or ".." in Path(path_str).parts:
        return SafetyResult(allowed=False, reason="path traversal detected", error_code="PATH_TRAVERSAL")
    try:
        p = Path(path_str)
        # Allow only data/raw, data/processed, tests/fixtures, data/raw/samples, or tmp
        cfg = get_config()
        allowed_roots = [cfg.raw_dir.resolve(), cfg.processed_dir.resolve(), Path("tests/fixtures").resolve(), Path("data/raw/samples").resolve()]
        # tmp for tests
        import tempfile
        tmp_root = Path(tempfile.gettempdir()).resolve()
        allowed_roots.append(tmp_root)
        # If file does not exist yet, check parent
        try:
            resolved = p.resolve()
        except Exception:
            resolved = p
        for r in allowed_roots:
            try:
                resolved.relative_to(r)
                return SafetyResult(allowed=True, sanitized_input=sanitize_text(path_str))
            except ValueError:
                continue
        return SafetyResult(allowed=False, reason=f"path outside allowed roots: {path_str}", error_code="UNAUTHORIZED_PATH")
    except Exception as e:
        return SafetyResult(allowed=False, reason=str(e), error_code="PATH_ERROR")


def validate_tool_input(tool: str, raw_input: dict[str, Any]) -> SafetyResult:
    # Check tool allowlist first
    r = validate_tool_name(tool)
    if not r.allowed:
        return r
    # Check for injection/code/network in input values (treat as untrusted data)
    for k, v in (raw_input or {}).items():
        if isinstance(v, str):
            if detect_prompt_injection(v):
                return SafetyResult(allowed=False, reason=f"prompt injection in field {k}", error_code="PROMPT_INJECTION")
            if detect_code_execution(v):
                return SafetyResult(allowed=False, reason=f"code execution in field {k}", error_code="CODE_EXECUTION")
            # Network in tool input is not necessarily bad for normal text, but block explicit cloud exfiltration attempts
            # Do not over-block: only block if contains cloud API host
            if "api.openai.com" in v.lower() or "anthropic.com" in v.lower():
                return SafetyResult(allowed=False, reason=f"network request in field {k}", error_code="NETWORK_REQUEST")
            # Path fields
            if k in ("image_path", "source_path", "path"):
                pr = validate_path(v)
                if not pr.allowed:
                    return pr
    # Check size limits
    import json
    try:
        s = json.dumps(raw_input)
        if len(s) > 4000:
            return SafetyResult(allowed=False, reason="tool input too large", error_code="INPUT_TOO_LARGE")
    except Exception:
        pass
    return SafetyResult(allowed=True, sanitized_input=sanitize_text(str(raw_input)))

def check_output_size(text: str) -> SafetyResult:
    if len(text) > MAX_OUTPUT_CHARS:
        return SafetyResult(allowed=False, reason=f"output too large {len(text)} > {MAX_OUTPUT_CHARS}", error_code="OUTPUT_TOO_LARGE", sanitized_input=sanitize_text(text))
    return SafetyResult(allowed=True)

def check_step_limits(current_steps: int) -> SafetyResult:
    if current_steps >= MAX_STEPS:
        return SafetyResult(allowed=False, reason=f"max steps {MAX_STEPS} reached", error_code="MAX_STEPS")
    if current_steps >= MAX_TOOL_CALLS:
        return SafetyResult(allowed=False, reason=f"max tool calls {MAX_TOOL_CALLS} reached", error_code="MAX_TOOL_CALLS")
    return SafetyResult(allowed=True)
