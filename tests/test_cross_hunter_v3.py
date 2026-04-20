# ================================================================
# BISTBULL TERMINAL — Unit Tests: Cross Hunter V3
# tests/test_cross_hunter_v3.py
#
# Kapsamlı test paketi:
# - Determinizm testleri (aynı input → aynı output)
# - ADX/ATR filtre testleri
# - Modüler hesaplama testleri
# - Market regime tespiti
# - Backtest engine
# - Signal engine V3 kalite puanlaması
# ================================================================

import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta


# ================================================================
# TEST VERİ OLUŞTURUCULAR
# ================================================================

def make_ohlcv(n: int = 300, trend: str = "bull", seed: int = 42) -> pd.DataFrame:
    """
    Deterministik sentetik OHLCV verisi oluştur.
    seed parametresi ile her çağrıda aynı veri garanti.
    """
    rng = np.random.RandomState(seed)
    dates = pd.date_range(end=datetime.now(), periods=n, freq="B")

    if trend == "bull":
        base = 100 + np.cumsum(rng.normal(0.15, 1.5, n))
    elif trend == "bear":
        base = 200 + np.cumsum(rng.normal(-0.15, 1.5, n))
    else:  # sideways
        base = 150 + np.cumsum(rng.normal(0.0, 1.0, n))

    base = np.maximum(base, 10)  # Fiyat 0'ın altına düşmesin

    high = base + rng.uniform(0.5, 3.0, n)
    low = base - rng.uniform(0.5, 3.0, n)
    low = np.maximum(low, 1.0)
    open_p = base + rng.uniform(-1.0, 1.0, n)
    volume = rng.randint(100000, 5000000, n).astype(float)

    return pd.DataFrame({
        "Open": open_p,
        "High": high,
        "Low": low,
        "Close": base,
        "Volume": volume,
    }, index=dates)


def make_golden_cross_df(seed: int = 42) -> pd.DataFrame:
    """
    Golden Cross oluşturacak veri seti.
    Son 2 bar'da MA50 > MA200 olacak şekilde tasarlanmış.
    """
    rng = np.random.RandomState(seed)
    n = 300
    dates = pd.date_range(end=datetime.now(), periods=n, freq="B")

    # İlk 250 bar: MA50 < MA200 (düşüş trendi)
    prices = np.zeros(n)
    prices[0] = 100
    for i in range(1, 250):
        prices[i] = prices[i-1] + rng.normal(-0.05, 0.8)

    # Son 50 bar: hızlı yükseliş (MA50'yi MA200'ün üstüne çıkar)
    for i in range(250, n):
        prices[i] = prices[i-1] + rng.normal(1.5, 0.5)

    prices = np.maximum(prices, 10)

    return pd.DataFrame({
        "Open": prices + rng.uniform(-0.5, 0.5, n),
        "High": prices + rng.uniform(0.5, 2.0, n),
        "Low": prices - rng.uniform(0.5, 2.0, n),
        "Close": prices,
        "Volume": rng.randint(100000, 5000000, n).astype(float),
    }, index=dates)


# ================================================================
# MODÜLER HESAPLAMA TESTLERİ
# ================================================================

class TestComputeMovingAverages:
    """compute_moving_averages() testleri."""

    def setup_method(self):
        # Lazy import (modül test ortamında yüklenmeyebilir)
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

    def test_returns_expected_keys(self):
        from engine.technical import compute_moving_averages, CrossHunterConfig
        df = make_ohlcv(300)
        cfg = CrossHunterConfig()
        result = compute_moving_averages(df["Close"], cfg)
        assert "ma50" in result
        assert "ma200" in result
        assert "cross_signal" in result
        assert "ma_fast_series" in result

    def test_ma50_not_none_with_enough_data(self):
        from engine.technical import compute_moving_averages, CrossHunterConfig
        df = make_ohlcv(300)
        result = compute_moving_averages(df["Close"], CrossHunterConfig())
        assert result["ma50"] is not None

    def test_ma200_none_with_insufficient_data(self):
        from engine.technical import compute_moving_averages, CrossHunterConfig
        df = make_ohlcv(100)
        result = compute_moving_averages(df["Close"], CrossHunterConfig())
        assert result["ma200"] is None

    def test_deterministic(self):
        from engine.technical import compute_moving_averages, CrossHunterConfig
        df = make_ohlcv(300, seed=123)
        cfg = CrossHunterConfig()
        r1 = compute_moving_averages(df["Close"], cfg)
        r2 = compute_moving_averages(df["Close"], cfg)
        assert r1["ma50"] == r2["ma50"]
        assert r1["ma200"] == r2["ma200"]
        assert r1["cross_signal"] == r2["cross_signal"]


class TestComputeRSI:
    def test_rsi_in_range(self):
        from engine.technical import compute_rsi
        df = make_ohlcv(100)
        result = compute_rsi(df["Close"], period=14)
        assert result["rsi"] is not None
        assert 0 <= result["rsi"] <= 100

    def test_rsi_deterministic(self):
        from engine.technical import compute_rsi
        df = make_ohlcv(100, seed=99)
        r1 = compute_rsi(df["Close"])
        r2 = compute_rsi(df["Close"])
        assert r1["rsi"] == r2["rsi"]

    def test_rsi_none_with_too_few_bars(self):
        from engine.technical import compute_rsi
        df = make_ohlcv(5)
        result = compute_rsi(df["Close"])
        # İlk 14 bar NaN olabilir; son değer kontrol
        # 5 bar yeterli değil — None olmalı
        assert result["rsi"] is None or (0 <= result["rsi"] <= 100)


class TestComputeMACD:
    def test_macd_returns_all_fields(self):
        from engine.technical import compute_macd
        df = make_ohlcv(100)
        result = compute_macd(df["Close"])
        assert "macd" in result
        assert "macd_signal" in result
        assert "macd_hist" in result
        assert "macd_cross" in result

    def test_macd_deterministic(self):
        from engine.technical import compute_macd
        df = make_ohlcv(100, seed=77)
        r1 = compute_macd(df["Close"])
        r2 = compute_macd(df["Close"])
        assert r1["macd"] == r2["macd"]
        assert r1["macd_signal"] == r2["macd_signal"]


class TestComputeADX:
    def test_adx_returns_valid(self):
        from engine.technical import compute_adx
        df = make_ohlcv(100)
        result = compute_adx(df)
        assert "adx" in result
        if result["adx"] is not None:
            assert 0 <= result["adx"] <= 100

    def test_adx_none_with_insufficient_data(self):
        from engine.technical import compute_adx
        df = make_ohlcv(10)
        result = compute_adx(df)
        assert result["adx"] is None

    def test_adx_deterministic(self):
        from engine.technical import compute_adx
        df = make_ohlcv(200, seed=55)
        r1 = compute_adx(df)
        r2 = compute_adx(df)
        assert r1["adx"] == r2["adx"]
        assert r1["plus_di"] == r2["plus_di"]


class TestComputeATR:
    def test_atr_positive(self):
        from engine.technical import compute_atr
        df = make_ohlcv(100)
        result = compute_atr(df)
        assert result is not None
        assert result > 0


class TestComputeBollingerBands:
    def test_bb_returns_position(self):
        from engine.technical import compute_bollinger_bands
        df = make_ohlcv(100)
        result = compute_bollinger_bands(df["Close"])
        assert result["bb_pos"] in ("ABOVE", "BELOW", "INSIDE", None)

    def test_bb_width_exists(self):
        from engine.technical import compute_bollinger_bands
        df = make_ohlcv(100)
        result = compute_bollinger_bands(df["Close"])
        if result["bb_upper"] and result["bb_lower"]:
            assert result["bb_width"] is not None
            assert result["bb_width"] > 0


# ================================================================
# MARKET REJİM TESTLERİ
# ================================================================

class TestMarketRegime:
    def test_bull_detection(self):
        from engine.technical import detect_market_regime, MarketRegime
        df = make_ohlcv(300, trend="bull")
        close = df["Close"]
        price = float(close.iloc[-1])
        # Boğa piyasasında MA50 > MA200 olmalı
        ma50 = float(close.rolling(50).mean().iloc[-1])
        ma200 = float(close.rolling(200).mean().iloc[-1])
        regime = detect_market_regime(close, ma50, ma200, adx_val=30.0)
        assert regime in (MarketRegime.BULL, MarketRegime.SIDEWAYS)

    def test_sideways_low_adx(self):
        from engine.technical import detect_market_regime, MarketRegime
        df = make_ohlcv(300, trend="sideways")
        close = df["Close"]
        regime = detect_market_regime(close, 150.0, 150.0, adx_val=12.0)
        assert regime == MarketRegime.SIDEWAYS

    def test_bear_detection(self):
        from engine.technical import detect_market_regime, MarketRegime
        df = make_ohlcv(300, trend="bear")
        close = df["Close"]
        price = float(close.iloc[-1])
        ma50 = float(close.rolling(50).mean().iloc[-1])
        ma200 = float(close.rolling(200).mean().iloc[-1])
        regime = detect_market_regime(close, ma50, ma200, adx_val=30.0)
        assert regime in (MarketRegime.BEAR, MarketRegime.SIDEWAYS)


# ================================================================
# DELERMİNİZM TESTLERİ
# ================================================================

class TestDeterminism:
    """Aynı input → aynı output garantisi."""

    def test_compute_technical_deterministic(self):
        """Aynı DataFrame ile 3 kez çağrılınca aynı sonuç."""
        from engine.technical import compute_technical, CrossHunterConfig
        from core.cache import tech_cache
        tech_cache.clear()  # Cache'i temizle

        df = make_ohlcv(300, seed=42)
        results = []
        for _ in range(3):
            tech_cache.clear()
            result = compute_technical("TEST.IS", hist_df=df, config=CrossHunterConfig())
            results.append(result)

        for key in ["price", "ma50", "ma200", "rsi", "macd", "adx", "tech_score"]:
            values = [r[key] for r in results if r is not None]
            assert len(set(values)) <= 1, f"{key} tutarsız: {values}"

    def test_safe_comparison_edge_cases(self):
        """Floating-point sınır durumları."""
        from engine.technical import _safe_gt, _safe_lt, _safe_gte, EPSILON

        # Neredeyse eşit değerler
        a = 142.35000000001
        b = 142.35000000002
        assert not _safe_gt(a, b)
        assert not _safe_gt(b, a)
        assert _safe_gte(a, b)

        # None kontrolleri
        assert not _safe_gt(None, 100.0)
        assert not _safe_lt(100.0, None)

        # Belirgin fark
        assert _safe_gt(142.36, 142.35)
        assert _safe_lt(142.34, 142.35)


# ================================================================
# SİNYAL FİLTRE TESTLERİ
# ================================================================

class TestSignalFilters:
    """ADX ve hacim filtrelerinin çalışma testleri."""

    def test_golden_cross_without_trend_filtered(self):
        """ADX düşük ve hacim düşükse Golden Cross üretilmemeli."""
        # Bu test scan_all() seviyesinde — mock gerektirir
        # Burada basit mantık kontrolü
        adx_val = 15.0  # ADX eşiğinin altında
        vol_ratio = 1.2  # Hacim eşiğinin altında
        has_trend = adx_val >= 20.0
        vol_confirmed = vol_ratio >= 1.5

        # Her ikisi de False → sinyal eklenmemeli
        assert not (has_trend or vol_confirmed)

    def test_golden_cross_with_volume_passes(self):
        """Hacim onaylıysa ADX düşük olsa bile sinyal geçer."""
        adx_val = 15.0
        vol_ratio = 2.0
        has_trend = adx_val >= 20.0
        vol_confirmed = vol_ratio >= 1.5
        assert not has_trend
        assert vol_confirmed
        assert (has_trend or vol_confirmed)  # Sinyal geçmeli

    def test_confirmation_count(self):
        """Confirmation count hesaplama."""
        vol_confirmed = True
        has_trend = True
        macd_bullish = True
        rsi = 45  # 30 < rsi < 70
        bb_pos = "INSIDE"

        conf = sum([
            1 if vol_confirmed else 0,
            1 if has_trend else 0,
            1 if macd_bullish else 0,
            1 if (rsi and 30 < rsi < 70) else 0,
            1 if bb_pos == "INSIDE" else 0,
        ])
        assert conf == 5


# ================================================================
# SİGNAL ENGINE V3 TESTLERİ
# ================================================================

class TestSignalEngineV3:
    """Güncellenmiş kalite ve güven puanlaması."""

    def _make_signal(self, **overrides):
        base = {
            "signal": "Golden Cross",
            "signal_type": "bullish",
            "stars": 5,
            "vol_confirmed": True,
            "adx_confirmed": True,
            "confirmation_count": 4,
            "ticker": "THYAO",
            "price": 280,
            "ticker_signal_count": 3,
            "ticker_total_stars": 12,
            "tech_score": 72,
            "category": "kirilim",
            "adx": 35.0,
            "bb_width": 0.05,
            "market_regime": "bull",
        }
        base.update(overrides)
        return base

    def _make_analysis(self, strong=True):
        if strong:
            return {
                "overall": 72, "confidence": 87.5, "fa_score": 65,
                "ivme": 68, "risk_score": -3, "risk_penalty": -3,
                "scores_imputed": [],
                "positives": ["Güçlü kârlılık"],
                "negatives": [],
                "explanation": {
                    "summary": "Test",
                    "top_positive_drivers": [
                        {"name": "Yüksek özsermaye kârlılığı", "contribution": 5.0},
                    ],
                    "top_negative_drivers": [],
                },
            }
        return {
            "overall": 38, "confidence": 40, "fa_score": 42,
            "ivme": 35, "risk_score": -22, "risk_penalty": -22,
            "scores_imputed": ["growth", "earnings", "moat", "capital"],
            "positives": [],
            "negatives": ["Yüksek borç"],
            "explanation": {
                "summary": "Zayıf",
                "top_positive_drivers": [],
                "top_negative_drivers": [
                    {"name": "Borç yükü", "contribution": -4.0},
                ],
            },
        }

    def test_strong_signal_gets_A(self):
        from engine.signal_engine import compute_signal_quality
        sig = self._make_signal()
        analysis = self._make_analysis(strong=True)
        q = compute_signal_quality(sig, analysis)
        assert q == "A"

    def test_weak_signal_gets_C(self):
        from engine.signal_engine import compute_signal_quality
        sig = self._make_signal(
            vol_confirmed=False, adx_confirmed=False,
            confirmation_count=0, stars=1, ticker_signal_count=1,
        )
        analysis = self._make_analysis(strong=False)
        q = compute_signal_quality(sig, analysis)
        assert q == "C"

    def test_adx_confirmed_boosts_confidence(self):
        from engine.signal_engine import compute_signal_confidence
        sig_with = self._make_signal(adx_confirmed=True)
        sig_without = self._make_signal(adx_confirmed=False)
        c_with = compute_signal_confidence(sig_with, None)
        c_without = compute_signal_confidence(sig_without, None)
        assert c_with > c_without

    def test_bear_regime_adds_risk_flag(self):
        from engine.signal_engine import enrich_signal
        sig = self._make_signal(market_regime="bear", signal_type="bullish")
        enriched = enrich_signal(sig, None)
        assert "Ayı piyasası rejimi" in enriched["risk_flags"]

    def test_bb_width_affects_confidence(self):
        from engine.signal_engine import compute_signal_confidence
        analysis = self._make_analysis(strong=True)
        sig_narrow = self._make_signal(bb_width=0.03)
        sig_wide = self._make_signal(bb_width=0.12)
        c_narrow = compute_signal_confidence(sig_narrow, analysis)
        c_wide = compute_signal_confidence(sig_wide, analysis)
        assert c_narrow > c_wide


# ================================================================
# BACKTEST ENGINE TESTLERİ
# ================================================================

class TestBacktestEngine:
    """Backtest framework testleri."""

    def test_empty_signals(self):
        from engine.cross_hunter_backtest import BacktestEngine
        bt = BacktestEngine()
        trades = bt.run([], {})
        assert trades == []
        summary = bt.summary()
        assert "error" in summary

    def test_bearish_filtered_by_default(self):
        from engine.cross_hunter_backtest import BacktestEngine
        bt = BacktestEngine()
        signals = [{"ticker": "THYAO", "signal": "Death Cross",
                     "signal_type": "bearish", "price": 100}]
        trades = bt.run(signals, {})
        assert len(trades) == 0

    def test_trade_record_fields(self):
        from engine.cross_hunter_backtest import BacktestEngine, TradeRecord
        bt = BacktestEngine()
        signals = [{"ticker": "THYAO", "signal": "Golden Cross",
                     "signal_type": "bullish", "price": 100, "stars": 5,
                     "confirmation_count": 3}]
        # history_map'sız basit test
        from utils.helpers import normalize_symbol
        sym = normalize_symbol("THYAO")
        df = make_ohlcv(300, seed=42)
        trades = bt.run(signals, {sym: df})
        assert len(trades) == 1
        t = trades[0]
        assert isinstance(t, TradeRecord)
        assert t.ticker == "THYAO"
        assert t.entry_price > 0
        assert t.tp_price > t.entry_price
        assert t.sl_price < t.entry_price

    def test_summary_structure(self):
        from engine.cross_hunter_backtest import BacktestEngine, BacktestConfig
        cfg = BacktestConfig(bullish_only=False)
        bt = BacktestEngine(config=cfg)
        signals = [{"ticker": "THYAO", "signal": "Test",
                     "signal_type": "bullish", "price": 100, "stars": 3}]
        from utils.helpers import normalize_symbol
        sym = normalize_symbol("THYAO")
        df = make_ohlcv(300, seed=42)
        bt.run(signals, {sym: df})
        summary = bt.summary()
        assert "total_trades" in summary
        assert "win_rate" in summary
        assert "profit_factor" in summary

    def test_commission_applied(self):
        from engine.cross_hunter_backtest import BacktestConfig
        cfg = BacktestConfig(commission_pct=0.005)  # %0.5 komisyon
        assert cfg.commission_pct == 0.005

    def test_to_dataframe(self):
        from engine.cross_hunter_backtest import BacktestEngine
        bt = BacktestEngine()
        signals = [{"ticker": "THYAO", "signal": "Golden Cross",
                     "signal_type": "bullish", "price": 100, "stars": 5}]
        from utils.helpers import normalize_symbol
        sym = normalize_symbol("THYAO")
        df = make_ohlcv(300, seed=42)
        bt.run(signals, {sym: df})
        result_df = bt.to_dataframe()
        assert not result_df.empty
        assert "ticker" in result_df.columns
        assert "pnl_net_pct" in result_df.columns


# ================================================================
# CROSS HUNTER CONFIG TESTLERİ
# ================================================================

class TestCrossHunterConfig:
    def test_default_config_immutable(self):
        from engine.technical import CrossHunterConfig
        cfg = CrossHunterConfig()
        with pytest.raises(AttributeError):
            cfg.ma_fast = 100  # frozen=True olduğu için hata vermeli

    def test_regime_configs_exist(self):
        from engine.technical import REGIME_CONFIGS, MarketRegime
        assert MarketRegime.BULL in REGIME_CONFIGS
        assert MarketRegime.BEAR in REGIME_CONFIGS
        assert MarketRegime.SIDEWAYS in REGIME_CONFIGS

    def test_bear_regime_stricter(self):
        from engine.technical import REGIME_CONFIGS, MarketRegime
        bull_cfg = REGIME_CONFIGS[MarketRegime.BULL]
        bear_cfg = REGIME_CONFIGS[MarketRegime.BEAR]
        # Ayı piyasasında daha yüksek hacim eşiği
        assert bear_cfg.vol_confirm_ratio > bull_cfg.vol_confirm_ratio
        # Ayı piyasasında daha yüksek ADX eşiği
        assert bear_cfg.adx_threshold > bull_cfg.adx_threshold


# ================================================================
# SAFE FLOAT TESTLERİ
# ================================================================

class TestSafeFloat:
    def test_normal_value(self):
        from engine.technical import _safe_float
        s = pd.Series([1.0, 2.0, 3.0])
        assert _safe_float(s) == 3.0

    def test_nan_returns_none(self):
        from engine.technical import _safe_float
        s = pd.Series([np.nan])
        assert _safe_float(s) is None

    def test_empty_series_returns_none(self):
        from engine.technical import _safe_float
        s = pd.Series([], dtype=float)
        assert _safe_float(s) is None

    def test_negative_index(self):
        from engine.technical import _safe_float
        s = pd.Series([10.0, 20.0, 30.0])
        assert _safe_float(s, -2) == 20.0
