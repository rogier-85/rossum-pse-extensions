#!/usr/bin/env python3
"""Build a Rossum Hook API request with extension.py embedded as source code."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", action="append", required=True, help="Queue API URL; repeatable")
    parser.add_argument("--token-owner", required=True, help="Rossum user API URL")
    parser.add_argument("--output", default="build/hook.json")
    args = parser.parse_args()

    source = (ROOT / "extension.py").read_text(encoding="utf-8")
    payload = {
        "type": "function",
        "name": "Invoice attachment linker",
        "description": "Links all annotations from one email below the extracted invoice.",
        "queues": args.queue,
        "run_after": [],
        "active": True,
        "events": ["annotation_content.initialize"],
        # Sideload current relations so retries can identify an existing match.
        # The function also queries the API, keeping it safe if the sideload is
        # unavailable in a manually configured hook.
        "sideload": ["relations", "child_relations"],
        "token_owner": args.token_owner,
        "token_lifetime_s": 600,
        "extension_source": "custom",
        "settings": {
            "document_sub_type_field": "document_sub_type",
            "invoice_type_value": "Invoice",
        },
        "settings_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "document_sub_type_field": {"type": "string", "minLength": 1},
                "invoice_type_value": {"type": "string", "minLength": 1},
            },
        },
        "config": {
            "runtime": "python3.12",
            "code": source,
            "third_party_library_pack": None,
            "timeout_s": 30,
            "memory_size_mb": 128,
            "retry_count": 4,
            "payload_logging_enabled": False,
        },
    }

    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
