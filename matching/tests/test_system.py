"""Health check, API sənədləri, admin paneli və idarəetmə əmrləri (seed_demo, check_gemini)."""
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APITestCase

from matching.ai import AIUnavailable
from matching.demo import document
from matching.models import AuditEvent, Case, Document, upload_path


class HealthTests(APITestCase):
    def test_health_is_public_and_reports_release(self):
        with override_settings(APP_RELEASE='test-commit'):
            response = self.client.get('/health/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok', 'service': 'HesabCheck', 'release': 'test-commit'})

    @patch('matching.health.connection.cursor', side_effect=DatabaseError)
    def test_unavailable_database_is_unhealthy(self, cursor):
        response = self.client.get('/health/')
        self.assertEqual((response.status_code, response.json()), (503, {'status': 'unavailable'}))

    def test_health_accepts_only_get(self):
        self.assertEqual(self.client.post('/health/').status_code, 405)


class ApiDocumentationTests(APITestCase):
    def test_root_redirects_to_swagger(self):
        response = self.client.get('/')
        self.assertEqual((response.status_code, response['Location']), (302, '/api/docs/'))

    def test_swagger_and_schema_are_public(self):
        self.assertEqual(self.client.get('/api/docs/').status_code, 200)
        schema = self.client.get('/api/schema/?format=json').json()
        for path in ['/api/cases/', '/api/cases/{id}/bundle/', '/api/cases/{id}/compare/', '/api/cases/{id}/suggestions/']:
            self.assertIn(path, schema['paths'])
        self.assertIn('delete', schema['paths']['/api/cases/{id}/'])


class AdminTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('admin', password='Admin-pass-391')
        self.client.force_login(self.admin)
        self.case = Case.objects.create(owner=self.admin, title='Admin test')

    def test_business_data_is_read_only(self):
        self.assertEqual(self.client.get(f'/admin/matching/case/{self.case.id}/change/').status_code, 200)
        self.assertEqual(self.client.get('/admin/matching/case/add/').status_code, 403)
        self.assertEqual(self.client.get(f'/admin/matching/case/{self.case.id}/delete/').status_code, 403)


class ModelTests(SimpleTestCase):
    def test_uploaded_files_get_random_names_inside_case_folder(self):
        instance = SimpleNamespace(case_id='case-1')
        first, second = upload_path(instance, 'Faktura Oktyabr.PDF'), upload_path(instance, 'Faktura Oktyabr.PDF')
        self.assertTrue(first.startswith('documents/case-1/') and first.endswith('.pdf'))
        self.assertNotEqual(first, second)
        self.assertNotIn('Faktura', first)


class SeedDemoCommandTests(TestCase):
    def test_creates_three_synthetic_scenarios(self):
        user = get_user_model().objects.create_user('tester')
        output = StringIO()
        call_command('seed_demo', username='tester', stdout=output)
        statuses = sorted(Case.objects.filter(owner=user).values_list('status', flat=True))
        self.assertEqual(statuses, ['matched', 'mismatch', 'needs_review'])
        self.assertEqual(Document.objects.count(), 9)
        self.assertEqual(AuditEvent.objects.filter(action='synthetic_demo_created').count(), 3)
        self.assertIn('mismatch: 240.00 AZN', output.getvalue())

    def test_unknown_user_is_an_error(self):
        with self.assertRaises(CommandError):
            call_command('seed_demo', username='missing', stdout=StringIO())
        self.assertFalse(Case.objects.exists())


class CheckGeminiCommandTests(TestCase):
    def fake_extract(self, received='80'):
        return lambda doc: (document(doc.kind, received if doc.kind == 'receipt' else '100'), {'total_tokens': 5, 'elapsed_seconds': 1.0})

    @override_settings(GEMINI_API_KEY='')
    @patch('matching.management.commands.check_gemini.extract')
    def test_requires_api_key_and_makes_no_request(self, extract):
        with self.assertRaises(CommandError):
            call_command('check_gemini', stdout=StringIO())
        extract.assert_not_called()

    @override_settings(GEMINI_API_KEY='test-key')
    def test_reports_success_for_expected_240(self):
        output = StringIO()
        with patch('matching.management.commands.check_gemini.extract', side_effect=self.fake_extract()):
            call_command('check_gemini', stdout=output)
        self.assertIn('mismatch; 240.00 AZN', output.getvalue())
        self.assertIn('uğurludur', output.getvalue())
        self.assertFalse(Case.objects.exists())  # bazaya yazmır

    @override_settings(GEMINI_API_KEY='test-key')
    def test_unexpected_result_fails(self):
        with patch('matching.management.commands.check_gemini.extract', side_effect=self.fake_extract(received='100')):
            with self.assertRaisesRegex(CommandError, '240'):
                call_command('check_gemini', stdout=StringIO())

    @override_settings(GEMINI_API_KEY='test-key')
    def test_unexpected_result_lists_issues(self):
        def extract(doc):
            data = document(doc.kind, '80' if doc.kind == 'receipt' else '100')
            data['warnings'] = ['Səhifə oxunmur'] if doc.kind == 'invoice' else []
            return data, {}
        output = StringIO()
        with patch('matching.management.commands.check_gemini.extract', side_effect=extract), self.assertRaises(CommandError):
            call_command('check_gemini', stdout=output)
        self.assertIn('- Sənəd natamamdır', output.getvalue())

    @override_settings(GEMINI_API_KEY='test-key', BASE_DIR='/nonexistent')
    def test_missing_sample_file_is_reported(self):
        with self.assertRaisesRegex(CommandError, 'order.txt'):
            call_command('check_gemini', stdout=StringIO())

    @override_settings(GEMINI_API_KEY='test-key')
    def test_ai_error_names_the_document(self):
        with patch('matching.management.commands.check_gemini.extract', side_effect=AIUnavailable('Kvota bitib')):
            with self.assertRaisesRegex(CommandError, 'order: Kvota bitib'):
                call_command('check_gemini', stdout=StringIO())
