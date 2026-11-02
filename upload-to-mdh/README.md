# Incoming CSV / Excel → MDH dataset replacement

A standalone Python 3.12 Rossum Custom Function. Filename substrings select an existing Master Data Hub dataset, and the original file is submitted for **full replacement**, removing old records absent from the new file. No external packages are required.

## Install

1. Create a Custom Function extension with Python 3.12 and paste the complete `main.py` into its editor.
2. Subscribe to **`upload.created` only** and attach the intended queues. This event covers manual/API uploads and uploads from incoming email. Do not also subscribe to `email.received`: using a single ingestion stage avoids submitting the same attachment twice.
3. Enable **Rossum API access**. The token owner needs permissions to read the uploaded documents and administer MDH datasets.
4. Paste `settings.example.json` into Settings. Replace dataset names and filename substrings with your actual names. Create those datasets first; this extension does not create datasets automatically.
5. Test with `dry_run: true`. It resolves matches and downloads files but does not replace datasets or change processing. Dry-run files can therefore enter normal extraction.
6. Set `dry_run: false` to enable replacements. With `suppress_processing: true` (the default), matched files receive `prevent_importing: true` after MDH acknowledges the request, preventing annotation creation/extraction. Unmatched files retain their incoming processing settings.

For example, `ERP_SUPPLIERS_2026-11-02.xlsx` matches `suppliers` and replaces the `suppliers` dataset. Matching is case-insensitive and uses the filename basename. A file matching multiple rules fails rather than selecting a potentially wrong dataset. Two files targeting one dataset in the same upload also fail before any replacement is submitted.

## Formats and settings

Supported files: **`.csv` and `.xlsx`**. Legacy `.xls` files must be saved as XLSX or CSV first. Original bytes are forwarded to MDH; the function does not convert values or workbook contents. MDH uses only the **active worksheet** of XLSX workbooks. Keep the expected dataset columns in your file.

- `rules`: list of `{ "filename_contains": "suppliers", "dataset": "suppliers" }` mappings.
- `encoding`: input character encoding, defaults to `utf-8` (relevant to CSV).
- `dynamic`: defaults to `true`, using MDH's automatic CSV dialect/header detection. With `false`, MDH uses the first row as headers and expects semicolon-separated CSV.
- `suppress_processing`: defaults to `true`; stops extraction for matched files once replacement is queued.
- `dry_run`: defaults to `false` in code; the example explicitly sets `true` for setup.

## Completion, failures, and limits

`PUT /svc/master-data-hub/api/v1/dataset/{dataset_name}` returns **202 Accepted** and an operation URL. This means **queued, not completed**. The function logs the operation URL; inspect its status or MDH Dataset Management → Upload Status to confirm success. Suppression happens on acceptance, so an asynchronously failed replacement still leaves that file suppressed. The original Rossum document remains available for investigation. No automatic completion monitor is included.

Only uploaded files actually exposed to `upload.created` can be handled. Ensure inbox attachment filtering and other email extensions allow CSV/XLSX attachments through to the upload stage. If an upstream filter removes them, this function never receives them. Verify email and manual upload behavior on your tenant before rollout. Documents already attached to annotations are rejected for matched files; use fresh uploads.

Empty/whitespace-only files and downloads exceeding 50 MiB are rejected. Other format validation is performed asynchronously by MDH; a header-only or malformed file is not locally validated. The API's upload limit is 50 MB. Keep files and batch counts within Rossum function memory and execution limits; each API request has a 15-second timeout.

HTTP errors raise extension errors for Rossum retries. All candidates are resolved/downloaded before writes, but a multi-dataset batch is not transactional: a later write can fail after earlier writes were accepted. Retries can enqueue duplicate replacements. Replacing with identical contents converges to the same data, but this function does not provide persistent deduplication or ordering across concurrent uploads. Avoid overlapping updates to the same dataset; check pending operations before manual retries, especially after timeouts. For strict delivery ordering, use an external serialized ingestion service.

Authentication is restricted to the payload's HTTPS Rossum host and redirects are rejected. Credentials and file contents are not logged. Anyone able to upload matching filenames into an attached queue can trigger replacement using the extension's permissions; attach it to an appropriately restricted intake queue.

## Verification and references

Run `python3 -m unittest discover -s upload-to-mdh/tests -v` from the repository root. Tests mock HTTP; live tenant ingestion and email delivery have not been tested.

- [Rossum extension events, upload payloads, and prevent_importing](https://example.app.rossum.ai/api/docs/openapi/guides/extensions/)
- [MDH API: replace endpoint, supported formats, and operation monitoring](https://elis.rossum.ai/svc/master-data-hub/api/docs)
- [Full dataset replacement and completion status](https://knowledge-base.rossum.ai/docs/full-dataset-replace)
