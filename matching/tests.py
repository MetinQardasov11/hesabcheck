import copy
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase
from rest_framework.exceptions import ValidationError
from .models import Case, Document
from .demo import document
from .engine import compare
from .schemas import validate_data
from .ai import AIUnavailable

def docs(received='100', invoiced='100', price='12.00'):
    return [SimpleNamespace(id=k, kind=k, data=document(k, quantity=received if k == 'receipt' else invoiced if k == 'invoice' else '100', price=price if k == 'invoice' else '12.00')) for k in ['order', 'receipt', 'invoice']]

class MatchingTests(SimpleTestCase):
    def test_short_delivery_240(self):
        result = compare(docs('80'))
        self.assertEqual((result['status'], result['disputed_amount']), ('mismatch', '240.00'))
        self.assertTrue(result['amount_complete'])
    def test_matching(self):
        self.assertEqual(compare(docs())['status'], 'matched')
    def test_price_and_quantity_no_double_count(self):
        self.assertEqual(compare(docs('80', price='13'))['disputed_amount'], '340.00')
    def test_currency_not_summed(self):
        rows = docs('80'); rows[-1].data['currency'] = 'USD'
        result = compare(rows)
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(result['disputed_amount'], '0.00')
    def test_missing_quantity(self):
        rows = docs(); rows[1].data['lines'][0]['quantity'] = None
        self.assertEqual(compare(rows)['status'], 'needs_review')
    def test_packaging(self):
        rows = docs(); rows[1].data['lines'][0]['pack_size'] = '10'
        self.assertEqual(compare(rows)['status'], 'needs_review')
    def test_receipt_without_unit_uses_order_and_invoice_unit(self):
        rows = docs('80'); rows[1].data['lines'][0]['unit'] = None
        self.assertEqual(compare(rows)['disputed_amount'], '240.00')
        rows[0].data['lines'][0]['unit'] = None
        self.assertEqual(compare(rows)['status'], 'needs_review')
    def test_vat_zero_is_fine_positive_needs_review(self):
        rows = docs('80'); rows[2].data['tax_total'] = '0.00'
        self.assertEqual(compare(rows)['status'], 'mismatch')
        rows[2].data['tax_total'] = '216.00'
        result = compare(rows)
        self.assertEqual(result['status'], 'needs_review')
        self.assertIn('tax_review', [issue['code'] for issue in result['issues']])
    def test_pack_size_not_stated_anywhere(self):
        rows = docs('80')
        for doc in rows:
            doc.data['lines'][0]['pack_size'] = None
        self.assertEqual((compare(rows)['status'], compare(rows)['disputed_amount']), ('mismatch', '240.00'))
        rows[0].data['lines'][0]['pack_size'] = '12'
        self.assertEqual(compare(rows)['status'], 'needs_review')
    def test_missing_document(self):
        self.assertEqual(compare(docs()[:2])['status'], 'needs_review')
    def test_missing_evidence(self):
        rows = docs(); rows[0].data['lines'][0]['source']['quantity']['page'] = None
        self.assertEqual(compare(rows)['status'], 'needs_review')
    def test_empty_document(self):
        rows = docs(); rows[1].data['lines'] = []
        self.assertEqual(compare(rows)['status'], 'needs_review')
    def test_duplicate_sku_requires_review(self):
        rows = docs(); rows[0].data['lines'] *= 2
        self.assertEqual(compare(rows)['status'], 'needs_review')
    def test_unknown_names_manual_mapping(self):
        rows = docs()
        for i, doc in enumerate(rows):
            doc.data['lines'][0]['sku'] = None
            doc.data['lines'][0]['name'] = f'Name in language {i}'
        self.assertEqual(compare(rows)['status'], 'needs_review')
        self.assertEqual(compare(rows, [{'order': 0, 'receipt': 0, 'invoice': 0}])['status'], 'matched')
    def test_unit_synonyms_across_languages(self):
        rows = docs('80')
        for doc, unit in zip(rows, ['qutu', 'кор.', 'Box']):
            doc.data['lines'][0]['unit'] = unit
        self.assertEqual(compare(rows)['disputed_amount'], '240.00')
        rows[2].data['lines'][0]['unit'] = 'kg'
        self.assertEqual(compare(rows)['status'], 'needs_review')
    def test_receipt_without_prices_or_currency(self):
        rows = docs('80')
        receipt = rows[1].data
        receipt['currency'] = receipt['total'] = None
        for field in ('unit_price', 'line_total'):
            receipt['lines'][0][field] = None
        result = compare(rows)
        self.assertEqual((result['status'], result['disputed_amount'], result['currency']), ('mismatch', '240.00', 'AZN'))
    def test_two_way_without_receipt(self):
        order, _, invoice = docs(price='13')
        result = compare([order, invoice])
        self.assertEqual((result['mode'], result['status'], result['disputed_amount']), ('two_way', 'mismatch', '100.00'))
        self.assertNotIn('receipt', result['matches'][0]['sources'])
        self.assertTrue(result['notes'])
        order, _, invoice = docs()
        self.assertEqual(compare([order, invoice])['status'], 'matched')
    def test_two_way_quantity_overbilling(self):
        order, _, invoice = docs()
        invoice.data['lines'][0]['quantity'] = '110'
        invoice.data['lines'][0]['line_total'] = invoice.data['total'] = '1320.00'
        result = compare([order, invoice])
        self.assertEqual((result['status'], result['disputed_amount']), ('mismatch', '120.00'))
    def test_unreadable_receipt_does_not_fall_back_to_two_way(self):
        rows = docs(); rows[1].data = {}
        result = compare(rows)
        self.assertEqual((result['mode'], result['status']), ('three_way', 'needs_review'))
    def test_two_way_requires_order_and_invoice(self):
        result = compare(docs()[:1])
        self.assertEqual((result['status'], result['issues'][0]['code']), ('needs_review', 'missing_document'))
    def test_two_way_mapping_keys(self):
        order, _, invoice = docs()
        self.assertEqual(compare([order, invoice], [{'order': 0, 'invoice': 0}])['status'], 'matched')
        with self.assertRaises(ValueError):
            compare([order, invoice], [{'order': 0, 'receipt': 0, 'invoice': 0}])
    def test_mapping_reuse_or_invalid(self):
        mapping = {'order': 0, 'receipt': 0, 'invoice': 0}
        with self.assertRaises(ValueError):
            compare(docs(), [mapping, mapping])
        with self.assertRaises(ValueError):
            compare(docs(), [{'order': -1, 'receipt': 0, 'invoice': 0}])
    def test_arithmetic(self):
        rows = docs(); rows[-1].data['lines'][0]['line_total'] = '1201'
        self.assertEqual(compare(rows)['status'], 'mismatch')
        self.assertFalse(compare(rows)['amount_complete'])
    def test_invalid_numeric_data(self):
        for bad in ['NaN', 'Infinity', '-1', '1e9999', '0.00000001']:
            data = document('order'); data['lines'][0]['quantity'] = bad
            with self.assertRaises(ValidationError):
                validate_data(data)
    def test_warning_never_auto_approved(self):
        rows = docs(); rows[-1].data['warnings'] = ['ƏDV daxildir']
        self.assertEqual(compare(rows)['status'], 'needs_review')
    def test_fractional_amount(self):
        rows = docs()
        for doc in rows:
            doc.data = document(doc.kind, '0.3', '0.1')
        self.assertEqual(compare(rows)['status'], 'matched')

class WorkflowTests(APITestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.setting = override_settings(MEDIA_ROOT=self.temp.name)
        self.setting.enable()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.setting.disable)
        self.user = get_user_model().objects.create_user(username='tester', password='Strong-test-pass-391')
        self.client.force_authenticate(self.user)
        response = self.client.post('/api/cases/', {'title': 'Test', 'supplier': 'Vendor'})
        self.assertEqual(response.status_code, 201)
        self.url = f'/api/cases/{response.data["id"]}/'
    def fill(self, received='80'):
        for kind in ['order', 'receipt', 'invoice']:
            response = self.client.post(self.url + 'document-data/', {'kind': kind, 'data': document(kind, received if kind == 'receipt' else '100'), 'note': 'Manual input'}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
    def test_end_to_end_and_audit(self):
        self.fill()
        response = self.client.post(self.url + 'compare/', {}, format='json')
        self.assertEqual(response.data['report']['disputed_amount'], '240.00')
        revision = response.data['revision']
        response = self.client.post(self.url + 'review/', {'revision': revision, 'decision': 'disputed', 'note': '20 ədəd çatışmır'}, format='json')
        self.assertEqual(response.status_code, 200)
        letter = self.client.post(self.url + 'dispute-letter/').data
        self.assertIn('240.00', letter['body'])
        self.assertFalse(letter['sent'])
        self.assertEqual(self.client.get(self.url + 'report/').status_code, 200)
        self.assertGreaterEqual(len(self.client.get(self.url + 'history/').data), 6)
    def test_correction_invalidates_report(self):
        self.fill('100')
        result = self.client.post(self.url + 'compare/', {}, format='json').data
        self.assertEqual(self.client.post(self.url + 'review/', {'revision': result['revision'], 'decision': 'approved', 'note': 'Yoxlanıb'}, format='json').status_code, 200)
        self.client.post(self.url + 'document-data/', {'kind': 'receipt', 'data': document('receipt', '80'), 'note': 'Correction'}, format='json')
        case = self.client.get(self.url).data
        self.assertEqual((case['report'], case['decision'], case['status']), ({}, '', 'draft'))
    def test_access_isolation(self):
        other = get_user_model().objects.create_user(username='other')
        self.client.force_authenticate(other)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url + 'compare/').status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/cases/').status_code, 401)
    def test_stale_or_false_approval(self):
        self.fill()
        result = self.client.post(self.url + 'compare/', {}, format='json').data
        self.assertEqual(self.client.post(self.url + 'review/', {'revision': 0, 'decision': 'disputed', 'note': 'Old'}, format='json').status_code, 409)
        self.assertEqual(self.client.post(self.url + 'review/', {'revision': result['revision'], 'decision': 'approved', 'note': 'Bad'}, format='json').status_code, 400)
    def test_upload_signature_and_download(self):
        bad = SimpleUploadedFile('evil.pdf', b'<html>bad</html>')
        self.assertEqual(self.client.post(self.url + 'documents/', {'kind': 'order', 'file': bad}).status_code, 400)
        good = SimpleUploadedFile('order.txt', b'100 units 12 AZN')
        response = self.client.post(self.url + 'documents/', {'kind': 'order', 'file': good})
        self.assertEqual(response.status_code, 201)
        download = self.client.get(self.url + f'documents/{response.data["id"]}/download/')
        self.assertEqual(b''.join(download.streaming_content), b'100 units 12 AZN')
        download.close()
    def test_delete_case_removes_documents_and_files(self):
        response = self.client.post(self.url + 'documents/', {'kind': 'order', 'file': SimpleUploadedFile('order.txt', b'100 units')})
        stored = Document.objects.get(id=response.data['id']).file
        other = get_user_model().objects.create_user(username='other')
        self.client.force_authenticate(other)
        self.assertEqual(self.client.delete(self.url).status_code, 404)
        self.client.force_authenticate(self.user)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.delete(self.url).status_code, 204)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertFalse(Case.objects.exists() or Document.objects.exists())
        self.assertFalse(stored.storage.exists(stored.name))
    @override_settings(GEMINI_API_KEY='')
    def test_no_key_is_explicit(self):
        file = SimpleUploadedFile('order.txt', b'100 units')
        self.client.post(self.url + 'documents/', {'kind': 'order', 'file': file})
        self.assertEqual(self.client.post(self.url + 'extract/').status_code, 503)
    @override_settings(GEMINI_API_KEY='test')
    @patch('matching.ai.extract', side_effect=AIUnavailable('Unreadable document'))
    def test_ai_failure_needs_review(self, mock):
        self.client.post(self.url + 'documents/', {'kind': 'order', 'file': SimpleUploadedFile('order.txt', b'?')})
        result = self.client.post(self.url + 'extract/').data
        self.assertEqual(result['documents'][0]['extraction_status'], 'failed')
        result = self.client.post(self.url + 'compare/', {}, format='json').data
        self.assertEqual(result['status'], 'needs_review')
    def test_token_login(self):
        self.client.force_authenticate(None)
        result = self.client.post('/api/auth/token/', {'username': 'tester', 'password': 'Strong-test-pass-391'})
        self.assertEqual(result.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + result.data['token'])
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_manual_mapping_requires_current_revision(self):
        self.fill()
        mappings = [{'order': 0, 'receipt': 0, 'invoice': 0}]
        self.assertEqual(self.client.post(self.url + 'compare/', {'mappings': mappings}, format='json').status_code, 409)
        revision = self.client.get(self.url).data['revision']
        result = self.client.post(self.url + 'compare/', {'revision': revision, 'mappings': mappings}, format='json')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['report']['disputed_amount'], '240.00')

    @override_settings(GEMINI_API_KEY='test')
    @patch('matching.ai.request_json')
    def test_successful_extraction(self, mock):
        mock.return_value = (document('order'), {'input_tokens': 10, 'output_tokens': 20})
        self.client.post(self.url + 'documents/', {'kind': 'order', 'file': SimpleUploadedFile('order.txt', b'100 units')})
        response = self.client.post(self.url + 'extract/')
        self.assertEqual(response.status_code, 200)
        extracted = response.data['documents'][0]
        self.assertEqual(extracted['extraction_status'], 'extracted')
        self.assertEqual(extracted['usage']['input_tokens'], 10)
        self.assertEqual(extracted['data']['lines'][0]['quantity'], '100')

    def test_two_way_workflow_and_letter(self):
        for kind, quantity in [('order', '100'), ('invoice', '110')]:
            data = document(kind, quantity)
            self.client.post(self.url + 'document-data/', {'kind': kind, 'data': data, 'note': 'Manual'}, format='json')
        result = self.client.post(self.url + 'compare/', {}, format='json').data
        self.assertEqual((result['report']['mode'], result['report']['disputed_amount']), ('two_way', '120.00'))
        result = self.client.post(self.url + 'compare/', {'revision': result['revision'], 'mappings': [{'order': 0, 'invoice': 0}]}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        letter = self.client.post(self.url + 'dispute-letter/').data
        self.assertIn('sifariş 100, faktura 110', letter['body'])
        self.assertIn('sifariş və faktura əsasında', letter['body'])

    @override_settings(GEMINI_API_KEY='test')
    @patch('matching.ai.request_json')
    def test_two_way_suggestions(self, mock):
        for kind in ['order', 'invoice']:
            self.client.post(self.url + 'document-data/', {'kind': kind, 'data': document(kind), 'note': 'Manual'}, format='json')
        mock.return_value = ({'suggestions': [{'order': 0, 'invoice': 0, 'reason': 'Eyni məhsul', 'confidence': 0.9}]}, {})
        response = self.client.post(self.url + 'suggestions/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn('receipt', mock.call_args.args[1]['properties']['suggestions']['items']['properties'])
        self.assertIn('"index": 0', mock.call_args.args[0][0].text)

    @override_settings(GEMINI_API_KEY='test')
    @patch('matching.ai.request_json')
    def test_invalid_suggestions_are_dropped_individually(self, mock):
        self.fill()
        mock.return_value = ({'suggestions': [
            {'order': 1, 'receipt': 1, 'invoice': 1, 'reason': 'Diapazondan kənar', 'confidence': 0.99},
            {'order': 0, 'receipt': 0, 'invoice': 0, 'reason': 'Eyni məhsul', 'confidence': 0.9},
            {'order': 0, 'receipt': 0, 'invoice': 0, 'reason': 'Təkrar', 'confidence': 0.5},
            {'order': 0, 'receipt': 0, 'invoice': 0, 'reason': 'Səhv əminlik', 'confidence': 7},
        ]}, {})
        data = self.client.post(self.url + 'suggestions/').data
        self.assertEqual([item['reason'] for item in data['suggestions']], ['Eyni məhsul'])
        self.assertEqual(data['discarded'], 3)

    @override_settings(GEMINI_API_KEY='test')
    @patch('matching.ai.request_json')
    def test_extract_only_uploaded_files_keeps_manual_data(self, mock):
        mock.return_value = (document('order'), {'input_tokens': 1})
        self.client.post(self.url + 'documents/', {'kind': 'order', 'file': SimpleUploadedFile('order.txt', b'100 units')})
        manual = document('invoice', '90')
        self.client.post(self.url + 'document-data/', {'kind': 'invoice', 'data': manual, 'note': 'Manual'}, format='json')
        response = self.client.post(self.url + 'extract/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(mock.call_count, 1)
        by_kind = {doc['kind']: doc for doc in response.data['documents']}
        self.assertEqual(by_kind['order']['extraction_status'], 'extracted')
        self.assertEqual((by_kind['invoice']['extraction_status'], by_kind['invoice']['data']), ('manual', manual))
        self.assertIn('extract only the order document', mock.call_args.args[2])

    def test_extract_requires_a_file(self):
        self.client.post(self.url + 'document-data/', {'kind': 'order', 'data': document('order'), 'note': 'Manual'}, format='json')
        with override_settings(GEMINI_API_KEY='test'):
            self.assertEqual(self.client.post(self.url + 'extract/').status_code, 400)

    @override_settings(GEMINI_API_KEY='test')
    @patch('matching.ai.request_json')
    def test_bundle_splits_documents(self, mock):
        mock.return_value = ({'documents': [
            {'kind': 'order', 'pages': [1], 'data': document('order')},
            {'kind': 'receipt', 'pages': [2], 'data': document('receipt', '80')},
            {'kind': 'invoice', 'pages': [3, 4], 'data': document('invoice')},
        ]}, {'total_tokens': 50})
        bundle = SimpleUploadedFile('scan.pdf', b'%PDF-bundle')
        response = self.client.post(self.url + 'bundle/', {'file': bundle})
        self.assertEqual(response.status_code, 200, response.data)
        by_kind = {doc['kind']: doc for doc in response.data['documents']}
        self.assertEqual(set(by_kind), {'order', 'receipt', 'invoice'})
        self.assertEqual(by_kind['invoice']['usage']['pages'], [3, 4])
        self.assertTrue(all(doc['usage']['bundle'] and doc['extraction_status'] == 'extracted' for doc in by_kind.values()))
        self.assertEqual(response.data['status'], 'draft')
        result = self.client.post(self.url + 'compare/', {}, format='json').data
        self.assertEqual(result['report']['disputed_amount'], '240.00')
        self.assertEqual(self.client.get(self.url + 'history/').data[-2]['action'], 'bundle_extracted')
        # Fayl birbaşa yaddaşdan yoxlanır: streaming cavabın bağlanması PostgreSQL-də test bağlantısını bağlayır.
        with Document.objects.get(id=by_kind['receipt']['id']).file.open('rb') as stored:
            self.assertEqual(stored.read(), b'%PDF-bundle')

    @override_settings(GEMINI_API_KEY='test')
    @patch('matching.ai.request_json')
    def test_bundle_partial_keeps_other_documents(self, mock):
        manual = document('receipt', '80')
        self.client.post(self.url + 'document-data/', {'kind': 'receipt', 'data': manual, 'note': 'Manual'}, format='json')
        mock.return_value = ({'documents': [{'kind': 'order', 'pages': [1], 'data': document('order')},
                                            {'kind': 'invoice', 'pages': [2], 'data': document('invoice')}]}, {})
        response = self.client.post(self.url + 'bundle/', {'file': SimpleUploadedFile('scan.txt', 'Sifariş və faktura'.encode())})
        by_kind = {doc['kind']: doc for doc in response.data['documents']}
        self.assertEqual((by_kind['receipt']['extraction_status'], by_kind['receipt']['data']), ('manual', manual))

    @override_settings(GEMINI_API_KEY='test')
    @patch('matching.ai.request_json')
    def test_bundle_rejects_duplicates_empty_and_invalid(self, mock):
        upload = lambda: self.client.post(self.url + 'bundle/', {'file': SimpleUploadedFile('scan.txt', b'text')})
        mock.return_value = ({'documents': [{'kind': 'order', 'pages': [1], 'data': document('order')}] * 2}, {})
        self.assertEqual(upload().status_code, 400)
        mock.return_value = ({'documents': []}, {})
        self.assertEqual(upload().status_code, 400)
        broken = document('order'); broken['currency'] = 'manat'
        mock.return_value = ({'documents': [{'kind': 'order', 'pages': [1], 'data': broken}]}, {})
        self.assertEqual(upload().status_code, 503)
        self.assertFalse(Document.objects.exists())
        mock.side_effect = AIUnavailable('Kvota bitib')
        self.assertEqual(upload().status_code, 503)
        bad = self.client.post(self.url + 'bundle/', {'file': SimpleUploadedFile('scan.pdf', b'<html>')})
        self.assertEqual(bad.status_code, 400)

    @override_settings(GEMINI_API_KEY='')
    def test_bundle_without_key(self):
        response = self.client.post(self.url + 'bundle/', {'file': SimpleUploadedFile('scan.txt', b'text')})
        self.assertEqual(response.status_code, 503)

    def test_seed_demo_command(self):
        from io import StringIO
        from django.core.management import call_command
        output = StringIO()
        call_command('seed_demo', username='tester', stdout=output)
        self.assertIn('mismatch: 240.00 AZN', output.getvalue())
        self.assertIn('needs_review:', output.getvalue())
        self.assertIn('matched:', output.getvalue())

@override_settings(GEMINI_API_KEY='test-key', GEMINI_MODEL='gemini-test')
class GeminiAdapterTests(SimpleTestCase):
    def response(self, text='{"ok": true}', finish='STOP'):
        from google.genai import types
        return types.GenerateContentResponse(
            candidates=[types.Candidate(finish_reason=finish, content=types.Content(parts=[types.Part.from_text(text=text)]))],
            usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=10, candidates_token_count=5, total_token_count=15),
        )

    def call(self):
        from .ai import request_json
        from .schemas import obj
        from google.genai import types
        return request_json([types.Part.from_text(text='test')], obj({'ok': {'type': 'boolean'}}), 'Extract.')

    @patch('matching.ai.genai.Client')
    def test_json_and_usage(self, mock):
        client = mock.return_value.__enter__.return_value
        client.models.generate_content.return_value = self.response()
        data, usage = self.call()
        self.assertEqual(data, {'ok': True})
        self.assertEqual(usage['total_tokens'], 15)
        self.assertEqual(usage['provider'], 'gemini')
        self.assertFalse(mock.call_args.kwargs['vertexai'])
        self.assertEqual(client.models.generate_content.call_args.kwargs['config'].response_mime_type, 'application/json')

    @patch('matching.ai.genai.Client')
    def test_truncated_blocked_and_malformed_responses(self, mock):
        client = mock.return_value.__enter__.return_value
        from google.genai import types
        for response in [self.response(finish='MAX_TOKENS'), types.GenerateContentResponse(candidates=[]),
                         self.response('not JSON'), self.response('{"ok": "wrong type"}')]:
            with self.subTest(response=response):
                client.models.generate_content.return_value = response
                with self.assertRaises(AIUnavailable):
                    self.call()

    @patch('matching.ai.genai.Client')
    def test_max_tokens_is_explained(self, mock):
        mock.return_value.__enter__.return_value.models.generate_content.return_value = self.response(finish='MAX_TOKENS')
        with self.assertRaises(AIUnavailable) as caught:
            self.call()
        self.assertIn('çox böyükdür', str(caught.exception))

    @patch('matching.ai.genai.Client')
    def test_quota_error_is_safe_and_actionable(self, mock):
        from google.genai import errors
        mock.return_value.__enter__.return_value.models.generate_content.side_effect = errors.ClientError(
            429, {'error': {'message': 'sensitive-provider-detail'}})
        with self.assertRaises(AIUnavailable) as caught:
            self.call()
        self.assertIn('kvotası', str(caught.exception))
        self.assertNotIn('sensitive-provider-detail', str(caught.exception))

    @patch('matching.ai.genai.Client')
    def test_timeout(self, mock):
        import httpx
        mock.return_value.__enter__.return_value.models.generate_content.side_effect = httpx.ReadTimeout('secret')
        with self.assertRaises(AIUnavailable) as caught:
            self.call()
        self.assertIn('vaxt limiti', str(caught.exception))

    @patch('matching.ai.request_json')
    def test_pdf_and_image_parts(self, mock):
        from django.core.files.base import ContentFile
        from .ai import extract
        mock.return_value = (document('order'), {})
        for name, raw, mime in [('order.pdf', b'%PDF-test', 'application/pdf'), ('order.png', b'png-bytes', 'image/png')]:
            doc = SimpleNamespace(file=ContentFile(raw), original_name=name, kind='order')
            extract(doc)
            part = mock.call_args.args[0][0]
            self.assertEqual(part.inline_data.mime_type, mime)
            self.assertEqual(part.inline_data.data, raw)

    @override_settings(GEMINI_API_KEY='')
    @patch('matching.ai.genai.Client')
    def test_smoke_command_without_key_makes_no_request(self, mock):
        from django.core.management import call_command, CommandError
        with self.assertRaises(CommandError):
            call_command('check_gemini')
        mock.assert_not_called()

def make_xlsx():
    import io
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Faktura'
    sheet.append(['Məhsul', 'Miqdar', 'Qiymət'])
    sheet.append(['A4 kağız', 100, 12.5])
    workbook.create_sheet('Qeyd').append(['ƏDV yoxdur'])
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()

def make_docx():
    import io, zipfile
    xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           '<w:p><w:r><w:t>Sifariş № 7</w:t></w:r></w:p><w:tbl>'
           '<w:tr><w:tc><w:p><w:r><w:t>Məhsul</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Miqdar</w:t></w:r></w:p></w:tc></w:tr>'
           '<w:tr><w:tc><w:p><w:r><w:t>Qələm</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>200</w:t></w:r></w:p></w:tc></w:tr>'
           '</w:tbl></w:body></w:document>')
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('word/document.xml', xml)
    return stream.getvalue()

class ConversionTests(SimpleTestCase):
    def test_xlsx_sheets_become_pages(self):
        from .convert import to_text
        text = to_text(make_xlsx(), 'invoice.xlsx')
        self.assertIn('=== Page 1: sheet "Faktura" ===', text)
        self.assertIn('A4 kağız\t100\t12.5', text)
        self.assertIn('=== Page 2: sheet "Qeyd" ===', text)
    def test_docx_paragraphs_and_tables(self):
        from .convert import to_text
        text = to_text(make_docx(), 'order.docx')
        self.assertIn('Sifariş № 7', text)
        self.assertIn('[table]\nMəhsul\tMiqdar\nQələm\t200\n[/table]', text)
    def test_csv_semicolon_and_bom(self):
        from .convert import to_text
        text = to_text('\ufeffMəhsul;Miqdar\nQələm;200\n'.encode('utf-8'), 'receipt.csv')
        self.assertIn('Qələm\t200', text)
    def test_broken_files(self):
        from .convert import to_text, ConversionError
        for name, raw in [('a.xlsx', b'PK\x03\x04broken'), ('a.docx', b'PK\x03\x04broken'), ('a.xls', b'\xd0\xcf\x11\xe0broken'),
                          ('a.csv', b'\xff\xfe\x00bad')]:
            with self.subTest(name=name), self.assertRaises(ConversionError):
                to_text(raw, name)
    @patch('matching.ai.request_json')
    def test_office_files_sent_as_text(self, mock):
        from django.core.files.base import ContentFile
        from .ai import extract
        mock.return_value = (document('invoice'), {})
        extract(SimpleNamespace(file=ContentFile(make_xlsx()), original_name='invoice.xlsx', kind='invoice'))
        part = mock.call_args.args[0][0]
        self.assertIn('A4 kağız', part.text)
        self.assertEqual(mock.call_args.kwargs['max_output_tokens'], 65536)
    @patch('matching.ai.request_json')
    def test_tax_warnings_dropped_when_tax_is_structured(self, mock):
        from django.core.files.base import ContentFile
        from .ai import extract
        data = document('order'); data['tax_total'] = '0.00'
        data['warnings'] = ['ƏDV tətbiq olunmuşdur.', 'VAT present', 'Səhifə 2 oxunmur']
        mock.return_value = (data, {})
        result, _ = extract(SimpleNamespace(file=ContentFile(b'x'), original_name='o.txt', kind='order'))
        self.assertEqual(result['warnings'], ['Səhifə 2 oxunmur'])
        legacy = document('order'); legacy['warnings'] = ['ƏDV var']
        mock.return_value = (legacy, {})
        self.assertEqual(extract(SimpleNamespace(file=ContentFile(b'x'), original_name='o.txt', kind='order'))[0]['warnings'], ['ƏDV var'])
    @patch('matching.ai.request_json')
    def test_phone_photo_types(self, mock):
        from django.core.files.base import ContentFile
        from .ai import extract
        mock.return_value = (document('receipt'), {})
        for name, mime in [('photo.heic', 'image/heic'), ('photo.webp', 'image/webp')]:
            extract(SimpleNamespace(file=ContentFile(b'bytes'), original_name=name, kind='receipt'))
            self.assertEqual(mock.call_args.args[0][0].inline_data.mime_type, mime)

class OfficeUploadTests(APITestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.setting = override_settings(MEDIA_ROOT=self.temp.name)
        self.setting.enable()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.setting.disable)
        self.user = get_user_model().objects.create_user(username='office')
        self.client.force_authenticate(self.user)
        self.url = f'/api/cases/{self.client.post("/api/cases/", {"title": "Office"}).data["id"]}/'
    def upload(self, name, raw, kind='invoice'):
        return self.client.post(self.url + 'documents/', {'kind': kind, 'file': SimpleUploadedFile(name, raw)})
    def test_accepts_office_and_photo_formats(self):
        heic = b'\x00\x00\x00\x18ftypheic' + b'\x00' * 8
        webp = b'RIFF\x00\x00\x00\x00WEBPVP8 '
        for name, raw in [('i.xlsx', make_xlsx()), ('o.docx', make_docx()), ('r.csv', 'a;b\n1;2'.encode()),
                          ('p.heic', heic), ('p.webp', webp)]:
            with self.subTest(name=name):
                self.assertEqual(self.upload(name, raw).status_code, 201)
    def test_rejects_broken_disguised_and_legacy_files(self):
        for name, raw in [('i.xlsx', b'PK\x03\x04broken'), ('i.xlsx', b'%PDF-'), ('o.doc', b'\xd0\xcf\x11\xe0'),
                          ('p.heic', b'not an image'), ('x.exe', b'MZ')]:
            with self.subTest(name=name):
                self.assertEqual(self.upload(name, raw).status_code, 400)
    @override_settings(GEMINI_API_KEY='test')
    @patch('matching.ai.request_json')
    def test_three_documents_extracted(self, mock):
        mock.side_effect = lambda content, *args, **kwargs: (document('order'), {})
        for kind, (name, raw) in zip(['order', 'receipt', 'invoice'], [('o.docx', make_docx()), ('r.csv', b'a;b'), ('i.xlsx', make_xlsx())]):
            self.upload(name, raw, kind)
        response = self.client.post(self.url + 'extract/')
        self.assertEqual(mock.call_count, 3)
        self.assertTrue(all(doc['extraction_status'] == 'extracted' for doc in response.data['documents']))

class HealthTests(APITestCase):
    def test_public_health_contains_release(self):
        with override_settings(APP_RELEASE='test-commit'):
            result = self.client.get('/health/')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['release'], 'test-commit')

    def test_root_opens_documentation(self):
        self.assertRedirects(self.client.get('/'), '/api/docs/', fetch_redirect_response=False)

    @patch('matching.health.connection.cursor')
    def test_unavailable_database_is_not_healthy(self, cursor):
        from django.db import DatabaseError
        cursor.side_effect = DatabaseError('private connection details')
        result = self.client.get('/health/')
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json(), {'status': 'unavailable'})
