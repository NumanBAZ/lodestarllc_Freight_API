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
