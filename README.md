# HesabCheck — Django backend

Satınalma sifarişi, qəbul sənədi və fakturanın üçtərəfli müqayisəsi üçün REST API. Python 3.12, Django 5.2, Django REST Framework və SQLite istifadə edir.

## İşə salmaq

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
cp .env.example .env
python manage.py migrate
python manage.py createsuperuser
python manage.py seed_demo --username <yaratdığınız-istifadəçi>
python manage.py runserver
```

- Swagger: http://127.0.0.1:8000/api/docs/
- OpenAPI: http://127.0.0.1:8000/api/schema/ və repodakı `openapi.yaml`
- Admin (biznes məlumatları yalnız oxunur): http://127.0.0.1:8000/admin/
- Health: http://127.0.0.1:8000/health/

`seed_demo` hər çağırışda üç yeni **sintetik** yoxlama yaradır: 240 AZN fərq, tam uyğun sənədlər və oxunmayan miqdar. Demo AI işlətmir və real qənaət sübutu deyil. Yüklənə bilən sənədlər `examples/*.txt`, manual JSON nümunələri `examples/*.json` fayllarındadır.

## Autentifikasiya

```bash
curl -X POST http://127.0.0.1:8000/api/auth/token/ \
  -H 'Content-Type: application/json' \
  -d '{"username":"YOUR_USERNAME","password":"YOUR_PASSWORD"}'
```

Cavabdakı `token` ilə bütün biznes endpoint-lərinə `Authorization: Token YOUR_TOKEN` başlığı göndərin. Swagger-in **Authorize** sahəsinə də `Token YOUR_TOKEN` yazın. Hər istifadəçi yalnız öz yoxlamalarını və fayllarını görə bilir. İstifadəçi yaradılması admin və ya `createsuperuser` vasitəsilədir; açıq qeydiyyat yoxdur. Tokenlər müddətsizdir; ləğv/yeniləmə adminin token bölməsindən aparılır. Frontend origin-lərini `.env`-də `CORS_ALLOWED_ORIGINS` ilə təyin edin.

## Frontend axını

| Metod | Yol | İş |
|---|---|---|
| GET / POST | `/api/cases/` | Səhifələnmiş siyahı / yeni yoxlama |
| GET | `/api/cases/{id}/` | Sənədlər, hesabat və cari `revision` |
| DELETE | `/api/cases/{id}/` | Yoxlamanı, sənədlərini, fayllarını və tarixçəsini silir (204) |
| POST | `/api/cases/{id}/documents/` | `multipart/form-data`: `kind`, `file` |
| POST | `/api/cases/{id}/extract/` | Faylı olan sənədlərdən real AI çıxarışı (1–3 fayl; manual sənədlərə toxunmur) |
| POST | `/api/cases/{id}/bundle/` | `multipart/form-data`: `file` — bir faylda olan sifariş/qəbul/fakturanı AI ilə ayırıb çıxarır |
| POST | `/api/cases/{id}/document-data/` | Manual çıxarış və ya düzəliş: `kind`, `data`, `note` |
| POST | `/api/cases/{id}/suggestions/` | AI məhsul uyğunlaşdırma təklifləri; avtomatik tətbiq edilmir |
| POST | `/api/cases/{id}/compare/` | Deterministik müqayisə; boş `{}` avtomatik uyğunlaşdırır |
| POST | `/api/cases/{id}/review/` | `revision`, `decision`, `note` ilə insan qərarı |
| GET | `/api/cases/{id}/report/` | JSON yoxlama hesabatı |
| POST | `/api/cases/{id}/dispute-letter/` | Azərbaycan dilində məktub qaralaması |
| GET | `/api/cases/{id}/history/` | Qərar və dəyişiklik tarixçəsi |
| GET | `/api/cases/{id}/documents/{document_id}/download/` | Autentifikasiya ilə mənbə faylı |

`kind`: `order`, `receipt`, `invoice`. Hər yoxlamada hər növdən bir sənəd var; yenisini yükləmək əvvəlkini əvəzləyir.

**Qismən sənədlər.** `extract/` yalnız faylı olan sənədləri oxuyur; qalanlarını `document-data/` ilə manual daxil etmək olar.

**Birləşmiş fayl.** `bundle/` bir PDF/şəkil/TXT içində olan sənədləri məzmununa görə ayırır. Hər tapılan növ üçün faylın surəti həmin sənədə bağlanır, `documents[].usage.bundle = true`, `usage.pages` isə həmin sənədin səhifələridir. Faylda tapılmayan növlərə toxunulmur. Eyni növdən iki sənəd və ya heç bir sənəd tapılmazsa 400, AI xətasında 503 qaytarılır. Sorğu sinxrondur (180 saniyəyə qədər).

**İkitərəfli müqayisə.** Yoxlamada qəbul sənədi ümumiyyətlə yoxdursa, `compare/` sifariş ↔ faktura müqayisəsi aparır: `report.mode = "two_way"`, `report.notes` xəbərdarlıq verir, manual `mappings` yalnız `order` və `invoice` indekslərini saxlayır. Mübahisəli məbləğ: `max(0, faktura_miqdarı × faktura_qiyməti − sifariş_miqdarı × sifariş_qiyməti)`. Qəbul sənədi yüklənib, amma oxunmayıbsa, sistem ikitərəfli rejimə keçmir və insan yoxlaması tələb edir. Üçtərəfli hesabatda `mode = "three_way"`. Maksimum 10 MB: PDF, PNG, JPG, UTF-8 TXT. Fayllar açıq media URL ilə yayımlanmır.

### API açarı olmadan tam demo

```bash
export HESABCHECK_TOKEN='YOUR_TOKEN'
curl -X POST http://127.0.0.1:8000/api/cases/ \
  -H "Authorization: Token $HESABCHECK_TOKEN" -H 'Content-Type: application/json' \
  -d '{"title":"Ofis kağızı alışının yoxlanması","supplier":"Demo Təchizatçı MMC"}'
export HESABCHECK_CASE='RETURNED_CASE_UUID'
for kind in order receipt invoice; do
  curl -X POST "http://127.0.0.1:8000/api/cases/$HESABCHECK_CASE/document-data/" \
    -H "Authorization: Token $HESABCHECK_TOKEN" -H 'Content-Type: application/json' \
    --data-binary "@examples/$kind.json"
done
curl -X POST "http://127.0.0.1:8000/api/cases/$HESABCHECK_CASE/compare/" \
  -H "Authorization: Token $HESABCHECK_TOKEN" -H 'Content-Type: application/json' -d '{}'
```

Nəticə: `status: "mismatch"`, `report.disputed_amount: "240.00"`, `currency: "AZN"`. 100 sifariş, 80 qəbul, 100 faktura, vahid qiymət 12 AZN.

### AI çıxarışı

`.env` faylında `GEMINI_API_KEY` və hesabınızda mövcud, structured outputs dəstəkləyən `GEMINI_MODEL` təyin edin. Serveri yenidən başladın. Hər üç sənədi yükləyib `extract/` çağırın:

```bash
curl -X POST "http://127.0.0.1:8000/api/cases/$HESABCHECK_CASE/documents/" \
  -H "Authorization: Token $HESABCHECK_TOKEN" -F kind=order -F file=@examples/order.txt
```

Çıxarış [Gemini structured outputs](https://ai.google.dev/gemini-api/docs/structured-output) və [PDF document input](https://ai.google.dev/gemini-api/docs/document-processing) formatlarından istifadə edir. Sənəd məzmunu Google Gemini-yə göndərilir. JSON sxemi `examples/extraction.schema.json` faylındadır. Pul və miqdarlar JSON-da decimal **string** kimi ötürülür. Oxunmayan və ya sənəddə olmayan sahələr `null` olur. Mənbədə qablaşdırma sayı göstərilməyibsə, sistem onu təxmin etmir — manual yoxlama tələb olunur.

`extract/` sinxron işləyir: hər sənəd üçün 90 saniyə timeout və maksimum bir təkrar cəhd var. Üç sənədin ümumi emalı uzun çəkə bilər; frontend/proxy timeout-u buna uyğun seçin. Bu MVP-də background queue yoxdur. Xəta hər sənədin `extraction_status`, `error` sahələrində saxlanılır; uğursuz çıxarışda məlumat boş qalır. Endpoint qismən uğursuzluqda da 200 qaytara bilər, ona görə frontend hər sənədin statusunu yoxlamalıdır. Yenidən çıxarış bütün yüklənmiş sənədləri, o cümlədən manual düzəlişləri əvəzləyir.

Model adı, token istifadəsi və vaxt `documents[].usage` daxilində saxlanılır. API xərci üçün tarif uydurulmur; bu tokenləri istifadə etdiyiniz modelin faktiki tarifi ilə qiymətləndirin. Real provider inteqrasiyasını öz açarınızla ayrıca yoxlayın.

### Gemini API açarı və canlı test

1. [Google AI Studio API Keys](https://aistudio.google.com/apikey) səhifəsinə Google hesabınızla daxil olun və tələb olunarsa şərtləri qəbul edin.
2. Hazır açar varsa onu istifadə edin; yoxdursa **Create API key** seçin və layihə seçin/yaradın. Mövcud Cloud layihəsi görünmürsə Dashboard → Projects → Import projects edin.
3. Açarı `.env` faylında `GEMINI_API_KEY=` sətrinə yazın. `.env.example` sadəcə boş nümunədir. Açarı çata və repoya əlavə etməyin.
4. `GEMINI_MODEL=gemini-3.5-flash-lite` saxlayın. Bu model PDF/şəkil girişi və structured outputs dəstəkləyir; hesabınızın model girişi və kvotası canlı testdə yoxlanacaq.
5. İşləyən Django serverini yenidən başladın.
6. `python manage.py check_gemini` işlədin. Komanda `examples/order.txt`, `receipt.txt`, `invoice.txt` fayllarını real Gemini-yə göndərir, sonra backend ilə **240.00 AZN** fərqi yoxlayır. Bazaya məlumat yazmır; üç model sorğusu və API kvotası istifadə edir. Canlı test adi `manage.py test` zamanı işləmir.

`403`: açar/layihə icazələri; `404`: model adı və giriş; `429`: kvota və ya sorğu limiti. Limitləri AI Studio-da yoxlayın. Ödənişsiz istifadə imkanları model və hesab kvotasından asılıdır, limitsiz istifadə vədi yoxdur.

Rəsmi mənbələr: [API key təlimatı](https://ai.google.dev/gemini-api/docs/api-key), [model imkanları](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite).

### Məhsul uyğunlaşdırılması və qərar

Avtomatik uyğunlaşdırma unikal eyni SKU, SKU olmadıqda isə eyni məhsul adı ilə aparılır. Təkrarlanan SKU və fərqli adlar insan yoxlamasına gedir. `suggestions/` müxtəlif dillərdəki adlar üçün AI təklifləri verir; bunlar yoxlanmadan tətbiq olunmur. İstifadəçi qəbul etdikdə indeksləri və cari `revision`-ı `compare/`-a göndərin:

```json
{"revision": 3, "mappings": [{"order": 0, "receipt": 0, "invoice": 0}]}
```

İndekslər `documents[].data.lines` siyahısında **0-dan** başlayır. Hər sətir yalnız bir dəfə istifadə edilə bilər. Göndərilən siyahı bütöv uyğunlaşdırma siyahısıdır; kənarda qalan sətirlər yoxlama tələb edir. Köhnə revision 409 qaytarır. Vahid, qablaşdırma və valyuta uyğun gəlmirsə manual uyğunlaşdırma da avtomatik “uyğundur” nəticəsi vermir. Mənbə istinadları modeldən gəlir və istifadəçi orijinal sənəd ilə yoxlamalıdır.

`review/` nümunəsi:

```json
{"revision": 4, "decision": "disputed", "note": "20 ədəd mal çatışmır; düzəliş tələb edilsin."}
```

Qərarlar: `approved`, `disputed`, `needs_review`. `approved` yalnız `matched` hesabat üçün qəbul olunur. `revision`-ı son cavabdan götürün. Sənəd düzəlişi/yüklənməsi və təkrar çıxarış əvvəlki hesabatı və qərarı ləğv edir. Tarixçədə düzəlişdən əvvəlki/sonrakı məlumatlar saxlanılır. Məktub deterministik şablonla yaranır, AI tərəfindən yazılmır və heç yerə göndərilmir.

## Hesablama sərhədləri

Hər uyğun sətir üçün mübahisəli məbləğ:

```text
max(0, faktura_miqdarı × faktura_qiyməti − min(sifariş_miqdarı, qəbul_miqdarı) × sifariş_qiyməti)
```

Bu qayda qiymət və miqdar fərqini iki dəfə saymır. Pul `Decimal`, yuvarlaqlaşdırma 2 rəqəm və `ROUND_HALF_UP` ilə hesablanır. Miqdar/qiymət fərqi məbləğ sıfır olsa belə qeyd edilir. Ümumi məbləğ yalnız uyğunlaşdırılmış və hesablana bilən sətirlər üçündür; `amount_complete: false` yekun rəqəm kimi göstərilməməlidir. Valyuta çevrilməsi yoxdur.

Bir sifariş, bir qəbul, bir faktura və eyni valyuta üzrə mal sətirləri dəstəklənir. Vergi, endirim, daşınma haqqı, kredit fakturaları, qismən fakturalaşdırma qaydaları və çoxsaylı qəbul sənədlərinin birləşdirilməsi avtomatlaşdırılmır. Bunlar insan yoxlaması tələb edir. Bank ödənişi və mühasibat inteqrasiyası daxil deyil.

## Test və yoxlamalar

```bash
python manage.py test
python manage.py check
python manage.py spectacular --file openapi.yaml --validate --fail-on-warn
```

Testlər hesablamaları, itkin məlumatları, valyuta/qablaşdırma fərqlərini, mənbələri, manual uyğunlaşdırmanı, autentifikasiyanı, fayl yükləməsini, AI xətalarını və qərar tarixçəsini əhatə edir. AI testləri mock-dur; bunlar real sənəd tanıma dəqiqliyi və ya benchmark iddiası deyil.

Lokal verilənlər bazası və fayllar `.gitignore` ilə xaric edilib. SQLite bu lokal/hackathon versiyası üçündür. Public yerləşdirmədən əvvəl `DJANGO_DEBUG=0`, güclü `DJANGO_SECRET_KEY`, düzgün host/origin və HTTPS konfiqurasiyası tələb olunur; çoxsaylı paralel istifadəçi üçün PostgreSQL və background worker əlavə etmək lazımdır. Production yerləşdirmə və GitHub Actions təlimatı: [deploy/README.md](deploy/README.md). Frontend daxil deyil.

## Production və CI/CD

Docker, PostgreSQL, Gunicorn və HTTPS ilə yerləşdirmə: [deploy təlimatı](deploy/README.md). `master` branch-ə push uğurlu PostgreSQL testlərindən sonra avtomatik deploy başladır.
