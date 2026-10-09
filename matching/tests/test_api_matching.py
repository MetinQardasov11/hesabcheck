"""Müqayisə (/compare/) və AI uyğunlaşdırma təklifləri (/suggestions/) endpoint-ləri."""
from unittest.mock import patch

from django.test import override_settings

from matching.ai import AIUnavailable
from matching.demo import document

from .helpers import CaseAPITestCase, without_prices


class CompareEndpointTests(CaseAPITestCase):
    def test_automatic_comparison_finds_short_delivery(self):
        self.fill()
        case = self.compare()
        report = case['report']
        self.assertEqual((case['status'], report['status'], report['mode']), ('mismatch', 'mismatch', 'three_way'))
        self.assertEqual((report['disputed_amount'], report['currency'], report['amount_complete']), ('240.00', 'AZN', True))

    def test_report_metadata(self):
        self.fill()
        case = self.compare()
        report = case['report']
        self.assertEqual(report['revision'], case['revision'])
        self.assertIn('generated_at', report)
        self.assertIn('Vergi', report['scope'])

    def test_each_comparison_increments_revision(self):
        self.fill()
        first = self.compare()['revision']
        self.assertEqual(self.compare()['revision'], first + 1)

    def test_comparison_without_documents_needs_review(self):
        report = self.compare()['report']
        self.assertEqual((report['status'], report['issues'][0]['code']), ('needs_review', 'missing_document'))

    def test_comparison_clears_previous_decision(self):
        self.fill()
        compared = self.compare()
        self.post('review/', {'revision': compared['revision'], 'decision': 'disputed', 'note': 'Etiraz'})
        self.assertEqual(self.compare()['decision'], '')

    def test_real_receipt_without_prices(self):
        self.fill(kinds=('order', 'invoice'))
        self.save_data('receipt', without_prices(document('receipt', '80')))
        self.assertEqual(self.compare()['report']['disputed_amount'], '240.00')

    def test_manual_mapping_requires_current_revision(self):
        self.fill()
        mappings = [{'order': 0, 'receipt': 0, 'invoice': 0}]
        self.assertEqual(self.post('compare/', {'mappings': mappings}).status_code, 409)
        self.assertEqual(self.post('compare/', {'revision': 0, 'mappings': mappings}).status_code, 409)
        revision = self.get_case()['revision']
        report = self.compare({'revision': revision, 'mappings': mappings})['report']
        self.assertEqual(report['disputed_amount'], '240.00')

    def test_invalid_manual_mapping_returns_400(self):
        self.fill()
        revision = self.get_case()['revision']
        for mappings in ([{'order': 5, 'receipt': 0, 'invoice': 0}],
                         [{'order': 0, 'receipt': 0, 'invoice': 0}] * 2,
                         [{'order': -1, 'receipt': 0, 'invoice': 0}],
                         [{'order': 0, 'invoice': 0}]):
            with self.subTest(mappings=mappings):
                self.assertEqual(self.post('compare/', {'revision': revision, 'mappings': mappings}).status_code, 400)

    def test_two_way_comparison_without_receipt(self):
        self.fill(kinds=('order', 'invoice'), invoice='110')
        case = self.compare()
        self.assertEqual((case['report']['mode'], case['report']['disputed_amount']), ('two_way', '120.00'))
        report = self.compare({'revision': case['revision'], 'mappings': [{'order': 0, 'invoice': 0}]})['report']
        self.assertEqual(report['matches'][0]['disputed_amount'], '120.00')

    def test_comparison_is_audited_with_report(self):
        self.fill()
        self.compare()
        event = self.client.get(self.url + 'history/').data[-1]
        self.assertEqual((event['action'], event['payload']['disputed_amount']), ('compared', '240.00'))


@override_settings(GEMINI_API_KEY='test-key')
@patch('matching.ai.request_json')
class SuggestionsEndpointTests(CaseAPITestCase):
    def test_returns_filtered_suggestions_for_human_confirmation(self, request_json):
        self.fill()
        request_json.return_value = ({'suggestions': [
            {'order': 0, 'receipt': 0, 'invoice': 0, 'reason': 'Eyni məhsul', 'confidence': 0.95},
            {'order': 3, 'receipt': 0, 'invoice': 0, 'reason': 'Diapazondan kənar', 'confidence': 0.99},
        ]}, {'total_tokens': 4})
        response = self.post('suggestions/')
        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertEqual([item['reason'] for item in data['suggestions']], ['Eyni məhsul'])
        self.assertEqual(data['discarded'], 1)
        self.assertTrue(data['requires_human_confirmation'])
        self.assertEqual(data['revision'], self.get_case()['revision'])
        self.assertEqual(data['usage'], {'total_tokens': 4})

    def test_suggestions_are_not_applied_automatically(self, request_json):
        self.fill()
        request_json.return_value = ({'suggestions': [{'order': 0, 'receipt': 0, 'invoice': 0, 'reason': 'x', 'confidence': 1}]}, {})
        before = self.get_case()
        self.post('suggestions/')
        after = self.get_case()
        self.assertEqual((after['revision'], after['report']), (before['revision'], before['report']))

    def test_two_way_suggestions_omit_receipt(self, request_json):
        self.fill(kinds=('order', 'invoice'))
        request_json.return_value = ({'suggestions': [{'order': 0, 'invoice': 0, 'reason': 'x', 'confidence': 0.9}]}, {})
        self.assertEqual(self.post('suggestions/').status_code, 200)
        properties = request_json.call_args.args[1]['properties']['suggestions']['items']['properties']
        self.assertNotIn('receipt', properties)

    def test_requires_document_data(self, request_json):
        self.fill(kinds=('order',))
        self.assertEqual(self.post('suggestions/').status_code, 400)
        self.upload('receipt')  # qəbul faylı var, amma oxunmayıb
        self.save_data('invoice')
        self.assertEqual(self.post('suggestions/').status_code, 400)
        request_json.assert_not_called()

    def test_ai_unavailable_returns_503(self, request_json):
        self.fill()
        request_json.side_effect = AIUnavailable('Kvota bitib')
        response = self.post('suggestions/')
        self.assertEqual((response.status_code, response.data['detail']), (503, 'Kvota bitib'))
