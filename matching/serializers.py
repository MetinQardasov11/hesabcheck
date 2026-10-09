from pathlib import Path
from rest_framework import serializers
from .models import Case, Document, AuditEvent
from . import convert
from .schemas import validate_data

class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        exclude = ['file']
        read_only_fields = [f.name for f in Document._meta.fields]

class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditEvent
        fields = ['id', 'actor', 'action', 'payload', 'created_at']

class CaseSerializer(serializers.ModelSerializer):
    documents = DocumentSerializer(many=True, read_only=True)
    class Meta:
        model = Case
        exclude = ['owner']
        read_only_fields = ['id', 'status', 'revision', 'report', 'decision', 'decision_note', 'created_at', 'updated_at']

SIGNATURES = {'.pdf': [b'%PDF-'], '.png': [b'\x89PNG\r\n\x1a\n'], '.jpg': [b'\xff\xd8\xff'], '.jpeg': [b'\xff\xd8\xff'],
              '.docx': [b'PK\x03\x04'], '.xlsx': [b'PK\x03\x04'], '.xls': [b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1']}
HEIF_BRANDS = {b'heic', b'heix', b'hevc', b'hevx', b'mif1', b'msf1', b'heif'}
ALLOWED = 'PDF, şəkil (PNG, JPG, WEBP, HEIC), Word (.docx), Excel (.xlsx, .xls), CSV və ya UTF-8 TXT'

def validate_upload(file):
    if file.size > 10 * 1024 * 1024:
        raise serializers.ValidationError('Maksimum fayl ölçüsü 10 MB.')
    suffix = Path(file.name).suffix.lower()
    header = file.read(16)
    file.seek(0)
    if suffix == '.doc':
        raise serializers.ValidationError('Köhnə Word (.doc) dəstəklənmir; faylı .docx və ya PDF kimi saxlayın.')
    if suffix in ('.txt', '.csv'):
        try:
            file.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            raise serializers.ValidationError(f'{suffix[1:].upper()} UTF-8 formatında olmalıdır.')
        finally:
            file.seek(0)
        return file
    valid = (any(header.startswith(sig) for sig in SIGNATURES.get(suffix, []))
             or (suffix == '.webp' and header[:4] == b'RIFF' and header[8:12] == b'WEBP')
             or (suffix in ('.heic', '.heif') and header[4:8] == b'ftyp' and header[8:12] in HEIF_BRANDS))
    if not valid:
        raise serializers.ValidationError(f'Etibarlı fayl yükləyin: {ALLOWED}.')
    if convert.is_office(file.name):
        # Zədələnmiş və ya şifrəli ofis faylları yükləmə zamanı aşkarlanır.
        try:
            convert.to_text(file.read(), file.name)
        except convert.ConversionError as exc:
            raise serializers.ValidationError(str(exc))
        finally:
            file.seek(0)
    return file

class UploadSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=Document.KINDS)
    file = serializers.FileField()
    def validate_file(self, file):
        return validate_upload(file)

class BundleSerializer(serializers.Serializer):
    file = serializers.FileField(help_text='Sifariş, qəbul sənədi və/və ya fakturanı birlikdə saxlayan bir fayl.')
    def validate_file(self, file):
        return validate_upload(file)

class DataSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=Document.KINDS)
    data = serializers.JSONField()
    note = serializers.CharField(max_length=1000)
    def validate_data(self, value):
        return validate_data(value)

class MappingSerializer(serializers.Serializer):
    order = serializers.IntegerField(min_value=0)
    receipt = serializers.IntegerField(min_value=0, required=False, help_text='Qəbul sənədi olmayan (sifariş ↔ faktura) yoxlamada göndərilmir.')
    invoice = serializers.IntegerField(min_value=0)

class CompareSerializer(serializers.Serializer):
    revision = serializers.IntegerField(min_value=0, required=False)
    mappings = MappingSerializer(many=True, required=False)

class ReviewSerializer(serializers.Serializer):
    revision = serializers.IntegerField(min_value=0)
    decision = serializers.ChoiceField(choices=['approved', 'disputed', 'needs_review'])
    note = serializers.CharField(max_length=2000)
