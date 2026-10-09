"""Live extraction smoke test using synthetic files; never changes the database."""
from pathlib import Path
from types import SimpleNamespace
from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from rest_framework.exceptions import ValidationError
from matching.ai import AIUnavailable, extract
from matching.engine import compare

class Command(BaseCommand):
    help = 'Üç sintetik TXT sənədini real Gemini API ilə oxuyub 240 AZN nəticəsini yoxlayır. API kvotası istifadə edir.'

    def handle(self, *args, **options):
        if not settings.GEMINI_API_KEY:
            raise CommandError('Əvvəl .env faylında GEMINI_API_KEY əlavə edin.')
        self.stdout.write(f'Model: {settings.GEMINI_MODEL}. Üç sintetik sənəd Google-a göndəriləcək.')
        docs = []
        for kind in ['order', 'receipt', 'invoice']:
            path = Path(settings.BASE_DIR) / 'examples' / f'{kind}.txt'
            try:
                with path.open('rb') as stream:
                    doc = SimpleNamespace(id=f'live-test-{kind}', kind=kind, original_name=path.name, file=File(stream))
                    doc.data, usage = extract(doc)
            except (AIUnavailable, ValidationError) as exc:
                raise CommandError(f'{kind}: {exc}') from None
            except OSError:
                raise CommandError(f'Nümunə sənədi oxunmadı: {path.name}') from None
            docs.append(doc)
            self.stdout.write(f'{kind}: {len(doc.data["lines"])} sətir; {usage.get("total_tokens")} token; {usage.get("elapsed_seconds")} saniyə')
        report = compare(docs)
        self.stdout.write(f'Nəticə: {report["status"]}; {report["disputed_amount"]} {report["currency"]}; amount_complete={report["amount_complete"]}')
        if (report['status'], report['disputed_amount'], report['currency'], report['amount_complete']) != ('mismatch', '240.00', 'AZN', True):
            for issue in report['issues']:
                self.stdout.write(f'- {issue["message"]}')
            raise CommandError('Gözlənilən tam 240 AZN nəticəsi alınmadı. Çıxarış və mənbələr yoxlanmalıdır.')
        self.stdout.write(self.style.SUCCESS('Real Gemini çıxarışı + backend müqayisəsi uğurludur. Bu, sintetik sınaqdır; real qənaət deyil.'))
