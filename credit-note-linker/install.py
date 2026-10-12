#!/usr/bin/env python3
"""Install the credit-note linker as a Rossum serverless function hook."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("ROSSUM_BASE_URL"))
    parser.add_argument("--token", default=os.environ.get("ROSSUM_TOKEN"))
    parser.add_argument("--queue-id", action="append", required=True, help="Repeat for each target queue")
    parser.add_argument("--token-owner", required=True, help="Rossum user ID used by the function")
    parser.add_argument(
        "--search-queue-id",
        action="append",
        default=[],
        help="Queue containing original invoices; repeat as needed (defaults to current queue)",
    )
    args = parser.parse_args()
    if not args.base_url or not args.token:
        parser.error("Provide --base-url/ROSSUM_BASE_URL and --token/ROSSUM_TOKEN")

    base = args.base_url.rstrip("/")
    code = Path(__file__).with_name("credit_note_link.py").read_text(encoding="utf-8")
    queue_urls = [f"{base}/api/v1/queues/{queue_id}" for queue_id in args.queue_id]
    settings = {"search_queue_ids": args.search_queue_id}
    settings_schema = {
        "type": "object",
        "properties": {
            "document_type_schema_id": {"type": "string", "default": "document_type"},
            "credit_note_values": {"type": "array", "items": {"type": "string"}, "default": ["credit_note"]},
            "original_invoice_number_schema_id": {"type": "string", "default": "original_document_id"},
            "invoice_number_schema_id": {"type": "string", "default": "document_id"},
            "invoice_document_type_values": {
                "type": "array",
                "items": {"type": "string"},
                "default": ["tax_invoice", "invoice"],
            },
            "relation_type": {
                "type": "string",
                "enum": ["attachment", "edit", "duplicate"],
                "default": "attachment",
            },
            "relation_key": {"type": ["string", "null"], "default": None},
            "search_queue_ids": {"type": "array", "items": {"type": ["string", "integer"]}, "default": []},
            "message_on_success": {"type": "boolean", "default": True},
        },
    }
    body = {
        "name": "Link credit notes to original invoices",
        "type": "function",
        "queues": queue_urls,
        "events": ["annotation_content.export"],
        "active": True,
        "token_owner": f"{base}/api/v1/users/{args.token_owner}",
        "token_lifetime_s": 600,
        "settings": settings,
        "settings_schema": settings_schema,
        "description": "Find an original invoice by exact invoice number and relate its credit note.",
        "config": {
            "runtime": "python3.12",
            "code": code,
            "timeout_s": 30,
            "retry_count": 4,
        },
    }
    request = urllib.request.Request(
        f"{base}/api/v1/hooks",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {args.token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            installed = json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Installation failed ({error.code}): {detail}") from error
    print(f"Installed hook {installed['id']}: {installed['url']}")


if __name__ == "__main__":
    main()

