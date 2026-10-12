"""FastAPI entrypoint for the Rossum extension."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError

from app.models import EmailReceivedPayload
from app.rossum import RossumClient, UntrustedRossumUrlError
from app.service import AttachmentProcessingError, process_email

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("rossum_password_unlocker")

app = FastAPI(title="Rossum Password-Protected PDF Extension", version="1.0.0")


def verify_signature(raw_body: bytes, signature_header: str | None) -> None:
    secret = os.getenv("ROSSUM_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook secret is not configured")
    if not signature_header:
        raise HTTPException(status_code=401, detail="Missing webhook signature")
    try:
        prefix, supplied = signature_header.split("=", 1)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid webhook signature") from exc
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if prefix != "sha256" or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/rossum/email-received")
async def email_received(request: Request) -> dict[str, object]:
    raw_body = await request.body()
    verify_signature(raw_body, request.headers.get("X-Rossum-Signature-SHA256"))

    try:
        payload = EmailReceivedPayload.model_validate(json.loads(raw_body))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(status_code=400, detail="Invalid email hook payload") from exc
    if payload.event != "email" or payload.action != "received":
        raise HTTPException(status_code=400, detail="Expected an email.received event")

    try:
        async with RossumClient(
            str(payload.base_url), payload.rossum_authorization_token
        ) as client:
            result = await process_email(payload, client)
    except AttachmentProcessingError as exc:
        logger.warning("Unable to process protected PDFs request_id=%s", payload.request_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except UntrustedRossumUrlError as exc:
        logger.warning("Rejected untrusted Rossum URL request_id=%s", payload.request_id)
        raise HTTPException(status_code=400, detail="Invalid Rossum document URL") from exc
    except httpx.HTTPError as exc:
        logger.error("Rossum API request failed request_id=%s", payload.request_id)
        raise HTTPException(status_code=502, detail="Rossum API request failed") from exc

    logger.info(
        "Email processed request_id=%s unlocked=%d passthrough=%d",
        payload.request_id,
        len(result.additional_files),
        len(result.files),
    )
    return result.model_dump()
