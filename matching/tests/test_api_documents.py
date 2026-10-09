"""Sənəd faylının yüklənməsi, manual məlumat daxil edilməsi və faylın endirilməsi."""
from matching.demo import document
from matching.models import Document

from .helpers import HEIC, JPG, PDF, PNG, WEBP, CaseAPITestCase, make_docx, make_xlsx, upload


class UploadTests(CaseAPITestCase):
    def test_supported_formats_are_accepted(self):
        files = [('o.pdf', PDF), ('o.png', PNG), ('o.jpg', JPG), ('o.jpeg', JPG), ('o.webp', WEBP), ('o.heic', HEIC),
                 ('o.heif', HEIC), ('o.docx', make_docx()), ('o.xlsx', make_xlsx()), ('o.csv', 'a;b\n1;2'.encode()),
                 ('o.txt', '100 ədəd'.encode()), ('O.PDF', PDF)]
        for name, content in files:
            with self.subTest(name=name):
                response = self.upload('order', name, content)
                self.assertEqual(response.status_code, 201, response.data)
                self.assertEqual((response.data['original_name'], response.data['extraction_status']), (name, 'pending'))
                self.assertNotIn('file', response.data)

    def test_invalid_files_are_rejected_with_reason(self):
        files = {
            'html disguised as pdf': ('evil.pdf', b'<html>bad</html>', 'Etibarlı fayl'),
            'pdf disguised as xlsx': ('i.xlsx', PDF, 'Etibarlı fayl'),
            'corrupt excel': ('i.xlsx', b'PK\x03\x04broken', 'Excel'),
            'legacy word': ('o.doc', b'\xd0\xcf\x11\xe0', '.docx'),
            'fake photo': ('p.heic', b'not an image at all', 'Etibarlı fayl'),
            'executable': ('x.exe', b'MZ', 'Etibarlı fayl'),
            'non-utf8 text': ('t.txt', b'\xff\xfe\x00', 'UTF-8'),
        }
        for label, (name, content, reason) in files.items():
            with self.subTest(label):
                response = self.upload('order', name, content)
                self.assertEqual(response.status_code, 400)
                self.assertIn(reason, str(response.data['file']))
        self.assertFalse(Document.objects.exists())

    def test_files_over_10_mb_are_rejected(self):
        response = self.upload('order', 'big.pdf', PDF + b'0' * (10 * 1024 * 1024))
        self.assertEqual(response.status_code, 400)
        self.assertIn('10 MB', str(response.data['file']))

    def test_kind_and_file_are_required(self):
        self.assertEqual(self.client.post(self.url + 'documents/', {'kind': 'order'}).status_code, 400)
        self.assertEqual(self.client.post(self.url + 'documents/', {'file': upload('o.pdf', PDF)}).status_code, 400)
        self.assertEqual(self.upload('contract', 'o.pdf', PDF).status_code, 400)

    def test_new_upload_replaces_previous_file_of_same_kind(self):
        first = Document.objects.get(id=self.upload('order', 'v1.pdf', PDF).data['id']).file
        with self.captureOnCommitCallbacks(execute=True):
            second = self.upload('order', 'v2.pdf', PDF + b'v2')
        self.assertEqual(Document.objects.filter(kind='order').count(), 1)
        self.assertEqual(second.data['original_name'], 'v2.pdf')
        self.assertFalse(first.storage.exists(first.name))

    def test_upload_resets_report_decision_and_extracted_data(self):
        self.fill()
        compared = self.compare()
        self.post('review/', {'revision': compared['revision'], 'decision': 'disputed', 'note': 'Etiraz'})
        response = self.upload('receipt', 'grn.pdf', PDF)
        case = self.get_case()
        self.assertEqual((case['status'], case['report'], case['decision'], case['decision_note']), ('draft', {}, '', ''))
        self.assertGreater(case['revision'], compared['revision'])
        self.assertEqual(response.data['data'], {})

    def test_upload_is_audited(self):
        document_id = self.upload('invoice', 'i.pdf', PDF).data['id']
        event = self.client.get(self.url + 'history/').data[-1]
        self.assertEqual((event['action'], event['payload']), ('document_uploaded', {'document_id': document_id, 'kind': 'invoice'}))


class ManualDataTests(CaseAPITestCase):
    def test_manual_data_is_stored(self):
        response = self.save_data('order', document('order', '50'))
        self.assertEqual((response.data['extraction_status'], response.data['error']), ('manual', ''))
        self.assertEqual(response.data['data']['lines'][0]['quantity'], '50')

    def test_manual_correction_keeps_uploaded_file(self):
        document_id = self.upload('order', 'o.pdf', PDF).data['id']
        self.save_data('order', document('order', '95'), note='AI 95-i 59 kimi oxumuşdu')
        stored = Document.objects.get(id=document_id)
        self.assertTrue(stored.file)
        self.assertEqual(stored.data['lines'][0]['quantity'], '95')

    def test_invalid_data_is_rejected_with_reason(self):
        broken = document('order')
        broken['currency'] = 'azn'
        response = self.post('document-data/', {'kind': 'order', 'data': broken, 'note': 'x'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('ISO', str(response.data))

    def test_note_and_kind_are_required(self):
        for payload in ({'kind': 'order', 'data': document('order')},
                        {'kind': 'order', 'data': document('order'), 'note': ''},
                        {'kind': 'order', 'data': document('order'), 'note': 'x' * 1001},
                        {'data': document('order'), 'note': 'x'}):
            with self.subTest(payload=sorted(payload)):
                self.assertEqual(self.post('document-data/', payload).status_code, 400)

    def test_correction_resets_report(self):
        self.fill()
        self.compare()
        self.save_data('receipt', document('receipt', '100'))
        self.assertEqual(self.get_case()['status'], 'draft')

    def test_correction_audit_keeps_before_after_and_note(self):
        self.save_data('order', document('order', '100'))
        self.save_data('order', document('order', '90'), note='Düzəliş: 90 ədəd')
        payload = self.client.get(self.url + 'history/').data[-1]['payload']
        self.assertEqual(payload['before']['lines'][0]['quantity'], '100')
        self.assertEqual(payload['after']['lines'][0]['quantity'], '90')
        self.assertEqual(payload['note'], 'Düzəliş: 90 ədəd')


class DownloadTests(CaseAPITestCase):
    def test_original_file_is_returned_as_attachment(self):
        document_id = self.upload('order', 'sifariş.pdf', PDF).data['id']
        response = self.client.get(self.url + f'documents/{document_id}/download/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('sifari', response['Content-Disposition'])
        self.assertEqual(self.read_download(response), PDF)

    def test_manual_document_has_no_file(self):
        document_id = self.save_data('order').data['id']
        response = self.client.get(self.url + f'documents/{document_id}/download/')
        self.assertEqual(response.status_code, 400)

    def test_unknown_document_returns_404(self):
        response = self.client.get(self.url + 'documents/00000000-0000-0000-0000-000000000000/download/')
        self.assertEqual(response.status_code, 404)
