from __future__ import annotations

import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app import main
from app.models import HookResponse


def signed_headers(body: bytes, secret: str = "test-secret") -> dict[str, str]:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {"X-Rossum-Signature-SHA256": f"sha256={digest}"}


def minimal_payload() -> dict:
    return {
        "event": "email",
        "action": "received",
        "request_id": "request-1",
        "base_url": "https://tenant.rossum.app/api",
        "rossum_authorization_token": "temporary-token",
        "files": [],
        "body": {"body_text_plain": "Password: never-log-this"},
    }


def test_health() -> None:
    assert TestClient(main.app).get("/health").json() == {"status": "ok"}


def test_invalid_signature_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("ROSSUM_WEBHOOK_SECRET", "test-secret")
    response = TestClient(main.app).post(
        "/rossum/email-received",
        json=minimal_payload(),
        headers={"X-Rossum-Signature-SHA256": "sha256=bad"},
    )
    assert response.status_code == 401


def test_valid_callback_returns_hook_response(monkeypatch) -> None:
    monkeypatch.setenv("ROSSUM_WEBHOOK_SECRET", "test-secret")

    async def fake_process(payload, client):
        return HookResponse(files=[], additional_files=[])

    monkeypatch.setattr(main, "process_email", fake_process)
    raw = json.dumps(minimal_payload(), separators=(",", ":")).encode()
    response = TestClient(main.app).post(
        "/rossum/email-received",
        content=raw,
        headers={"content-type": "application/json", **signed_headers(raw)},
    )
    assert response.status_code == 200
    assert response.json() == {"files": [], "additional_files": []}


def test_wrong_event_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("ROSSUM_WEBHOOK_SECRET", "test-secret")
    data = minimal_payload()
    data["action"] = "scheduled"
    raw = json.dumps(data).encode()
    response = TestClient(main.app).post(
        "/rossum/email-received",
        content=raw,
        headers={"content-type": "application/json", **signed_headers(raw)},
    )
    assert response.status_code == 400
