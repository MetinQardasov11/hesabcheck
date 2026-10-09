"""Conservative, deterministic matching. All monetary arithmetic uses Decimal."""
from decimal import Decimal, ROUND_HALF_UP
from collections import Counter

D = Decimal

def money(value):
    return str(value.quantize(D('0.01'), rounding=ROUND_HALF_UP))

def identity(line):
    return ('sku', line['sku'].strip().casefold()) if line.get('sku') else ('name', (line.get('name') or '').strip().casefold())

THREE_WAY = ('order', 'receipt', 'invoice')
TWO_WAY = ('order', 'invoice')

def compare(documents, mappings=None):
    all_docs = {d.kind: d for d in documents}
    issues, results = [], []
    total = D('0')
    # Qəbul sənədi ümumiyyətlə yoxdursa sifariş ↔ faktura müqayisəsi aparılır.
    # Qəbul sənədi var, amma oxunmayıbsa, səssizcə ikitərəfli rejimə keçmirik.
    kinds = THREE_WAY if 'receipt' in all_docs else TWO_WAY
    mode = 'three_way' if kinds == THREE_WAY else 'two_way'
    def issue(code, message, **extra):
        issues.append({'code': code, 'message': message, **extra})
    if any(k not in all_docs or not all_docs[k].data for k in kinds):
        message = ('Üç sənədin məlumatı da tələb olunur.' if mode == 'three_way'
                   else 'Ən azı sifariş və fakturanın məlumatı tələb olunur.')
        return {'status': 'needs_review', 'mode': mode, 'currency': None, 'disputed_amount': '0.00', 'amount_complete': False,
                'issues': [{'code': 'missing_document', 'message': message}], 'matches': [], 'notes': []}
    docs = {k: all_docs[k] for k in kinds}
    currencies = [docs[k].data['currency'] for k in kinds]
    currency_ok = all(currencies) and len(set(currencies)) == 1
    if not currency_ok:
        issue('currency_review', 'Valyutalar fərqlidir və ya göstərilməyib; məbləğlər toplanmır.')
    for kind, doc in docs.items():
        data = doc.data
        if data['warnings'] or not data['lines'] or not data['document_number']:
            issue('incomplete_document', 'Sənəd natamamdır və ya çıxarış xəbərdarlığı var.', document_id=str(doc.id))
        for row in [data] + data['lines']:
            for field, source in row['source'].items():
                if row.get(field) is not None and (not source['page'] or not source['quote']):
                    issue('missing_evidence', 'Dolu sahənin səhifə və mənbə mətni yoxdur.', document_id=str(doc.id), field=field)
        # MVP totals are tax-exclusive goods subtotals; tax/discount must be flagged during extraction.
        if kind in ('order', 'invoice'):
            amounts = []
            for i, line in enumerate(data['lines']):
                if all(line[f] is not None for f in ('quantity', 'unit_price', 'line_total')):
                    calculated = D(line['quantity']) * D(line['unit_price'])
                    amounts.append(D(line['line_total']))
                    if money(calculated) != money(D(line['line_total'])):
                        issue('arithmetic_mismatch', 'Sətir cəmi miqdar × qiymət ilə uyğun deyil.', document_id=str(doc.id), line=i)
                else:
                    issue('missing_amount', 'Miqdar, qiymət və ya sətir cəmi oxunmayıb.', document_id=str(doc.id), line=i)
            if data['total'] is None:
                issue('missing_total', 'Sənəd cəmi oxunmayıb.', document_id=str(doc.id))
            elif len(amounts) == len(data['lines']) and money(sum(amounts, D('0'))) != money(D(data['total'])):
                issue('total_mismatch', 'Sənəd cəmi sətirlərin cəminə bərabər deyil.', document_id=str(doc.id))
    lines = {k: docs[k].data['lines'] for k in kinds}
    if mappings is None:
        counts = {k: Counter(identity(x) for x in lines[k]) for k in kinds}
        mappings = []
        for index, line in enumerate(lines['order']):
            key = identity(line)
            if key[1] and all(counts[k][key] == 1 for k in kinds):
                mappings.append({k: index if k == 'order' else next(i for i, x in enumerate(lines[k]) if identity(x) == key) for k in kinds})
    used = {k: set() for k in kinds}
    for mapping in mappings:
        if set(mapping) != set(kinds) or any(type(mapping[k]) is not int or mapping[k] < 0 or mapping[k] >= len(lines[k]) or mapping[k] in used[k] for k in kinds):
            raise ValueError('Uyğunlaşdırma indeksləri etibarlı, təkrarsız və hər üç sənəd üçün olmalıdır.')
        for k in kinds:
            used[k].add(mapping[k])
        rows = {k: lines[k][mapping[k]] for k in kinds}
        refs = {k: {'document_id': str(docs[k].id), 'line': mapping[k], 'values': rows[k]} for k in kinds}
        result = {'sources': refs, 'status': 'matched', 'differences': [], 'disputed_amount': None}
        required = ('name', 'quantity', 'unit', 'pack_size')
        complete = all(all(row.get(f) is not None and row.get(f) != '' for f in required) for row in rows.values())
        compatible = complete and len({r['unit'].strip().casefold() for r in rows.values()}) == 1 and len({D(r['pack_size']) for r in rows.values()}) == 1
        if not compatible or not currency_ok or any(rows[k]['unit_price'] is None for k in ('order', 'invoice')):
            result['status'] = 'needs_review'
            result['differences'].append('Miqdar, vahid, qablaşdırma, qiymət və ya valyuta yoxlanmalıdır.')
        else:
            ordered, invoiced = D(rows['order']['quantity']), D(rows['invoice']['quantity'])
            price, charged = D(rows['order']['unit_price']), D(rows['invoice']['unit_price'])
            if 'receipt' in rows:
                received = D(rows['receipt']['quantity'])
                if ordered != received or received != invoiced:
                    result['differences'].append('Sifariş, qəbul və faktura miqdarları fərqlidir.')
                accepted = min(ordered, received)
            else:
                if ordered != invoiced:
                    result['differences'].append('Sifariş və faktura miqdarları fərqlidir.')
                accepted = ordered
            if price != charged:
                result['differences'].append('Fakturanın vahid qiyməti sifarişdən fərqlidir.')
            # Net positive overbilling per line avoids counting quantity/price overlap twice.
            disputed = max(D('0'), invoiced * charged - accepted * price)
            result['disputed_amount'] = money(disputed)
            total += D(result['disputed_amount'])
            if result['differences']:
                result['status'] = 'mismatch'
        results.append(result)
    for kind in kinds:
        for i in set(range(len(lines[kind]))) - used[kind]:
            issue('unmatched_line', 'Məhsul sətri insan tərəfindən uyğunlaşdırılmalıdır.', document_id=str(docs[kind].id), kind=kind, line=i)
    review_codes = {'currency_review', 'incomplete_document', 'missing_evidence', 'missing_amount', 'missing_total', 'unmatched_line'}
    review = any(i['code'] in review_codes for i in issues) or any(r['status'] == 'needs_review' for r in results)
    mismatch = bool(issues) or any(r['status'] == 'mismatch' for r in results)
    notes = [] if mode == 'three_way' else ['Qəbul sənədi olmadan sifariş ↔ faktura müqayisəsi: malların faktiki qəbulu yoxlanmayıb.']
    return {'status': 'needs_review' if review else 'mismatch' if mismatch else 'matched', 'mode': mode,
            'currency': currencies[0] if currency_ok else None, 'disputed_amount': money(total),
            'amount_complete': not review and not issues, 'issues': issues, 'matches': results, 'notes': notes}
