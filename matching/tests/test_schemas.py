"""Sənəd məlumatının sxem və rəqəm validasiyası (matching/schemas.py)."""
import copy

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from matching.demo import document
from matching.schemas import DOCUMENT_SCHEMA, validate_data


class DocumentDataValidationTests(SimpleTestCase):
    def assertInvalid(self, data):
        with self.assertRaises(ValidationError):
            validate_data(data)

    def test_valid_document_passes(self):
        data = document('order')
        self.assertIs(validate_data(data), data)

    def test_all_values_may_be_null_when_unreadable(self):
        data = document('receipt')
        for key in ('document_number', 'currency', 'total'):
            data[key] = None
        for key in ('name', 'sku', 'quantity', 'unit', 'pack_size', 'unit_price', 'line_total'):
            data['lines'][0][key] = None
        validate_data(data)

    def test_tax_total_and_notes_are_optional_for_stored_data(self):
        data = document('invoice')
        validate_data(data)
        data.update(tax_total='0.00', notes=['Ödəniş 15 gün ərzində'])
        validate_data(data)

    def test_ai_schema_requires_tax_total_and_notes(self):
        self.assertIn('tax_total', DOCUMENT_SCHEMA['required'])
        self.assertIn('notes', DOCUMENT_SCHEMA['required'])

    def test_structure_errors(self):
        broken = {
            'missing lines': lambda d: d.pop('lines'),
            'unknown field': lambda d: d.update(extra=1),
            'number instead of string': lambda d: d['lines'][0].update(quantity=100),
            'notes not a list': lambda d: d.update(notes='not a list'),
            'missing line source': lambda d: d['lines'][0].pop('source'),
        }
        for label, change in broken.items():
            with self.subTest(label):
                data = document('order')
                change(data)
                self.assertInvalid(data)

    def test_invalid_numbers(self):
        for value in ['NaN', 'Infinity', '-1', '1e9999', '0.00000001', '1000000000001', 'on iki', '12,50']:
            with self.subTest(value=value):
                data = document('order')
                data['lines'][0]['quantity'] = value
                self.assertInvalid(data)

    def test_negative_tax_total_is_invalid(self):
        data = document('invoice')
        data['tax_total'] = '-5'
        self.assertInvalid(data)

    def test_pack_size_cannot_be_zero(self):
        data = document('order')
        data['lines'][0]['pack_size'] = '0'
        self.assertInvalid(data)

    def test_currency_must_be_uppercase_iso_code(self):
        for value in ['azn', 'MANAT', '₼', 'AZ']:
            with self.subTest(value=value):
                data = document('order')
                data['currency'] = value
                self.assertInvalid(data)

    def test_evidence_page_starts_at_one(self):
        data = document('order')
        data['lines'][0]['source']['quantity']['page'] = 0
        self.assertInvalid(data)

    def test_at_most_500_lines(self):
        data = document('order')
        data['lines'] = [copy.deepcopy(data['lines'][0]) for _ in range(501)]
        self.assertInvalid(data)

    def test_error_message_names_the_problem(self):
        data = document('order')
        data['currency'] = 'azn'
        with self.assertRaises(ValidationError) as caught:
            validate_data(data)
        self.assertIn('ISO', str(caught.exception.detail['data']))
