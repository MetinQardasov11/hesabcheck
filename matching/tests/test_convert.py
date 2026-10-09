"""Word, Excel və CSV fayllarının AI üçün strukturlu mətnə çevrilməsi (matching/convert.py)."""
from datetime import datetime
from unittest.mock import patch

from django.test import SimpleTestCase

from matching.convert import ConversionError, cell_text, is_office, rows_to_text, to_text

from .helpers import make_docx, make_xlsx


class ExcelConversionTests(SimpleTestCase):
    def test_each_sheet_becomes_a_page(self):
        text = to_text(make_xlsx(), 'faktura.xlsx')
        self.assertIn('=== Page 1: sheet "Faktura" ===', text)
        self.assertIn('Məhsul\tMiqdar\tQiymət', text)
        self.assertIn('A4 kağız\t100\t12.5', text)
        self.assertIn('=== Page 2: sheet "Qeyd" ===', text)

    def test_header_explains_source_format(self):
        self.assertTrue(to_text(make_xlsx(), 'faktura.xlsx').startswith('[Converted from .xlsx file "faktura.xlsx".'))

    def test_empty_rows_and_trailing_cells_are_dropped(self):
        text = to_text(make_xlsx(rows=[('A', None, None), (None, None), ('B', 2)], extra_sheet=False), 'x.xlsx')
        self.assertIn('A\nB\t2', text)

    def test_legacy_xls_is_read(self):
        class Sheet:
            name, nrows, ncols = 'Sheet1', 2, 2
            def cell_value(self, row, col):
                return [['Məhsul', 'Miqdar'], ['Qələm', 200.0]][row][col]
        with patch('xlrd.open_workbook') as open_workbook:
            open_workbook.return_value.sheets.return_value = [Sheet()]
            text = to_text(b'\xd0\xcf\x11\xe0', 'old.xls')
        self.assertIn('=== Page 1: sheet "Sheet1" ===\nMəhsul\tMiqdar\nQələm\t200', text)


class WordConversionTests(SimpleTestCase):
    def test_paragraphs_and_tables_keep_structure(self):
        text = to_text(make_docx(), 'sifaris.docx')
        self.assertIn('Sifariş № 7', text)
        self.assertIn('[table]\nMəhsul\tMiqdar\nQələm\t200\n[/table]', text)

    def test_document_without_body_is_rejected(self):
        import io, zipfile
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>')
        with self.assertRaisesRegex(ConversionError, 'mətn tapılmadı'):
            to_text(stream.getvalue(), 'nobody.docx')

    def test_document_without_text_is_rejected(self):
        with self.assertRaises(ConversionError):
            to_text(make_docx(body_xml=''), 'empty.docx')


class CsvConversionTests(SimpleTestCase):
    def test_delimiters_are_detected(self):
        for delimiter in [',', ';', '\t', '|']:
            with self.subTest(delimiter=delimiter):
                raw = f'Məhsul{delimiter}Miqdar\nQələm{delimiter}200\n'.encode()
                self.assertIn('Qələm\t200', to_text(raw, 'qebul.csv'))

    def test_utf8_bom_is_removed(self):
        self.assertIn('=== Page 1 ===\nMəhsul', to_text('﻿Məhsul;Miqdar\n'.encode('utf-8'), 'q.csv'))

    def test_non_utf8_is_rejected(self):
        with self.assertRaises(ConversionError):
            to_text('Məhsul;Miqdar'.encode('cp1254', errors='replace') + b'\xff\xfe', 'q.csv')


class ConversionSafetyTests(SimpleTestCase):
    def test_broken_files_raise_conversion_error(self):
        for name, raw in [('a.xlsx', b'PK\x03\x04broken'), ('a.docx', b'PK\x03\x04broken'), ('a.xls', b'\xd0\xcf\x11\xe0broken')]:
            with self.subTest(name=name), self.assertRaises(ConversionError):
                to_text(raw, name)

    def test_file_without_content_is_rejected(self):
        with self.assertRaises(ConversionError):
            to_text(b'\n\n', 'empty.csv')

    def test_very_large_content_is_rejected(self):
        with patch('matching.convert.MAX_TEXT_CHARS', 50), self.assertRaises(ConversionError):
            to_text(make_xlsx(rows=[('x' * 100,)]), 'big.xlsx')

    def test_is_office_by_extension(self):
        self.assertTrue(all(is_office(name) for name in ['a.XLSX', 'b.xls', 'c.csv', 'd.docx']))
        self.assertFalse(any(is_office(name) for name in ['a.pdf', 'b.txt', 'c.doc', 'd.png']))

    def test_cell_formatting(self):
        self.assertEqual(cell_text(100.0), '100')
        self.assertEqual(cell_text(12.5), '12.5')
        self.assertEqual(cell_text(None), '')
        self.assertEqual(cell_text(datetime(2026, 10, 9)), '2026-10-09T00:00:00')
        self.assertEqual(cell_text('a\tb\nc'), 'a b c')
        self.assertEqual(rows_to_text([('a', 'b'), (), ('', '')]), 'a\tb')
