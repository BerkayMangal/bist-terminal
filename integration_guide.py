# ================================================================
# BISTBULL TERMINAL — CROSS HUNTER V3 ENTEGRASYON REHBERİ
# integration_guide.py
#
# Bu dosya doğrudan çalıştırılmaz — entegrasyon referansıdır.
# Her bölüm hangi dosyada ne değişeceğini gösterir.
# ================================================================

"""
╔══════════════════════════════════════════════════════════════════╗
║          CROSS HUNTER V3 — ENTEGRASYON PLANI                    ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  ADIM 1: Dosya Değiştirme                                       ║
║  ─────────────────────────                                       ║
║  engine/technical.py     ← cross_hunter_v3.py ile değiştir      ║
║  engine/signal_engine.py ← signal_engine_v3.py ile değiştir     ║
║  engine/cross_hunter_backtest.py ← YENİ dosya ekle              ║
║  tests/test_cross_hunter_v3.py   ← YENİ test dosyası ekle      ║
║                                                                  ║
║  ADIM 2: Config Güncellemesi (config.py)                        ║
║  ─────────────────────────────────────                          ║
║  Yeni config sabitleri ekle (aşağıya bak)                       ║
║                                                                  ║
║  ADIM 3: Background Tasks Güncellemesi                          ║
║  ─────────────────────────────────────                          ║
║  paper_trade_loop() içine backtest raporlama ekle               ║
║                                                                  ║
║  ADIM 4: API Endpoint (opsiyonel)                               ║
║  ────────────────────────────────                               ║
║  /api/backtest endpoint'i ekle                                  ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
"""

# ================================================================
# ADIM 2: config.py'ye EKLENECEK YENİ SABİTLER
# ================================================================

# --- config.py'nin sonuna ekle ---

"""
# ================================================================
# CROSS HUNTER V3 CONFIG
# ================================================================

# ADX (Average Directional Index)
ADX_PERIOD: int = 14
ADX_TREND_THRESHOLD: float = 20.0

# ATR (Average True Range)
ATR_PERIOD: int = 14
ATR_BREAKOUT_MULTIPLIER: float = 0.5

# Hacim onay eşiği (V3'te yükseltildi)
VOL_CONFIRM_RATIO: float = 1.5

# Backtest defaults
BACKTEST_TP_PCT: float = 0.03
BACKTEST_SL_PCT: float = 0.02
BACKTEST_COMMISSION_PCT: float = 0.002
BACKTEST_SLIPPAGE_PCT: float = 0.001
BACKTEST_MAX_HOLDING_BARS: int = 20

# Market regime dynamic parameters
REGIME_BULL_ADX_THRESHOLD: float = 18.0
REGIME_BEAR_ADX_THRESHOLD: float = 25.0
REGIME_SIDEWAYS_ADX_THRESHOLD: float = 15.0
"""


# ================================================================
# ADIM 3: background_tasks.py GÜNCELLEME
# ================================================================

"""
engine/background_tasks.py'de paper_trade_loop() fonksiyonuna
haftalık backtest rapor üretimi eklenebilir:

    # Her Pazartesi 02:00'da haftalık backtest raporu
    if dt.datetime.now().weekday() == 0 and dt.datetime.now().hour == 2:
        from engine.cross_hunter_backtest import BacktestEngine
        from engine.signal_tracker import signal_tracker
        
        bt = BacktestEngine()
        all_signals = signal_tracker._signals  # Tüm tarihsel sinyaller
        # ... backtest çalıştır ve Redis'e kaydet
"""


# ================================================================
# ADIM 4: API ENDPOINT ÖRNEĞİ (app.py'ye ekle)
# ================================================================

"""
# app.py içinde, diğer API endpoint'lerinin yanına:

@app.get("/api/signals/backtest")
async def api_backtest(
    days: int = Query(default=30, ge=7, le=365),
    min_stars: int = Query(default=1, ge=1, le=5),
    timeframe: str = Query(default="1G"),
):
    from engine.cross_hunter_backtest import (
        BacktestEngine, BacktestConfig, TIMEFRAME_BACKTEST_CONFIGS,
    )
    from engine.signal_tracker import signal_tracker
    
    cfg = TIMEFRAME_BACKTEST_CONFIGS.get(timeframe, BacktestConfig())
    cfg_dict = {
        "min_stars": min_stars,
        "tp_pct": cfg.tp_pct,
        "sl_pct": cfg.sl_pct,
        "commission_pct": cfg.commission_pct,
    }
    
    # Not: Gerçek backtest için walk-forward gerekir
    # Bu endpoint sadece mevcut sinyallerin summary'sini verir
    track_record = signal_tracker.get_track_record(days=days)
    
    return {
        "backtest_config": cfg_dict,
        "track_record": track_record,
    }
"""


# ================================================================
# PRATİK KULLANIM ÖRNEKLERİ
# ================================================================

def example_basic_usage():
    """
    Temel kullanım: CrossHunter V3 ile scan ve backtest.
    
    Bu örnek, mevcut kod yapısı ile nasıl entegre edileceğini gösterir.
    """
    from engine.technical import (
        CrossHunter,
        CrossHunterConfig,
        MarketRegime,
        REGIME_CONFIGS,
        batch_download_history,
        compute_adx,
    )
    from engine.signal_engine import enrich_signals
    from engine.cross_hunter_backtest import BacktestEngine, BacktestConfig
    from config import UNIVERSE
    from utils.helpers import normalize_symbol
    
    # 1. Veri indir
    symbols = [normalize_symbol(t) for t in UNIVERSE]
    history_map = batch_download_history(symbols, period="1y", interval="1d")
    
    # 2. CrossHunter V3 — adaptive regime
    hunter = CrossHunter()  # Default config
    signals = hunter.scan_all(
        history_map=history_map,
        adaptive_regime=True,  # V3: Piyasa rejimine göre parametre ayarla
    )
    
    print(f"Toplam yeni sinyal: {len(signals)}")
    print(f"Tespit edilen rejim: {hunter.last_regime.value}")
    
    # 3. Her sinyal artık V3 ek alanlarına sahip
    for sig in signals[:5]:
        print(f"  {sig['ticker']} | {sig['signal']}")
        print(f"    ADX teyidi: {sig.get('adx_confirmed', False)}")
        print(f"    Hacim teyidi: {sig.get('vol_confirmed', False)}")
        print(f"    Onay sayısı: {sig.get('confirmation_count', 0)}")
        print(f"    Piyasa rejimi: {sig.get('market_regime', 'N/A')}")
    
    # 4. Signal Engine ile zenginleştir
    from core.cache import analysis_cache
    enriched = enrich_signals(signals, analysis_cache)
    
    for sig in enriched[:3]:
        print(f"  {sig['ticker']}: Kalite={sig['signal_quality']}, "
              f"Güven={sig['signal_confidence']}")
    
    return signals, history_map


def example_backtest():
    """
    Backtest örneği: sinyalleri geçmiş veri üzerinde test et.
    """
    from engine.technical import CrossHunter
    from engine.cross_hunter_backtest import (
        BacktestEngine,
        BacktestConfig,
        TIMEFRAME_BACKTEST_CONFIGS,
    )
    from config import UNIVERSE
    from utils.helpers import normalize_symbol
    from engine.technical import batch_download_history
    
    # 1. Veri indir
    symbols = [normalize_symbol(t) for t in UNIVERSE]
    history_map = batch_download_history(symbols, period="1y", interval="1d")
    
    # 2. Sinyal üret
    hunter = CrossHunter()
    signals = hunter.scan_all(history_map=history_map)
    
    # 3. Backtest config seç
    # Günlük timeframe — komisyon dahil
    cfg = BacktestConfig(
        tp_pct=0.03,            # %3 take profit
        sl_pct=0.02,            # %2 stop loss
        commission_pct=0.002,   # %0.2 BIST komisyon
        slippage_pct=0.001,     # %0.1 slippage
        max_holding_bars=20,    # Max 20 iş günü
        bullish_only=True,      # Sadece alış sinyalleri
        min_stars=3,            # Minimum 3 yıldız
        min_confirmations=2,    # Minimum 2 teyit (V3)
    )
    
    # 4. Backtest çalıştır
    bt = BacktestEngine(config=cfg)
    trades = bt.run(signals, history_map)
    
    # 5. Sonuçları analiz et
    summary = bt.summary()
    print("\n═══ BACKTEST SONUÇLARI ═══")
    print(f"Toplam trade: {summary['total_trades']}")
    print(f"Kapanan: {summary['closed_trades']}")
    print(f"  TP: {summary['tp_count']}")
    print(f"  SL: {summary['sl_count']}")
    print(f"  Timeout: {summary['timeout_count']}")
    print(f"Win Rate (brüt): %{summary['win_rate']}")
    print(f"Win Rate (net):  %{summary['win_rate_net']}")
    print(f"Ort. P&L (brüt): %{summary['avg_pnl_pct']}")
    print(f"Ort. P&L (net):  %{summary['avg_pnl_net_pct']}")
    print(f"Profit Factor: {summary['profit_factor']}")
    print(f"Ort. Holding: {summary['avg_bars_held']} bar")
    print(f"Toplam Komisyon: %{summary['total_commission_pct']}")
    
    # 6. Sinyal tipine göre breakdown
    print("\n═══ SİNYAL BAZLI PERFORMANS ═══")
    for sig_type, stats in summary.get("by_signal", {}).items():
        print(f"  {sig_type}: {stats['count']} trade, "
              f"WR=%{stats['win_rate']}, "
              f"Ort. P&L=%{stats['avg_pnl_net_pct']}")
    
    # 7. Onay sayısına göre breakdown (V3 filtre kalitesi)
    print("\n═══ ONAY SAYISI PERFORMANSI ═══")
    for conf, stats in summary.get("by_confirmation", {}).items():
        print(f"  {conf} onay: {stats['count']} trade, "
              f"WR=%{stats['win_rate']}, "
              f"Ort. P&L=%{stats['avg_pnl_net_pct']}")
    
    # 8. DataFrame olarak export
    df = bt.to_dataframe()
    print(f"\nDataFrame shape: {df.shape}")
    # df.to_csv("backtest_results.csv", index=False)
    
    return summary


def example_walk_forward_backtest():
    """
    Walk-Forward Backtest: Look-ahead bias olmadan gerçekçi test.
    
    Bu en doğru backtest yöntemidir:
    - Veriyi parçalara böl
    - Her parçada scan yap (sadece o ana kadar gelen veriyi gör)
    - Sonraki parça ile TP/SL kontrol et
    """
    from engine.technical import CrossHunter, batch_download_history
    from engine.cross_hunter_backtest import BacktestEngine, BacktestConfig
    from config import UNIVERSE
    from utils.helpers import normalize_symbol
    
    symbols = [normalize_symbol(t) for t in UNIVERSE]
    history_map = batch_download_history(symbols, period="1y", interval="1d")
    
    hunter = CrossHunter()
    cfg = BacktestConfig(
        tp_pct=0.03,
        sl_pct=0.02,
        commission_pct=0.002,
        max_holding_bars=20,
        min_stars=3,
    )
    
    bt = BacktestEngine(config=cfg)
    trades = bt.run_walkforward(
        history_map=history_map,
        scan_fn=hunter.scan_all,
        lookback_bars=252,    # 1 yıl geriye bak
        step_bars=5,          # 1 hafta ilerle
    )
    
    summary = bt.summary()
    print(f"Walk-Forward: {summary['total_trades']} trade, "
          f"WR={summary['win_rate']}%")
    
    return summary


def example_custom_config():
    """
    Özel config ile farklı piyasa koşullarını test et.
    """
    from engine.technical import CrossHunter, CrossHunterConfig
    
    # Agresif strateji — düşük eşikler
    aggressive = CrossHunterConfig(
        adx_threshold=15.0,
        vol_confirm_ratio=1.2,
        atr_breakout_mult=0.3,
        rsi_overbought=75.0,   # Daha geç sat
        rsi_oversold=25.0,     # Daha geç al
        min_confirmations=1,
    )
    
    # Konservatif strateji — yüksek eşikler
    conservative = CrossHunterConfig(
        adx_threshold=25.0,
        vol_confirm_ratio=2.0,
        atr_breakout_mult=0.7,
        rsi_overbought=65.0,
        rsi_oversold=35.0,
        min_confirmations=3,
    )
    
    hunter_agg = CrossHunter(config=aggressive)
    hunter_con = CrossHunter(config=conservative)
    
    # Her iki config ile scan yap ve sinyal sayısını karşılaştır
    # signals_agg = hunter_agg.scan_all(history_map=...)
    # signals_con = hunter_con.scan_all(history_map=...)
    # print(f"Agresif: {len(signals_agg)} sinyal")
    # print(f"Konservatif: {len(signals_con)} sinyal")


def example_compare_regime_performance():
    """
    Piyasa rejimine göre backtest karşılaştırması.
    """
    from engine.technical import CrossHunter, REGIME_CONFIGS, MarketRegime
    from engine.cross_hunter_backtest import BacktestEngine, BacktestConfig
    
    results = {}
    for regime in MarketRegime:
        cfg = REGIME_CONFIGS[regime]
        hunter = CrossHunter(config=cfg)
        bt = BacktestEngine(config=BacktestConfig(
            min_confirmations=cfg.min_confirmations,
        ))
        # signals = hunter.scan_all(history_map=...)
        # trades = bt.run(signals, history_map)
        # results[regime.value] = bt.summary()
    
    # Karşılaştır
    # for regime, summary in results.items():
    #     print(f"{regime}: WR={summary['win_rate']}%, PF={summary['profit_factor']}")


# ================================================================
# SIGNAL TRACKER ENTEGRASYONU
# ================================================================

"""
Mevcut signal_tracker.py hiçbir değişiklik gerektirmez.
CrossHunter V3'ün ürettiği yeni alanlar (adx_confirmed,
confirmation_count, market_regime) signal_tracker tarafından
otomatik olarak kaydedilir — record dict'e **details ile eklenir.

signal_tracker.log_signals() zaten sig.get("vol_confirmed")
kullanıyor — V3'ün ürettiği sinyaller backward-compatible.
"""


# ================================================================
# SCAN COORDINATOR ENTEGRASYONU
# ================================================================

"""
core/scan_coordinator.py'de CrossHunter çağrısı şu şekilde yapılır:

    from engine.technical import cross_hunter
    signals = cross_hunter.scan_all(history_map=history_map)

V3'te bu çağrı AYNI — sadece cross_hunter global instance'ı
artık V3 CrossHunter sınıfını kullanıyor. Ek parametre:

    signals = cross_hunter.scan_all(
        history_map=history_map,
        adaptive_regime=True,  # Yeni — rejime göre adaptif
    )

adaptive_regime=False geçilirse V2 davranışı korunur.
"""


# ================================================================
# GEÇİŞ KONTROLÜ (Migration Checklist)
# ================================================================

MIGRATION_CHECKLIST = """
□ engine/technical.py → cross_hunter_v3.py ile değiştirildi
□ engine/signal_engine.py → signal_engine_v3.py ile değiştirildi  
□ engine/cross_hunter_backtest.py → yeni dosya eklendi
□ tests/test_cross_hunter_v3.py → yeni test dosyası eklendi
□ config.py → V3 sabitleri eklendi
□ pytest çalıştırıldı ve tüm testler geçti
□ Mevcut signal_tracker uyumluluğu kontrol edildi
□ scan_coordinator.py'de adaptive_regime parametresi eklendi (opsiyonel)
□ /api/backtest endpoint'i eklendi (opsiyonel)
□ Redis'te eski cache invalidate edildi (tech_cache.clear())
"""
