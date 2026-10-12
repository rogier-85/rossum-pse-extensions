"""Small, token-scoped client for the Rossum API."""

from __future__ import annotations

import json
from typing import Any, Self
from urllib.parse import urlsplit

import httpx

EXTENSION_METADATA_KEY = "password_protected_pdf_unlocker"
EXTENSION_VERSION = "1"


class UntrustedRossumUrlError(ValueError):
    """Raised before sending a Rossum token to an unexpected URL."""


class RossumClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._base = urlsplit(self.base_url)
        self._client = http_client or httpx.AsyncClient(timeout=45.0)
        self._owns_client = http_client is None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    def validate_url(self, url: str) -> None:
        parsed = urlsplit(url)
        base_path = self._base.path.rstrip("/")
        if (
            parsed.scheme != self._base.scheme
            or parsed.netloc != self._base.netloc
            or (base_path and parsed.path != base_path and not parsed.path.startswith(base_path + "/"))
        ):
            raise UntrustedRossumUrlError("Rossum document URL is outside base_url")

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    async def download_document(self, document_url: str) -> bytes:
        self.validate_url(document_url)
        response = await self._client.get(
            f"{document_url.rstrip('/')}/content",
            headers=self._headers,
            follow_redirects=False,
        )
        response.raise_for_status()
        return response.content

    async def find_unlocked_child(self, parent_url: str) -> str | None:
        self.validate_url(parent_url)
        response = await self._client.get(
            f"{self.base_url}/v1/documents",
            headers=self._headers,
            params={"parent": parent_url, "page_size": 100},
        )
        response.raise_for_status()
        for document in response.json().get("results", []):
            marker = document.get("metadata", {}).get(EXTENSION_METADATA_KEY, {})
            if (
                document.get("parent") == parent_url
                and marker.get("version") == EXTENSION_VERSION
                and marker.get("source_document") == parent_url
            ):
                child_url = document.get("url")
                if isinstance(child_url, str):
                    self.validate_url(child_url)
                    return child_url
        return None

    async def create_unlocked_child(
        self,
        parent_url: str,
        filename: str,
        content: bytes,
    ) -> str:
        self.validate_url(parent_url)
        metadata: dict[str, Any] = {
            EXTENSION_METADATA_KEY: {
                "version": EXTENSION_VERSION,
                "source_document": parent_url,
            }
        }
        response = await self._client.post(
            f"{self.base_url}/v1/documents",
            headers=self._headers,
            data={"parent": parent_url, "metadata": json.dumps(metadata)},
            files={"content": (filename, content, "application/pdf")},
        )
        response.raise_for_status()
        child_url = response.json().get("url")
        if not isinstance(child_url, str):
            raise httpx.HTTPError("Rossum create document response has no URL")
        self.validate_url(child_url)
        return child_url
