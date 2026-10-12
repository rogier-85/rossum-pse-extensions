"""Rossum serverless function for linking WHT certificates to invoices.

The module is deliberately self-contained so its complete contents can be pasted
into a Rossum Python 3.12 serverless function.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


SUPPLIER_MATCH_SCHEMA_ID = "supplier_match"
COA_MATCH_SCHEMA_ID = "coa_match"
ITEMS_SCHEMA_ID = "items"
ITEM_WHT_RATE_SCHEMA_ID = "item_wht_rate"
WHT_RATE_SCHEMA_ID = "wht_rate"


@dataclass
class RossumApiError(Exception):
    """A sanitized Rossum API failure suitable for handler-level reporting."""

    message: str
    status: int | None = None

    def __str__(self) -> str:
        if self.status is None:
            return self.message
        return f"{self.message} (HTTP {self.status})"


class RossumApiClient:
    """Small standard-library client scoped to the Rossum API from the payload."""

    def __init__(self, base_url: str, token: str, timeout: float = 10.0) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url is missing")
        if not isinstance(token, str) or not token.strip():
            raise ValueError("rossum_authorization_token is missing")

        parsed = urllib.parse.urlparse(base_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute HTTP(S) URL")

        normalized = base_url.strip().rstrip("/")
        if normalized.endswith("/api/v1"):
            api_base = normalized
        elif normalized.endswith("/api"):
            api_base = normalized + "/v1"
        else:
            api_base = normalized + "/api/v1"

        self.api_base = api_base
        self.origin = (parsed.scheme.lower(), parsed.netloc.lower())
        self.token = token.strip()
        self.timeout = timeout

    def request(
        self,
        method: str,
        path_or_url: str,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[Any, Mapping[str, str]]:
        url = self._url(path_or_url)
        data = None if body is None else json.dumps(body).encode("utf-8")
        request_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        if data is not None:
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)

        request = urllib.request.Request(
            url,
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                response_headers = dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            # Never include the response body: it can contain operational details.
            status = exc.code
            exc.close()
            raise RossumApiError("Rossum API request failed", status) from exc
        except urllib.error.URLError as exc:
            raise RossumApiError("Rossum API request could not be completed") from exc
        except OSError as exc:
            raise RossumApiError("Rossum API request could not be completed") from exc

        if not raw:
            return {}, response_headers
        try:
            return json.loads(raw), response_headers
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RossumApiError("Rossum API returned malformed JSON") from exc

    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            parsed = urllib.parse.urlparse(path_or_url)
            if (parsed.scheme.lower(), parsed.netloc.lower()) != self.origin:
                raise RossumApiError("Rossum API returned a URL on another host")
            return path_or_url
        return self.api_base + "/" + path_or_url.lstrip("/")


def rossum_hook_request_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Handle ``annotation_content.started`` calls from Rossum."""

    if payload.get("event") != "annotation_content" or payload.get("action") != "started":
        return _empty_response()

    try:
        settings = _mapping(payload.get("settings"), "settings")
        target_queue_url = settings.get("target_queue_url")
        if not isinstance(target_queue_url, str) or not target_queue_url.strip():
            raise ValueError("settings.target_queue_url is missing")

        annotation = _mapping(payload.get("annotation"), "annotation")
        annotation_url = annotation.get("url")
        if not isinstance(annotation_url, str) or not annotation_url:
            raise ValueError("annotation.url is missing")
        annotation_id = _resource_id(annotation_url, "annotations")

        content = annotation.get("content")
        if not isinstance(content, list):
            raise ValueError("annotation.content is missing")

        supplier_match = _datapoint_value(_find_schema_node(content, SUPPLIER_MATCH_SCHEMA_ID))
        coa_match = _datapoint_value(_find_schema_node(content, COA_MATCH_SCHEMA_ID))
        wht_rate_datapoint = _find_schema_node(content, WHT_RATE_SCHEMA_ID)
        if (
            _is_empty(supplier_match)
            or _is_empty(coa_match)
            or not _is_datapoint(wht_rate_datapoint)
        ):
            return _empty_response()

        base_url = payload.get("base_url")
        token = payload.get("rossum_authorization_token")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url is missing")
        if not isinstance(token, str) or not token.strip():
            raise ValueError("rossum_authorization_token is missing")
        client = RossumApiClient(base_url, token)
        _validate_queue_url(client, target_queue_url)

        matched_annotation_url = _find_newest_match(
            client,
            target_queue_url.strip(),
            str(supplier_match),
            str(coa_match),
            annotation_url,
        )
        if matched_annotation_url is None:
            return _empty_response()

        matched_content = _get_annotation_content(client, matched_annotation_url)
        item_wht_rate = _first_item_wht_rate(matched_content)
        if _is_empty(item_wht_rate):
            return _empty_response()

        _ensure_attachment_relation(
            client,
            parent_annotation_url=annotation_url,
            parent_annotation_id=annotation_id,
            attachment_annotation_url=matched_annotation_url,
        )

        return {
            "messages": [],
            "operations": [
                {
                    "op": "replace",
                    "id": wht_rate_datapoint["id"],
                    "value": {"content": {"value": str(item_wht_rate)}},
                }
            ],
        }
    except (RossumApiError, ValueError, KeyError, TypeError) as exc:
        return _error_response(str(exc))


def _find_newest_match(
    client: RossumApiClient,
    target_queue_url: str,
    supplier_match: str,
    coa_match: str,
    current_annotation_url: str,
) -> str | None:
    query = {
        "query": {
            "$and": [
                {"queue": {"$eq": target_queue_url}},
                {f"field.{SUPPLIER_MATCH_SCHEMA_ID}.string": {"$eq": supplier_match}},
                {f"field.{COA_MATCH_SCHEMA_ID}.string": {"$eq": coa_match}},
                {"annotation": {"$ne": current_annotation_url}},
            ]
        }
    }
    data, _ = client.request(
        "POST",
        "/annotations/search?ordering=-created_at&page_size=1",
        query,
    )
    result = _mapping(data, "annotation search response")
    matches = result.get("results")
    if not isinstance(matches, list):
        raise RossumApiError("Rossum annotation search returned malformed results")
    if not matches:
        return None
    newest = _mapping(matches[0], "annotation search result")
    url = newest.get("url")
    if not isinstance(url, str) or not url:
        raise RossumApiError("Rossum annotation search result has no URL")
    return url


def _get_annotation_content(client: RossumApiClient, annotation_url: str) -> list[Any]:
    annotation_id = _resource_id(annotation_url, "annotations")
    data, _ = client.request(
        "GET", f"/annotations/{annotation_id}/content?deprecated_fields=false"
    )
    result = _mapping(data, "annotation content response")
    content = result.get("content")
    if not isinstance(content, list):
        raise RossumApiError("Rossum annotation content response is malformed")
    return content


def _first_item_wht_rate(content: list[Any]) -> Any | None:
    items = _find_schema_node(content, ITEMS_SCHEMA_ID)
    if not isinstance(items, Mapping):
        return None
    rows = items.get("children")
    if not isinstance(rows, list) or not rows:
        return None
    first_row = rows[0]
    if not isinstance(first_row, Mapping):
        return None
    rate = _find_schema_node([first_row], ITEM_WHT_RATE_SCHEMA_ID)
    return _datapoint_value(rate)


def _ensure_attachment_relation(
    client: RossumApiClient,
    parent_annotation_url: str,
    parent_annotation_id: int,
    attachment_annotation_url: str,
) -> None:
    relations = _list_attachment_relations(client, parent_annotation_id)
    for relation in relations:
        annotations = relation.get("annotations")
        if isinstance(annotations, list) and attachment_annotation_url in annotations:
            return

    if relations:
        _append_attachment(client, relations[0], attachment_annotation_url)
        return

    try:
        client.request(
            "POST",
            "/relations",
            {
                "type": "attachment",
                "key": None,
                "parent": parent_annotation_url,
                "annotations": [attachment_annotation_url],
            },
        )
    except RossumApiError as exc:
        if exc.status != 409:
            raise
        # Another invocation may have created the relation after our list call.
        relations = _list_attachment_relations(client, parent_annotation_id)
        for relation in relations:
            annotations = relation.get("annotations")
            if isinstance(annotations, list) and attachment_annotation_url in annotations:
                return
        if not relations:
            raise
        _append_attachment(client, relations[0], attachment_annotation_url)


def _list_attachment_relations(
    client: RossumApiClient, parent_annotation_id: int
) -> list[dict[str, Any]]:
    path: str | None = (
        "/relations?"
        + urllib.parse.urlencode(
            {"type": "attachment", "parent": parent_annotation_id, "page_size": 100}
        )
    )
    relations: list[dict[str, Any]] = []
    while path:
        data, _ = client.request("GET", path)
        result = _mapping(data, "relation list response")
        page = result.get("results")
        if not isinstance(page, list):
            raise RossumApiError("Rossum relation list returned malformed results")
        for relation in page:
            relations.append(dict(_mapping(relation, "relation")))
        pagination = result.get("pagination")
        if pagination is None:
            path = None
        else:
            pagination_mapping = _mapping(pagination, "relation pagination")
            next_url = pagination_mapping.get("next")
            if next_url is not None and not isinstance(next_url, str):
                raise RossumApiError("Rossum relation pagination is malformed")
            path = next_url
    return relations


def _append_attachment(
    client: RossumApiClient,
    relation: Mapping[str, Any],
    attachment_annotation_url: str,
) -> None:
    relation_url = relation.get("url")
    if not isinstance(relation_url, str) or not relation_url:
        relation_id = relation.get("id")
        if not isinstance(relation_id, int):
            raise RossumApiError("Rossum attachment relation has no URL")
        relation_url = f"/relations/{relation_id}"

    for attempt in range(2):
        current, headers = client.request("GET", relation_url)
        current_relation = _mapping(current, "attachment relation")
        annotations = current_relation.get("annotations")
        if not isinstance(annotations, list):
            raise RossumApiError("Rossum attachment relation is malformed")
        if attachment_annotation_url in annotations:
            return

        patch_headers: dict[str, str] = {}
        etag = _header(headers, "etag")
        if etag:
            patch_headers["If-Match"] = etag
        try:
            client.request(
                "PATCH",
                relation_url,
                {"annotations": [*annotations, attachment_annotation_url]},
                patch_headers,
            )
            return
        except RossumApiError as exc:
            if attempt == 0 and exc.status in {409, 412}:
                continue
            raise


def _find_schema_node(nodes: Sequence[Any], schema_id: str) -> dict[str, Any] | None:
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        if node.get("schema_id") == schema_id:
            return dict(node)
        children = node.get("children")
        if isinstance(children, list):
            found = _find_schema_node(children, schema_id)
            if found is not None:
                return found
    return None


def _datapoint_value(node: Mapping[str, Any] | None) -> Any | None:
    if not _is_datapoint(node):
        return None
    content = node.get("content")
    if not isinstance(content, Mapping):
        return None
    return content.get("value")


def _is_datapoint(node: Mapping[str, Any] | None) -> bool:
    return isinstance(node, Mapping) and node.get("category") == "datapoint" and "id" in node


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RossumApiError(f"{label} is malformed")
    return value


def _resource_id(url: str, resource: str) -> int:
    parsed = urllib.parse.urlparse(url)
    segments = [segment for segment in parsed.path.split("/") if segment]
    try:
        index = segments.index(resource)
        return int(segments[index + 1])
    except (ValueError, IndexError) as exc:
        raise ValueError(f"invalid {resource} URL") from exc


def _validate_queue_url(client: RossumApiClient, queue_url: str) -> None:
    parsed = urllib.parse.urlparse(queue_url.strip())
    if (parsed.scheme.lower(), parsed.netloc.lower()) != client.origin:
        raise ValueError("settings.target_queue_url must use the payload base_url host")
    _resource_id(queue_url, "queues")


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def _empty_response() -> dict[str, list[Any]]:
    return {"messages": [], "operations": []}


def _error_response(detail: str) -> dict[str, list[dict[str, Any]]]:
    return {
        "messages": [
            {
                "type": "error",
                "content": f"WHT certificate linking failed: {detail}",
            }
        ],
        "operations": [],
    }
