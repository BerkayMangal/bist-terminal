# ================================================================
# BISTBULL TERMINAL — CROSS HUNTER BACKTEST FRAMEWORK
# engine/cross_hunter_backtest.py
#
# Cross Hunter sinyallerini geçmiş veriler üzerinde test eder.
# Komisyon dahil gerçekçi P&L hesaplama.
#
# KULLANIM:
#   from engine.cross_hunter_backtest import BacktestEngine
#   bt = BacktestEngine(commission_pct=0.002)
#   results = bt.run(signals, history_map)
#   summary = bt.summary()
# ================================================================

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

import numpy as np
import pandas as pd

log = logging.getLogger("bistbull.backtest")


# ================================================================
# SABİTLER & TİPLER
# ================================================================

class TradeOutcome(Enum):
    """Pozisyon kapanış sebebi."""
    TP_HIT = "tp"
    SL_HIT = "sl"
    TIMEOUT = "timeout"       # Max holding süresi doldu
    STILL_ACTIVE = "active"   # Henüz kapanmadı


@dataclass
class TradeRecord:
    """Tek bir backtest trade kaydı."""
    ticker: str
    signal: str
    signal_type: str
    entry_date: str
    entry_price: float
    tp_price: float
    sl_price: float
    exit_date: Optional[str] = None
    exit_price: Optional[float] = None
    outcome: TradeOutcome = TradeOutcome.STILL_ACTIVE
    pnl_pct: float = 0.0
    pnl_net_pct: float = 0.0           # Komisyon düşülmüş
    commission_cost_pct: float = 0.0
    bars_held: int = 0
    # Sinyal meta
    vol_confirmed: bool = False
    adx_confirmed: bool = False
    confirmation_count: int = 0
    stars: int = 0
    tech_score: float = 0.0


@dataclass
class BacktestConfig:
    """Backtest parametre seti."""
    # TP/SL yüzdeleri (default günlük timeframe)
    tp_pct: float = 0.03          # %3 take profit
    sl_pct: float = 0.02          # %2 stop loss
    # Komisyon (alış + satış toplam)
    commission_pct: float = 0.002  # %0.2 (BIST ortalaması)
    # Slippage (tahmini kayma)
    slippage_pct: float = 0.001   # %0.1
    # Max holding süresi (bar)
    max_holding_bars: int = 20    # 20 iş günü ≈ 1 ay
    # Sadece bullish sinyalleri test et
    bullish_only: bool = True
    # Minimum sinyal yıldız eşiği
    min_stars: int = 1
    # Minimum onay sayısı
    min_confirmations: int = 0


# ================================================================
# TIMEFRAME BAZLI BACKTEST PREFABRİKLERİ
# ================================================================
TIMEFRAME_BACKTEST_CONFIGS: dict[str, BacktestConfig] = {
    "15m": BacktestConfig(
        tp_pct=0.015, sl_pct=0.008,
        commission_pct=0.002, slippage_pct=0.001,
        max_holding_bars=32,  # 8 saat / 15dk
    ),
    "60m": BacktestConfig(
        tp_pct=0.025, sl_pct=0.015,
        commission_pct=0.002, slippage_pct=0.001,
        max_holding_bars=24,  # 3 gün / 1saat
    ),
    "1G": BacktestConfig(
        tp_pct=0.03, sl_pct=0.02,
        commission_pct=0.002, slippage_pct=0.001,
        max_holding_bars=20,
    ),
}


# ================================================================
# BACKTEST ENGINE
# ================================================================

class BacktestEngine:
    """
    Cross Hunter sinyal backtest motoru.

    Her sinyal için:
    1. Giriş fiyatını kaydet (sinyal bar'ının kapanışı)
    2. Sonraki bar'lardan itibaren TP/SL kontrol et
    3. Max holding süresi aşılırsa zorla kapat
    4. Komisyon + slippage düş
    5. İstatistik üret

    Deterministic: Aynı sinyal + aynı veri = aynı sonuç.
    """

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()
        self.trades: list[TradeRecord] = []
        self._is_run = False

    def run(
        self,
        signals: list[dict],
        history_map: dict[str, pd.DataFrame],
    ) -> list[TradeRecord]:
        """
        Sinyalleri geçmiş veri üzerinde test et.

        Args:
            signals: CrossHunter.scan_all() formatında sinyal listesi
                     (her sinyal bir "anı" temsil eder, entry bar son bar)
            history_map: {symbol: DataFrame} — tam tarihsel veri

        Returns:
            TradeRecord listesi

        NOT:
            Bu fonksiyon "walk-forward" backtest yapmaz.
            CrossHunter'ın scan_all()'ı zaten son bar üzerinde sinyal üretir.
            Burada o sinyalin SONRAKİ bar'larda nasıl performans gösterdiğini
            simüle ediyoruz.

            Tam walk-forward backtest için:
            - Veriyi parçalara böl
            - Her parça sonunda scan_all() çağır
            - Sonraki parça ile bu fonksiyonu çağır
        """
        self.trades = []
        cfg = self.config

        for sig in signals:
            ticker = sig.get("ticker", "").strip().upper()
            signal_name = sig.get("signal", "")
            signal_type = sig.get("signal_type", "bullish")
            entry_price = sig.get("price")

            # Filtreler
            if cfg.bullish_only and signal_type != "bullish":
                continue
            if sig.get("stars", 0) < cfg.min_stars:
                continue
            if sig.get("confirmation_count", 0) < cfg.min_confirmations:
                continue
            if not entry_price or entry_price <= 0:
                continue

            # Veri çözümleme
            from utils.helpers import normalize_symbol
            symbol = normalize_symbol(ticker)
            df = history_map.get(symbol)
            if df is None or len(df) < 2:
                continue

            # Slippage uygula (giriş fiyatını biraz yükselt)
            adjusted_entry = entry_price * (1 + cfg.slippage_pct)
            tp_price = adjusted_entry * (1 + cfg.tp_pct)
            sl_price = adjusted_entry * (1 - cfg.sl_pct)

            # Son bar'dan sonraki bar'ları simüle et
            # Gerçek backtestte sinyal tarihini bilmemiz lazım
            # Burada son bar = entry varsayıyoruz
            trade = self._simulate_trade(
                df=df,
                ticker=ticker,
                signal_name=signal_name,
                signal_type=signal_type,
                entry_price=adjusted_entry,
                tp_price=tp_price,
                sl_price=sl_price,
                sig=sig,
            )
            self.trades.append(trade)

        self._is_run = True
        return self.trades

    def run_walkforward(
        self,
        history_map: dict[str, pd.DataFrame],
        scan_fn,
        lookback_bars: int = 252,
        step_bars: int = 1,
    ) -> list[TradeRecord]:
        """
        Walk-Forward backtest: veriyi parçalara böl, her adımda scan yap.

        Args:
            history_map: {symbol: full DataFrame}
            scan_fn: CrossHunter.scan_all fonksiyonu
            lookback_bars: Her scan için geriye bakma penceresi
            step_bars: Kaç bar ilerle (1 = günlük)

        Returns:
            Tüm trade kayıtları
        """
        self.trades = []
        cfg = self.config

        # Ortak tarih indeksi bul
        all_dates = set()
        for df in history_map.values():
            all_dates.update(df.index.tolist())
        sorted_dates = sorted(all_dates)

        if len(sorted_dates) < lookback_bars + cfg.max_holding_bars:
            log.warning("Yetersiz veri: walk-forward yapılamıyor")
            return []

        # Sliding window
        active_trades: list[dict] = []

        for i in range(lookback_bars, len(sorted_dates) - cfg.max_holding_bars, step_bars):
            window_end = sorted_dates[i]

            # Her sembol için windowed DataFrame oluştur
            windowed_map = {}
            for sym, df in history_map.items():
                mask = df.index <= window_end
                window_df = df[mask].tail(lookback_bars)
                if len(window_df) >= 50:
                    windowed_map[sym] = window_df

            if not windowed_map:
                continue

            # Scan yap
            try:
                signals = scan_fn(history_map=windowed_map)
            except Exception as e:
                log.debug(f"Walk-forward scan error at {window_end}: {e}")
                continue

            # Her sinyal için gelecek bar'larda simüle et
            future_start = i + 1
            future_end = min(i + 1 + cfg.max_holding_bars, len(sorted_dates))

            for sig in signals:
                ticker = sig.get("ticker", "").strip().upper()
                signal_type = sig.get("signal_type", "bullish")
                entry_price = sig.get("price")

                if cfg.bullish_only and signal_type != "bullish":
                    continue
                if not entry_price or entry_price <= 0:
                    continue

                from utils.helpers import normalize_symbol
                symbol = normalize_symbol(ticker)
                df = history_map.get(symbol)
                if df is None:
                    continue

                # Gelecek bar'ları al
                future_dates = sorted_dates[future_start:future_end]
                future_mask = df.index.isin(future_dates)
                future_df = df[future_mask]

                if len(future_df) == 0:
                    continue

                adjusted_entry = entry_price * (1 + cfg.slippage_pct)
                tp_price = adjusted_entry * (1 + cfg.tp_pct)
                sl_price = adjusted_entry * (1 - cfg.sl_pct)

                trade = self._simulate_from_future(
                    future_df=future_df,
                    ticker=ticker,
                    signal_name=sig.get("signal", ""),
                    signal_type=signal_type,
                    entry_price=adjusted_entry,
                    entry_date=str(window_end),
                    tp_price=tp_price,
                    sl_price=sl_price,
                    sig=sig,
                )
                self.trades.append(trade)

        self._is_run = True
        return self.trades

    def _simulate_trade(
        self,
        df: pd.DataFrame,
        ticker: str,
        signal_name: str,
        signal_type: str,
        entry_price: float,
        tp_price: float,
        sl_price: float,
        sig: dict,
    ) -> TradeRecord:
        """Tek bir trade'i simüle et (son bar = entry, sonrakiler = future)."""
        cfg = self.config
        entry_date = str(df.index[-1])

        # Son bar = entry, geriye doğru simüle edemeyiz
        # Bu basit modda son bar'ı kendisi ile test ederiz
        trade = TradeRecord(
            ticker=ticker,
            signal=signal_name,
            signal_type=signal_type,
            entry_date=entry_date,
            entry_price=round(entry_price, 4),
            tp_price=round(tp_price, 4),
            sl_price=round(sl_price, 4),
            vol_confirmed=sig.get("vol_confirmed", False),
            adx_confirmed=sig.get("adx_confirmed", False),
            confirmation_count=sig.get("confirmation_count", 0),
            stars=sig.get("stars", 0),
            tech_score=sig.get("tech_score", 0),
        )

        # Henüz gelecek bar yok — aktif olarak bırak
        trade.outcome = TradeOutcome.STILL_ACTIVE
        return trade

    def _simulate_from_future(
        self,
        future_df: pd.DataFrame,
        ticker: str,
        signal_name: str,
        signal_type: str,
        entry_price: float,
        entry_date: str,
        tp_price: float,
        sl_price: float,
        sig: dict,
    ) -> TradeRecord:
        """Gelecek bar'lar üzerinde TP/SL simülasyonu."""
        cfg = self.config
        comm_total = cfg.commission_pct * 2  # Giriş + çıkış

        trade = TradeRecord(
            ticker=ticker,
            signal=signal_name,
            signal_type=signal_type,
            entry_date=entry_date,
            entry_price=round(entry_price, 4),
            tp_price=round(tp_price, 4),
            sl_price=round(sl_price, 4),
            vol_confirmed=sig.get("vol_confirmed", False),
            adx_confirmed=sig.get("adx_confirmed", False),
            confirmation_count=sig.get("confirmation_count", 0),
            stars=sig.get("stars", 0),
            tech_score=sig.get("tech_score", 0),
        )

        for bar_idx in range(len(future_df)):
            row = future_df.iloc[bar_idx]
            bar_high = float(row["High"])
            bar_low = float(row["Low"])
            bar_close = float(row["Close"])
            bar_date = str(future_df.index[bar_idx])

            trade.bars_held = bar_idx + 1

            # Aynı bar içinde SL ve TP ikisi de tetiklenebilir
            # Konservatif yaklaşım: SL önce kontrol edilir (worst-case)
            if bar_low <= sl_price:
                trade.exit_date = bar_date
                trade.exit_price = round(sl_price, 4)
                trade.outcome = TradeOutcome.SL_HIT
                trade.pnl_pct = round(
                    (sl_price - entry_price) / entry_price * 100, 2
                )
                trade.commission_cost_pct = round(comm_total * 100, 2)
                trade.pnl_net_pct = round(trade.pnl_pct - trade.commission_cost_pct, 2)
                return trade

            if bar_high >= tp_price:
                trade.exit_date = bar_date
                trade.exit_price = round(tp_price, 4)
                trade.outcome = TradeOutcome.TP_HIT
                trade.pnl_pct = round(
                    (tp_price - entry_price) / entry_price * 100, 2
                )
                trade.commission_cost_pct = round(comm_total * 100, 2)
                trade.pnl_net_pct = round(trade.pnl_pct - trade.commission_cost_pct, 2)
                return trade

            # Timeout kontrolü
            if trade.bars_held >= cfg.max_holding_bars:
                trade.exit_date = bar_date
                trade.exit_price = round(bar_close, 4)
                trade.outcome = TradeOutcome.TIMEOUT
                trade.pnl_pct = round(
                    (bar_close - entry_price) / entry_price * 100, 2
                )
                trade.commission_cost_pct = round(comm_total * 100, 2)
                trade.pnl_net_pct = round(trade.pnl_pct - trade.commission_cost_pct, 2)
                return trade

        # Veri bitmeden kapanmadı
        last_close = float(future_df.iloc[-1]["Close"])
        trade.exit_price = round(last_close, 4)
        trade.exit_date = str(future_df.index[-1])
        trade.outcome = TradeOutcome.STILL_ACTIVE
        trade.pnl_pct = round(
            (last_close - entry_price) / entry_price * 100, 2
        )
        trade.commission_cost_pct = round(comm_total * 100, 2)
        trade.pnl_net_pct = round(trade.pnl_pct - trade.commission_cost_pct, 2)
        return trade

    # ================================================================
    # İSTATİSTİK & RAPORLAMA
    # ================================================================

    def summary(self) -> dict:
        """
        Backtest özet istatistikleri.

        Returns: {
            total_trades, tp_count, sl_count, timeout_count, active_count,
            win_rate, win_rate_net,
            avg_pnl_pct, avg_pnl_net_pct,
            best_trade, worst_trade,
            profit_factor,
            avg_bars_held,
            total_commission_pct,
            by_signal, by_stars
        }
        """
        if not self.trades:
            return {"error": "No trades. Run backtest first."}

        closed = [t for t in self.trades if t.outcome != TradeOutcome.STILL_ACTIVE]
        tp_trades = [t for t in closed if t.outcome == TradeOutcome.TP_HIT]
        sl_trades = [t for t in closed if t.outcome == TradeOutcome.SL_HIT]
        timeout_trades = [t for t in closed if t.outcome == TradeOutcome.TIMEOUT]
        active = [t for t in self.trades if t.outcome == TradeOutcome.STILL_ACTIVE]

        # Win rate (gross)
        win_count = len(tp_trades)
        loss_count = len(sl_trades) + len([t for t in timeout_trades if t.pnl_net_pct < 0])
        total_closed = len(closed)
        win_rate = round(win_count / total_closed * 100, 1) if total_closed > 0 else 0.0

        # Win rate (net — komisyon sonrası)
        net_winners = [t for t in closed if t.pnl_net_pct > 0]
        win_rate_net = round(len(net_winners) / total_closed * 100, 1) if total_closed > 0 else 0.0

        # P&L
        pnls = [t.pnl_pct for t in closed]
        pnls_net = [t.pnl_net_pct for t in closed]
        avg_pnl = round(np.mean(pnls), 2) if pnls else 0.0
        avg_pnl_net = round(np.mean(pnls_net), 2) if pnls_net else 0.0

        # Profit Factor
        gross_profit = sum(t.pnl_net_pct for t in closed if t.pnl_net_pct > 0)
        gross_loss = abs(sum(t.pnl_net_pct for t in closed if t.pnl_net_pct < 0))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf")

        # Bars held
        bars = [t.bars_held for t in closed if t.bars_held > 0]
        avg_bars = round(np.mean(bars), 1) if bars else 0.0

        # Commission total
        total_commission = round(sum(t.commission_cost_pct for t in closed), 2)

        # Best/Worst
        best = max(pnls_net) if pnls_net else 0.0
        worst = min(pnls_net) if pnls_net else 0.0

        # Breakdown by signal type
        by_signal = self._breakdown_by_key(closed, "signal")

        # Breakdown by stars
        by_stars = self._breakdown_by_key(closed, "stars")

        # Breakdown by confirmation
        by_confirmation = self._breakdown_by_confirmation(closed)

        return {
            "total_trades": len(self.trades),
            "closed_trades": total_closed,
            "tp_count": len(tp_trades),
            "sl_count": len(sl_trades),
            "timeout_count": len(timeout_trades),
            "active_count": len(active),
            "win_rate": win_rate,
            "win_rate_net": win_rate_net,
            "avg_pnl_pct": avg_pnl,
            "avg_pnl_net_pct": avg_pnl_net,
            "best_trade_pct": round(best, 2),
            "worst_trade_pct": round(worst, 2),
            "profit_factor": profit_factor,
            "avg_bars_held": avg_bars,
            "total_commission_pct": total_commission,
            "by_signal": by_signal,
            "by_stars": by_stars,
            "by_confirmation": by_confirmation,
        }

    def _breakdown_by_key(self, trades: list[TradeRecord], key: str) -> dict:
        """Belirli bir key'e göre trade istatistik breakdown'ı."""
        groups: dict[str, list[TradeRecord]] = {}
        for t in trades:
            val = str(getattr(t, key, "unknown"))
            groups.setdefault(val, []).append(t)

        result = {}
        for group_key, group_trades in sorted(groups.items()):
            wins = sum(1 for t in group_trades if t.pnl_net_pct > 0)
            count = len(group_trades)
            pnls = [t.pnl_net_pct for t in group_trades]
            result[group_key] = {
                "count": count,
                "win_rate": round(wins / count * 100, 1) if count > 0 else 0.0,
                "avg_pnl_net_pct": round(np.mean(pnls), 2) if pnls else 0.0,
            }
        return result

    def _breakdown_by_confirmation(self, trades: list[TradeRecord]) -> dict:
        """Onay sayısına göre win rate breakdown — filtre kalitesini ölçer."""
        groups: dict[int, list[TradeRecord]] = {}
        for t in trades:
            cc = t.confirmation_count
            groups.setdefault(cc, []).append(t)

        result = {}
        for cc in sorted(groups.keys()):
            group = groups[cc]
            wins = sum(1 for t in group if t.pnl_net_pct > 0)
            count = len(group)
            pnls = [t.pnl_net_pct for t in group]
            result[str(cc)] = {
                "count": count,
                "win_rate": round(wins / count * 100, 1) if count > 0 else 0.0,
                "avg_pnl_net_pct": round(np.mean(pnls), 2) if pnls else 0.0,
            }
        return result

    def to_dataframe(self) -> pd.DataFrame:
        """Tüm trade kayıtlarını DataFrame'e dönüştür."""
        if not self.trades:
            return pd.DataFrame()

        records = []
        for t in self.trades:
            records.append({
                "ticker": t.ticker,
                "signal": t.signal,
                "signal_type": t.signal_type,
                "stars": t.stars,
                "entry_date": t.entry_date,
                "entry_price": t.entry_price,
                "exit_date": t.exit_date,
                "exit_price": t.exit_price,
                "tp_price": t.tp_price,
                "sl_price": t.sl_price,
                "outcome": t.outcome.value,
                "pnl_pct": t.pnl_pct,
                "pnl_net_pct": t.pnl_net_pct,
                "commission_pct": t.commission_cost_pct,
                "bars_held": t.bars_held,
                "vol_confirmed": t.vol_confirmed,
                "adx_confirmed": t.adx_confirmed,
                "confirmation_count": t.confirmation_count,
                "tech_score": t.tech_score,
            })
        return pd.DataFrame(records)
