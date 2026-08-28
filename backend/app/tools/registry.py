"""Registry — explicit allowlist, no arbitrary execution."""

from typing import Any, Callable
from pydantic import BaseModel

from backend.app.tools.search_documents import SearchDocumentsInput, search_documents
from backend.app.tools.retrieve_evidence import RetrieveEvidenceInput, retrieve_evidence
from backend.app.tools.query_sensor_data import QuerySensorDataInput, query_sensor_data
from backend.app.tools.search_maintenance_logs import SearchMaintenanceLogsInput, search_maintenance_logs
from backend.app.tools.inspect_image import InspectImageInput, inspect_image

TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    "search_documents": {"input_model": SearchDocumentsInput, "handler": search_documents},
    "retrieve_evidence": {"input_model": RetrieveEvidenceInput, "handler": retrieve_evidence},
    "query_sensor_data": {"input_model": QuerySensorDataInput, "handler": query_sensor_data},
    "search_maintenance_logs": {"input_model": SearchMaintenanceLogsInput, "handler": search_maintenance_logs},
    "inspect_image": {"input_model": InspectImageInput, "handler": inspect_image},
}


def list_tools() -> list[str]:
    return sorted(TOOL_REGISTRY.keys())


def get_tool(name: str) -> dict[str, Any]:
    if name not in TOOL_REGISTRY:
        raise ValueError(f"Unknown tool: {name}. Allowed: {list_tools()}")
    return TOOL_REGISTRY[name]


def execute_tool(name: str, raw_input: dict[str, Any], **kwargs) -> Any:
    """Validate tool name + input, then execute deterministically."""
    spec = get_tool(name)
    input_model = spec["input_model"]
    handler: Callable = spec["handler"]
    # Pydantic validation
    validated = input_model(**raw_input)
    # Pass through extra kwargs (e.g., retriever, db_path) if handler accepts them
    return handler(validated, **kwargs)
