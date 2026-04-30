"""Phase 4.7 v3 ROUND B — end-to-end ingest test with ground-truth KAP
labels from Colab explore_borsapy_labels.py output.

Differs from tests/test_ingest_real_labels.py (v2) by using the EXACT
labels observed in production borsapy output: ALL-CAPS gross/operating/
net income, indent-prefixed cash flow labels, duplicate 'Finansal
Borçlar' across current + long-term liability sections, 'Serbest Nakit
Akım' (with 'Akım' not 'Akışı').
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
_spec = importlib.util.spec_from_file_location("fa_ingest_rb", _SCRIPT_PATH)
fa_ingest = importlib.util.module_from_spec(_spec)
sys.modules["fa_ingest_rb"] = fa_ingest
_spec.loader.exec_module(fa_ingest)


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "round_b.db"
    monkeypatch.setenv("BISTBULL_DB_PATH", str(db))
    import infra.storage
    infra.storage._local = threading.local()
    infra.storage.DB_PATH = str(db)
    from infra.storage import init_db
    init_db()
    yield db


def _make_ground_truth_dataframes(period_ends: list[date]):
    """Build DataFrames shaped like REAL borsapy output per ROUND B
    Colab discovery. Uses verbatim labels including:
      - ALL-CAPS rows: 'BRÜT KAR (ZARAR)', 'FAALİYET KARI (ZARARI)',
        'DÖNEM KARI (ZARARI)', 'TOPLAM VARLIKLAR', 'TOPLAM KAYNAKLAR'
      - Indent-prefixed rows: '  Nakit ve Nakit Benzerleri',
        '  Ödenmiş Sermaye'
      - Duplicate index: 'Finansal Borçlar' appears TWICE (short-term
        + long-term liability sections)
      - 'Serbest Nakit Akım' not 'Akışı'
      - 1-space prefix: ' İşletme Faaliyetlerinden Kaynaklanan Net Nakit'
    """
    pd = pytest.importorskip("pandas")

    cols = [qe.isoformat() for qe in period_ends]
    n = len(cols)

    # Income statement — REAL KAP labels
    income_data = [
        ("Satış Gelirleri",                                  [1_000_000] * n),
        ("Satışların Maliyeti (-)",                          [-700_000] * n),
        ("BRÜT KAR (ZARAR)",                                 [300_000] * n),
        ("Pazarlama, Satış ve Dağıtım Giderleri (-)",        [-50_000] * n),
        ("Genel Yönetim Giderleri (-)",                      [-40_000] * n),
        ("FAALİYET KARI (ZARARI)",                           [200_000] * n),
        ("Finansman Gideri Öncesi Faaliyet Karı/Zararı",     [220_000] * n),
        ("(Esas Faaliyet Dışı) Finansal Giderler (-)",       [-50_000] * n),
        ("SÜRDÜRÜLEN FAALİYETLER VERGİ ÖNCESİ KARI (ZARARI)",[170_000] * n),
        ("DÖNEM KARI (ZARARI)",                              [120_000] * n),
        ("Ana Ortaklık Payları",                             [110_000] * n),
    ]
    inc_df = pd.DataFrame({col: [row[1][i] for row in income_data]
                            for i, col in enumerate(cols)},
                            index=[r[0] for r in income_data])

    # Balance sheet — INCLUDING DUPLICATE 'Finansal Borçlar'
    balance_data = [
        ("Dönen Varlıklar",                       [800_000] * n),
        ("  Nakit ve Nakit Benzerleri",           [200_000] * n),
        ("  Ticari Alacaklar",                    [300_000] * n),
        ("  Stoklar",                             [250_000] * n),
        ("Duran Varlıklar",                       [1_700_000] * n),
        ("TOPLAM VARLIKLAR",                      [2_500_000] * n),
        ("Kısa Vadeli Yükümlülükler",             [400_000] * n),
        ("  Finansal Borçlar",                    [200_000] * n),  # short-term
        ("Uzun Vadeli Yükümlülükler",             [500_000] * n),
        ("  Finansal Borçlar",                    [400_000] * n),  # long-term (DUPLICATE!)
        ("Özkaynaklar",                           [1_200_000] * n),
        ("  Ana Ortaklığa Ait Özkaynaklar",       [1_100_000] * n),
        ("  Ödenmiş Sermaye",                     [100_000] * n),
        ("TOPLAM KAYNAKLAR",                      [2_500_000] * n),
    ]
    bal_df = pd.DataFrame({col: [row[1][i] for row in balance_data]
                            for i, col in enumerate(cols)},
                            index=[r[0] for r in balance_data])
    # Verify we actually created duplicates
    assert list(bal_df.index).count("  Finansal Borçlar") == 2

    cashflow_data = [
        ("Amortisman Giderleri",                              [30_000] * n),
        (" Düzeltme Öncesi Kar",                              [200_000] * n),
        (" İşletme Faaliyetlerinden Kaynaklanan Net Nakit",   [140_000] * n),
        (" Yatırım Faaliyetlerinden Kaynaklanan Nakit",       [-60_000] * n),
        ("Finansman Faaliyetlerden Kaynaklanan Nakit",        [-50_000] * n),
        ("Serbest Nakit Akım",                                [80_000] * n),
    ]
    cf_df = pd.DataFrame({col: [row[1][i] for row in cashflow_data]
                            for i, col in enumerate(cols)},
                            index=[r[0] for r in cashflow_data])

    return inc_df, bal_df, cf_df


def _ground_truth_fetcher(symbol: str, start: date, end: date) -> list[dict]:
    """Simulate full fetcher using ground-truth labels from ROUND B."""
    from utils.label_matching import pick_value, pick_all_values

    qes: list[date] = []
    for year in range(start.year, end.year + 1):
        for m in (3, 6, 9, 12):
            if m == 12: qe = date(year, 12, 31)
            elif m == 3: qe = date(year, 3, 31)
            elif m == 6: qe = date(year, 6, 30)
            else: qe = date(year, 9, 30)
            if start <= qe <= end:
                qes.append(qe)

    inc_df, bal_df, cf_df = _make_ground_truth_dataframes(qes)

    out = []
    for qe in qes:
        col = qe.isoformat()

        income = {
            "revenue": pick_value(inc_df, col, ["Satış Gelirleri", "Hasılat"]),
            "gross_profit": pick_value(inc_df, col, ["BRÜT KAR (ZARAR)", "Brüt Kar"]),
            "operating_income": pick_value(inc_df, col, [
                "FAALİYET KARI (ZARARI)", "Esas Faaliyet Karı",
            ]),
            "net_income": pick_value(inc_df, col, [
                "DÖNEM KARI (ZARARI)", "Dönem Net Karı",
            ]),
            "ebit": pick_value(inc_df, col, [
                "Finansman Gideri Öncesi Faaliyet Karı/Zararı",
                "FAVÖK", "FAALİYET KARI (ZARARI)",
            ]),
            "interest_expense": pick_value(inc_df, col, [
                "(Esas Faaliyet Dışı) Finansal Giderler (-)",
                "Finansman Giderleri",
            ]),
        }

        # Duplicate-label handling for total_debt
        financial_debt_parts = pick_all_values(
            bal_df, col, ["Finansal Borçlar"], allow_substring=False,
        )
        total_debt_sum = sum(financial_debt_parts) if financial_debt_parts else None

        balance = {
            "equity": pick_value(bal_df, col, ["Özkaynaklar"]),
            "total_debt": total_debt_sum,
            "cash": pick_value(bal_df, col, ["Nakit ve Nakit Benzerleri"]),
            "current_assets": pick_value(bal_df, col, ["Dönen Varlıklar"]),
            "current_liabilities": pick_value(bal_df, col, [
                "Kısa Vadeli Yükümlülükler",
            ]),
            "total_assets": pick_value(bal_df, col, [
                "TOPLAM VARLIKLAR", "Toplam Varlıklar",
            ]),
            "paid_in_capital": pick_value(bal_df, col, ["Ödenmiş Sermaye"]),
        }

        cashflow = {
            "free_cashflow": pick_value(cf_df, col, [
                "Serbest Nakit Akım", "Serbest Nakit Akışı",
            ]),
            "operating_cf": pick_value(cf_df, col, [
                "İşletme Faaliyetlerinden Kaynaklanan Net Nakit",
                "İşletme Faaliyetlerinden Sağlanan Nakit Akışı",
            ]),
            "depreciation": pick_value(cf_df, col, [
                "Amortisman Giderleri", "Amortisman",
            ]),
        }

        fast = {"market_cap": 100_000_000 * 10}

        out.append({
            "period_end": qe,
            "filed_at": qe + timedelta(days=45),
            "income": income, "balance": balance,
            "cashflow": cashflow, "fast": fast,
        })
    return out


class TestRoundBLabels:
    """End-to-end with ground-truth labels: every field resolves,
    every metric is computable, total_debt SUMS both financial debt rows."""

    def test_every_statement_field_resolves(self):
        """Each income/balance/cashflow field must be non-None."""
        quarters = _ground_truth_fetcher(
            "THYAO", date(2020, 1, 1), date(2020, 6, 30),
        )
        assert len(quarters) == 2  # 2020 Q1 + Q2
        q = quarters[0]
        # Income: every field non-None
        for k, v in q["income"].items():
            assert v is not None, f"income.{k} resolved to None"
        # Balance: every field non-None
        for k, v in q["balance"].items():
            assert v is not None, f"balance.{k} resolved to None"
        # Cashflow: every field non-None (including new depreciation)
        for k, v in q["cashflow"].items():
            assert v is not None, f"cashflow.{k} resolved to None"

    def test_total_debt_sums_both_finansal_borclar(self):
        """Critical ROUND B test: KAP balance sheet has 'Finansal
        Borçlar' TWICE (short-term + long-term sections). total_debt
        must be the SUM, not just one of them."""
        quarters = _ground_truth_fetcher(
            "ANY", date(2020, 1, 1), date(2020, 3, 31),
        )
        q = quarters[0]
        # Fixture: short-term = 200_000, long-term = 400_000 → sum = 600_000
        assert q["balance"]["total_debt"] == 600_000, (
            f"total_debt should sum both rows: got {q['balance']['total_debt']}, "
            "expected 600_000 (200_000 ST + 400_000 LT)"
        )

    def test_all_caps_labels_match(self):
        """Ground-truth ALL-CAPS labels ('BRÜT KAR (ZARAR)', 'FAALİYET
        KARI (ZARARI)', 'DÖNEM KARI (ZARARI)') must resolve via
        case-fold normalization."""
        quarters = _ground_truth_fetcher(
            "ANY", date(2020, 1, 1), date(2020, 3, 31),
        )
        q = quarters[0]
        assert q["income"]["gross_profit"] == 300_000  # from BRÜT KAR (ZARAR)
        assert q["income"]["operating_income"] == 200_000  # FAALİYET KARI
        assert q["income"]["net_income"] == 120_000  # DÖNEM KARI

    def test_indent_prefix_stripped(self):
        """'  Nakit ve Nakit Benzerleri' (2-space prefix) and
        '  Ödenmiş Sermaye' must resolve."""
        quarters = _ground_truth_fetcher(
            "ANY", date(2020, 1, 1), date(2020, 3, 31),
        )
        q = quarters[0]
        assert q["balance"]["cash"] == 200_000
        assert q["balance"]["paid_in_capital"] == 100_000

    def test_serbest_nakit_akim_variant(self):
        """FCF label is 'Akım' not 'Akışı' in real KAP."""
        quarters = _ground_truth_fetcher(
            "ANY", date(2020, 1, 1), date(2020, 3, 31),
        )
        q = quarters[0]
        assert q["cashflow"]["free_cashflow"] == 80_000

    def test_isletme_faaliyetlerinden_with_prefix(self):
        """Operating CF has 1-space prefix + 'Kaynaklanan' not 'Sağlanan'."""
        quarters = _ground_truth_fetcher(
            "ANY", date(2020, 1, 1), date(2020, 3, 31),
        )
        q = quarters[0]
        assert q["cashflow"]["operating_cf"] == 140_000

    def test_depreciation_available(self):
        """ROUND B adds depreciation from 'Amortisman Giderleri' so
        EBITDA computation uses real D&A, not a proxy."""
        quarters = _ground_truth_fetcher(
            "ANY", date(2020, 1, 1), date(2020, 3, 31),
        )
        q = quarters[0]
        assert q["cashflow"]["depreciation"] == 30_000

    def test_ebitda_uses_real_depreciation(self):
        """Verify net_debt_ebitda uses ebit + depreciation now, not the
        old operating_cf proxy."""
        q = _ground_truth_fetcher(
            "ANY", date(2020, 1, 1), date(2020, 3, 31),
        )[0]
        metrics = fa_ingest._derive_metrics_from_statements(q)
        assert "net_debt_ebitda" in metrics
        # Manual computation:
        # net_debt = 600_000 (total debt) - 200_000 (cash) = 400_000
        # ebitda = (220_000 ebit + 30_000 D&A) × 4 = 1_000_000 annualized
        # 400_000 / 1_000_000 = 0.4
        assert metrics["net_debt_ebitda"] == pytest.approx(0.4, rel=1e-3)

    def test_all_16_metrics_populated_from_round_b(self, fresh_db, tmp_path):
        """Smoking gun: ground-truth labels → all 16 registered metrics
        populate (minus revenue_growth needing prev_year_q in Q1)."""
        from infra.pit import save_price
        d = date(2019, 1, 1)
        while d <= date(2022, 6, 30):
            if d.weekday() < 5:
                save_price("THYAO", d, "test",
                           close=100.0 * (1 + (d - date(2019, 1, 1)).days / 2000))
            d += timedelta(days=1)

        out = tmp_path / "events.csv"
        cp = tmp_path / "cp.json"
        # 2 years → 8 quarters, revenue_growth kicks in from year 2
        n_events, n_failed = fa_ingest.ingest_symbols(
            ["THYAO"], date(2019, 1, 1), date(2020, 12, 31),
            _ground_truth_fetcher, out, cp, sleep_between_symbols=0,
        )
        assert n_failed == 0

        with out.open() as f:
            rows = list(csv.DictReader(f))
        metrics_present = {r["metric"] for r in rows}

        expected_all = {
            "roe", "roic", "roa", "net_margin", "gross_margin",
            "operating_margin", "fcf_yield", "fcf_margin", "cfo_to_ni",
            "current_ratio", "interest_coverage",
            "pe", "pb", "debt_equity", "net_debt_ebitda",
            "revenue_growth",  # should appear from year 2 onward
        }
        missing = expected_all - metrics_present
        assert not missing, (
            f"ROUND B expected all 16 metrics; missing: {missing}. "
            f"Present: {metrics_present}"
        )


class TestPickAllValues:
    """pick_all_values helper for duplicate-index labels."""

    def test_empty_df_returns_empty_list(self):
        from utils.label_matching import pick_all_values
        assert pick_all_values(None, "col", ["any"]) == []

    def test_single_match_returns_singleton(self):
        pd = pytest.importorskip("pandas")
        from utils.label_matching import pick_all_values
        df = pd.DataFrame({"2023-12-31": [100.0]}, index=["Finansal Borçlar"])
        assert pick_all_values(df, "2023-12-31", ["Finansal Borçlar"]) == [100.0]

    def test_duplicate_label_returns_both(self):
        """The ROUND B scenario: 'Finansal Borçlar' appears twice in
        balance sheet (current + long-term). pick_all_values returns
        both values so caller can SUM."""
        pd = pytest.importorskip("pandas")
        from utils.label_matching import pick_all_values
        df = pd.DataFrame(
            {"2023-12-31": [200.0, 400.0, 9999.0]},
            index=["Finansal Borçlar", "Finansal Borçlar", "Other Row"],
        )
        vals = pick_all_values(df, "2023-12-31", ["Finansal Borçlar"])
        assert sorted(vals) == [200.0, 400.0]
        assert sum(vals) == 600.0

    def test_no_substring_match_by_default(self):
        """Critical: when summing, substring match would double-count.
        'Ticari Alacaklar' shouldn't also match 'Uzun Vadeli Ticari
        Alacaklar'."""
        pd = pytest.importorskip("pandas")
        from utils.label_matching import pick_all_values
        df = pd.DataFrame(
            {"2023-12-31": [100.0, 200.0]},
            index=["Ticari Alacaklar", "Uzun Vadeli Ticari Alacaklar"],
        )
        # Default allow_substring=False → only exact match, returns [100]
        vals = pick_all_values(df, "2023-12-31", ["Ticari Alacaklar"])
        assert vals == [100.0]

    def test_allow_substring_opt_in(self):
        """When substring explicitly enabled, both match."""
        pd = pytest.importorskip("pandas")
        from utils.label_matching import pick_all_values
        df = pd.DataFrame(
            {"2023-12-31": [100.0, 200.0]},
            index=["Ticari Alacaklar", "Uzun Vadeli Ticari Alacaklar"],
        )
        vals = pick_all_values(df, "2023-12-31",
                               ["Ticari Alacaklar"], allow_substring=True)
        assert sorted(vals) == [100.0, 200.0]

    def test_nan_filtered(self):
        pd = pytest.importorskip("pandas")
        from utils.label_matching import pick_all_values
        df = pd.DataFrame(
            {"2023-12-31": [100.0, float("nan")]},
            index=["Finansal Borçlar", "Finansal Borçlar"],
        )
        # NaN row skipped, only the valid 100 returned
        assert pick_all_values(df, "2023-12-31", ["Finansal Borçlar"]) == [100.0]
