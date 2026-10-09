from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.db import transaction
from matching.models import Case, Document, AuditEvent
from matching.demo import document
from matching.engine import compare

class Command(BaseCommand):
    help = 'Mövcud istifadəçi üçün üç sintetik demo yoxlaması yaradır.'
    def add_arguments(self, parser):
        parser.add_argument('--username', required=True)
    @transaction.atomic
    def handle(self, *args, **options):
        try:
            user = get_user_model().objects.get(username=options['username'])
        except get_user_model().DoesNotExist:
            raise CommandError('İstifadəçi yoxdur. Əvvəl createsuperuser işlədin.')
        for scenario in ['mismatch', 'matched', 'needs_review']:
            case = Case.objects.create(owner=user, title=f'Sintetik demo — {scenario}', supplier='Demo Təchizatçı MMC')
            for kind in ['order', 'receipt', 'invoice']:
                data = document(kind, quantity='80' if kind == 'receipt' and scenario == 'mismatch' else '100',
                    name={'order': 'A4 kağız 80 q/m²', 'receipt': 'Бумага A4 80 г/м²', 'invoice': 'A4 paper 80gsm'}[kind])
                if scenario == 'needs_review' and kind == 'receipt':
                    data['lines'][0]['quantity'] = None
                    data['warnings'] = ['Sintetik nümunə: miqdar oxunmur.']
                Document.objects.create(case=case, kind=kind, data=data, extraction_status='demo')
            case.report = compare(case.documents.all())
            case.status = case.report['status']
            case.revision = 1
            case.report['revision'] = 1
            case.save()
            AuditEvent.objects.create(case=case, actor=user, action='synthetic_demo_created')
            self.stdout.write(self.style.SUCCESS(f'{case.id} {case.status}: {case.report["disputed_amount"]} AZN'))
