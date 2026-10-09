"""Deterministik müqayisə mühərriki (matching/engine.py): hesablama, uyğunlaşdırma və məlumat keyfiyyəti qaydaları."""
from types import SimpleNamespace

from django.test import SimpleTestCase

from matching.demo import document
from matching.engine import compare, normalize_unit

from .helpers import engine_docs, without_prices


def codes(report):
    return [issue['code'] for issue in report['issues']]


def set_line(doc, **fields):
    doc.data['lines'][0].update(fields)


class ThreeWayCalculationTests(SimpleTestCase):
    """Sifariş ↔ qəbul ↔ faktura: mübahisəli məbləğ = max(0, faktura_miq × faktura_qiy − min(sifariş, qəbul) × sifariş_qiy)."""

    def test_identical_documents_are_matched(self):
        report = compare(engine_docs())
        self.assertEqual(report['status'], 'matched')
        self.assertEqual(report['mode'], 'three_way')
        self.assertEqual(report['disputed_amount'], '0.00')
        self.assertTrue(report['amount_complete'])
        self.assertEqual(report['issues'], [])
        self.assertEqual(report['notes'], [])

    def test_short_delivery_is_disputed(self):
        report = compare(engine_docs(received='80'))
        self.assertEqual((report['status'], report['disputed_amount'], report['currency']), ('mismatch', '240.00', 'AZN'))
        self.assertTrue(report['amount_complete'])
        self.assertIn('Sifariş, qəbul və faktura miqdarları fərqlidir.', report['matches'][0]['differences'])

    def test_price_increase_is_disputed(self):
        report = compare(engine_docs(price='13.00'))
        self.assertEqual((report['status'], report['disputed_amount']), ('mismatch', '100.00'))
        self.assertIn('Fakturanın vahid qiyməti sifarişdən fərqlidir.', report['matches'][0]['differences'])

    def test_quantity_and_price_overlap_is_not_counted_twice(self):
        # 100 × 13 − min(100, 80) × 12 = 340, ayrıca (20 × 12) + (100 × 1) = 340-dan artıq sayılmır.
        self.assertEqual(compare(engine_docs(received='80', price='13.00'))['disputed_amount'], '340.00')

    def test_overbilled_quantity_is_disputed(self):
        self.assertEqual(compare(engine_docs(invoiced='110'))['disputed_amount'], '120.00')

    def test_underbilling_never_produces_negative_amount(self):
        report = compare(engine_docs(invoiced='90', price='11.00'))
        self.assertEqual(report['status'], 'mismatch')
        self.assertEqual(report['disputed_amount'], '0.00')

    def test_over_delivery_does_not_increase_accepted_quantity(self):
        # Sifarişdən artıq qəbul edilmiş mal fakturalanmış sifariş miqdarını haqlı çıxarmır.
        self.assertEqual(compare(engine_docs(received='120', invoiced='120'))['disputed_amount'], '240.00')

    def test_decimal_arithmetic_has_no_float_errors(self):
        # Float ilə 0.3 × 0.1 = 0.030000000000000002 olardı; Decimal ilə dəqiq 0.03-dür.
        docs = engine_docs()
        for doc in docs:
            doc.data = document(doc.kind, '0.3', '0.1')
        self.assertEqual(compare(docs)['status'], 'matched')

    def test_amounts_round_half_up_to_cents(self):
        from decimal import Decimal
        from matching.engine import money
        self.assertEqual(money(Decimal('0.125')), '0.13')
        self.assertEqual(money(Decimal('2.675')), '2.68')
        docs = engine_docs(price='12.005')
        set_line(docs[2], line_total='1200.50')
        docs[2].data['total'] = '1200.50'
        self.assertEqual(compare(docs)['disputed_amount'], '0.50')

    def test_total_sums_all_lines(self):
        docs = engine_docs(received='80')
        for doc in docs:
            second = document(doc.kind, quantity='10' if doc.kind != 'invoice' else '12', price='5.00', name='Qələm')['lines'][0]
            second['sku'] = 'PEN-01'
            doc.data['lines'].append(second)
            if doc.data['total'] is not None:
                doc.data['total'] = f"{sum(float(l['line_total']) for l in doc.data['lines']):.2f}"
        report = compare(docs)
        self.assertEqual(len(report['matches']), 2)
        self.assertEqual(report['disputed_amount'], '250.00')  # 240 + (12 × 5 − 10 × 5)


class CurrencyAndTaxTests(SimpleTestCase):
    def test_different_currencies_are_not_summed(self):
        docs = engine_docs(received='80')
        docs[2].data['currency'] = 'USD'
        report = compare(docs)
        self.assertEqual((report['status'], report['currency'], report['disputed_amount']), ('needs_review', None, '0.00'))
        self.assertIn('currency_review', codes(report))
        self.assertFalse(report['amount_complete'])

    def test_missing_order_currency_needs_review(self):
        docs = engine_docs()
        docs[0].data['currency'] = None
        self.assertIn('currency_review', codes(compare(docs)))

    def test_receipt_without_prices_or_currency_is_normal(self):
        docs = engine_docs(received='80')
        docs[1].data = without_prices(docs[1].data)
        report = compare(docs)
        self.assertEqual((report['status'], report['disputed_amount'], report['currency']), ('mismatch', '240.00', 'AZN'))
        self.assertEqual(report['issues'], [])

    def test_zero_vat_is_not_an_issue(self):
        docs = engine_docs()
        for doc in docs:
            doc.data['tax_total'] = '0.00'
        self.assertEqual(compare(docs)['status'], 'matched')

    def test_positive_vat_requires_review(self):
        docs = engine_docs()
        docs[2].data['tax_total'] = '216.00'
        report = compare(docs)
        self.assertEqual(report['status'], 'needs_review')
        self.assertEqual(codes(report), ['tax_review'])
        self.assertIn('216.00', report['issues'][0]['message'])


class DataQualityTests(SimpleTestCase):
    """Natamam və ya ziddiyyətli məlumat heç vaxt avtomatik 'uyğundur' nəticəsi vermir."""

    def test_missing_receipt_data_requires_review(self):
        docs = engine_docs()
        docs[1].data = {}
        report = compare(docs)
        self.assertEqual((report['mode'], report['status']), ('three_way', 'needs_review'))
        self.assertEqual(codes(report), ['missing_document'])

    def test_missing_invoice_requires_review(self):
        report = compare(engine_docs()[:2])
        self.assertEqual((report['status'], codes(report)), ('needs_review', ['missing_document']))

    def test_unreadable_quantity_requires_review(self):
        docs = engine_docs()
        set_line(docs[1], quantity=None)
        report = compare(docs)
        self.assertEqual(report['status'], 'needs_review')
        self.assertIsNone(report['matches'][0]['disputed_amount'])

    def test_extraction_warning_marks_document_incomplete(self):
        docs = engine_docs()
        docs[0].data['warnings'] = ['Səhifə 2 oxunmur.']
        report = compare(docs)
        self.assertEqual(report['status'], 'needs_review')
        self.assertEqual(codes(report), ['incomplete_document'])

    def test_document_without_lines_or_number_is_incomplete(self):
        for change in ({'lines': []}, {'document_number': None}):
            with self.subTest(change=change):
                docs = engine_docs()
                docs[1].data.update(change)
                self.assertIn('incomplete_document', codes(compare(docs)))

    def test_value_without_evidence_requires_review(self):
        docs = engine_docs()
        docs[0].data['lines'][0]['source']['quantity'] = {'page': None, 'quote': None}
        report = compare(docs)
        self.assertEqual(report['status'], 'needs_review')
        self.assertEqual(report['issues'][0]['field'], 'quantity')

    def test_line_arithmetic_error_is_reported(self):
        docs = engine_docs()
        set_line(docs[2], line_total='1300.00')
        docs[2].data['total'] = '1300.00'
        report = compare(docs)
        self.assertIn('arithmetic_mismatch', codes(report))
        self.assertFalse(report['amount_complete'])
        self.assertNotEqual(report['status'], 'matched')

    def test_document_total_must_equal_line_sum(self):
        docs = engine_docs()
        docs[0].data['total'] = '999.00'
        self.assertIn('total_mismatch', codes(compare(docs)))

    def test_missing_total_and_amount_require_review(self):
        docs = engine_docs()
        docs[2].data['total'] = None
        set_line(docs[0], unit_price=None)
        report = compare(docs)
        self.assertEqual(report['status'], 'needs_review')
        self.assertTrue({'missing_total', 'missing_amount'} <= set(codes(report)))


class LineMatchingTests(SimpleTestCase):
    def test_lines_are_matched_by_sku_regardless_of_name_and_order(self):
        docs = engine_docs(received='80')
        set_line(docs[1], name='Бумага A4')
        set_line(docs[2], name='A4 paper')
        self.assertEqual(compare(docs)['disputed_amount'], '240.00')

    def test_lines_without_sku_are_matched_by_identical_name(self):
        docs = engine_docs(received='80')
        for doc in docs:
            set_line(doc, sku=None, name='  A4 Kağız ')
        self.assertEqual(compare(docs)['disputed_amount'], '240.00')

    def test_different_names_without_sku_stay_unmatched(self):
        docs = engine_docs()
        for index, doc in enumerate(docs):
            set_line(doc, sku=None, name=f'Name in language {index}')
        report = compare(docs)
        self.assertEqual(report['status'], 'needs_review')
        self.assertEqual(report['matches'], [])
        self.assertEqual(codes(report).count('unmatched_line'), 3)
        self.assertEqual({issue['kind'] for issue in report['issues']}, {'order', 'receipt', 'invoice'})

    def test_duplicate_sku_is_not_auto_matched(self):
        docs = engine_docs()
        docs[0].data['lines'] *= 2
        self.assertEqual(compare(docs)['status'], 'needs_review')

    def test_manual_mapping_matches_translated_names(self):
        docs = engine_docs(received='80')
        for index, doc in enumerate(docs):
            set_line(doc, sku=None, name=f'Name in language {index}')
        report = compare(docs, [{'order': 0, 'receipt': 0, 'invoice': 0}])
        self.assertEqual((report['status'], report['disputed_amount']), ('mismatch', '240.00'))
        self.assertEqual(report['matches'][0]['sources']['receipt']['line'], 0)

    def test_manual_mapping_replaces_automatic_mapping(self):
        report = compare(engine_docs(), [])
        self.assertEqual(report['matches'], [])
        self.assertEqual(codes(report).count('unmatched_line'), 3)

    def test_invalid_manual_mappings_are_rejected(self):
        mapping = {'order': 0, 'receipt': 0, 'invoice': 0}
        invalid = {
            'reused line': [mapping, mapping],
            'index out of range': [{**mapping, 'invoice': 1}],
            'negative index': [{**mapping, 'order': -1}],
            'missing kind': [{'order': 0, 'invoice': 0}],
            'non-integer': [{**mapping, 'receipt': '0'}],
        }
        for label, mappings in invalid.items():
            with self.subTest(label), self.assertRaises(ValueError):
                compare(engine_docs(), mappings)

    def test_match_sources_reference_document_lines(self):
        match = compare(engine_docs())['matches'][0]
        self.assertEqual(set(match['sources']), {'order', 'receipt', 'invoice'})
        self.assertEqual(match['sources']['invoice']['document_id'], 'invoice')
        self.assertEqual(match['sources']['order']['values']['sku'], 'PAPER-A4-80')


class UnitAndPackagingTests(SimpleTestCase):
    def test_unit_synonyms_across_languages(self):
        cases = [('ədəd', 'шт.', 'pcs'), ('qutu', 'кор.', 'Box'), ('kq', 'кг', 'KG'), ('dəst', 'комплект', 'set')]
        for units in cases:
            with self.subTest(units=units):
                docs = engine_docs(received='80')
                for doc, unit in zip(docs, units):
                    set_line(doc, unit=unit)
                self.assertEqual(compare(docs)['disputed_amount'], '240.00')

    def test_normalize_unit(self):
        self.assertEqual(normalize_unit(' Шт. '), 'piece')
        self.assertEqual(normalize_unit('Ədəd'), 'piece')
        self.assertEqual(normalize_unit('palet'), 'palet')  # naməlum vahid olduğu kimi qalır

    def test_different_units_require_review(self):
        docs = engine_docs()
        set_line(docs[2], unit='kg')
        self.assertEqual(compare(docs)['status'], 'needs_review')

    def test_receipt_without_unit_uses_order_and_invoice_unit(self):
        docs = engine_docs(received='80')
        set_line(docs[1], unit=None)
        self.assertEqual(compare(docs)['disputed_amount'], '240.00')
        set_line(docs[0], unit=None)
        self.assertEqual(compare(docs)['status'], 'needs_review')

    def test_pack_size_not_stated_anywhere_is_equal(self):
        docs = engine_docs(received='80')
        for doc in docs:
            set_line(doc, pack_size=None)
        self.assertEqual(compare(docs)['disputed_amount'], '240.00')

    def test_pack_size_stated_in_some_documents_requires_review(self):
        docs = engine_docs()
        for doc in docs:
            set_line(doc, pack_size=None)
        set_line(docs[0], pack_size='12')
        self.assertEqual(compare(docs)['status'], 'needs_review')

    def test_different_pack_sizes_require_review(self):
        docs = engine_docs()
        set_line(docs[1], pack_size='10')
        self.assertEqual(compare(docs)['status'], 'needs_review')


class TwoWayMatchingTests(SimpleTestCase):
    """Qəbul sənədi ümumiyyətlə yoxdursa sifariş ↔ faktura müqayisəsi aparılır."""

    def two_way(self, **kwargs):
        order, _, invoice = engine_docs(**kwargs)
        return [order, invoice]

    def test_matching_without_receipt(self):
        report = compare(self.two_way())
        self.assertEqual((report['mode'], report['status']), ('two_way', 'matched'))
        self.assertEqual(len(report['notes']), 1)
        self.assertNotIn('receipt', report['matches'][0]['sources'])

    def test_price_difference_without_receipt(self):
        report = compare(self.two_way(price='13.00'))
        self.assertEqual((report['status'], report['disputed_amount']), ('mismatch', '100.00'))

    def test_overbilled_quantity_without_receipt(self):
        report = compare(self.two_way(invoiced='110'))
        self.assertEqual(report['disputed_amount'], '120.00')
        self.assertIn('Sifariş və faktura miqdarları fərqlidir.', report['matches'][0]['differences'])

    def test_requires_order_and_invoice(self):
        order = engine_docs()[0]
        report = compare([order])
        self.assertEqual((report['mode'], codes(report)), ('two_way', ['missing_document']))

    def test_manual_mapping_uses_only_order_and_invoice(self):
        docs = self.two_way()
        self.assertEqual(compare(docs, [{'order': 0, 'invoice': 0}])['status'], 'matched')
        with self.assertRaises(ValueError):
            compare(docs, [{'order': 0, 'receipt': 0, 'invoice': 0}])

    def test_empty_receipt_document_does_not_switch_to_two_way(self):
        docs = engine_docs()
        docs[1] = SimpleNamespace(id='receipt', kind='receipt', data={})
        self.assertEqual(compare(docs)['mode'], 'three_way')
