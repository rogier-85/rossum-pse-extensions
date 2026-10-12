"""Rossum serverless function that links credit notes to their original invoice."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DEFAULTS = {
    "document_type_schema_id": "document_type",
    "credit_note_values": ["credit_note"],
    "original_invoice_number_schema_id": "original_document_id",
    "invoice_number_schema_id": "document_id",
    "invoice_document_type_values": ["tax_invoice", "invoice"],
    "relation_type": "attachment",
    "relation_key": None,
    "search_queue_ids": [],
    "message_on_success": True,
}


def _message(kind: str, content: str, datapoint_id: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"type": kind, "content": content}
    if datapoint_id is not None:
        result["id"] = datapoint_id
    return result


def _response(*messages: dict[str, Any]) -> dict[str, Any]:
    return {"messages": list(messages), "operations": []}


def _find_datapoints(node: Any, schema_id: str) -> list[dict[str, Any]]:
    """Find datapoints recursively in Rossum's section/tuple content tree."""
    found: list[dict[str, Any]] = []
    if isinstance(node, list):
        for child in node:
            found.extend(_find_datapoints(child, schema_id))
    elif isinstance(node, dict):
        if node.get("category") == "datapoint" and node.get("schema_id") == schema_id:
            found.append(node)
        for child_key in ("children",):
            if child_key in node:
                found.extend(_find_datapoints(node[child_key], schema_id))
    return found


def _field(content: Any, schema_id: str) -> tuple[Any, int | None]:
    datapoints = _find_datapoints(content, schema_id)
    if not datapoints:
        return None, None
    datapoint = datapoints[0]
    value = datapoint.get("content", {}).get("value")
    if isinstance(value, str):
        value = value.strip()
    return value, datapoint.get("id")


def _setting(payload: dict[str, Any], name: str) -> Any:
    return payload.get("settings", {}).get(name, DEFAULTS[name])


def _id_from_url(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def _queue_urls(payload: dict[str, Any], annotation: dict[str, Any]) -> list[str]:
    configured = _setting(payload, "search_queue_ids") or []
    if not configured:
        return [annotation["queue"]]
    base_url = payload["base_url"].rstrip("/")
    return [
        str(value) if str(value).startswith("http") else f"{base_url}/api/v1/queues/{value}"
        for value in configured
    ]


def _api_request(
    payload: dict[str, Any],
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = path if path.startswith("http") else payload["base_url"].rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": "Bearer " + payload["rossum_authorization_token"],
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Rossum API {method} {url} failed ({error.code}): {detail}") from error


def _search_original_invoice(
    payload: dict[str, Any], annotation: dict[str, Any], invoice_number: str
) -> list[dict[str, Any]]:
    clauses: list[dict[str, Any]] = [
        {f"field.{_setting(payload, 'invoice_number_schema_id')}.string": {"$eq": invoice_number}},
        {"queue": {"$in": _queue_urls(payload, annotation)}},
        {"annotation": {"$ne": annotation["url"]}},
    ]
    invoice_types = _setting(payload, "invoice_document_type_values") or []
    if invoice_types:
        clauses.append(
            {
                f"field.{_setting(payload, 'document_type_schema_id')}.string": {
                    "$in": invoice_types
                }
            }
        )
    result = _api_request(
        payload,
        "POST",
        "/api/v1/annotations/search?page_size=10&ordering=-created_at",
        {"query": {"$and": clauses}},
    )
    return result.get("results", [])


def _relation_query_path(original_id: str, relation_type: str) -> str:
    query = urllib.parse.urlencode(
        {"parent": original_id, "type": relation_type, "page_size": 100}
    )
    return "/api/v1/relations?" + query


def _ensure_relation(
    payload: dict[str, Any], original: dict[str, Any], credit_note: dict[str, Any]
) -> str:
    relation_type = str(_setting(payload, "relation_type"))
    if relation_type not in {"attachment", "edit", "duplicate"}:
        raise ValueError("relation_type must be attachment, edit, or duplicate")

    original_url = original["url"]
    credit_note_url = credit_note["url"]
    listed = _api_request(
        payload, "GET", _relation_query_path(_id_from_url(original_url), relation_type)
    )
    relation_key = _setting(payload, "relation_key")
    relations = listed.get("results", [])
    if relation_key is not None:
        relations = [relation for relation in relations if relation.get("key") == relation_key]

    for relation in relations:
        if credit_note_url in relation.get("annotations", []):
            return "exists"

    if relations:
        relation = relations[0]
        annotations = list(dict.fromkeys([*relation.get("annotations", []), credit_note_url]))
        _api_request(payload, "PATCH", f"/api/v1/relations/{relation['id']}", {"annotations": annotations})
        return "updated"

    relation_body: dict[str, Any] = {
        "type": relation_type,
        "parent": original_url,
        "annotations": [credit_note_url],
    }
    if relation_type != "attachment" and relation_key is not None:
        relation_body["key"] = relation_key
    _api_request(payload, "POST", "/api/v1/relations", relation_body)
    return "created"


def rossum_hook_request_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Entrypoint required by the Rossum Python 3.12 function runtime."""
    annotation = payload.get("annotation") or {}
    content = annotation.get("content") or []

    document_type, _ = _field(content, str(_setting(payload, "document_type_schema_id")))
    credit_note_values = {str(value).casefold() for value in _setting(payload, "credit_note_values")}
    if not isinstance(document_type, str) or document_type.casefold() not in credit_note_values:
        return _response()

    original_number, datapoint_id = _field(
        content, str(_setting(payload, "original_invoice_number_schema_id"))
    )
    if not original_number:
        return _response(
            _message("warning", "Credit note has no original invoice number; no relation was created.", datapoint_id)
        )

    matches = _search_original_invoice(payload, annotation, str(original_number))
    if not matches:
        return _response(
            _message(
                "warning",
                f'No original invoice with number "{original_number}" was found; no relation was created.',
                datapoint_id,
            )
        )
    if len(matches) > 1:
        return _response(
            _message(
                "warning",
                f'Found {len(matches)} invoices with number "{original_number}"; no relation was created.',
                datapoint_id,
            )
        )

    outcome = _ensure_relation(payload, matches[0], annotation)
    if not _setting(payload, "message_on_success"):
        return _response()
    verb = "already exists" if outcome == "exists" else "was created"
    return _response(
        _message(
            "info",
            f'Link to original invoice "{original_number}" {verb}.',
            datapoint_id,
        )
    )

