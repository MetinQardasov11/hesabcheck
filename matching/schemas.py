from decimal import Decimal, InvalidOperation
import jsonschema
from rest_framework.exceptions import ValidationError

def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}
TEXT = {'type': ['string', 'null']}
SOURCE = obj({'page': {'type': ['integer', 'null']}, 'quote': TEXT})
FIELDS = ['name', 'sku', 'quantity', 'unit', 'pack_size', 'unit_price', 'line_total']
LINE = obj({**{key: TEXT for key in FIELDS}, 'source': obj({key: SOURCE for key in FIELDS})})
DOCUMENT_SCHEMA = obj({
    'document_number': TEXT, 'currency': TEXT, 'total': TEXT, 'tax_total': TEXT,
    'source': obj({key: SOURCE for key in ['document_number', 'currency', 'total']}),
    'lines': {'type': 'array', 'items': LINE},
    'warnings': {'type': 'array', 'items': {'type': 'string'}},
})

# Köhnə və manual məlumatlarda tax_total olmaya bilər; AI cavabında isə məcburidir.
STORED_SCHEMA = {**DOCUMENT_SCHEMA, 'required': [key for key in DOCUMENT_SCHEMA['required'] if key != 'tax_total']}

def validate_data(data):
    try:
        jsonschema.validate(data, STORED_SCHEMA)
        if len(data['lines']) > 500:
            raise ValueError('Maksimum 500 məhsul sətri.')
        if data['currency'] is not None and (len(data['currency']) != 3 or not data['currency'].isascii() or not data['currency'].isupper()):
            raise ValueError('Valyuta 3 hərfli ISO kodu olmalıdır (AZN).')
        for row in [data] + data['lines']:
            for key in ['quantity', 'pack_size', 'unit_price', 'line_total', 'total', 'tax_total']:
                value = row.get(key)
                if value is not None:
                    number = Decimal(value)
                    if not number.is_finite() or number < 0 or number > Decimal('1000000000000') or number.as_tuple().exponent < -6:
                        raise ValueError(f'{key}: düzgün, mənfi olmayan, maksimum 6 onluqlu ədəd tələb olunur.')
                    if key == 'pack_size' and number == 0:
                        raise ValueError('pack_size sıfır ola bilməz.')
            for source in row.get('source', {}).values():
                if source['page'] is not None and source['page'] < 1:
                    raise ValueError('Səhifə 1-dən başlamalıdır.')
    except (jsonschema.ValidationError, InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationError({'data': str(exc).split('\n')[0]})
    return data
