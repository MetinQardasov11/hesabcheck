"""Yoxlama (case) yaratmaq, siyahı, baxış və silmə."""
from matching.models import AuditEvent, Case, Document

from .helpers import CaseAPITestCase


class CreateCaseTests(CaseAPITestCase):
    def test_new_case_starts_as_empty_draft(self):
        case = self.case
        self.assertEqual((case['title'], case['supplier']), ('Ofis kağızı alışı', 'Demo Təchizatçı MMC'))
        self.assertEqual((case['status'], case['revision'], case['report'], case['decision']), ('draft', 0, {}, ''))
        self.assertEqual(case['documents'], [])
        self.assertNotIn('owner', case)

    def test_supplier_is_optional(self):
        response = self.client.post('/api/cases/', {'title': 'Yalnız başlıq'}, format='json')
        self.assertEqual((response.status_code, response.data['supplier']), (201, ''))

    def test_title_is_required_and_limited(self):
        for payload in ({}, {'title': ''}, {'title': 'x' * 201}):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post('/api/cases/', payload, format='json').status_code, 400)

    def test_read_only_fields_cannot_be_set(self):
        response = self.client.post('/api/cases/', {'title': 'T', 'status': 'matched', 'revision': 9, 'decision': 'approved'},
                                    format='json')
        self.assertEqual((response.data['status'], response.data['revision'], response.data['decision']), ('draft', 0, ''))

    def test_creation_is_audited(self):
        self.assertEqual(self.events(), ['created'])


class ListAndRetrieveTests(CaseAPITestCase):
    def test_list_is_newest_first(self):
        newer = self.create_case(title='Daha yeni')
        titles = [item['title'] for item in self.client.get('/api/cases/').data['results']]
        self.assertEqual(titles, [newer['title'], 'Ofis kağızı alışı'])

    def test_list_is_paginated_by_25(self):
        for index in range(25):
            self.create_case(title=f'Yoxlama {index}')
        first = self.client.get('/api/cases/').data
        self.assertEqual((first['count'], len(first['results'])), (26, 25))
        self.assertIsNotNone(first['next'])
        second = self.client.get('/api/cases/?page=2').data
        self.assertEqual(len(second['results']), 1)
        self.assertIsNone(second['next'])

    def test_retrieve_includes_documents_and_report(self):
        self.fill()
        self.compare()
        case = self.get_case()
        self.assertEqual({doc['kind'] for doc in case['documents']}, {'order', 'receipt', 'invoice'})
        self.assertEqual(case['report']['disputed_amount'], '240.00')
        self.assertNotIn('file', case['documents'][0])

    def test_malformed_id_is_not_routed(self):
        self.assertEqual(self.client.get('/api/cases/not-a-uuid/').status_code, 404)


class DeleteCaseTests(CaseAPITestCase):
    def test_delete_removes_case_documents_events_and_files(self):
        document_id = self.upload('order').data['id']
        stored = Document.objects.get(id=document_id).file
        self.assertTrue(stored.storage.exists(stored.name))
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.delete(self.url).status_code, 204)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertFalse(Case.objects.exists() or Document.objects.exists() or AuditEvent.objects.exists())
        self.assertFalse(stored.storage.exists(stored.name))

    def test_delete_case_with_manual_data_only(self):
        self.fill()
        self.assertEqual(self.client.delete(self.url).status_code, 204)
        self.assertFalse(Document.objects.exists())
