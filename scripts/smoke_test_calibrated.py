#!/usr/bin/env python3
"""Phase 4.7 calibrated scoring smoke test — production deploy verification.

USAGE:
    python scripts/smoke_test_calibrated.py --url=https://bistbull.ai --symbol=THYAO

Performs a single HTTP GET to /api/analyze/<symbol>?scoring_version=calibrated_2026Q1
and verifies three deploy-readiness criteria:

  1. scoring_version_effective == 'calibrated_2026Q1'
     (ensures fits file is deployed; if V13 fallback → fits not loaded)
  2. deger_score in the [1, 99] output range
     (confirms the full K1→K3→K4 pipeline produces a sane final score)
  3. turkey_realities multiplier + academic layer penalty present
     (confirms K3 and K4 layers ran in the calibrated path)

Renkli Türkçe çıktı, exit code 0 = başarı, 1 = başarısızlık.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any, Optional


# ANSI colors — fall back to empty strings if terminal doesn't support
def _supports_color() -> bool:
    return sys.stdout.isatty() and sys.platform != "win32"


if _supports_color():
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BOLD = "\033[1m"
    RESET = "\033[0m"
else:
    GREEN = RED = YELLOW = BOLD = RESET = ""


def _ok(msg: str) -> None:
    print(f"{GREEN}✅{RESET} {msg}")


def _fail(msg: str) -> None:
    print(f"{RED}❌{RESET} {msg}")


def _warn(msg: str) -> None:
    print(f"{YELLOW}⚠️{RESET}  {msg}")


def _info(msg: str) -> None:
    print(f"   {msg}")


def _fetch_json(url: str, timeout: float) -> Any:
    """HTTP GET a URL, return parsed JSON. Raises with helpful message."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"HTTP {e.code} from {url}\n  body: {e.read().decode('utf-8', errors='replace')[:500]}"
        )
    except urllib.error.URLError as e:
        raise RuntimeError(f"URL error reaching {url}: {e.reason}")
    except TimeoutError:
        raise RuntimeError(f"Timeout after {timeout}s fetching {url}")

    if status >= 400:
        raise RuntimeError(f"HTTP {status} from {url}: {body[:500]}")
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Non-JSON response from {url}:\n  {body[:500]}\n  error: {e}")


def _get_nested(d: dict, *keys, default=None):
    """Safe nested dict lookup: _get_nested(d, 'data', 'v13', 'verdict')."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
        if cur is default:
            return default
    return cur


def check_scoring_version_effective(payload: dict) -> bool:
    """(1) scoring_version_effective == 'calibrated_2026Q1'."""
    print(f"\n{BOLD}1. Calibrated scoring aktif mi?{RESET}")
    # Response envelope: {"ok": true, "data": {...}, ...}
    data = payload.get("data", payload)
    meta = data.get("_meta") or {}
    effective = meta.get("scoring_version_effective")
    requested = meta.get("scoring_version")

    if effective == "calibrated_2026Q1":
        _ok(f"scoring_version_effective = 'calibrated_2026Q1'")
        return True

    if effective == "v13_handpicked" and requested == "calibrated_2026Q1":
        _fail(f"V13 fallback'e düştü")
        _info("Muhtemel sebep: reports/fa_isotonic_fits.json production'a deploy edilmemiş")
        _info("Çözüm: git ls-files reports/fa_isotonic_fits.json")
        _info("       Boş dönüyorsa commit et + Railway redeploy")
        return False

    if effective is None:
        _fail("_meta alanı response'ta yok")
        _info("Response'un en üst seviyesinde _meta yoksa endpoint eski kodu çalıştırıyor olabilir")
        _info("Çözüm: deploy commit'i çekildi mi kontrol et")
        return False

    _fail(f"Beklenmeyen scoring_version_effective: {effective!r}")
    return False


def check_deger_score_range(payload: dict) -> bool:
    """(2) deger_score (final overall score) must be in [1, 99]."""
    print(f"\n{BOLD}2. Deger skoru geçerli range'de mi?{RESET}")
    data = payload.get("data", payload)
    # V13 final score lives at r["overall"] or r["deger"] — we try both
    score = data.get("overall") or data.get("deger")
    if score is None:
        # Also try inside v13 block
        score = _get_nested(data, "v13", "deger_score") or \
                _get_nested(data, "v13", "final_score")

    if score is None:
        _fail("deger_score response'ta bulunamadı")
        _info(f"Response top-level keys: {sorted(data.keys())[:10]}")
        return False

    try:
        s = float(score)
    except (TypeError, ValueError):
        _fail(f"deger_score sayısal değil: {score!r}")
        return False

    if 1 <= s <= 99:
        _ok(f"deger_score = {s:.1f} (range [1, 99])")
        return True

    _fail(f"deger_score = {s} RANGE DIŞI (beklenen [1, 99])")
    return False


def check_k3_k4_present(payload: dict) -> bool:
    """(3) Turkey realities multiplier + academic layer penalty present."""
    print(f"\n{BOLD}3. K3 (Türkiye Gerçekleri) + K4 (Akademik) katmanları çalıştı mı?{RESET}")
    data = payload.get("data", payload)

    # K3 lives at data["turkey"] per engine/analysis.py
    turkey = data.get("turkey") or _get_nested(data, "v13", "turkey")
    academic = data.get("academic") or _get_nested(data, "v13", "academic")

    k3_ok = False
    if isinstance(turkey, dict) and "composite_multiplier" in turkey:
        mult = turkey["composite_multiplier"]
        if isinstance(mult, (int, float)) and 0.5 <= mult <= 1.5:
            _ok(f"K3 turkey_realities.composite_multiplier = {mult:.3f}")
            k3_ok = True
        else:
            _fail(f"K3 composite_multiplier out of expected [0.5, 1.5]: {mult}")
    elif turkey is None:
        _fail("K3 turkey_realities bloğu response'ta yok")
        _info("Muhtemel sebep: engine/analysis.py:compute_turkey_realities çağrılmamış")
    else:
        _fail(f"K3 turkey bloğu şekli beklenmedik: {type(turkey).__name__}")

    k4_ok = False
    if isinstance(academic, dict):
        # Either 'academic_penalty' or 'total_adjustment_pct' or 'composite_penalty'
        penalty_keys = ("academic_penalty", "total_adjustment_pct",
                        "composite_penalty")
        found = next((k for k in penalty_keys if k in academic), None)
        if found is not None:
            _ok(f"K4 academic.{found} = {academic[found]}")
            k4_ok = True
        else:
            _fail(f"K4 academic bloğunda penalty alanı yok. "
                  f"Mevcut anahtarlar: {sorted(academic.keys())[:5]}")
    elif academic is None:
        _fail("K4 academic bloğu response'ta yok")
        _info("Muhtemel sebep: engine/analysis.py:compute_academic_adjustments çağrılmamış")
    else:
        _fail(f"K4 academic bloğu şekli beklenmedik: {type(academic).__name__}")

    return k3_ok and k4_ok


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", required=True,
                   help="Base URL (örn https://bistbull.ai veya http://localhost:8000)")
    p.add_argument("--symbol", default="THYAO",
                   help="Test sembolü (default: THYAO)")
    p.add_argument("--timeout", type=float, default=20.0,
                   help="HTTP timeout saniye (default: 20)")
    p.add_argument("--version", default="calibrated_2026Q1",
                   help="scoring_version query param (default: calibrated_2026Q1)")
    args = p.parse_args(argv)

    base = args.url.rstrip("/")
    path = f"/api/analyze/{args.symbol}?scoring_version={args.version}"
    url = f"{base}{path}"

    print(f"{BOLD}BistBull Phase 4.7 Calibrated Scoring — Smoke Test{RESET}")
    print(f"URL:     {url}")
    print(f"Timeout: {args.timeout}s")

    try:
        payload = _fetch_json(url, args.timeout)
    except RuntimeError as e:
        _fail(f"HTTP isteği başarısız")
        print(f"   {e}")
        return 1

    # Run the 3 checks
    results = [
        check_scoring_version_effective(payload),
        check_deger_score_range(payload),
        check_k3_k4_present(payload),
    ]

    passed = sum(results)
    total = len(results)
    print(f"\n{BOLD}Sonuç: {passed}/{total} kontrol başarılı{RESET}")
    if passed == total:
        _ok("Calibrated scoring production'da sağlıklı ✓")
        return 0
    _fail("Deploy incomplete — yukarıdaki hata mesajlarına bak")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
