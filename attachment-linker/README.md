# Rossum Invoice Attachment Linker

This Rossum serverless function runs immediately after extraction. When the
configured document-subtype field is exactly `Invoice`, it:

1. finds the incoming email linked to the annotation's document;
2. reads every annotation created from that email;
3. creates an `attachment` relation with the invoice as `parent` and every
   other annotation as a child.

The default field IDs are:

| Purpose | Schema ID |
| --- | --- |
| Document subtype | `document_sub_type` |

The field ID and exact invoice subtype value are hook settings and can be
changed without editing the function.

## Prerequisites

- Rossum's attachment-relations feature must be enabled for the organization.
- The hook's token owner must be able to read documents, emails, annotations,
  and relations and to create/update relations in every configured queue.
- The invoice and its attachment annotations must originate from the same
  Rossum email.

## Install in the Rossum UI

1. Create a **Function** extension.
2. Set the runtime to **Python 3.12**.
3. Paste [`extension.py`](extension.py) into the code editor.
4. Select event **Annotation content initialized**
   (`annotation_content.initialize`).
5. Select the applicable queues.
6. Add **relations** and **child relations** as sideloaded resources.
7. Set a **token owner** with the permissions described above. Without this,
   `rossum_authorization_token` is not included and the function cannot use the
   Rossum API.
8. Add the settings shown below, or rely on the code defaults.
9. Keep retries enabled and activate the extension.

```json
{
  "document_sub_type_field": "document_sub_type",
  "invoice_type_value": "Invoice"
}
```

## Install through the Hook API

Build a ready-to-send hook payload (the source code is embedded automatically):

```bash
python3 scripts/build_hook_payload.py \
  --queue https://YOUR_TENANT.rossum.app/api/v1/queues/QUEUE_ID \
  --token-owner https://YOUR_TENANT.rossum.app/api/v1/users/USER_ID
```

Then create the hook using an admin API token:

```bash
curl -X POST https://YOUR_TENANT.rossum.app/api/v1/hooks \
  -H "Authorization: Bearer $ROSSUM_API_TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary @build/hook.json
```

Do not commit `build/hook.json` if your local version contains tenant-specific
queue or user URLs.

## Behavior and retry safety

- Non-invoices return immediately without API calls.
- An invoice without an email or sibling annotation returns a visible message
  and does not create an empty relation.
- Before creating a relation, the function looks for an existing attachment
  relation with the same parent. A retry therefore does not create a duplicate.
  If later execution finds more email annotations, it adds only the missing
  children.
- API failures raise an exception so Rossum's retry mechanism can retry the
  initialization hook.
- The short-lived bearer token is sent only to URLs on the payload's Rossum
  tenant host.

The relation request is:

```json
{
  "type": "attachment",
  "key": null,
  "parent": "https://YOUR_TENANT.rossum.app/api/v1/annotations/10",
  "annotations": [
    "https://YOUR_TENANT.rossum.app/api/v1/annotations/11"
  ]
}
```

## Test

```bash
python3 -m unittest discover -s tests -v
```
