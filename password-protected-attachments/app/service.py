"""Email attachment orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol

from pypdf.errors import WrongPasswordError

from app.models import EmailFile, EmailReceivedPayload, HookResponse
from app.passwords import extract_password_candidates
from app.pdf import InvalidPdfError, decrypt_pdf


class AttachmentProcessingError(Exception):
    """Safe, non-sensitive error indicating the email should be retried."""


class RossumApi(Protocol):
    async def download_document(self, document_url: str) -> bytes: ...

    async def find_unlocked_child(self, parent_url: str) -> str | None: ...

    async def create_unlocked_child(
        self, parent_url: str, filename: str, content: bytes
    ) -> str: ...


@dataclass(frozen=True)
class PendingUpload:
    source: EmailFile
    filename: str
    content: bytes


def unlocked_filename(original: str) -> str:
    safe_name = original.replace("\\", "/").rsplit("/", 1)[-1]
    path = PurePosixPath(safe_name)
    stem = path.stem if path.stem not in {"", "."} else "document"
    return f"{stem}_unlocked.pdf"


async def process_email(payload: EmailReceivedPayload, client: RossumApi) -> HookResponse:
    candidates = extract_password_candidates(
        payload.body.body_text_plain,
        payload.body.body_text_html,
    )
    passthrough: list[dict[str, object]] = []
    pending: list[PendingUpload] = []

    # Phase one is read-only. No child is created unless every protected PDF opens.
    for attachment in payload.files:
        if attachment.mime_type.lower() != "application/pdf":
            passthrough.append({"id": attachment.id, "values": []})
            continue

        try:
            source_url = str(attachment.document)
            content = await client.download_document(source_url)
            decrypted = decrypt_pdf(content, candidates)
        except (WrongPasswordError, InvalidPdfError) as exc:
            raise AttachmentProcessingError(
                "Unable to unlock one or more protected PDF attachments"
            ) from exc

        if decrypted is None:
            passthrough.append({"id": attachment.id, "values": []})
        else:
            pending.append(
                PendingUpload(
                    source=attachment,
                    filename=unlocked_filename(attachment.filename),
                    content=decrypted,
                )
            )

    # Phase two writes, with parent/metadata lookup providing retry idempotency.
    additional_files: list[dict[str, object]] = []
    for upload in pending:
        parent_url = str(upload.source.document)
        child_url = await client.find_unlocked_child(parent_url)
        if child_url is None:
            child_url = await client.create_unlocked_child(
                parent_url,
                upload.filename,
                upload.content,
            )
        additional_files.append(
            {"document": child_url, "import_document": True, "values": []}
        )

    return HookResponse(files=passthrough, additional_files=additional_files)
