# LodeStar Freight Quote Platform

FastAPI tabanlı, müşteri odaklı WARP taşıma teklif platformu. Uygulama yalnızca teklif ve izin verilen salt-okunur işlemleri destekler; rezervasyon, yeniden rezervasyon ve ödeme uçları sunulmaz.

## Özellikler

- Rota, yük bilgileri ve ek hizmetlerden oluşan üç adımlı teklif akışı
- LTL için WARP `market-options` yanıtındaki tüm taşıyıcıların karşılaştırılması
- En düşük fiyat, en hızlı teslimat ve taşıyıcı adına göre sıralama
- İlk altı teklif ve isteğe bağlı tüm teklifler görünümü
- FTL, Box Truck ve Cargo Van için tek WARP Network sonucu
- Teklif seçimi, özet ve yapılandırılabilir iletişim kanalları
- Mobil uyumlu kartlar ve karşılaştırma görünümü
- Normal kullanımda gizli, yalnızca `?debug=true` ile açılan geliştirici çıktısı

## Kurulum

Python 3.11 veya daha güncel bir sürüm önerilir.

```powershell
cd "C:\Users\mnbaz\Documents\lodestarllc_Freight_API"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env` içinde kullanılacak ortamı ve yalnızca backend tarafından okunacak anahtarı ayarlayın:

```dotenv
WARP_ENV=sandbox
WARP_SANDBOX_KEY=...
WARP_LIVE_KEY=...
WARP_BASE_URL=https://www.wearewarp.com/api/v1
```

Teklif seçiminden sonra iletişim düğmelerini etkinleştirmek için:

```dotenv
CONTACT_WHATSAPP=15551234567
CONTACT_PHONE=+15551234567
CONTACT_EMAIL=quotes@example.com
```

## Çalıştırma

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --reload
```

Ardından `http://127.0.0.1:8000` adresini açın.

## Test

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

API anahtarları HTML veya JavaScript içine yazılmaz ve hiçbir backend yanıtında döndürülmez. Ortam seçimi tarayıcıdan yapılamaz; yalnızca `.env` içindeki `WARP_ENV` değeri belirleyicidir.

## Staff Booking Panel

`/staff` paneli ayrı backend kimlik doğrulaması kullanır. Aşağıdaki değerleri
yalnızca `.env` veya deployment secret ayarlarında tanımlayın:

```dotenv
STAFF_USERNAME=lodestar-staff
STAFF_PASSWORD=...
STAFF_SESSION_SECRET=... # en az 32 rastgele karakter
STAFF_COOKIE_SECURE=true # production
```

Production booking gerçek mali işlem oluşturabilir. Geliştirme ve otomatik
testlerde `WARP_ENV=sandbox` ile `wak_test_*` anahtarı kullanın. Public
`/api/warp/*` allowlist'i booking işlemi sunmaz; `/api/staff/book` imzalı staff
session ve CSRF doğrulaması gerektirir.

### Vercel production variables

Vercel project settings under **Environment Variables** must define these names
for the Production environment. Store real values only in Vercel; do not commit
them to the repository:

```text
STAFF_USERNAME
STAFF_PASSWORD
STAFF_SESSION_SECRET
STAFF_COOKIE_SECURE
STAFF_BOOKING_ENABLED
```

Set `STAFF_COOKIE_SECURE=true`. Keep `STAFF_BOOKING_ENABLED=false` until live
booking is intentionally approved. When disabled, staff can quote and review a
bookable offer, but both the confirmation UI and backend prevent `POST /book`.
