from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.rossum import EXTENSION_METADATA_KEY, RossumClient, UntrustedRossumUrlError


def test_rejects_foreign_document_url() -> None:
    client = RossumClient("https://tenant.rossum.app/api", "secret", httpx.AsyncClient())
    with pytest.raises(UntrustedRossumUrlError):
        client.validate_url("https://attacker.example/api/v1/documents/1")
    asyncio.run(client._client.aclose())


def test_finds_extension_created_child() -> None:
    parent = "https://tenant.rossum.app/api/v1/documents/1"
    child = "https://tenant.rossum.app/api/v1/documents/2"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer temporary"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": child,
                        "parent": parent,
                        "metadata": {
                            EXTENSION_METADATA_KEY: {
                                "version": "1",
                                "source_document": parent,
                            }
                        },
                    }
                ]
            },
        )

    async def run() -> str | None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = RossumClient(
                "https://tenant.rossum.app/api", "temporary", http_client
            )
            return await client.find_unlocked_child(parent)

    assert asyncio.run(run()) == child


def test_create_child_sends_parent_filename_and_metadata() -> None:
    parent = "https://tenant.rossum.app/api/v1/documents/1"
    child = "https://tenant.rossum.app/api/v1/documents/2"

    async def run() -> str:
        captured: dict[str, bytes] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = await request.aread()
            return httpx.Response(201, json={"url": child})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = RossumClient(
                "https://tenant.rossum.app/api", "temporary", http_client
            )
            result = await client.create_unlocked_child(parent, "bill_unlocked.pdf", b"%PDF")
        body = captured["body"]
        assert parent.encode() in body
        assert b"bill_unlocked.pdf" in body
        assert json.dumps({"version": "1", "source_document": parent}).encode() in body
        return result

    assert asyncio.run(run()) == child
