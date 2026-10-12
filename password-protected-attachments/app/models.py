"""Validated subset of the Rossum email.received payload."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class EmailBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    body_text_plain: str | None = None
    body_text_html: str | None = None


class EmailFile(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | int
    filename: str = "document"
    mime_type: str
    document: HttpUrl


class EmailReceivedPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    event: str
    action: str
    request_id: str | None = None
    base_url: HttpUrl
    rossum_authorization_token: str = Field(min_length=1)
    files: list[EmailFile]
    body: EmailBody = Field(default_factory=EmailBody)


class HookResponse(BaseModel):
    files: list[dict[str, Any]]
    additional_files: list[dict[str, Any]]
