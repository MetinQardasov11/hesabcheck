"""İnsan qərarı (/review/), hesabat (/report/), etiraz məktubu (/dispute-letter/) və audit tarixçəsi (/history/)."""
from matching.demo import document

from .helpers import CaseAPITestCase


class ReviewTests(CaseAPITestCase):
    def review(self, revision, decision='disputed', note='20 ədəd çatışmır'):
        return self.post('review/', {'revision': revision, 'decision': decision, 'note': note})

    def test_dispute_is_recorded(self):
        self.fill()
        compared = self.compare()
        response = self.review(compared['revision'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.data['decision'], response.data['decision_note']), ('disputed', '20 ədəd çatışmır'))
        self.assertEqual(response.data['revision'], compared['revision'] + 1)

    def test_matched_report_can_be_approved(self):
        self.fill(received='100')
        compared = self.compare()
        self.assertEqual(self.review(compared['revision'], 'approved', 'Yoxlanıldı').data['decision'], 'approved')

    def test_mismatch_cannot_be_approved(self):
        self.fill()
        response = self.review(self.compare()['revision'], 'approved', 'Səhv təsdiq')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.get_case()['decision'], '')

    def test_needs_review_can_be_escalated(self):
        self.fill(kinds=('order',))
        compared = self.compare()
        self.assertEqual(self.review(compared['revision'], 'needs_review', 'Faktura gözlənilir').status_code, 200)

    def test_stale_revision_or_missing_report_returns_409(self):
        self.assertEqual(self.review(0).status_code, 409)  # hesabat yoxdur
        self.fill()
        compared = self.compare()
        self.assertEqual(self.review(compared['revision'] - 1).status_code, 409)
        self.review(compared['revision'])
        self.assertEqual(self.review(compared['revision']).status_code, 409)  # qərar revision-u artırıb

    def test_invalid_payloads(self):
        self.fill()
        revision = self.compare()['revision']
        for payload in ({'revision': revision, 'decision': 'paid', 'note': 'x'},
                        {'revision': revision, 'decision': 'disputed', 'note': ''},
                        {'revision': revision, 'decision': 'disputed', 'note': 'x' * 2001},
                        {'decision': 'disputed', 'note': 'x'}):
            with self.subTest(payload=payload):
                self.assertEqual(self.post('review/', payload).status_code, 400)

    def test_review_is_audited(self):
        self.fill()
        revision = self.compare()['revision']
        self.review(revision)
        payload = self.client.get(self.url + 'history/').data[-1]['payload']
        self.assertEqual((payload['decision'], payload['revision'], payload['new_revision']), ('disputed', revision, revision + 1))


class ReportEndpointTests(CaseAPITestCase):
    def test_requires_comparison(self):
        self.assertEqual(self.client.get(self.url + 'report/').status_code, 400)

    def test_returns_report_with_case_context_and_decision(self):
        self.fill()
        revision = self.compare()['revision']
        self.post('review/', {'revision': revision, 'decision': 'disputed', 'note': 'Etiraz'})
        data = self.client.get(self.url + 'report/').data
        self.assertEqual(data['case_id'], self.case['id'])
        self.assertEqual((data['title'], data['supplier']), ('Ofis kağızı alışı', 'Demo Təchizatçı MMC'))
        self.assertEqual((data['decision'], data['decision_note']), ('disputed', 'Etiraz'))
        self.assertEqual(data['report']['disputed_amount'], '240.00')


class DisputeLetterTests(CaseAPITestCase):
    def letter(self):
        return self.post('dispute-letter/')

    def test_letter_lists_differences_and_amount(self):
        self.fill()
        self.compare()
        response = self.letter()
        self.assertEqual(response.status_code, 200)
        letter = response.data
        self.assertEqual(letter['subject'], 'Sənədlərdə uyğunsuzluq — Ofis kağızı alışı')
        self.assertTrue(letter['body'].startswith('Hörmətli Demo Təchizatçı MMC,'))
        self.assertIn('sifariş 100, qəbul 80, faktura 100', letter['body'])
        self.assertIn('240.00 AZN', letter['body'])
        self.assertNotIn('yekun deyil', letter['body'])
        self.assertEqual((letter['is_draft'], letter['sent'], letter['generator']), (True, False, 'deterministic_template'))

    def test_two_way_letter_mentions_basis(self):
        self.fill(kinds=('order', 'invoice'), invoice='110')
        self.compare()
        body = self.letter().data['body']
        self.assertIn('sifariş 100, faktura 110', body)
        self.assertIn('sifariş və faktura əsasında', body)

    def test_incomplete_amount_is_marked_not_final(self):
        self.fill(kinds=('order', 'receipt'))
        invoice = document('invoice')
        invoice['warnings'] = ['Səhifə 2 oxunmur']
        self.save_data('invoice', invoice)
        self.compare()
        body = self.letter().data['body']
        self.assertIn('yekun deyil', body)
        self.assertIn('Sənəd natamamdır', body)

    def test_supplier_fallback(self):
        self.case = self.create_case(title='Təchizatçısız', supplier='')
        self.url = f'/api/cases/{self.case["id"]}/'
        self.fill()
        self.compare()
        self.assertTrue(self.letter().data['body'].startswith('Hörmətli təchizatçı,'))

    def test_not_available_without_report_or_when_matched(self):
        self.assertEqual(self.letter().status_code, 400)
        self.fill(received='100')
        self.compare()
        self.assertEqual(self.letter().status_code, 400)

    def test_drafting_is_audited(self):
        self.fill()
        revision = self.compare()['revision']
        self.letter()
        event = self.client.get(self.url + 'history/').data[-1]
        self.assertEqual((event['action'], event['payload']), ('letter_drafted', {'revision': revision}))


class AuditHistoryTests(CaseAPITestCase):
    def test_full_workflow_is_recorded_in_order(self):
        self.upload('order')
        self.fill(kinds=('receipt', 'invoice'))
        revision = self.compare()['revision']
        self.post('review/', {'revision': revision, 'decision': 'disputed', 'note': 'Etiraz'})
        self.post('dispute-letter/')
        self.assertEqual(self.events(), ['created', 'document_uploaded', 'document_corrected', 'document_corrected',
                                         'compared', 'reviewed', 'letter_drafted'])

    def test_events_record_actor_and_time(self):
        event = self.client.get(self.url + 'history/').data[0]
        self.assertEqual(event['actor'], self.user.id)
        self.assertTrue(event['created_at'])
