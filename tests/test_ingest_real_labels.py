"""End-to-end ingest test with a MOCK borsapy fetcher producing the
real Turkish KAP label shapes we expect from production.

Context (Phase 4.7 v2): we can't run real borsapy in CI, but we CAN
simulate the DataFrame shape it returns. This test pipes a fake
borsapy-shaped fetcher through the ingest pipeline and asserts the
label-matching + metric-derivation path produces the expected 16
metrics per quarter, not 3.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest


_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "ingest_fa_for_calibration.py"
)
_spec = importlib.util.spec_from_file_location("fa_ingest_real_labels", _SCRIPT_PATH)
fa_ingest = importlib.util.module_from_spec(_spec)
sys.modules["fa_ingest_real_labels"] = fa_ingest
_spec.loader.exec_module(fa_ingest)


# Real Turkish KAP label examples — these are what borsapy is expected
# to return. The mock fetcher pipes them through pick_value to mimic
# the production code path.
_REAL_INCOME_LABELS = [
    "Hasılat",
    "Satışların Maliyeti (-)",
    "Brüt Kar/(Zarar)",
    "Esas Faaliyet Karı/Zararı",
    "Finansman Giderleri (-)",
    "Dönem Net Karı/Zararı",
    "Ana Ortaklık Paylarına Düşen Dönem Karı/Zararı",
]
_REAL_BALANCE_LABELS = [
    "Dönen Varlıklar",
    "Kısa Vadeli Yükümlülükler",
    "Toplam Varlıklar",
    "Nakit ve Nakit Benzerleri",
    "Toplam Finansal Borçlar",
    "Özkaynaklar",
    "Ödenmiş Sermaye",
]
_REAL_CASHFLOW_LABELS = [
    "İşletme Faaliyetlerinden Sağlanan Nakit Akışı",
    "Yatırım Faaliyetlerinden Kaynaklanan Nakit Akışı",
    "Serbest Nakit Akışı",
]


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "real_labels.db"
    monkeypatch.setenv("BISTBULL_DB_PATH", str(db))
    import infra.storage
    infra.storage._local = threading.local()
    infra.storage.DB_PATH = str(db)
    from infra.storage import init_db
    init_db()
    yield db


def _make_mock_borsapy_dataframes(period_ends: list[date]):
    """Build pandas DataFrames shaped like what borsapy returns for
    a non-bank symbol, with the exact Turkish KAP labels.
    """
    pd = pytest.importorskip("pandas")

    cols = [qe.isoformat() for qe in period_ends]

    # Income: realistic quarterly values
    income_rows = {
        "Hasılat":                            [1_000_000] * len(cols),
        "Satışların Maliyeti (-)":            [-700_000] * len(cols),
        "Brüt Kar/(Zarar)":                   [300_000] * len(cols),
        "Esas Faaliyet Karı/Zararı":          [200_000] * len(cols),
        "Finansman Giderleri (-)":            [-50_000] * len(cols),
        "Dönem Net Karı/Zararı":              [120_000] * len(cols),
        "Ana Ortaklık Paylarına Düşen Dönem Karı/Zararı": [110_000] * len(cols),
    }
    balance_rows = {
        "Dönen Varlıklar":                    [800_000] * len(cols),
        "Kısa Vadeli Yükümlülükler":          [400_000] * len(cols),
        "Toplam Varlıklar":                   [2_500_000] * len(cols),
        "Nakit ve Nakit Benzerleri":          [200_000] * len(cols),
        "Toplam Finansal Borçlar":            [600_000] * len(cols),
        "Özkaynaklar":                        [1_200_000] * len(cols),
        "Ödenmiş Sermaye":                    [100_000] * len(cols),
    }
    cashflow_rows = {
        "İşletme Faaliyetlerinden Sağlanan Nakit Akışı": [140_000] * len(cols),
        "Yatırım Faaliyetlerinden Kaynaklanan Nakit Akışı": [-60_000] * len(cols),
        "Serbest Nakit Akışı":                [80_000] * len(cols),
    }
    inc_df = pd.DataFrame(income_rows).T
    inc_df.columns = cols
    bal_df = pd.DataFrame(balance_rows).T
    bal_df.columns = cols
    cf_df = pd.DataFrame(cashflow_rows).T
    cf_df.columns = cols
    return inc_df, bal_df, cf_df


def _mock_borsapy_fetcher(symbol: str, start: date, end: date) -> list[dict]:
    """Simulate the borsapy fetcher's per-quarter dict output, built
    by calling pick_value() against mock DataFrames with real labels.
    This exercises the production fuzzy-match path end-to-end.
    """
    from utils.label_matching import pick_value

    # Generate quarter ends in [start, end]
    qes: list[date] = []
    for year in range(start.year, end.year + 1):
        for m in (3, 6, 9, 12):
            if m == 12: qe = date(year, 12, 31)
            elif m == 3: qe = date(year, 3, 31)
            elif m == 6: qe = date(year, 6, 30)
            else: qe = date(year, 9, 30)
            if start <= qe <= end:
                qes.append(qe)

    inc_df, bal_df, cf_df = _make_mock_borsapy_dataframes(qes)

    # Replicate the production borsapy_fetcher path exactly
    out = []
    for qe in qes:
        col = qe.isoformat()
        income = {
            "revenue": pick_value(inc_df, col, [
                "Hasılat", "Satış Gelirleri", "Net Satışlar",
            ]),
            "gross_profit": pick_value(inc_df, col, [
                "Brüt Kar", "Brüt Kar/Zarar",
            ]),
            "operating_income": pick_value(inc_df, col, [
                "Esas Faaliyet Karı", "Faaliyet Karı",
            ]),
            "net_income": pick_value(inc_df, col, [
                "Dönem Net Karı", "Ana Ortaklığa Ait Dönem Net Karı",
                "Dönem Net Karı/Zararı",
            ]),
            "ebit": pick_value(inc_df, col, [
                "FAVÖK", "Esas Faaliyet Karı", "Faaliyet Karı",
            ]),
            "interest_expense": pick_value(inc_df, col, [
                "Finansman Giderleri", "Faiz Giderleri",
            ]),
        }
        balance = {
            "equity": pick_value(bal_df, col, [
                "Özkaynaklar", "Toplam Özkaynaklar",
            ]),
            "total_debt": pick_value(bal_df, col, [
                "Toplam Finansal Borçlar", "Finansal Borçlar",
            ]),
            "cash": pick_value(bal_df, col, [
                "Nakit ve Nakit Benzerleri",
            ]),
            "current_assets": pick_value(bal_df, col, [
                "Dönen Varlıklar",
            ]),
            "current_liabilities": pick_value(bal_df, col, [
                "Kısa Vadeli Yükümlülükler",
            ]),
            "total_assets": pick_value(bal_df, col, [
                "Toplam Varlıklar", "Aktifler Toplamı",
            ]),
            "paid_in_capital": pick_value(bal_df, col, [
                "Ödenmiş Sermaye", "Çıkarılmış Sermaye",
            ]),
        }
        cashflow = {
            "free_cashflow": pick_value(cf_df, col, [
                "Serbest Nakit Akışı", "FCF",
            ]),
            "operating_cf": pick_value(cf_df, col, [
                "İşletme Faaliyetlerinden Sağlanan Nakit Akışı",
                "Faaliyetlerden Sağlanan Nakit",
            ]),
        }
        # Mock PIT mcap — we don't test the price path here; pretend
        # shares_outstanding = 100M and close = 10 (simple)
        fast = {"market_cap": 100_000_000 * 10}

        out.append({
            "period_end": qe,
            "filed_at": qe + timedelta(days=45),
            "income": income, "balance": balance,
            "cashflow": cashflow, "fast": fast,
        })
    return out


class TestMockBorsapyRealLabels:
    """Full pipeline: pick_value extracts real KAP labels,
    _derive_metrics computes the 16 metrics, ingest_symbols writes
    all 16 to CSV per quarter."""

    def test_all_16_metrics_populated(self, fresh_db, tmp_path):
        """The smoking-gun test. Colab ROUND A produced only 3 of
        expected metrics. With the v2 fix, running through the
        real-labeled mock should produce all 16."""
        from infra.pit import save_price
        # Seed prices so forward_return_60d has data
        d = date(2020, 1, 1)
        while d <= date(2022, 6, 30):
            if d.weekday() < 5:
                save_price("THYAO", d, "test", close=100.0 * (1 + (d - date(2020, 1, 1)).days / 1000))
            d += timedelta(days=1)

        out = tmp_path / "events.csv"
        cp = tmp_path / "cp.json"
        n_events, n_failed = fa_ingest.ingest_symbols(
            ["THYAO"], date(2020, 1, 1), date(2021, 12, 31),
            _mock_borsapy_fetcher, out, cp, sleep_between_symbols=0,
        )
        assert n_failed == 0
        assert n_events > 0

        # Read CSV and count metrics
        with out.open() as f:
            rows = list(csv.DictReader(f))
        metrics_present = {r["metric"] for r in rows}

        # All 16 registered metrics should appear (minus
        # revenue_growth for the first year when prev_year_q is
        # unavailable — that's documented behavior)
        expected_min = {
            "roe", "roic", "roa", "net_margin", "gross_margin",
            "operating_margin", "fcf_yield", "fcf_margin", "cfo_to_ni",
            "current_ratio", "interest_coverage",
            "pe", "pb", "debt_equity", "net_debt_ebitda",
        }
        missing = expected_min - metrics_present
        assert not missing, (
            f"Metrics missing from CSV: {missing}. "
            f"Present: {metrics_present}"
        )

    def test_fuzzy_labels_survive_case_diacritic_variation(
        self, fresh_db, tmp_path,
    ):
        """If real borsapy returns 'HASİLAT' (uppercase + diacritic)
        instead of 'Hasılat', the extraction still works because
        normalize_label folds both to 'hasilat'."""
        pd = pytest.importorskip("pandas")
        from utils.label_matching import pick_value

        # Pathological casing
        df = pd.DataFrame({
            "2023-06-30": [1_000_000.0],
        }, index=["HASİLAT"])  # all caps + Turkish diacritic
        # Our candidate list has normal-case 'Hasılat'
        val = pick_value(df, "2023-06-30", ["Hasılat"])
        assert val == 1_000_000.0

    def test_ana_ortakliga_fallback_via_substring(self, fresh_db, tmp_path):
        """Some symbols report 'Ana Ortaklık Paylarına Düşen...' instead
        of plain 'Dönem Net Karı'. Substring match should find it."""
        pd = pytest.importorskip("pandas")
        from utils.label_matching import pick_value

        df = pd.DataFrame({
            "2023-06-30": [500_000.0],
        }, index=["Ana Ortaklık Paylarına Düşen Dönem Karı/Zararı"])
        val = pick_value(df, "2023-06-30", [
            "Dönem Net Karı",
            "Ana Ortaklığa Ait Dönem Net Karı",
            "Dönem Karı/Zararı",
        ])
        assert val == 500_000.0


class TestRegistryConsistency:
    """All 16 METRIC_REGISTRY entries must be in METRIC_DIRECTIONS
    with matching direction."""

    def test_all_registry_metrics_directions_aligned(self):
        from engine.scoring_calibrated import METRIC_DIRECTIONS
        for mname, direction, _hint in fa_ingest.METRIC_REGISTRY:
            assert mname in METRIC_DIRECTIONS, (
                f"Registry has '{mname}' but METRIC_DIRECTIONS doesn't. "
                f"Add it to engine/scoring_calibrated.py"
            )
            assert METRIC_DIRECTIONS[mname] == direction, (
                f"{mname}: direction mismatch. registry={direction} vs "
                f"METRIC_DIRECTIONS={METRIC_DIRECTIONS[mname]}"
            )
