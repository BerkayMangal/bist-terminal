# TahtaLab — Araştırma Notları (v2+ planı)

TahtaLab v1 **canlı bir uyarı sayfasıdır** — backtest motoru DEĞİLDİR.
Bu klasör yalnızca gelecekteki doğrulama çalışmalarının planını
belgeler; v1'de kod içermez.

## v1 kapsamı (mevcut)
- `engine/tahta_warning_registry.py` — 10 uyarı kuralının tipli tanımı.
- `engine/tahta_warnings.py` — OR-bazlı uyarı motoru (günlük OHLCV).
- `api/tahtalab.py` — `GET /api/tahtalab`, `GET /api/tahtalab/{ticker}`.
- Frontend: TahtaLab sekmesi.

v1'de yalnız günlük (EOD) OHLCV gerektiren kurallar canlı uyarı üretir.
Intraday ve kurumsal-olay kuralları kütüphanede görünür ama veri
olmadan uyarı üretmez.

## v2+ planlanan çalışmalar (henüz uygulanmadı)
1. **Event-study**: her uyarı kuralının tetiklendiği günden sonraki
   1g / 3g / 5g / 10g getiri dağılımı. "Geçmişte sık görülen" iddiasını
   ölçülebilir kılmak.
2. **Walk-forward doğrulama**: eşiklerin dönem-dışı stabilitesi.
3. **Intraday entegrasyonu**: `hold_above_open` / `pressure_below_open`
   kuralları için gün içi bar verisi.
4. **KAP kurumsal-olay beslemesi**: `split_at_peak` için gerçek
   bölünme/bedelsiz tetikleyicileri.
5. **Kalibrasyon**: kural başına taban oranı (base rate) ve gürültü
   filtresi.

## İlkeler
- TahtaLab AL/SAT önerisi üretmez; üretmemelidir.
- Veri yoksa uyarı uydurulmaz — "veri yok" durumu açıkça gösterilir.
- Backtest sonuçları üretilene kadar kurallar "geçmişte sık görülen
  davranış" olarak sunulur, kanıtlanmış edge olarak değil.
