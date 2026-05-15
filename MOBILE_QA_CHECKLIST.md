# Mobile QA Checklist — Phase 5

Manuel test rehberi. Production deploy'dan önce her viewport için
hızlı bir geçiş yap. Test sırası: 320 → 480 → 768 → 1280.

## Test Cihazları / Viewport

| Boyut | Cihaz | Test Şekli |
|---|---|---|
| 320×568 | iPhone SE | Chrome DevTools → Device Toolbar |
| 390×844 | iPhone 14 | Chrome DevTools |
| 768×1024 | iPad | Chrome DevTools |
| 1280×800 | Desktop | Normal tarayıcı penceresi |

---

## 1. Genel Layout (her viewport'ta)

- [ ] Sayfa açılırken yatay scroll **yok** (320px dahil)
- [ ] Header logo + nav butonları görünüyor (768+ tüm tab'lar; 480- bottom-nav)
- [ ] Tüm butonlar **min 44×44px** tap target (parmakla rahat basılabiliyor)
- [ ] Footer yatırım tavsiyesi disclaimer'ı her sayfada görünüyor

## 2. Hero / Anasayfa

- [ ] BIST30 ticker bar üstte düzgün kayıyor (60s loop)
- [ ] Search input + quick-tickers düzeni mobilde overflow yok
- [ ] "Tara" butonu tıklanınca scan başlıyor, progress bar görünüyor
- [ ] Top10 hisse tablosu mobilde scroll edilebiliyor (yatay overflow)

## 3. **HEATMAP — Phase 5.1.1 (KRİTİK)**

### 3.1 Skeleton Render
- [ ] Sayfa ilk açıldığında heatmap kartı içinde **shimmer iskelet** anında görünüyor (gri kareler titreşiyor)
- [ ] `data-testid="heatmap-skeleton"` DevTools'ta görünüyor
- [ ] Backend `computing=true` döndüğünde skeleton kalıyor, boş ekran yok

### 3.2 Polling
- [ ] Skeleton 5 saniyede bir API çağrısı yapıyor (Network sekmesinden doğrula)
- [ ] 30 saniye sonra hâlâ veri yoksa: "Veri henüz hazır değil, sayfa yenile" mesajı (`data-testid="heatmap-timeout"`)
- [ ] Veri hazır olunca skeleton anında gerçek heatmap'e dönüşüyor

### 3.3 Stale-While-Error
- [ ] Mevcut heatmap görünüyorken backend 500 döndür (manuel: `/api/heatmap` rota'sını killset) → eski heatmap üstüne kırmızı banner: "Bağlantı sorunu — son veriler gösteriliyor"
- [ ] `data-testid="heatmap-stale-banner"` görünüyor
- [ ] İlk yüklemede 500 dönerse "Heatmap yüklenemedi — bağlantı sorunu" (data-testid="heatmap-error")

### 3.4 Mobile heatmap (480- viewport)
- [ ] 480px altında heatmap **list-view**'a düşüyor (treemap yerine tablo)
- [ ] Her satır: ticker + fiyat + %değişim, min 44px yükseklik

### 3.5 AbortController
- [ ] Heatmap yüklenirken Cross sayfasına geç → console.error YOK, pending request iptal oluyor
- [ ] Geri Anasayfa'ya dönünce yeni loadHeatmap tetikleniyor

## 4. **TÜRKİYE FİLTRE — Phase 5.2.1 (KRİTİK)**

### 4.1 Görünürlük
- [ ] Bir hisseye tıkla (örn. THYAO) → detail panel açılıyor
- [ ] **Verdict block'un HEMEN altında** "🇹🇷 Türkiye Filtresi" section'ı var
- [ ] `data-testid="turkey-filter-section"` DevTools'ta görünüyor
- [ ] 4 satır görünüyor: Döviz Kalkanı, Faiz Direnci, Fiyat Geçişkenliği, TMS 29

### 4.2 Görsel Doğruluk
- [ ] Her satırda: ikon (💱📈🏷️📊) + isim + grade pill (A/B/C/D/F renkli) + progress bar + signed mult (+%X / -%X) + 1 cümle
- [ ] Mult > 1.0 → yeşil bar; mult < 1.0 → kırmızı bar; ortada flat
- [ ] Composite summary altta gri kart içinde
- [ ] "?" butonuna tıkla → modal açılıyor, 4 filtreyi açıklıyor

## 5. **SİNYAL AÇIKLAMA KARTLARI — Phase 5.2.2**

- [ ] Detail panel açıldıktan ~50ms sonra "⚡ Aktif Sinyaller — Açıklamalı" kartı doluyor
- [ ] Aktif sinyali olmayan hisselerde: "Bu hisse için aktif sinyal yok"
- [ ] Aktif sinyal varsa her kart: isim + reliability badge (✅/⚠️/🟡) + plain-TR açıklama + Sharpe/60g/⭐ tag'leri + suggested action
- [ ] `data-testid="signal-explain-card"` DevTools'ta görünüyor

## 6. **AI CONSENSUS — Phase 5.2.3**

- [ ] Detail panel açıldıktan sonra "🤖 AI Konsensüs" kartı 4 provider'ı paralel çağırıyor
- [ ] Network sekmesinde 1 tek `/api/ai/{symbol}/consensus` çağrısı var (4 ayrı değil — backend paralelize ediyor)
- [ ] Lider model badge: "⭐ Konsensüs Lider" (yeşil) — provider adı + confidence %
- [ ] Diğer 3 model accordion altında (`<details>` summary): tıklayınca açılıyor
- [ ] **Split case (4 model farklı):** badge "🤔 Modeller bölünmüş" (sarı), tüm yanıtlar eşit gösteriliyor
- [ ] Errored providers ayrı accordion'da: hata mesajı görünüyor

## 7. Hisse Detay — Genel

- [ ] Tab'lar (Özet / Neden / Skorlar / Değerleme / Kalite / Teknik / Göstergeler / Grafik) yatay scroll edilebiliyor mobilde
- [ ] Tab'lar arası geçişte içerik anında değişiyor, lag yok
- [ ] "⭐ Takibe Al" / "Takipten Çıkar" butonu çalışıyor

## 8. Cross Sayfası

- [ ] Sinyal listesi yüklenince A/B kalite badge'leri renkli görünüyor
- [ ] Sinyal sayısı 60'ın üzerindeyse pagination veya scroll çalışıyor
- [ ] Bullish (yeşil) / bearish (kırmızı) sinyal tipi badge'leri ayırt edilebiliyor

## 9. Macro Sayfası

- [ ] TCMB faiz / TÜFE / kur kartları görünüyor
- [ ] Macro AI yorumu yükleniyor
- [ ] (Gelecek) TradingView calendar widget mount noktası boş olduğunda placeholder var

## 10. Landing (`/`)

- [ ] Hero h1 + sub + CTA "Terminale Gir" çalışıyor
- [ ] Live preview card mobilde de görünüyor
- [ ] 3 value props (🇹🇷 / 📊 / 🤖) aynı satırda 768+, üst üste 480-
- [ ] Nasıl Çalışıyor 3 step görünüyor
- [ ] Founder story Berkay Kangal görünüyor
- [ ] Final CTA "Terminale Gir" çalışıyor (3. CTA)
- [ ] Disclaimer "Yatırım tavsiyesi değildir" altta görünüyor

## 11. Performance Smoke

- [ ] Anasayfa First Contentful Paint **< 2s** (DevTools Performance)
- [ ] /api/heatmap response **< 1s** (Network)
- [ ] /api/cross/{symbol}/explain response **< 500ms**
- [ ] /api/ai/{symbol}/consensus response **< 20s** (4 paralel AI çağrısı)
- [ ] Yatay scrollbar hiçbir viewport'ta görünmüyor

## 12. Console Sanity

- [ ] DevTools Console'da error YOK (ya da sadece beklenen 4xx CORS error'ları)
- [ ] Hisse detayda "AbortError" log'u olmamalı (eski heatmap polling cancel'ı)

---

## Hızlı Sanity Test Senaryosu (5 dakika)

1. **Mobile (375px):** Anasayfa aç → heatmap shimmer görünüyor mu? → THYAO'ya tıkla → Türkiye filter section verdict altında mı?
2. **Tablet (768px):** Cross sayfası aç → sinyal listesi düzgün mü?
3. **Desktop (1280px):** Hisse detayda AI consensus accordion açılıyor mu?
4. **Network kesik:** Tarayıcıda offline → heatmap stale banner görünüyor mu?
5. **Landing:** `/` aç → 3 CTA da `/terminal`'e gidiyor mu?

Hepsi ✅ ise production deploy hazır.
