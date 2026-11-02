"""Rossum Python 3.12 Custom Function: upload.created -> full MDH replacement."""
import codecs
import json
import logging
import re
import uuid
from urllib import error, request
from urllib.parse import quote, urlsplit

MAX_BYTES = 50 * 1024 * 1024


class ExtensionError(RuntimeError):
    pass


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.port not in (None, 443)
            or not any(parsed.hostname == d or parsed.hostname.endswith('.' + d)
                       for d in ('rossum.ai', 'rossum.app'))):
        raise ExtensionError('Expected an HTTPS Rossum base_url.')
    return 'https://' + parsed.hostname


def document_url(value, base):
    if not isinstance(value, str) or not re.fullmatch(
            re.escape(base) + r'/api/v1/documents/[0-9]+', value):
        raise ExtensionError('Expected a document URL on the payload Rossum host.')
    return value


def api_call(url, token, method='GET', body=None, content_type=None, binary=False):
    headers = {'Authorization': 'Bearer ' + token}
    if content_type:
        headers['Content-Type'] = content_type
    req = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.build_opener(NoRedirect()).open(req, timeout=15) as response:
            data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ExtensionError('File or API response exceeds 50 MiB.')
            return response.status, response.headers, data if binary else json.loads(data or b'{}')
    except error.HTTPError as exc:
        raise ExtensionError('Rossum API request failed (HTTP %s).' % exc.code) from None
    except (error.URLError, TimeoutError):
        raise ExtensionError('Rossum API request timed out or failed; delivery may be uncertain.') from None


def configuration(settings):
    if not isinstance(settings, dict):
        raise ExtensionError('settings must be an object.')
    rules = settings.get('rules')
    if not isinstance(rules, list) or not rules:
        raise ExtensionError('Configure at least one filename rule.')
    for rule in rules:
        if not isinstance(rule, dict) or any(
                not isinstance(rule.get(k), str) or not rule[k].strip()
                for k in ('filename_contains', 'dataset')):
            raise ExtensionError('Each rule needs filename_contains and dataset strings.')
    for key in ('dry_run', 'suppress_processing', 'dynamic'):
        if key in settings and not isinstance(settings[key], bool):
            raise ExtensionError(key + ' must be a boolean.')
    try:
        codecs.lookup(settings.get('encoding', 'utf-8'))
    except (LookupError, TypeError):
        raise ExtensionError('Configure a valid encoding.') from None
    return settings


def route(filename, rules):
    # Match basename only; paths must not influence dataset selection.
    name = filename.replace('\\', '/').rsplit('/', 1)[-1].casefold()
    if not name.endswith(('.csv', '.xlsx')):
        return None
    matches = [r['dataset'] for r in rules if r['filename_contains'].casefold() in name]
    if len(matches) > 1:
        raise ExtensionError('Ambiguous filename: more than one MDH rule matches.')
    return matches[0] if matches else None


def multipart(data, suffix, settings):
    boundary = 'rossum-' + uuid.uuid4().hex
    fields = {'encoding': settings.get('encoding', 'utf-8'),
              'dynamic': str(settings.get('dynamic', True)).lower(),
              'replace_or_new': 'false'}
    chunks = []
    for name, value in fields.items():
        chunks.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                       % (boundary, name, value)).encode())
    # Fixed safe filename preserves the format without interpolating input into headers.
    chunks.append(('--%s\r\nContent-Disposition: form-data; name="file"; filename="dataset.%s"'
                   '\r\nContent-Type: application/octet-stream\r\n\r\n' % (boundary, suffix)).encode())
    chunks.extend([data, ('\r\n--%s--\r\n' % boundary).encode()])
    return b''.join(chunks), 'multipart/form-data; boundary=' + boundary


def rossum_hook_request_handler(payload):
    if (payload.get('event'), payload.get('action')) != ('upload', 'created'):
        return {}
    settings = configuration(payload.get('settings', {}))
    base = origin(payload.get('base_url', ''))
    token = payload.get('rossum_authorization_token')
    if not isinstance(token, str) or not token:
        raise ExtensionError('Enable Rossum API access for the extension.')
    files = payload.get('files')
    if not isinstance(files, list):
        raise ExtensionError('Upload payload must include files.')
    documents = {d['url']: d for d in payload.get('documents', []) if isinstance(d, dict) and 'url' in d}
    plans, results, datasets = [], [], set()
    # Resolve every rule and download every candidate before submitting any replacement.
    for file in files:
        url = document_url(file.get('document'), base)
        result = {k: file[k] for k in ('document', 'prevent_importing', 'failed_import', 'values') if k in file}
        results.append(result)
        doc = documents.get(url, {})
        filename = doc.get('original_file_name')
        if not isinstance(filename, str):
            _, _, doc = api_call(url, token)
            filename = doc.get('original_file_name')
        if not isinstance(filename, str):
            raise ExtensionError('Document has no original_file_name.')
        dataset = route(filename, settings['rules'])
        if dataset is None:
            continue
        if dataset in datasets:
            raise ExtensionError('Multiple files target the same dataset in this upload.')
        datasets.add(dataset)
        if file.get('annotation'):
            raise ExtensionError('Matched document already has an annotation; use a fresh upload.')
        _, _, data = api_call(url + '/content', token, binary=True)
        if not data or not data.strip():
            raise ExtensionError('Refusing to replace a dataset with an empty file.')
        plans.append((result, dataset, data, filename.rsplit('.', 1)[-1].lower()))
    for result, dataset, data, suffix in plans:
        if settings.get('dry_run', False):
            logging.info('MDH importer: candidate validated; dry run, no replacement submitted.')
            continue
        body, content_type = multipart(data, suffix, settings)
        status, headers, _ = api_call(base + '/svc/master-data-hub/api/v1/dataset/'
                                    + quote(dataset, safe=''), token, 'PUT', body, content_type)
        location = headers.get('Location')
        if status != 202 or not location:
            raise ExtensionError('MDH did not acknowledge a queued operation; inspect operations before retrying.')
        logging.info('MDH replacement queued; completion pending. Operation: %s', location)
        if settings.get('suppress_processing', True):
            result['prevent_importing'] = True
    return {'files': results}
