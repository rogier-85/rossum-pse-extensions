# Rossum line items → Master Data Hub

A standalone Python 3.12 serverless function. Each populated line-item row becomes one record in the configured MDH dataset/collection. No external packages are required.

## Install in Rossum

1. Create a **Custom Function** extension, select Python 3.12, and paste all of `main.py` into its code editor.
2. Set the trigger to **annotation_status.changed** and attach the intended queues.
3. Enable **Rossum API access** so the payload includes `rossum_authorization_token`. The token owner must have admin or organization group admin privileges for MDH.
4. Paste `settings.example.json` into the extension's **Settings** JSON section. Change `dataset`, `line_items_schema_id`, and `fields` to your actual dataset name and queue schema IDs.
5. Create the target dataset first, or set `create_dataset_if_missing` to `true`.
6. Initially set `dry_run` to `true` and test with an exported annotation. The function validates mappings and identities and logs the record count without writing. Then set it to `false` and test a real write; check the logged operation URL or MDH Dataset Management for its final result.

## Settings

`fields` maps **MDH destination column → Rossum line-item schema ID**. Remove any unwanted columns or add others. Values are stored as Rossum supplies them (normally strings); no implicit numeric conversions occur. Set `use_normalized_values` to `true` to prefer `normalized_value` where present.

Two optional special sources are supported: `@annotation_id` and `@row_id`. The example uses these as a composite identity so retries and re-exporting update the same row instead of adding duplicates. Both columns must be retained when using the example's `id_keys`. For a product master dataset, you could instead map `product_code` to `item_code` and set `id_keys` to `["product_code"]`. Choose keys unique across all documents writing to that dataset; blank or duplicate identities within one document are rejected before submission.

`statuses` defaults to `["exported"]`. Add other statuses only if you intend to store data at those workflow stages. Other events/actions and other statuses perform no writes. Status events normally contain a content URL, so the function fetches content by annotation ID using the passed token.

## Write behavior and limits

The function submits one JSON file using `PATCH /svc/master-data-hub/api/v1/dataset/{dataset_name}` with multipart `file`, `encoding`, `id_keys`, and `update_or_new`. It adds new records and updates matching records, preserving unrelated dataset records. Removed invoice rows are **not deleted** from MDH. Missing mapped fields in a populated row fail the entire submission; wholly empty rows are ignored. Destination names must be flat columns (no dotted paths or MongoDB operators).

MDH returns **202 Accepted**, with an operation URL in `Location`. This means queued, not completed. The function logs that URL and returns; it does not wait for background ingestion or mark asynchronous failures as successful. Monitor the operation endpoint or MDH UI. A separate scheduled monitor is needed if automatic completion alerts are required. HTTP failures raise an extension error so Rossum's status-event retry mechanism can act. Upsert identities make repeated submissions safe for the same records, but multiple queued operations can still be created. A timeout may occur after MDH has accepted a write; check operations before manually retrying. Later submissions for the same keys update earlier records. Avoid high-frequency status triggers because MDH queues operations per dataset.

Tokens and row values are not logged. The function uses the payload's Rossum HTTPS origin and rejects redirects, keeping authentication on that host. No credentials belong in Settings.

## Local verification

Run `python3 -m unittest discover -s tests -v`. Tests mock HTTP; live tenant access and final MDH ingestion have not been verified.

## API references

- [MDH API: dataset updates, identities, authentication, operation status](https://elis.rossum.ai/svc/master-data-hub/api/docs)
- [Rossum extension payloads, runtimes, and event retry behavior](https://example.app.rossum.ai/api/docs/openapi/guides/extensions/)
- [Asynchronous MDH update guidance](https://knowledge-base.rossum.ai/docs/using-api)
