# Rossum WHT Certificate Linker

This Python 3.12 Rossum serverless function runs when an invoice review starts. It finds the newest annotation in a configured WHT certificate queue with the same exact `supplier_match` and `coa_match`, attaches that annotation to the invoice, and replaces `wht_rate` with `item_wht_rate` from the first row of the matched annotation's `items` table.

## Behavior

- Matching is exact and case-sensitive.
- All source annotation statuses are eligible.
- If several annotations match, Rossum search ordering selects the newest `created_at` value.
- The current annotation is the relation parent; the matched annotation is its attachment.
- Existing attachment relations are reused and updated with ETag protection. Reopening review does not add the same annotation again.
- An existing `wht_rate` is always replaced. A numeric zero is a valid value.
- Missing match fields, no match, a missing first row/rate, or a missing target field is a silent no-op.
- Configuration, authorization, malformed API responses, and relation write failures produce a document-level error and no field operation.

## Rossum setup

1. Confirm that the organization has the `attachment` relation feature enabled.
2. Open [`hook.example.json`](hook.example.json) and replace:
   - `example.rossum.app` with the account's Rossum host.
   - `CURRENT_QUEUE_ID` with the queue where invoice reviews start.
   - `WHT_QUEUE_ID` with the queue containing WHT certificates.
   - `TOKEN_OWNER_ID` with a user that can read both queues and create/update relations.
3. Replace the `config.code` placeholder with the complete, JSON-escaped contents of [`wht_certificate_linker.py`](wht_certificate_linker.py), or paste that file into Rossum's serverless-function editor.
4. Create the hook through the Rossum Hook API or UI, keep it inactive initially, and verify:
   - event: `annotation_content.started`
   - runtime: `python3.12`
   - setting: `target_queue_url`
   - token owner is configured
5. Test the hook, then activate it.

The target queue URL must be on the same Rossum host supplied in the hook payload. The handler sends the temporary bearer token only to that host.

## Hook test

Use Rossum's **Test hook** action with a real generated payload when possible. A minimal representative payload has this shape:

```json
{
  "event": "annotation_content",
  "action": "started",
  "base_url": "https://example.rossum.app",
  "rossum_authorization_token": "TEST_TOKEN",
  "settings": {
    "target_queue_url": "https://example.rossum.app/api/v1/queues/WHT_QUEUE_ID"
  },
  "annotation": {
    "id": 100,
    "url": "https://example.rossum.app/api/v1/annotations/100",
    "content": [
      {
        "id": 1,
        "schema_id": "invoice_info_section",
        "category": "section",
        "children": [
          {
            "id": 11,
            "schema_id": "supplier_match",
            "category": "datapoint",
            "content": {"value": "SUP-1"}
          },
          {
            "id": 12,
            "schema_id": "coa_match",
            "category": "datapoint",
            "content": {"value": "COA-1"}
          },
          {
            "id": 13,
            "schema_id": "wht_rate",
            "category": "datapoint",
            "content": {"value": ""}
          }
        ]
      }
    ]
  }
}
```

A successful response contains one `replace` operation for datapoint `13`. Verify in Rossum that the current annotation also has an `attachment` relation whose child is the newest matching WHT annotation. Run the same test or reopen review again and verify that no duplicate child is added.

## Local tests

The suite uses only standard-library mocks and can run without contacting Rossum:

```bash
python3 -m unittest discover -s tests -v
```

It is also pytest-compatible:

```bash
python3 -m pip install -e '.[test]'
python3 -m pytest
```
