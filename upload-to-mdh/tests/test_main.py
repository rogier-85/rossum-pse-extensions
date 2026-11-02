import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('importer', Path(__file__).parents[1] / 'main.py')
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)
BASE = 'https://example.rossum.app'
DOC = BASE + '/api/v1/documents/1'


class ImporterTests(unittest.TestCase):
    def payload(self, filename='SUPPLIERS_2026.csv'):
        return {'event': 'upload', 'action': 'created', 'base_url': BASE,
                'rossum_authorization_token': 'test-token',
                'settings': {'rules': [{'filename_contains': 'suppliers', 'dataset': 'vendor master'}]},
                'files': [{'document': DOC, 'prevent_importing': False, 'values': []}],
                'documents': [{'url': DOC, 'original_file_name': filename}]}

    def run_hook(self, payload):
        with patch.object(main, 'api_call', side_effect=[
                (200, {}, b'id,name\n1,Example\n'),
                (202, {'Location': '/operations/123'}, {})]) as api:
            result = main.rossum_hook_request_handler(payload)
        return result, api

    def test_replace_and_suppress(self):
        payload = self.payload()
        original = copy.deepcopy(payload)
        result, api = self.run_hook(payload)
        self.assertTrue(result['files'][0]['prevent_importing'])
        args = api.call_args.args
        self.assertEqual(args[2], 'PUT')
        self.assertTrue(args[0].endswith('/dataset/vendor%20master'))
        self.assertIn(b'filename="dataset.csv"', args[3])
        self.assertIn(b'Content-Type: text/csv\r\n', args[3])
        self.assertTrue(args[4].startswith('multipart/form-data; boundary='))
        self.assertIn(b'id,name\n1,Example\n', args[3])
        self.assertEqual(payload, original)

    def test_xlsx_passthrough_bytes(self):
        payload = self.payload('suppliers.xlsx')
        with patch.object(main, 'api_call', side_effect=[
                (200, {}, b'PK\x03\x04binary'), (202, {'Location': '/operation/1'}, {})]) as api:
            main.rossum_hook_request_handler(payload)
        self.assertIn(b'filename="dataset.xlsx"', api.call_args.args[3])
        self.assertIn(b'Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n', api.call_args.args[3])
        self.assertIn(b'PK\x03\x04binary', api.call_args.args[3])

    def test_unmatched_and_unsupported_untouched(self):
        for name in ('invoice.csv', 'suppliers.pdf', 'suppliers.xls'):
            with self.subTest(name=name), patch.object(main, 'api_call') as api:
                result = main.rossum_hook_request_handler(self.payload(name))
                api.assert_not_called()
                self.assertFalse(result['files'][0]['prevent_importing'])

    def test_dry_run_does_not_suppress_or_write(self):
        payload = self.payload()
        payload['settings']['dry_run'] = True
        result, api = self.run_hook(payload)
        self.assertEqual(api.call_count, 1)
        self.assertFalse(result['files'][0]['prevent_importing'])

    def test_optional_processing(self):
        payload = self.payload()
        payload['settings']['suppress_processing'] = False
        result, _ = self.run_hook(payload)
        self.assertFalse(result['files'][0]['prevent_importing'])

    def test_ambiguous_before_download(self):
        payload = self.payload()
        payload['settings']['rules'].append({'filename_contains': '2026', 'dataset': 'other'})
        with patch.object(main, 'api_call') as api, self.assertRaises(main.ExtensionError):
            main.rossum_hook_request_handler(payload)
        api.assert_not_called()

    def test_same_dataset_batch_no_write(self):
        payload = self.payload()
        payload['files'].append({'document': DOC + '2'})
        payload['documents'].append({'url': DOC + '2', 'original_file_name': 'suppliers.xlsx'})
        with patch.object(main, 'api_call', return_value=(200, {}, b'data')) as api:
            with self.assertRaises(main.ExtensionError):
                main.rossum_hook_request_handler(payload)
        self.assertTrue(all(c.kwargs.get('binary') for c in api.call_args_list))

    def test_empty_file_no_write(self):
        with patch.object(main, 'api_call', return_value=(200, {}, b'')) as api:
            with self.assertRaises(main.ExtensionError):
                main.rossum_hook_request_handler(self.payload())
        self.assertEqual(api.call_count, 1)

    def test_failed_acknowledgement(self):
        with patch.object(main, 'api_call', side_effect=[(200, {}, b'data'), (202, {}, {})]):
            with self.assertRaises(main.ExtensionError):
                main.rossum_hook_request_handler(self.payload())

    def test_fetch_missing_document_metadata(self):
        payload = self.payload()
        payload['documents'] = []
        with patch.object(main, 'api_call', side_effect=[
                (200, {}, {'original_file_name': 'suppliers.csv'}),
                (200, {}, b'data'), (202, {'Location': '/op/1'}, {})]) as api:
            main.rossum_hook_request_handler(payload)
        self.assertEqual(api.call_args_list[0].args[0], DOC)

    def test_unsafe_document_origin(self):
        payload = self.payload()
        payload['files'][0]['document'] = 'https://attacker.example/api/v1/documents/1'
        with patch.object(main, 'api_call') as api, self.assertRaises(main.ExtensionError):
            main.rossum_hook_request_handler(payload)
        api.assert_not_called()

    def test_email_event_ignored_to_prevent_double_import(self):
        with patch.object(main, 'api_call') as api:
            self.assertEqual(main.rossum_hook_request_handler({'event': 'email', 'action': 'received'}), {})
        api.assert_not_called()


if __name__ == '__main__':
    unittest.main()
