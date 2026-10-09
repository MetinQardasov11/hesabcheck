from pathlib import Path
from rest_framework import serializers
from .models import Case, Document, AuditEvent
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

def validate_upload(file):
    if file.size > 10 * 1024 * 1024:
        raise serializers.ValidationError('Maksimum fayl ölçüsü 10 MB.')
    suffix = Path(file.name).suffix.lower()
    header = file.read(8)
    file.seek(0)
    signatures = {'.pdf': b'%PDF-', '.png': b'\x89PNG\r\n\x1a\n', '.jpg': b'\xff\xd8\xff', '.jpeg': b'\xff\xd8\xff'}
    if suffix == '.txt':
        try:
            file.read().decode('utf-8')
        except UnicodeDecodeError:
            raise serializers.ValidationError('TXT UTF-8 formatında olmalıdır.')
        finally:
            file.seek(0)
    elif suffix not in signatures or not header.startswith(signatures[suffix]):
        raise serializers.ValidationError('Etibarlı PDF, PNG, JPG və ya UTF-8 TXT yükləyin.')
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
