from decimal import Decimal
from .schemas import FIELDS

def document(kind, quantity='100', price='12.00', name='A4 kağız 80 q/m²'):
    total = str(Decimal(quantity) * Decimal(price))
    row = dict(name=name, sku='PAPER-A4-80', quantity=quantity, unit='ədəd', pack_size='1', unit_price=price, line_total=total)
    quote = ' | '.join(row.values())
    row['source'] = {key: {'page': 1, 'quote': quote} for key in FIELDS}
    return {'document_number': f'DEMO-{kind.upper()}-001', 'currency': 'AZN', 'total': total,
            'source': {key: {'page': 1, 'quote': f'DEMO-{kind.upper()}-001 AZN {total}'} for key in ['document_number', 'currency', 'total']},
            'lines': [row], 'warnings': []}
