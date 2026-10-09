"""Testlər üçün ortaq köməkçilər: sənəd məlumatı, API test bazası, ofis faylları və Gemini cavabı."""
import copy
import io
import tempfile
import zipfile
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase

from matching.demo import document

KINDS = ('order', 'receipt', 'invoice')
PASSWORD = 'Strong-test-pass-391'

# Fayl imzaları: serializer yalnız düzgün başlıqlı faylları qəbul edir.
PDF = b'%PDF-1.7 test'
PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 8
JPG = b'\xff\xd8\xff\xe0' + b'0' * 8
WEBP = b'RIFF\x00\x00\x00\x00WEBPVP8 '
HEIC = b'\x00\x00\x00\x18ftypheic' + b'\x00' * 8


def engine_docs(received='100', invoiced='100', price='12.00', ordered='100'):
    """Mühərrik testləri üçün DB-siz sənəd obyektləri (default: tam uyğun 100 × 12 AZN)."""
    quantities = {'order': ordered, 'receipt': received, 'invoice': invoiced}
    return [SimpleNamespace(id=kind, kind=kind,
                            data=document(kind, quantity=quantities[kind], price=price if kind == 'invoice' else '12.00'))
            for kind in KINDS]


def without_prices(data):
    """Real qəbul aktı kimi: qiymət, sətir cəmi, valyuta və sənəd cəmi yoxdur."""
    data = copy.deepcopy(data)
    data['currency'] = data['total'] = None
    for line in data['lines']:
        line['unit_price'] = line['line_total'] = None
    return data


def upload(name, content):
    return SimpleUploadedFile(name, content)


def make_xlsx(rows=(('Məhsul', 'Miqdar', 'Qiymət'), ('A4 kağız', 100, 12.5)), extra_sheet=True):
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Faktura'
    for row in rows:
        sheet.append(list(row))
    if extra_sheet:
        workbook.create_sheet('Qeyd').append(['ƏDV yoxdur'])
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def make_docx(body_xml=None):
    if body_xml is None:
        body_xml = (
            '<w:p><w:r><w:t>Sifariş № 7</w:t></w:r></w:p><w:tbl>'
            '<w:tr><w:tc><w:p><w:r><w:t>Məhsul</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Miqdar</w:t></w:r></w:p></w:tc></w:tr>'
            '<w:tr><w:tc><w:p><w:r><w:t>Qələm</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>200</w:t></w:r></w:p></w:tc></w:tr>'
            '</w:tbl>')
    xml = f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body_xml}</w:body></w:document>'
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('word/document.xml', xml)
    return stream.getvalue()


def gemini_response(text='{"ok": true}', finish='STOP', prompt_tokens=10, output_tokens=5):
    from google.genai import types
    return types.GenerateContentResponse(
        candidates=[types.Candidate(finish_reason=finish, content=types.Content(parts=[types.Part.from_text(text=text)]))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens, candidates_token_count=output_tokens,
            total_token_count=prompt_tokens + output_tokens),
    )


class SyncExecutor:
    """ThreadPoolExecutor əvəzi: paralel oxumanı testdə eyni thread-də (eyni DB bağlantısında) icra edir."""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def map(self, function, items):
        return map(function, items)


# Testdə real PBKDF2 hash-i hər istifadəçi üçün ~0.3 s çəkir; təhlükəsizliyi yoxlamadığımız üçün sürətli hasher kifayətdir.
@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CaseAPITestCase(APITestCase):
    """Müvəqqəti media qovluğu, autentifikasiya olunmuş istifadəçi və bir boş yoxlama ilə API testləri."""

    def setUp(self):
        cache.clear()
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        media_settings = override_settings(MEDIA_ROOT=media.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        self.user = get_user_model().objects.create_user(username='tester', password=PASSWORD)
        self.client.force_authenticate(self.user)
        self.case = self.create_case()
        self.url = f'/api/cases/{self.case["id"]}/'

    def create_case(self, title='Ofis kağızı alışı', supplier='Demo Təchizatçı MMC'):
        response = self.client.post('/api/cases/', {'title': title, 'supplier': supplier}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def get_case(self):
        return self.client.get(self.url).data

    def post(self, path, data=None, **kwargs):
        kwargs.setdefault('format', 'json')
        return self.client.post(self.url + path, data if data is not None else {}, **kwargs)

    def upload(self, kind, name='order.txt', content=b'100 units 12 AZN'):
        return self.client.post(self.url + 'documents/', {'kind': kind, 'file': upload(name, content)})

    def save_data(self, kind, data=None, note='Manual daxil edildi'):
        response = self.post('document-data/', {'kind': kind, 'data': data or document(kind), 'note': note})
        self.assertEqual(response.status_code, 200, response.data)
        return response

    def fill(self, received='80', kinds=KINDS, **quantities):
        """Sənədləri manual doldurur. Default: 100 sifariş, 80 qəbul, 100 faktura → 240.00 AZN fərq."""
        for kind in kinds:
            quantity = quantities.get(kind, received if kind == 'receipt' else '100')
            self.save_data(kind, document(kind, quantity))

    def compare(self, payload=None):
        response = self.post('compare/', payload or {})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def events(self):
        return [event['action'] for event in self.client.get(self.url + 'history/').data]

    def read_download(self, response):
        """Stream-i sona qədər oxuyur; test klienti faylı bu zaman özü təhlükəsiz bağlayır.

        response.close()-u əlavə çağırmaq olmaz: request_finished siqnalı PostgreSQL test bağlantısını bağlayır.
        """
        return b''.join(response.streaming_content)
