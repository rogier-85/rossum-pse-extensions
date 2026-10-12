"""Rossum function extension linking email attachments to their invoice.

Configure this function for the ``annotation_content.initialize`` event and
enable an API token owner on the hook.  It intentionally uses only Python's
standard library so it can run with ``third_party_library_pack`` set to null.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterable


DEFAULT_SETTINGS = {
    "document_sub_type_field": "document_sub_type",
    "invoice_type_value": "Invoice",
}


class RossumApiError(RuntimeError):
    """Raised when a Rossum API request fails."""


class RossumApi:
    def __init__(
        self,
        base_url: str,
        token: str,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.opener = opener

    def request(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self._trusted_url(url)
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener(request, timeout=20) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise RossumApiError(
                f"Rossum API {method} {url} failed with HTTP {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RossumApiError(f"Rossum API {method} {url} failed: {exc.reason}") from exc

        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RossumApiError(
                f"Rossum API {method} {url} returned invalid JSON"
            ) from exc
        if not isinstance(parsed, dict):
            raise RossumApiError(
                f"Rossum API {method} {url} returned an unexpected response"
            )
        return parsed

    def get(self, url: str) -> dict[str, Any]:
        return self.request("GET", url)

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", f"{self.base_url}{path}", body)

    def patch(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("PATCH", url, body)

    def iter_results(self, url: str) -> Iterable[dict[str, Any]]:
        while url:
            page = self.get(url)
            results = page.get("results", [])
            if not isinstance(results, list):
                raise RossumApiError("Rossum API paginated response has no results list")
            yield from (item for item in results if isinstance(item, dict))
            pagination = page.get("pagination") or {}
            url = pagination.get("next") or ""

    def _trusted_url(self, url: str) -> str:
        """Prevent the bearer token from being sent outside this Rossum tenant."""
        candidate = urllib.parse.urlsplit(url)
        base = urllib.parse.urlsplit(self.base_url)
        if (
            candidate.scheme not in {"http", "https"}
            or candidate.scheme != base.scheme
            or candidate.netloc != base.netloc
        ):
            raise RossumApiError("Refusing to send the Rossum token to an untrusted URL")
        return url


def _walk_datapoints(nodes: Any) -> Iterable[dict[str, Any]]:
    if isinstance(nodes, dict):
        if nodes.get("category") == "datapoint":
            yield nodes
        children = nodes.get("children")
        if isinstance(children, list):
            for child in children:
                yield from _walk_datapoints(child)
    elif isinstance(nodes, list):
        for node in nodes:
            yield from _walk_datapoints(node)


def _field_value(content: Any, schema_id: str) -> str | None:
    for datapoint in _walk_datapoints(content):
        if datapoint.get("schema_id") != schema_id:
            continue
        field_content = datapoint.get("content") or {}
        value = field_content.get("normalized_value")
        if value in (None, ""):
            value = field_content.get("value")
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None
    return None


def _message(message_type: str, content: str) -> dict[str, str]:
    return {"type": message_type, "content": content}


def _annotation_url(annotation: dict[str, Any], base_url: str) -> str:
    if annotation.get("url"):
        return str(annotation["url"])
    if annotation.get("id") is not None:
        return f"{base_url.rstrip('/')}/api/v1/annotations/{annotation['id']}"
    raise ValueError("The hook payload does not identify the annotation")


def _settings(payload: dict[str, Any]) -> dict[str, str]:
    configured = payload.get("settings") or {}
    return {
        key: str(configured.get(key, default)).strip()
        for key, default in DEFAULT_SETTINGS.items()
    }


def _email_url(payload: dict[str, Any], api: RossumApi) -> str | None:
    document = payload.get("document") or {}
    if document.get("email"):
        return str(document["email"])

    annotation = payload.get("annotation") or {}
    if annotation.get("email"):
        return str(annotation["email"])

    document_url = document.get("url") or annotation.get("document")
    if not document_url:
        return None
    return api.get(str(document_url)).get("email")


def _unique_urls(urls: Any, excluded: str) -> list[str]:
    if not isinstance(urls, list):
        return []
    seen = {excluded}
    output: list[str] = []
    for value in urls:
        if not isinstance(value, str) or not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _resource_id(resource_url: str, resource_name: str) -> int:
    """Extract the numeric ID expected by Rossum list endpoint filters."""
    path = urllib.parse.urlsplit(resource_url).path.rstrip("/")
    value = path.rsplit("/", 1)[-1]
    if not value.isdigit():
        raise RossumApiError(
            f"Cannot extract numeric {resource_name} ID from Rossum resource URL"
        )
    return int(value)


def _find_attachment_relation(
    api: RossumApi,
    annotation_url: str,
    sideloaded_relations: Any = None,
) -> dict[str, Any] | None:
    if isinstance(sideloaded_relations, list):
        for relation in sideloaded_relations:
            if isinstance(relation, dict) and _is_attachment_relation(
                relation, annotation_url
            ):
                return relation

    annotation_id = _resource_id(annotation_url, "annotation")
    query = urllib.parse.urlencode({"annotation": annotation_id})
    url = f"{api.base_url}/api/v1/relations?{query}"
    for relation in api.iter_results(url):
        if _is_attachment_relation(relation, annotation_url):
            return relation
    return None


def _is_attachment_relation(
    relation: dict[str, Any], annotation_url: str
) -> bool:
    return (
        relation.get("type") == "attachment"
        and relation.get("parent") == annotation_url
    )


def _upsert_relation(
    api: RossumApi,
    annotation_url: str,
    children: list[str],
    sideloaded_relations: Any = None,
) -> str:
    existing = _find_attachment_relation(
        api, annotation_url, sideloaded_relations
    )
    if existing is None:
        api.post(
            "/api/v1/relations",
            {
                "type": "attachment",
                "key": None,
                "parent": annotation_url,
                "annotations": children,
            },
        )
        return "created"

    existing_children = existing.get("annotations") or []
    merged = _unique_urls([*existing_children, *children], excluded=annotation_url)
    if merged != existing_children:
        relation_url = existing.get("url")
        if not relation_url:
            raise RossumApiError("Existing relation has no URL")
        api.patch(str(relation_url), {"annotations": merged})
        return "updated"
    return "unchanged"


def process_payload(
    payload: dict[str, Any],
    api_factory: Callable[[str, str], RossumApi] = RossumApi,
) -> dict[str, list[dict[str, str]]]:
    """Process one Rossum hook payload. Split out for deterministic tests."""
    response: dict[str, list[Any]] = {"messages": [], "operations": []}
    if payload.get("event") != "annotation_content" or payload.get("action") != "initialize":
        response["messages"].append(
            _message("warning", "Invoice attachment linker only handles annotation_content.initialize.")
        )
        return response

    annotation = payload.get("annotation") or {}
    settings = _settings(payload)
    content = annotation.get("content") or []
    document_type = _field_value(content, settings["document_sub_type_field"])
    if document_type != settings["invoice_type_value"]:
        return response

    base_url = str(payload.get("base_url") or "").rstrip("/")
    token = str(payload.get("rossum_authorization_token") or "")
    if not base_url or not token:
        raise ValueError("The hook must have a token owner so Rossum supplies API access")

    api = api_factory(base_url, token)
    parent_url = _annotation_url(annotation, base_url)
    email_url = _email_url(payload, api)
    if not email_url:
        response["messages"].append(
            _message("warning", "No relation created: this annotation is not linked to an email.")
        )
        return response

    email = api.get(email_url)
    children = _unique_urls(email.get("annotations"), excluded=parent_url)
    if not children:
        response["messages"].append(
            _message("info", "No relation created: the email has no other annotations.")
        )
        return response

    sideloaded_relations: list[Any] = []
    for key in ("relations", "child_relations"):
        value = payload.get(key)
        if isinstance(value, list):
            sideloaded_relations.extend(value)

    result = _upsert_relation(
        api,
        parent_url,
        children,
        sideloaded_relations,
    )
    response["messages"].append(
        _message(
            "info",
            f"Attachment relation {result} with {len(children)} child annotation(s).",
        )
    )
    return response


def rossum_hook_request_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Entrypoint required by Rossum serverless functions."""
    return process_payload(payload)
