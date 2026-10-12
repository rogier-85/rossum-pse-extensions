import copy
import json
import unittest
from unittest.mock import patch
import main


def payload():
    with open('settings.example.json') as handle:
        settings = json.load(handle)
    row = {'id': 20, 'category': 'tuple', 'children': [
        {'category': 'datapoint', 'schema_id': source, 'content': {'value': '12', 'normalized_value': '12.0'}}
        for source in settings['fields'].values() if not source.startswith('@')]}
    return {'event': 'annotation_status', 'action': 'changed', 'base_url': 'https://example.rossum.app',
            'rossum_authorization_token': 'test-token', 'settings': settings,
            'annotation': {'id': 10, 'status': 'exported', 'content': [
                {'schema_id': 'line_items', 'category': 'multivalue', 'children': [row]}]}}


class WriterTests(unittest.TestCase):
    @patch('main.api_call', return_value=(202, {'Location': '/operation/1'}, {}))
    def test_write_wire_format(self, call):
        p = payload()
        self.assertEqual(main.rossum_hook_request_handler(p), {'messages': [], 'operations': []})
        args = call.call_args.args
        self.assertEqual(args[2], 'PATCH')
        self.assertEqual(args[0], 'https://example.rossum.app/svc/master-data-hub/api/v1/dataset/invoice_line_items')
        self.assertIn(b'"annotation_id": 10, "row_id": 20', args[3])
        self.assertEqual(args[3].count(b'name="id_keys"'), 2)
        self.assertIn(b'name="update_or_new"\r\n\r\nfalse', args[3])

    @patch('main.api_call')
    def test_no_write_other_status_or_dry_run(self, call):
        p = payload()
        p['annotation']['status'] = 'reviewing'
        main.rossum_hook_request_handler(p)
        p['annotation']['status'] = 'exported'
        p['settings']['dry_run'] = True
        main.rossum_hook_request_handler(p)
        call.assert_not_called()

    @patch('main.api_call')
    def test_reject_duplicate_and_missing(self, call):
        p = payload()
        rows = p['annotation']['content'][0]['children']
        rows.append(copy.deepcopy(rows[0]))
        with self.assertRaisesRegex(main.ExtensionError, 'Duplicate'):
            main.rossum_hook_request_handler(p)
        rows.pop()
        rows[0]['children'].pop()
        with self.assertRaisesRegex(main.ExtensionError, 'exactly one'):
            main.rossum_hook_request_handler(p)
        call.assert_not_called()

    @patch('main.api_call')
    def test_fetch_content(self, call):
        p = payload()
        content = p['annotation']['content']
        p['annotation']['content'] = 'https://untrusted.example/content'
        call.side_effect = [(200, {}, content), (202, {'Location': '/operation/1'}, {})]
        main.rossum_hook_request_handler(p)
        self.assertEqual(call.call_args_list[0].args[0], 'https://example.rossum.app/api/v1/annotations/10/content')

    def test_normalized(self):
        p = payload()
        p['settings']['use_normalized_values'] = True
        self.assertEqual(main.records_from_content(p['annotation']['content'], p['settings'])[0]['quantity'], '12.0')

    def test_host_guard(self):
        for host in ['http://example.rossum.app', 'https://rossum.app.evil.test', 'https://token@elis.rossum.ai']:
            with self.assertRaises(main.ExtensionError):
                main.origin(host)

    @patch('main.api_call', return_value=(202, {}, {}))
    def test_no_operation_location(self, call):
        with self.assertRaisesRegex(main.ExtensionError, 'Location'):
            main.rossum_hook_request_handler(payload())

    def test_invalid_identity_settings(self):
        p = payload()
        p['settings']['id_keys'] = ['unknown']
        with self.assertRaises(main.ExtensionError):
            main.configuration(p['settings'])

    @patch('main.api_call')
    def test_empty_identity(self, call):
        p = payload()
        p['annotation']['content'][0]['children'][0].pop('id')
        with self.assertRaisesRegex(main.ExtensionError, 'identity field is empty'):
            main.rossum_hook_request_handler(p)
        call.assert_not_called()
