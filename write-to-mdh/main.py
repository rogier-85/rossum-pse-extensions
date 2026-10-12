"""Paste this entire file into a Rossum Python 3.12 serverless function."""
import json
import logging
import uuid
from urllib import request, error
from urllib.parse import urlsplit, quote


class ExtensionError(RuntimeError):
    pass


def walk(nodes):
    for node in nodes:
        yield node
        yield from walk(node.get('children', []))


def origin(base_url):
    parsed = urlsplit(base_url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.port not in (None, 443)
            or not any(parsed.hostname.endswith('.' + domain) or parsed.hostname == domain
                       for domain in ('rossum.app', 'rossum.ai'))):
        raise ExtensionError('base_url must be an HTTPS Rossum host.')
    return 'https://' + parsed.hostname


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api_call(url, token, method='GET', body=None, content_type=None):
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}
    if content_type:
        headers['Content-Type'] = content_type
    req = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.build_opener(NoRedirect()).open(req, timeout=15) as response:
            data = response.read()
            return response.status, response.headers, json.loads(data) if data else {}
    except error.HTTPError as exc:
        # Never expose response bodies, credentials, or extracted invoice data.
        raise ExtensionError('Rossum API request failed (HTTP %s).' % exc.code) from None
    except (error.URLError, TimeoutError):
        raise ExtensionError('Rossum API request failed or timed out; delivery may be uncertain.') from None


def configuration(settings):
    if not isinstance(settings, dict):
        raise ExtensionError('settings must be an object.')
    for key in ('dataset', 'line_items_schema_id'):
        if not isinstance(settings.get(key), str) or not settings[key].strip():
            raise ExtensionError('Configure settings.' + key + '.')
    fields = settings.get('fields')
    if not isinstance(fields, dict) or not fields:
        raise ExtensionError('fields must map MDH column names to line-item schema IDs.')
    for target, source in fields.items():
        if (not isinstance(target, str) or not target or '.' in target
                or any(c in target for c in ('"', '\r', '\n', ','))
                or target.startswith('$') or target == '_id'
                or not isinstance(source, str) or not source):
            raise ExtensionError('Use flat MDH column names and non-empty source schema IDs.')
    keys = settings.get('id_keys')
    if not isinstance(keys, list) or not keys or any(not isinstance(k, str) or k not in fields for k in keys):
        raise ExtensionError('id_keys must contain column names from fields.')
    for key in ('create_dataset_if_missing', 'use_normalized_values', 'dry_run'):
        if key in settings and not isinstance(settings[key], bool):
            raise ExtensionError(key + ' must be true or false.')
    statuses = settings.get('statuses', ['exported'])
    if not isinstance(statuses, list) or not statuses or any(not isinstance(s, str) or not s for s in statuses):
        raise ExtensionError('statuses must be a non-empty list of document statuses.')
    return settings


def records_from_content(content, settings):
    tables = [n for n in walk(content) if n.get('schema_id') == settings['line_items_schema_id']
              and n.get('category') == 'multivalue']
    if not tables:
        raise ExtensionError('Configured line-items multivalue was not found.')
    records = []
    for table in tables:
        for row in table.get('children', []):
            datapoints = [n for n in walk([row]) if n.get('category') == 'datapoint']
            if not any(n.get('content', {}).get('value') not in (None, '') for n in datapoints):
                continue
            record = {}
            for target, schema_id in settings['fields'].items():
                if schema_id == '@annotation_id' or schema_id == '@row_id':
                    record[target] = row.get('id') if schema_id == '@row_id' else None
                    continue
                matches = [n for n in datapoints if n.get('schema_id') == schema_id]
                if len(matches) != 1:
                    raise ExtensionError('Each populated row must contain exactly one field: ' + schema_id)
                value = matches[0].get('content', {})
                record[target] = (value.get('normalized_value', value.get('value'))
                                  if settings.get('use_normalized_values', False) else value.get('value'))
            records.append(record)
    return records


def multipart(records, settings):
    boundary = 'rossum-' + uuid.uuid4().hex
    chunks = []
    for name, value in [('encoding', 'utf-8'), ('update_or_new', str(settings.get('create_dataset_if_missing', False)).lower())] + [('id_keys', key) for key in settings['id_keys']]:
        chunks.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, name, value)).encode())
    chunks.append(('--%s\r\nContent-Disposition: form-data; name="file"; filename="line_items.json"\r\nContent-Type: application/json\r\n\r\n' % boundary).encode())
    chunks.append(json.dumps(records, ensure_ascii=False, allow_nan=False).encode('utf-8'))
    chunks.append(('\r\n--%s--\r\n' % boundary).encode())
    body = b''.join(chunks)
    if len(body) > 50 * 1024 * 1024:
        raise ExtensionError('MDH upload exceeds 50 MB.')
    return body, 'multipart/form-data; boundary=' + boundary


def rossum_hook_request_handler(payload):
    response = {'messages': [], 'operations': []}
    if payload.get('event') != 'annotation_status' or payload.get('action') != 'changed':
        return response
    settings = configuration(payload.get('settings', {}))
    annotation = payload.get('annotation', {})
    if annotation.get('status') not in settings.get('statuses', ['exported']):
        return response
    base = origin(payload.get('base_url', ''))
    token = payload.get('rossum_authorization_token')
    if not isinstance(token, str) or not token:
        raise ExtensionError('Enable Rossum API access in the extension configuration.')
    content = annotation.get('content')
    if not isinstance(content, list):
        annotation_id = annotation.get('id')
        if not isinstance(annotation_id, int) or isinstance(annotation_id, bool):
            raise ExtensionError('Missing numeric annotation ID.')
        _, _, content = api_call(base + '/api/v1/annotations/%s/content' % annotation_id, token)
        if isinstance(content, dict):
            content = content.get('content')
    if not isinstance(content, list):
        raise ExtensionError('Annotation content must be a list.')
    records = records_from_content(content, settings)
    identities = set()
    for record in records:
        for target, source in settings['fields'].items():
            if source == '@annotation_id':
                record[target] = annotation.get('id')
        if any(record[k] in (None, '') for k in settings['id_keys']):
            raise ExtensionError('An identity field is empty; no records submitted.')
        identity = json.dumps([record[k] for k in settings['id_keys']], sort_keys=True)
        if identity in identities:
            raise ExtensionError('Duplicate identity in document; choose more specific id_keys.')
        identities.add(identity)
    if not records or settings.get('dry_run', False):
        logging.info('MDH writer: %s records prepared; no write submitted.', len(records))
        return response
    body, content_type = multipart(records, settings)
    status, headers, _ = api_call(base + '/svc/master-data-hub/api/v1/dataset/' + quote(settings['dataset'], safe=''),
                                  token, 'PATCH', body, content_type)
    if status != 202 or not headers.get('Location'):
        raise ExtensionError('MDH did not return 202 with an operation Location; check MDH operations before retrying.')
    logging.info('MDH writer: %s records submitted, completion pending. Operation: %s', len(records), headers['Location'])
    return response
