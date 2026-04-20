"""Shared test path constants (Phase 4 FAZ 4.3.5).

Separated from conftest.py because conftest isn't importable as a
module; pytest picks it up implicitly for fixtures but `from conftest
import X` fails in most layouts. This module IS importable, so tests
can do `from tests._paths import UNIVERSE_CSV`.

All paths resolved at module-load time (Path(__file__).resolve()) so
os.chdir between import and use is immaterial.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
UNIVERSE_CSV = DATA_DIR / "universe_history.csv"
UPLOADS_DIR = Path("/mnt/user-data/uploads")
DEEP_EVENTS_CSV = UPLOADS_DIR / "deep_events.csv"
DEEP_SUMMARY_CSV = UPLOADS_DIR / "deep_summary.csv"
