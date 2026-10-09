"""AI çıxarışı endpoint-i: /extract/ (ayrı fayllar) və /bundle/ (bir faylda bir neçə sənəd)."""
from unittest.mock import patch

from django.test import override_settings

from matching.ai import AIUnavailable
from matching.demo import document
from matching.models import Case, Document

from .helpers import PDF, CaseAPITestCase, SyncExecutor, make_xlsx, upload


@override_settings(GEMINI_API_KEY='test-key')
@patch('matching.ai.request_json')
class ExtractEndpointTests(CaseAPITestCase):
    def upload_all(self):
        for kind in ('order', 'receipt', 'invoice'):
            self.upload(kind, f'{kind}.pdf', PDF)

    def test_reads_every_uploaded_file(self, request_json):
        request_json.side_effect = lambda content, *a, **k: (document('order'), {'total_tokens': 7})
        self.upload_all()
        response = self.post('extract/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(request_json.call_count, 3)
        for doc in response.data['documents']:
            self.assertEqual((doc['extraction_status'], doc['error'], doc['usage']), ('extracted', '', {'total_tokens': 7}))

    def test_reads_only_uploaded_files_and_keeps_manual_data(self, request_json):
        request_json.return_value = (document('order'), {})
        self.upload('order', 'o.pdf', PDF)
        manual = document('invoice', '90')
        self.save_data('invoice', manual)
        response = self.post('extract/')
        self.assertEqual(request_json.call_count, 1)
        by_kind = {doc['kind']: doc for doc in response.data['documents']}
        self.assertEqual(by_kind['order']['extraction_status'], 'extracted')
        self.assertEqual((by_kind['invoice']['extraction_status'], by_kind['invoice']['data']), ('manual', manual))

    def test_failed_document_keeps_reason_others_succeed(self, request_json):
        def fake(content, schema, instruction, **kwargs):
            if 'only the receipt' in instruction:
                raise AIUnavailable('Gemini kvotası bitib.')
            return document('order'), {}
        request_json.side_effect = fake
        self.upload_all()
        by_kind = {doc['kind']: doc for doc in self.post('extract/').data['documents']}
        self.assertEqual((by_kind['receipt']['extraction_status'], by_kind['receipt']['error']), ('failed', 'Gemini kvotası bitib.'))
        self.assertEqual(by_kind['receipt']['data'], {})
        self.assertEqual(by_kind['order']['extraction_status'], 'extracted')

    def test_invalid_model_output_marks_document_failed(self, request_json):
        broken = document('order')
        broken['currency'] = 'manat'
        request_json.return_value = (broken, {})
        self.upload('order', 'o.pdf', PDF)
        doc = self.post('extract/').data['documents'][0]
        self.assertEqual(doc['extraction_status'], 'failed')
        self.assertIn('ISO', doc['error'])

    def test_long_error_is_truncated(self, request_json):
        request_json.side_effect = AIUnavailable('x' * 900)
        self.upload('order', 'o.pdf', PDF)
        self.assertEqual(len(self.post('extract/').data['documents'][0]['error']), 500)

    def test_office_file_is_read_as_text(self, request_json):
        request_json.return_value = (document('invoice'), {})
        self.upload('invoice', 'faktura.xlsx', make_xlsx())
        self.post('extract/')
        self.assertIn('A4 kağız\t100', request_json.call_args.args[0][0].text)

    def test_extraction_resets_report_and_is_audited(self, request_json):
        request_json.return_value = (document('order'), {'total_tokens': 5})
        self.fill()
        self.compare()
        self.upload('order', 'o.pdf', PDF)
        self.post('extract/')
        case = self.get_case()
        self.assertEqual((case['status'], case['report']), ('draft', {}))
        event = self.client.get(self.url + 'history/').data[-1]
        self.assertEqual(event['action'], 'extracted')
        self.assertEqual(event['payload']['documents'][0]['usage'], {'total_tokens': 5})

    def test_requires_at_least_one_file(self, request_json):
        self.fill()
        response = self.post('extract/')
        self.assertEqual(response.status_code, 400)
        request_json.assert_not_called()

    def test_without_api_key_returns_503(self, request_json):
        self.upload('order', 'o.pdf', PDF)
        with override_settings(GEMINI_API_KEY=''):
            response = self.post('extract/')
        self.assertEqual(response.status_code, 503)
        request_json.assert_not_called()

    @patch('matching.views.ThreadPoolExecutor', SyncExecutor)
    def test_documents_changed_during_extraction_returns_409(self, request_json):
        self.upload('order', 'o.pdf', PDF)
        case_id = self.case['id']

        def change_case(*args, **kwargs):
            Case.objects.filter(id=case_id).update(revision=99)
            return document('order'), {}
        request_json.side_effect = change_case
        response = self.post('extract/')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Document.objects.get(kind='order').extraction_status, 'pending')


@override_settings(GEMINI_API_KEY='test-key')
@patch('matching.ai.request_json')
class BundleEndpointTests(CaseAPITestCase):
    def send(self, name='scan.pdf', content=PDF):
        return self.client.post(self.url + 'bundle/', {'file': upload(name, content)})

    def ai_finds(self, request_json, *kinds_pages, usage=None):
        request_json.return_value = ({'documents': [
            {'kind': kind, 'pages': pages, 'data': document(kind, '80' if kind == 'receipt' else '100')}
            for kind, pages in kinds_pages]}, usage or {'total_tokens': 50})

    def test_splits_one_file_into_three_documents(self, request_json):
        self.ai_finds(request_json, ('order', [1]), ('receipt', [2]), ('invoice', [3, 4]))
        response = self.send()
        self.assertEqual(response.status_code, 200, response.data)
        by_kind = {doc['kind']: doc for doc in response.data['documents']}
        self.assertEqual(set(by_kind), {'order', 'receipt', 'invoice'})
        self.assertEqual(by_kind['invoice']['usage']['pages'], [3, 4])
        for doc in by_kind.values():
            self.assertEqual((doc['extraction_status'], doc['original_name'], doc['usage']['bundle']), ('extracted', 'scan.pdf', True))
        self.assertEqual(self.compare()['report']['disputed_amount'], '240.00')

    def test_each_document_gets_its_own_copy_of_the_file(self, request_json):
        self.ai_finds(request_json, ('order', [1]), ('invoice', [2]))
        self.send(content=PDF + b'bundle')
        files = [doc.file for doc in Document.objects.all()]
        self.assertEqual(len({file.name for file in files}), 2)
        for file in files:
            with file.open('rb') as stream:
                self.assertEqual(stream.read(), PDF + b'bundle')

    def test_kinds_not_found_in_file_are_untouched(self, request_json):
        manual = document('receipt', '80')
        self.save_data('receipt', manual)
        self.ai_finds(request_json, ('order', [1]), ('invoice', [2]))
        by_kind = {doc['kind']: doc for doc in self.send().data['documents']}
        self.assertEqual((by_kind['receipt']['extraction_status'], by_kind['receipt']['data']), ('manual', manual))

    def test_replaces_previous_file(self, request_json):
        old = Document.objects.get(id=self.upload('order', 'old.pdf', PDF).data['id']).file
        self.ai_finds(request_json, ('order', [1]))
        with self.captureOnCommitCallbacks(execute=True):
            self.send()
        self.assertFalse(old.storage.exists(old.name))

    def test_is_audited_with_pages(self, request_json):
        self.ai_finds(request_json, ('order', [1]), ('invoice', [2]))
        self.send()
        event = self.client.get(self.url + 'history/').data[-1]
        self.assertEqual(event['action'], 'bundle_extracted')
        self.assertEqual(event['payload']['documents'], [{'kind': 'order', 'pages': [1]}, {'kind': 'invoice', 'pages': [2]}])

    def test_unusable_ai_results(self, request_json):
        broken = document('order')
        broken['currency'] = 'manat'
        results = {
            'duplicate kind': (({'documents': [{'kind': 'order', 'pages': [1], 'data': document('order')}] * 2}, {}), 400),
            'nothing found': (({'documents': []}, {}), 400),
            'invalid data': (({'documents': [{'kind': 'order', 'pages': [1], 'data': broken}]}, {}), 503),
        }
        for label, (result, status) in results.items():
            with self.subTest(label):
                request_json.return_value = result
                self.assertEqual(self.send().status_code, status)
        self.assertFalse(Document.objects.exists())

    def test_ai_unavailable_returns_503(self, request_json):
        request_json.side_effect = AIUnavailable('Kvota bitib')
        response = self.send()
        self.assertEqual((response.status_code, response.data['detail']), (503, 'Kvota bitib'))

    def test_invalid_file_is_rejected_before_ai(self, request_json):
        self.assertEqual(self.send('scan.pdf', b'<html>').status_code, 400)
        self.assertEqual(self.send('scan.xlsx', b'PK\x03\x04broken').status_code, 400)
        request_json.assert_not_called()

    def test_without_api_key_returns_503(self, request_json):
        with override_settings(GEMINI_API_KEY=''):
            self.assertEqual(self.send().status_code, 503)
        request_json.assert_not_called()

    def test_documents_changed_during_reading_returns_409(self, request_json):
        case_id = self.case['id']

        def change_case(*args, **kwargs):
            Case.objects.filter(id=case_id).update(revision=99)
            return {'documents': [{'kind': 'order', 'pages': [1], 'data': document('order')}]}, {}
        request_json.side_effect = change_case
        self.assertEqual(self.send().status_code, 409)
        self.assertFalse(Document.objects.exists())
