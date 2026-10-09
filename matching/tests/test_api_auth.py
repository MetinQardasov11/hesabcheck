"""Autentifikasiya, token girişi və istifadəçilər arası təcrid."""
import uuid

from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token

from matching.demo import document

from .helpers import PASSWORD, CaseAPITestCase, upload


class TokenLoginTests(CaseAPITestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(None)

    def test_valid_credentials_return_token(self):
        response = self.client.post('/api/auth/token/', {'username': 'tester', 'password': PASSWORD}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['token'], Token.objects.get(user=self.user).key)

    def test_token_authorizes_requests(self):
        token = self.client.post('/api/auth/token/', {'username': 'tester', 'password': PASSWORD}).data['token']
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_wrong_password_is_rejected(self):
        response = self.client.post('/api/auth/token/', {'username': 'tester', 'password': 'wrong'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('token', response.data)

    def test_missing_fields_are_rejected(self):
        self.assertEqual(self.client.post('/api/auth/token/', {}, format='json').status_code, 400)

    def test_login_is_rate_limited(self):
        statuses = [self.client.post('/api/auth/token/', {'username': 'tester', 'password': 'wrong'}).status_code
                    for _ in range(11)]
        self.assertEqual(statuses[:10], [400] * 10)
        self.assertEqual(statuses[10], 429)


class AuthenticationRequiredTests(CaseAPITestCase):
    def test_anonymous_requests_are_rejected(self):
        self.client.force_authenticate(None)
        for method, path in [('get', '/api/cases/'), ('post', '/api/cases/'), ('get', self.url), ('delete', self.url),
                             ('post', self.url + 'compare/'), ('get', self.url + 'history/')]:
            with self.subTest(method=method, path=path):
                self.assertEqual(getattr(self.client, method)(path).status_code, 401)

    def test_invalid_token_is_rejected(self):
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION='Token invalid')
        self.assertEqual(self.client.get('/api/cases/').status_code, 401)


class OwnershipIsolationTests(CaseAPITestCase):
    """Hər istifadəçi yalnız öz yoxlamalarını görür; başqasının yoxlaması mövcud olmayan kimi (404) davranır."""

    def setUp(self):
        super().setUp()
        self.fill()
        self.compare()
        self.document_id = self.get_case()['documents'][0]['id']
        self.other = get_user_model().objects.create_user(username='other')
        self.client.force_authenticate(self.other)

    def test_list_contains_only_own_cases(self):
        self.assertEqual(self.client.get('/api/cases/').data['count'], 0)

    def test_every_case_endpoint_returns_404_even_for_valid_requests(self):
        multipart = lambda **data: {'data': data, 'format': 'multipart'}
        endpoints = [
            ('get', '', {}), ('delete', '', {}),
            ('post', 'documents/', multipart(kind='order', file=upload('o.txt', b'100'))),
            ('post', 'document-data/', {'data': {'kind': 'order', 'data': document('order'), 'note': 'x'}, 'format': 'json'}),
            ('post', 'extract/', {}), ('post', 'bundle/', multipart(file=upload('b.txt', b'100'))),
            ('post', 'compare/', {'data': {}, 'format': 'json'}), ('post', 'suggestions/', {}),
            ('post', 'review/', {'data': {'revision': 2, 'decision': 'disputed', 'note': 'x'}, 'format': 'json'}),
            ('get', 'report/', {}), ('post', 'dispute-letter/', {}), ('get', 'history/', {}),
            ('get', f'documents/{self.document_id}/download/', {}),
        ]
        for method, path, kwargs in endpoints:
            with self.subTest(method=method, path=path):
                self.assertEqual(getattr(self.client, method)(self.url + path, **kwargs).status_code, 404)

    def test_unknown_case_returns_404(self):
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get(f'/api/cases/{uuid.uuid4()}/').status_code, 404)
