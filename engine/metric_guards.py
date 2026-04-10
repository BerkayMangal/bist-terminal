from __future__ import annotations
import math, logging
from typing import Any
log = logging.getLogger('bistbull.guards')
METRIC_BOUNDS = {
    'pe': (-500, 500), 'pb': (-50, 100), 'ev_ebitda': (-100, 200),
    'roe': (-5.0, 5.0), 'roa': (-2.0, 2.0), 'roa_prev': (-2.0, 2.0),
    'roic': (-2.0, 2.0), 'gross_margin': (-2.0, 1.0), 'gross_margin_prev': (-2.0, 1.0),
    'operating_margin': (-5.0, 1.0), 'net_margin': (-10.0, 1.0),
    'current_ratio': (0, 100), 'current_ratio_prev': (0, 100),
    'debt_equity': (-1000, 5000), 'net_debt_ebitda': (-50, 100),
    'interest_coverage': (-500, 5000), 'fcf_yield': (-2.0, 2.0),
    'fcf_margin': (-5.0, 2.0), 'cfo_to_ni': (-50, 50),
    'revenue_growth': (-1.0, 20.0), 'eps_growth': (-1.0, 100.0),
    'ebitda_growth': (-1.0, 100.0), 'peg': (0, 100),
    'piotroski_f': (0, 9), 'altman_z': (-20, 50), 'beneish_m': (-10, 10),
    'margin_safety': (-20, 1.0), 'share_change': (-1.0, 10.0),
    'asset_turnover': (0, 20), 'asset_turnover_prev': (0, 20), 'ciro_pd': (0, 100),
}
def validate_metrics(m: dict) -> dict:
    result = dict(m); violations = []
    for key, (lo, hi) in METRIC_BOUNDS.items():
        v = result.get(key)
        if v is None: continue
        try: v = float(v)
        except (TypeError, ValueError): result[key] = None; violations.append((key, v, 'bad')); continue
        if math.isnan(v) or math.isinf(v): result[key] = None; violations.append((key, v, 'nan')); continue
        if v < lo or v > hi: result[key] = None; violations.append((key, round(v,4), f'range[{lo},{hi}]'))
    if violations: log.warning(f'GUARD [{m.get("ticker","?")}]: {len(violations)} violation(s)')
    result['_metric_violations'] = len(violations)
    result['_metric_violation_details'] = [{'field':k,'value':v,'reason':r} for k,v,r in violations]
    return result
