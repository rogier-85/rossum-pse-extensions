# Rossum password-protected PDF unlocker

A Rossum `email.received` webhook that finds explicitly labelled passwords in an
email body, unlocks encrypted PDF attachments, and sends the unlocked copies to
Rossum for extraction. It is designed for a native Python Web Service on Render;
Docker is not required.

## What it does

For every incoming email, the extension:

1. Validates Rossum's HMAC-SHA256 webhook signature.
2. Finds password candidates labelled `password`, `passcode`, `pwd`, `PIN`, or
   `code` in the plain-text and HTML bodies.
3. Downloads and inspects each PDF attachment.
4. If all encrypted PDFs can be opened, uploads unencrypted copies named
   `<original>_unlocked.pdf`.
5. Returns those copies as Rossum `additional_files` with `import_document: true`.

Each unlocked document has its encrypted source as its Rossum `parent`. The
encrypted original therefore remains linked to the incoming email but does not
produce an annotation. Unencrypted PDFs and non-PDF attachments pass through
unchanged. If any encrypted PDF cannot be unlocked, the webhook returns HTTP 500
before creating any new documents, causing Rossum to retry and fail the whole
email rather than processing it partially.

Passwords can use any of these forms:

```text
Password: SeCrEt123!
Passcode = 123456
PIN is 9876
pwd: "a password containing spaces"
code-myCode
```

Passwords are case-sensitive. Labels are case-insensitive. Unlabelled words are
never guessed as passwords.

## Deploy to Render

### Blueprint deployment

1. Push this repository to GitHub, GitLab, or Bitbucket.
2. In Render, select **New > Blueprint** and connect the repository.
3. Render reads [`render.yaml`](render.yaml) and prompts for
   `ROSSUM_WEBHOOK_SECRET`. Enter a long, random value and save it somewhere
   secure; the identical value must be configured in Rossum.
4. Deploy and verify:

   ```text
   https://YOUR_SERVICE.onrender.com/health
   ```

   The response must be `{"status":"ok"}`.

The Blueprint uses Render's always-on `starter` plan. A free instance can be
selected for testing, but free Render services sleep when idle and their cold
start can exceed Rossum's webhook timeout.

### Manual Web Service deployment

If you do not want to use the Blueprint, create a Render **Web Service** with:

| Setting | Value |
| --- | --- |
| Language | Python 3 |
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Health check path | `/health` |
| Environment variable | `ROSSUM_WEBHOOK_SECRET=<long random value>` |

No database, disk, or other Render service is required.

## Configure Rossum

Create an active webhook hook for each queue receiving these emails. You can use
[`rossum-hook.example.json`](rossum-hook.example.json) as the body for
`POST https://YOUR_TENANT.rossum.app/api/v1/hooks`, replacing every placeholder.

Important settings:

- Event: `email.received`
- URL: `https://YOUR_SERVICE.onrender.com/rossum/email-received`
- Secret: exactly the value of Render's `ROSSUM_WEBHOOK_SECRET`
- Timeout: 60 seconds
- Retries: 4
- Payload logging: disabled
- Token owner: a Rossum user that can read and create documents in the selected
  queues. Rossum organization-group administrators cannot be token owners.

The service uses only the temporary `rossum_authorization_token` supplied in the
callback. No permanent Rossum token is stored on Render.

Before enabling the hook in production, use Rossum's hook test facility or email
a test queue with:

- one ordinary PDF, confirming it is processed unchanged;
- one encrypted PDF and a correct labelled password, confirming that only the
  `_unlocked.pdf` copy creates an annotation;
- one encrypted PDF with a wrong password, confirming the hook fails and no copy
  is created.

## Local development

Python 3.13 is configured in [`.python-version`](.python-version).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q
.venv/bin/ruff check app tests
ROSSUM_WEBHOOK_SECRET=development-secret .venv/bin/uvicorn app.main:app --reload
```

To call the webhook locally, send the exact JSON bytes used to calculate this
header:

```text
X-Rossum-Signature-SHA256: sha256=<HMAC_SHA256_HEX>
```

## Retry and linking behavior

The extension stores only this non-sensitive metadata on each unlocked child:

```json
{
  "password_protected_pdf_unlocker": {
    "version": "1",
    "source_document": "https://.../api/v1/documents/123"
  }
}
```

Before creating a child it queries documents by `parent` and reuses a matching
child. This prevents duplicate copies if Rossum retries after an upload succeeded
but before the webhook response was delivered.

The service is stateless and processes PDF bytes in memory. It never logs email
bodies, candidate passwords, document contents, the shared webhook secret, or
Rossum bearer tokens. Logs contain only the Rossum request ID and attachment
counts.

## Operational errors

| Status | Meaning |
| --- | --- |
| `400` | Invalid payload, wrong event, or a document URL outside the Rossum tenant |
| `401` | Missing or invalid HMAC signature |
| `500` | A protected PDF could not be unlocked or parsed; Rossum should retry |
| `502` | A Rossum download, lookup, or upload request failed |
| `503` | `ROSSUM_WEBHOOK_SECRET` is missing on Render |

For HTTP 500 errors, check that the email body contains a supported English label
and the exact case-sensitive password. Sensitive values are deliberately omitted
from both the response and logs.
