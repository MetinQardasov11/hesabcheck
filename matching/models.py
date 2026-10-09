import uuid
from pathlib import Path
from django.conf import settings
from django.db import models

def upload_path(instance, filename):
    return f'documents/{instance.case_id}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}'

class Case(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    supplier = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=30, default='draft')
    revision = models.PositiveIntegerField(default=0)
    report = models.JSONField(default=dict)
    decision = models.CharField(max_length=30, blank=True)
    decision_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['-created_at']

class Document(models.Model):
    KINDS = [('order', 'Satınalma sifarişi'), ('receipt', 'Qəbul sənədi'), ('invoice', 'Faktura')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(Case, related_name='documents', on_delete=models.CASCADE)
    kind = models.CharField(max_length=10, choices=KINDS)
    file = models.FileField(upload_to=upload_path, blank=True)
    original_name = models.CharField(max_length=255, blank=True)
    data = models.JSONField(default=dict)
    extraction_status = models.CharField(max_length=30, default='pending')
    error = models.CharField(max_length=500, blank=True)
    usage = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['case', 'kind'], name='one_document_per_kind')]
        ordering = ['kind']

class AuditEvent(models.Model):
    case = models.ForeignKey(Case, related_name='events', on_delete=models.CASCADE)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=50)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['id']
