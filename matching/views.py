from concurrent.futures import ThreadPoolExecutor
from django.core.files.base import ContentFile
from django.db import transaction
from django.conf import settings
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.throttling import AnonRateThrottle
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes
from .models import Case, Document, AuditEvent
from .serializers import (CaseSerializer, DocumentSerializer, EventSerializer, UploadSerializer,
    BundleSerializer, DataSerializer, CompareSerializer, ReviewSerializer)
from .engine import compare, THREE_WAY, TWO_WAY
from . import ai, convert

class LoginThrottle(AnonRateThrottle):
    scope = 'login'

class LoginView(ObtainAuthToken):
    throttle_classes = [LoginThrottle]

class CaseViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    serializer_class = CaseSerializer
    lookup_value_regex = '[0-9a-fA-F-]{36}'
    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Case.objects.none()
        return Case.objects.filter(owner=self.request.user).prefetch_related('documents')

    def event(self, case, action, payload=None):
        AuditEvent.objects.create(case=case, actor=self.request.user, action=action, payload=payload or {})

    def perform_create(self, serializer):
        with transaction.atomic():
            case = serializer.save(owner=self.request.user)
            self.event(case, 'created')

    def perform_destroy(self, case):
        with transaction.atomic():
            files = [doc.file for doc in case.documents.all() if doc.file]
            case.delete()
            # Fayllar yalnız baza silinməsi uğurlu olduqdan sonra diskdən silinir.
            transaction.on_commit(lambda: [file.storage.delete(file.name) for file in files])

    def locked(self):
        return get_object_or_404(Case.objects.select_for_update(), pk=self.kwargs['pk'], owner=self.request.user)

    def invalidate(self, case):
        case.report = {}
        case.status = 'draft'
        case.decision = ''
        case.decision_note = ''
        case.revision += 1
        case.save()

    @extend_schema(request=UploadSerializer, responses=DocumentSerializer)
    @action(detail=True, methods=['post'], url_path='documents')
    def upload(self, request, pk=None):
        serializer = UploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            case = self.locked()
            kind, file = serializer.validated_data['kind'], serializer.validated_data['file']
            doc, _ = Document.objects.get_or_create(case=case, kind=kind)
            old = doc.file.name
            doc.original_name, doc.file = file.name, file
            doc.data, doc.usage, doc.error, doc.extraction_status = {}, {}, '', 'pending'
            doc.save()
            if old:
                transaction.on_commit(lambda: doc.file.storage.delete(old))
            self.invalidate(case)
            self.event(case, 'document_uploaded', {'document_id': str(doc.id), 'kind': kind})
        return Response(DocumentSerializer(doc).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=DataSerializer, responses=DocumentSerializer)
    @action(detail=True, methods=['post'], url_path='document-data')
    def document_data(self, request, pk=None):
        serializer = DataSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        with transaction.atomic():
            case = self.locked()
            doc, _ = Document.objects.get_or_create(case=case, kind=values['kind'])
            before = doc.data
            doc.data, doc.extraction_status, doc.error = values['data'], 'manual', ''
            doc.save()
            self.invalidate(case)
            self.event(case, 'document_corrected', {'document_id': str(doc.id), 'before': before, 'after': doc.data, 'note': values['note']})
        return Response(DocumentSerializer(doc).data)

    @extend_schema(request=None, responses=CaseSerializer)
    @action(detail=True, methods=['post'])
    def extract(self, request, pk=None):
        case = self.get_object()
        revision = case.revision
        # Yalnız faylı olan sənədlər oxunur; manual daxil edilmiş sənədlərə toxunulmur.
        documents = [doc for doc in case.documents.all() if doc.file]
        if not documents:
            raise ValidationError('AI çıxarışı üçün ən azı bir fayl yüklənməlidir.')
        if not settings.GEMINI_API_KEY:
            return Response({'detail': 'GEMINI_API_KEY təyin edilməyib.'}, status=503)
        def read(doc):
            try:
                data, usage = ai.extract(doc)
                return doc, data, usage, ''
            except (ai.AIUnavailable, ValidationError, convert.ConversionError) as exc:
                return doc, {}, {}, str(exc)
        # Sənədlər paralel oxunur; thread-lərdə verilənlər bazasına müraciət yoxdur.
        with ThreadPoolExecutor(max_workers=3) as pool:
            extracted = list(pool.map(read, documents))
        with transaction.atomic():
            current = self.locked()
            if current.revision != revision:
                return Response({'detail': 'Sənədlər dəyişib. Çıxarışı yenidən başladın.'}, status=409)
            for doc, data, usage, error in extracted:
                doc.data, doc.usage, doc.error = data, usage, error[:500]
                doc.extraction_status = 'failed' if error else 'extracted'
                doc.save()
            self.invalidate(current)
            self.event(current, 'extracted', {'documents': [{'id': str(d.id), 'status': d.extraction_status, 'usage': d.usage} for d, *_ in extracted]})
        return Response(CaseSerializer(current).data)

    @extend_schema(request=BundleSerializer, responses=CaseSerializer,
                   description='Bir faylda (məs. skan PDF) olan sifariş, qəbul sənədi və fakturanı AI ilə ayırır və hər birini '
                               'ayrıca sənəd kimi saxlayır. Faylda tapılmayan növlərə toxunulmur. Səhifələr documents[].usage.pages-dədir.')
    @action(detail=True, methods=['post'], url_path='bundle')
    def bundle(self, request, pk=None):
        serializer = BundleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        file = serializer.validated_data['file']
        case = self.get_object()
        revision = case.revision
        if not settings.GEMINI_API_KEY:
            return Response({'detail': 'GEMINI_API_KEY təyin edilməyib.'}, status=503)
        raw = file.read()
        try:
            found, usage = ai.extract_bundle(raw, file.name)
        except (ai.BundleContentError, convert.ConversionError) as exc:
            raise ValidationError(str(exc))
        except ai.AIUnavailable as exc:
            return Response({'detail': str(exc)}, status=503)
        with transaction.atomic():
            current = self.locked()
            if current.revision != revision:
                return Response({'detail': 'Sənədlər dəyişib. Faylı yenidən göndərin.'}, status=409)
            for kind, pages, data in found:
                doc, _ = Document.objects.get_or_create(case=current, kind=kind)
                old = doc.file.name
                doc.original_name = file.name
                doc.file.save(file.name, ContentFile(raw), save=False)
                doc.data, doc.error, doc.extraction_status = data, '', 'extracted'
                doc.usage = {**usage, 'bundle': True, 'pages': pages}
                doc.save()
                if old:
                    transaction.on_commit(lambda name=old, storage=doc.file.storage: storage.delete(name))
            self.invalidate(current)
            self.event(current, 'bundle_extracted', {'file': file.name, 'usage': usage,
                       'documents': [{'kind': kind, 'pages': pages} for kind, pages, _ in found]})
        return Response(CaseSerializer(current).data)

    @extend_schema(request=CompareSerializer, responses=CaseSerializer)
    @action(detail=True, methods=['post'])
    def compare(self, request, pk=None):
        serializer = CompareSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            case = self.locked()
            if 'mappings' in serializer.validated_data and serializer.validated_data.get('revision') != case.revision:
                return Response({'detail': 'Manual uyğunlaşdırma üçün cari revision tələb olunur.'}, status=409)
            try:
                report = compare(list(case.documents.all()), serializer.validated_data.get('mappings'))
            except ValueError as exc:
                raise ValidationError(str(exc))
            case.revision += 1
            report['revision'] = case.revision
            report['generated_at'] = timezone.now().isoformat()
            report['scope'] = 'Vergi, endirim və daşınma haqqı olmayan mal sətirləri; bir sənəd/növ.'
            case.report, case.status, case.decision, case.decision_note = report, report['status'], '', ''
            case.save()
            self.event(case, 'compared', report)
        return Response(CaseSerializer(case).data)

    @extend_schema(request=None, responses=dict)
    @action(detail=True, methods=['post'])
    def suggestions(self, request, pk=None):
        case = self.get_object()
        by_kind = {d.kind: d for d in case.documents.all()}
        kinds = THREE_WAY if 'receipt' in by_kind else TWO_WAY
        if any(k not in by_kind or not by_kind[k].data for k in kinds):
            raise ValidationError('Əvvəl sifariş və fakturanın (qəbul sənədi varsa, onun da) məlumatlarını daxil edin.')
        docs = [by_kind[k] for k in kinds]
        try:
            result, usage = ai.suggest(docs)
            mappings = [{k: item[k] for k in kinds} for item in result['suggestions']]
            compare(docs, mappings)  # Validate ranges and one-to-one mapping.
            if any(not 0 <= item['confidence'] <= 1 for item in result['suggestions']):
                raise ValueError('Etibarsız əminlik göstəricisi.')
        except (ai.AIUnavailable, ValueError) as exc:
            return Response({'detail': str(exc)}, status=503)
        return Response({**result, 'usage': usage, 'revision': case.revision, 'requires_human_confirmation': True})

    @extend_schema(request=ReviewSerializer, responses=CaseSerializer)
    @action(detail=True, methods=['post'])
    def review(self, request, pk=None):
        serializer = ReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        with transaction.atomic():
            case = self.locked()
            if not case.report or case.revision != values['revision']:
                return Response({'detail': 'Cari hesabat və uyğun revision tələb olunur.'}, status=409)
            if values['decision'] == 'approved' and case.status != 'matched':
                raise ValidationError('Uyğunsuz və ya natamam hesabat təsdiqlənə bilməz; əvvəl məlumatları düzəldin.')
            case.decision, case.decision_note = values['decision'], values['note']
            case.revision += 1
            case.save()
            self.event(case, 'reviewed', {**values, 'new_revision': case.revision})
        return Response(CaseSerializer(case).data)

    @extend_schema(responses=dict)
    @action(detail=True, methods=['get'])
    def report(self, request, pk=None):
        case = self.get_object()
        if not case.report:
            raise ValidationError('Əvvəl müqayisəni başladın.')
        return Response({'case_id': str(case.id), 'title': case.title, 'supplier': case.supplier,
                         'report': case.report, 'decision': case.decision, 'decision_note': case.decision_note})

    @extend_schema(request=None, responses=dict)
    @action(detail=True, methods=['post'], url_path='dispute-letter')
    def dispute_letter(self, request, pk=None):
        case = self.get_object()
        if not case.report or case.status == 'matched':
            raise ValidationError('Etiraz üçün uyğunsuzluq və ya yoxlama tələb edən hesabat olmalıdır.')
        report = case.report
        details = []
        for row in report['matches']:
            if row['status'] != 'matched':
                refs = row['sources']
                received = f", qəbul {refs['receipt']['values']['quantity']}" if 'receipt' in refs else ''
                details.append(f"- {refs['invoice']['values']['name']}: sifariş {refs['order']['values']['quantity']}{received}, faktura {refs['invoice']['values']['quantity']}. " + ' '.join(row['differences']))
        details.extend('- ' + issue['message'] for issue in report['issues'])
        text = (f"Hörmətli {case.supplier or 'təchizatçı'},\n\n{case.title} üzrə sənədlərin yoxlanmasında aşağıdakı fərqlər müəyyən edilib:\n"
                + '\n'.join(details) + f"\n\nHesablana bilən mübahisəli məbləğ: {report['disputed_amount']} {report['currency'] or '(valyuta qeyri-müəyyəndir)'}."
                + (' Natamam məlumatlara görə bu məbləğ yekun deyil.' if not report['amount_complete'] else '')
                + (' Yoxlama sifariş və faktura əsasında aparılıb.' if report.get('mode') == 'two_way' else '')
                + '\nZəhmət olmasa sənədləri yoxlayın və düzəliş edilmiş fakturanı və ya izahı təqdim edin.\n\nHörmətlə,\nSatınalma komandası')
        self.event(case, 'letter_drafted', {'revision': case.revision})
        return Response({'subject': f'Sənədlərdə uyğunsuzluq — {case.title}', 'body': text,
                         'is_draft': True, 'generator': 'deterministic_template', 'sent': False})

    @extend_schema(responses=EventSerializer(many=True))
    @action(detail=True, methods=['get'])
    def history(self, request, pk=None):
        return Response(EventSerializer(self.get_object().events.all(), many=True).data)

    @extend_schema(parameters=[OpenApiParameter('document_id', OpenApiTypes.UUID, OpenApiParameter.PATH)], responses={(200, 'application/octet-stream'): OpenApiTypes.BINARY})
    @action(detail=True, methods=['get'], url_path=r'documents/(?P<document_id>[0-9a-fA-F-]{36})/download')
    def download(self, request, pk=None, document_id=None):
        doc = get_object_or_404(Document, case=self.get_object(), id=document_id)
        if not doc.file:
            raise ValidationError('Bu sənəd manual daxil edilib; fayl yoxdur.')
        return FileResponse(doc.file.open('rb'), as_attachment=True, filename=doc.original_name)
