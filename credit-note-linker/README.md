# Rossum Credit Note Link Extension

A Rossum Python 3.12 serverless function that runs when an annotation is exported. For a credit note it:

1. reads the original invoice number from `original_document_id`;
2. searches for an exact `document_id` match in the configured queue(s);
3. requires one unambiguous invoice match; and
4. creates (or idempotently updates) a Rossum annotation relation with the invoice as parent and the credit note as child.

The defaults match Rossum's standard extraction schema: `document_type=credit_note`, `original_document_id`, and `document_id`.

## Install

The API user selected as token owner must be able to read annotations in all search queues and create/update relations. Run:

```bash
export ROSSUM_BASE_URL="https://your-domain.rossum.app"
export ROSSUM_TOKEN="your-api-token"
python3 install.py \
  --queue-id 12345 \
  --token-owner 67890 \
  --search-queue-id 12345
```

Repeat `--queue-id` for each queue on which the extension should run, and `--search-queue-id` for every queue that can contain original invoices. If no search queue is supplied, each credit note is searched only against its own queue.

The installer registers the `annotation_content.export` event and enables temporary Rossum authorization-token passing to the function.

## Settings

The installed hook exposes configurable schema IDs, accepted credit-note/invoice document-type values, search queue IDs, success messages, and relation type. The default relation type is `attachment`, because Rossum currently limits annotation relations to `attachment`, `edit`, and `duplicate`; it does not provide a dedicated credit-note relation type.

If the original invoice is absent or the invoice number matches multiple annotations, the extension creates no relation and returns a warning. This prevents silently linking a credit note to the wrong invoice.

## Test

```bash
python3 -m unittest discover -v
```

No third-party packages are required.

## Rossum API references

- [Serverless Function Extension](https://elis.rossum.ai/api/docs/internal/#serverless-function-extension)
- [Search for annotations](https://elis.rossum.ai/api/docs/internal/#search-for-annotations)
- [Relation API](https://elis.rossum.ai/api/docs/internal/#relation)
