from __future__ import annotations

from typing import Any


def add_mutation_receipt(
    data: Any,
    *,
    command: str,
    request_id: str,
    json_body: object | None,
    response_headers: dict[str, str] | None = None,
) -> Any:
    """Attach a small, stable receipt to legacy response DTOs.

    The backend keeps returning its established resource shape for backwards
    compatibility.  This additive field gives an Agent a deterministic way to
    identify the operation and affected IDs without requiring every DTO to be
    rewritten at once.  Backend-provided receipt/audit data wins when present.
    """

    if not isinstance(data, dict):
        return data
    if isinstance(data.get("mutation_receipt"), dict):
        return data
    changed_fields = (
        sorted(
            str(key)
            for key in json_body
            if isinstance(json_body, dict)
            and key not in {"request_id", "idempotency_key"}
        )
        if isinstance(json_body, dict)
        else []
    )
    resource = command.rsplit(".", 1)[0]
    identifier = _first_receipt_identifier(data)
    changed_resources = [
        {
            "type": resource,
            "id": str(identifier) if identifier is not None else None,
            "changed_fields": changed_fields,
            "after": _redact_receipt_value(data),
        },
    ]
    response_status = (response_headers or {}).get("x-agent-mutation-status")
    mutation_status = (
        response_status
        if response_status in {"pending", "applied", "replayed"}
        else "applied"
    )
    receipt: dict[str, object] = {
        "request_id": request_id,
        "status": mutation_status,
        "changed_resources": changed_resources,
    }
    audit_reference = (response_headers or {}).get("x-audit-reference")
    if audit_reference and audit_reference != request_id:
        receipt["audit_reference"] = audit_reference
    header_receipt = (response_headers or {}).get("x-agent-mutation-receipt")
    if header_receipt:
        receipt["backend_receipt_id"] = header_receipt
    header_command = (response_headers or {}).get("x-agent-mutation-command")
    if header_command and header_command != command:
        receipt["backend_command"] = header_command
    return {**data, "mutation_receipt": receipt}


_RECEIPT_IDENTIFIER_KEYS = (
    "id",
    "task_id",
    "job_id",
    "plan_id",
    "professor_id",
    "identity_id",
    "group_id",
    "material_id",
    "template_id",
    "profile_id",
    "candidate_id",
    "campaign_id",
    "item_id",
)


def _first_receipt_identifier(value: Any) -> object | None:
    """Find a stable affected-object ID in common Agent response envelopes."""

    if isinstance(value, dict):
        for key in _RECEIPT_IDENTIFIER_KEYS:
            candidate = value.get(key)
            if isinstance(candidate, (str, int)) and not isinstance(candidate, bool):
                return candidate
        for nested in value.values():
            identifier = _first_receipt_identifier(nested)
            if identifier is not None:
                return identifier
    elif isinstance(value, list):
        for nested in value:
            identifier = _first_receipt_identifier(nested)
            if identifier is not None:
                return identifier
    return None


_SECRET_KEY_PARTS = ("password", "api_key", "token", "secret", "credential")


def _redact_receipt_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if any(part in key.lower() for part in _SECRET_KEY_PARTS)
                else _redact_receipt_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_receipt_value(item) for item in value]
    return value
