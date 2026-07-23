#!/usr/bin/env python3
import argparse
import json
import os
import sys
from typing import Optional, List, Dict, Tuple

import numpy as np
import pandas as pd

# add this
try:
    from tqdm import tqdm
except Exception:
    # no-op fallback so script still runs without tqdm
    def tqdm(*args, **kwargs):
        class _T:
            def update(self, *a, **k): pass
            def close(self): pass
        return _T()


# =========================
# DEFAULTS / CONFIG
# =========================
DEFAULT_INPUT   = "snapshots.csv"
DEFAULT_TABLE   = "parameters.csv"
DEFAULT_OUTPUT  = "data_output.csv"
DEFAULT_CHUNK   = 25000          # rows per chunk to keep memory stable
CHECKPOINT_EXT  = ".checkpoint.json"
EPS             = 1e-9

# P-values to compute (P in percent). Using 100-P reflection for lookup.
PVALS: List[float] = [
    0.01, 0.1, 1, 2, 3, 5,
    10, 15, 20, 25, 30, 35, 40, 45, 50,
    55, 60, 65, 70, 75, 80, 85, 90,
    95, 97, 98, 99, 99.9, 99.99
]

# =========================
# UTILITIES
# =========================
def normalize(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum() or ch == "_")

def find_col(df_columns, candidates) -> Optional[str]:
    """Column resolver that tolerates case/format differences (works with just a list of columns)."""
    cols = list(df_columns)
    lower = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    def norm_keepdot(s: str) -> str:
        return "".join(ch for ch in s.lower() if ch.isalnum() or ch in "_.")
    keepdot = {norm_keepdot(c): c for c in cols}
    for cand in candidates:
        key = norm_keepdot(cand)
        if key in keepdot:
            return keepdot[key]
    norm = {normalize(c): c for c in cols}
    for cand in candidates:
        key = normalize(cand)
        if key in norm:
            return norm[key]
    return None

def append_cols(df: pd.DataFrame, newcols: Dict[str, pd.Series]) -> pd.DataFrame:
    """Append many columns at once to avoid fragmentation warnings."""
    if not newcols:
        return df
    df = pd.concat([df, pd.DataFrame(newcols, index=df.index)], axis=1)
    # Optional: de-fragment after big appends
    return df.copy()

def require_col(df_columns, candidates, label: str) -> str:
    col = find_col(df_columns, candidates)
    if not col:
        raise SystemExit(f"Missing required column for {label}. Tried: {candidates}")
    return col

def clamp_0_100(x: float) -> float:
    return max(0.0, min(100.0, x))

def _clean_number(x: float) -> str:
    t = f"{x:.10g}"
    if t.endswith("."):
        t = t[:-1]
    return t

def _parse_p_to_percent(s: str) -> float:
    token = s.strip().lower().replace("%", "")
    if token.startswith("p"):
        token = token[1:]
    if token.startswith("."):
        token = "0" + token
    val = float(token)
    return max(0.0, min(100.0, val))

def _build_p_candidates(p_value_percent: float):
    out = []
    vclean = _clean_number(p_value_percent)
    out += [f"p{vclean}", f"p_{vclean}"]
    if float(p_value_percent).is_integer():
        vi = int(round(p_value_percent))
        out += [f"p{vi}", f"p_{vi}", f"p{vi:02d}", f"p_{vi:02d}"]
    return out

def pick_p_column_reflected(tbl_columns, p_raw: str) -> str:
    """Use column for (100 - p). Example: p=99.9 -> p0.1"""
    try:
        canonical = _parse_p_to_percent(p_raw)
    except Exception:
        like_p = [c for c in tbl_columns if normalize(c).startswith("p")]
        raise SystemExit(
            f"Couldn't parse p value: {p_raw}. "
            f"Available p-like columns: {', '.join(like_p) if like_p else '(none)'}"
        )
    target = 100.0 - max(0.0, min(100.0, canonical))
    candidates = _build_p_candidates(target)
    like_p = [c for c in tbl_columns if normalize(c).startswith("p")]
    for cand in candidates:
        col = find_col(tbl_columns, [cand])
        if col:
            return col
    raise SystemExit(
        f"P column (for 100 - {p_raw}) not found. Tried {candidates}. "
        f"Available p-like columns: {', '.join(like_p) if like_p else '(none)'}"
    )

def pick_p_column_exact(tbl_columns, p_raw: str) -> str:
    """
    Resolve the parameters table column for the EXACT p value (not reflected).
    Accepts inputs like '0.1', '1', '99.9', 'p0.1', 'p_1', '1%'.
    Returns the actual column name found in parameters.csv (e.g., 'p0.1', 'p1', 'p99.9').
    """
    try:
        canonical = _parse_p_to_percent(p_raw)  # 0..100 float (e.g., 0.1, 1, 99.9)
    except Exception:
        like_p = [c for c in tbl_columns if normalize(c).startswith("p")]
        raise SystemExit(
            f"Couldn't parse p value: {p_raw}. "
            f"Available p-like columns: {', '.join(like_p) if like_p else '(none)'}"
        )
    candidates = _build_p_candidates(canonical)  # e.g. ['p0.1','p_0.1','p1','p_1','p01','p_01']
    like_p = [c for c in tbl_columns if normalize(c).startswith("p")]
    for cand in candidates:
        col = find_col(tbl_columns, [cand])
        if col:
            return col
    raise SystemExit(
        f"P column (for {p_raw}) not found. Tried {candidates}. "
        f"Available p-like columns: {', '.join(like_p) if like_p else '(none)'}"
    )


def prepare_table(df_tbl: pd.DataFrame) -> Tuple[pd.DataFrame, str, str, str, str]:
    margin_lo = find_col(df_tbl.columns, ["margin_low","marginlow","margin_lo","marginlo"])
    margin_hi = find_col(df_tbl.columns, ["margin_high","marginhigh","margin_hi","marginhi"])
    bin_lo    = find_col(df_tbl.columns, ["bin_low","binlow","eevp_low","percentin_low"])
    bin_hi    = find_col(df_tbl.columns, ["bin_hi","binhigh","eevp_high","percentin_high","bin_high"])
    missing = [name for name, col in {
        "margin_low": margin_lo, "margin_high": margin_hi,
        "bin_low": bin_lo, "bin_high": bin_hi
    }.items() if col is None]
    if missing:
        raise SystemExit(f"Missing required columns in table CSV: {', '.join(missing)}")
    for col in [margin_lo, margin_hi, bin_lo, bin_hi]:
        df_tbl[col] = pd.to_numeric(df_tbl[col], errors="coerce")
    df_tbl = df_tbl.dropna(subset=[margin_lo, margin_hi, bin_lo, bin_hi]).copy()
    return df_tbl, margin_lo, margin_hi, bin_lo, bin_hi

def lookup_value(df_tbl: pd.DataFrame,
                 margin_lo: str, margin_hi: str, bin_lo: str, bin_hi: str,
                 p_col: str,
                 margin: float, pct_in: float) -> Optional[float]:
    hits = df_tbl.loc[
        (df_tbl[margin_lo] - EPS <= margin) & (margin <= df_tbl[margin_hi] + EPS) &
        (df_tbl[bin_lo]    - EPS <= pct_in) & (pct_in <= df_tbl[bin_hi]    + EPS)
    ]
    if hits.empty:
        hits = df_tbl.loc[
            (df_tbl[margin_lo] <= margin) & (margin < df_tbl[margin_hi]) &
            (df_tbl[bin_lo]    <= pct_in) & (pct_in < df_tbl[bin_hi])
        ]
        if hits.empty:
            return None
    interior = (
        (hits[margin_lo] + EPS < margin) & (margin < hits[margin_hi] - EPS) &
        (hits[bin_lo]    + EPS < pct_in) & (pct_in < hits[bin_hi]    - EPS)
    )
    if interior.any():
        hits = hits.loc[interior]
    widths = (hits[margin_hi] - hits[margin_lo]).abs() * (hits[bin_hi] - hits[bin_lo]).abs()
    hits = hits.loc[widths.sort_values(kind="stable").index]
    row = hits.iloc[0]
    try:
        return float(row[p_col])
    except Exception:
        try:
            return float(pd.to_numeric(row[p_col], errors="coerce"))
        except Exception:
            return None

def ensure_numeric(s: pd.Series, default=np.nan) -> pd.Series:
    out = pd.to_numeric(s, errors="coerce")
    if not (isinstance(default, float) and np.isnan(default)):
        out = out.fillna(default)
    return out

# =========================
# STATEWIDE MARGIN (for neutralized fallback)
# =========================
def compute_statewide_margin_percent(df_chunk: pd.DataFrame, cols_map: Dict[str, str]) -> pd.Series:
    """
    Returns a per-row series of statewide margin (Trump% - Harris%) in percentage points.
    Prefers explicit statewide % columns; else derives from statewide vote columns.
    If unavailable, returns 0 for those rows (no swing).
    """
    # Try percent columns
    h_pct_col = cols_map.get("h_sw_pct")
    t_pct_col = cols_map.get("t_sw_pct")

    if h_pct_col and t_pct_col and h_pct_col in df_chunk.columns and t_pct_col in df_chunk.columns:
        hsp = ensure_numeric(df_chunk[h_pct_col], default=np.nan)
        tsp = ensure_numeric(df_chunk[t_pct_col], default=np.nan)
        margin = tsp - hsp
        margin = margin.fillna(0.0)
        return margin

    # Derive from statewide votes
    h_sw_v = cols_map.get("h_sw_v")
    t_sw_v = cols_map.get("t_sw_v")
    o_sw_v = cols_map.get("o_sw_v")
    if h_sw_v and t_sw_v and o_sw_v:
        hv = ensure_numeric(df_chunk[h_sw_v], default=0.0)
        tv = ensure_numeric(df_chunk[t_sw_v], default=0.0)
        ov = ensure_numeric(df_chunk[o_sw_v], default=0.0)
        tot = hv + tv + ov
        with np.errstate(divide="ignore", invalid="ignore"):
            hsp = np.where(tot > 0, (hv / tot) * 100.0, np.nan)
            tsp = np.where(tot > 0, (tv / tot) * 100.0, np.nan)
        margin = pd.Series(tsp - hsp, index=df_chunk.index).fillna(0.0)
        return margin

    # No info -> no swing
    return pd.Series(0.0, index=df_chunk.index)

# =========================
# ROW-WISE CORE (vector-friendly)
# =========================
def compute_expected_totals_with_fallback(df_chunk: pd.DataFrame,
                                          eevp_col: str,
                                          harris_col: str, trump_col: str, other_col: str,
                                          final_harris_col: str, final_trump_col: str, final_other_col: str
                                          ) -> Dict[str, pd.Series]:
    eevp_vals = ensure_numeric(df_chunk[eevp_col], default=np.nan)  # 0..100
    h_vals    = ensure_numeric(df_chunk[harris_col], default=0.0)
    t_vals    = ensure_numeric(df_chunk[trump_col], default=0.0)
    o_vals    = ensure_numeric(df_chunk[other_col], default=0.0)

    fh_vals   = ensure_numeric(df_chunk[final_harris_col], default=0.0)
    ft_vals   = ensure_numeric(df_chunk[final_trump_col],  default=0.0)
    fo_vals   = ensure_numeric(df_chunk[final_other_col],  default=0.0)

    snapshot_total = h_vals + t_vals + o_vals

    denom = eevp_vals / 100.0
    with np.errstate(divide="ignore", invalid="ignore"):
        total_expected_base = np.where(denom > 0, snapshot_total / denom, np.nan)

    final_total = fh_vals + ft_vals + fo_vals
    need_fallback = (denom <= 0) | np.isnan(denom)
    te_fallback = pd.Series(total_expected_base, index=df_chunk.index)
    te_fallback[need_fallback] = final_total[need_fallback]

    return {
        "snapshot_total": snapshot_total,
        "total_expected_fallback": te_fallback
    }

def _apply_votes_and_percents_named(df_chunk: pd.DataFrame, cols_map: Dict[str, str],
                                    totals_map: Dict[str, pd.Series],
                                    p_label: str, name_suffix: str = "") -> Dict[str, pd.Series]:
    """
    Build all per-p vote/total outputs into a dict, using a naming suffix.
    Suffix examples: "" (leader set), "_trailing" (trailer set).
    """
    eevp_col = cols_map["eevp"]
    final_h  = cols_map["final_h"]
    final_t  = cols_map["final_t"]
    final_o  = cols_map["final_o"]

    hp_new = f"harris_percent_new_{p_label}{name_suffix}"
    tp_new = f"trump_percent_new_{p_label}{name_suffix}"

    te_out = f"total_expected_{p_label}{name_suffix}"
    ts_out = f"total_snapshot_{p_label}{name_suffix}"
    hv_out = f"harris_votes_new_{p_label}{name_suffix}"
    tv_out = f"trump_votes_new_{p_label}{name_suffix}"
    m_out  = f"margin_new_{p_label}{name_suffix}"

    out: Dict[str, pd.Series] = {}

    # Totals
    out[ts_out] = totals_map["snapshot_total"]
    out[te_out] = totals_map["total_expected_fallback"].copy()

    # votes from percents × expected totals
    hp_vals = ensure_numeric(df_chunk.get(hp_new, np.nan), default=np.nan) / 100.0
    tp_vals = ensure_numeric(df_chunk.get(tp_new, np.nan), default=np.nan) / 100.0
    te_vals = ensure_numeric(out[te_out], default=np.nan)

    out[hv_out] = hp_vals * te_vals
    out[tv_out] = tp_vals * te_vals

    # ---------------- Neutralized fallback for EEVP==0/NaN ----------------
    eevp_vals = ensure_numeric(df_chunk[eevp_col], default=np.nan)
    mask0 = (eevp_vals <= 0) | np.isnan(eevp_vals)

    fh_vals = ensure_numeric(df_chunk[final_h], default=0.0)
    ft_vals = ensure_numeric(df_chunk[final_t], default=0.0)
    fo_vals = ensure_numeric(df_chunk[final_o], default=0.0)
    final_total = fh_vals + ft_vals + fo_vals

    # NEW: normalized fallback votes (if present)
    h_norm_col = "harris_normalized_fallback_votes"
    t_norm_col = "trump_normalized_fallback_votes"
    h_norm = ensure_numeric(df_chunk.get(h_norm_col, np.nan), default=np.nan)
    t_norm = ensure_numeric(df_chunk.get(t_norm_col, np.nan), default=np.nan)
    # base per-county H/T used for fallback rows: normalized when available
    base_h = np.where(mask0, np.where(~np.isnan(h_norm), h_norm, fh_vals), fh_vals)
    base_t = np.where(mask0, np.where(~np.isnan(t_norm), t_norm, ft_vals), ft_vals)
    base_tot = base_h + base_t + fo_vals

    # Replace total_expected with final_total when falling back
    tmp_te = out[te_out].copy()
    tmp_te[mask0] = base_tot[mask0]
    out[te_out] = tmp_te

    # --- Compute statewide margin and swing (−margin/2), per row
    margin_state_pct = compute_statewide_margin_percent(df_chunk, cols_map)  # Trump% − Harris%
    swing = (-margin_state_pct) / 2.0  # points to move from the statewide leader to the trailer

    # Keep Others share constant; shift only within H+T
    with np.errstate(divide="ignore", invalid="ignore"):
        # Use normalized H/T for fallback rows (EEVP==0), else the final_* base
        opct = np.where(base_tot > 0, (fo_vals / base_tot) * 100.0, np.nan)   # Others %
        hp0  = np.where(base_tot > 0, (base_h / base_tot) * 100.0, np.nan)    # base Harris %
        tp0  = np.where(base_tot > 0, (base_t / base_tot) * 100.0, np.nan)    # base Trump  %
        ht_cap = 100.0 - opct                                                 # space for H+T


        # Move swing points from the statewide leader to the trailer
        # Equivalent to adding swing to Harris and subtracting swing from Trump,
        # since swing = −(Trump% − Harris%)/2
        hp1 = hp0 + swing
        # Clamp within available headroom and 0 bounds
        hp1 = np.clip(hp1, 0.0, ht_cap)
        tp1 = ht_cap - hp1

        # Replace percents in fallback rows
        hp_adj = pd.Series(df_chunk.get(hp_new, np.nan), index=df_chunk.index)
        tp_adj = pd.Series(df_chunk.get(tp_new, np.nan), index=df_chunk.index)
        hp_adj[mask0] = hp1[mask0]
        tp_adj[mask0] = tp1[mask0]
        out[hp_new] = hp_adj
        out[tp_new] = tp_adj

        # Replace votes in fallback rows using adjusted percents and final_total
        tmp_hv = out[hv_out].copy()
        tmp_tv = out[tv_out].copy()
        # NEW: build fallback votes from normalized base_tot (not from final_total)
        tmp_hv[mask0] = (hp1[mask0] / 100.0) * base_tot[mask0]
        tmp_tv[mask0] = (tp1[mask0] / 100.0) * base_tot[mask0]
        out[hv_out] = tmp_hv
        out[tv_out] = tmp_tv

    # keep margin_new aligned when we had to fallback
    if m_out in df_chunk.columns:
        m_series = pd.Series(df_chunk[m_out], index=df_chunk.index)
        # margin = Trump% - Harris%
        m_series[mask0] = (out[tp_new][mask0] - out[hp_new][mask0])
        out[m_out] = m_series

    return out

def apply_votes_and_percents_for_p_chunk(df_chunk: pd.DataFrame, cols_map: Dict[str, str],
                                         totals_map: Dict[str, pd.Series],
                                         p_label: str) -> Dict[str, pd.Series]:
    # Backward-compatible wrapper for leader set (no suffix)
    return _apply_votes_and_percents_named(df_chunk, cols_map, totals_map, p_label, name_suffix="")

# ---------- leader vs trailer shifts ----------
def compute_statewide_percents(df_chunk: pd.DataFrame, cols_map: Dict[str, str]) -> Tuple[pd.Series, pd.Series]:
    if cols_map["h_sw_pct"] and cols_map["t_sw_pct"]:
        hs = pd.to_numeric(df_chunk[cols_map["h_sw_pct"]], errors="coerce")
        ts = pd.to_numeric(df_chunk[cols_map["t_sw_pct"]], errors="coerce")
        return hs, ts
    # derive from statewide votes
    h_sw_v = pd.to_numeric(df_chunk[cols_map["h_sw_v"]], errors="coerce").fillna(0)
    t_sw_v = pd.to_numeric(df_chunk[cols_map["t_sw_v"]], errors="coerce").fillna(0)
    o_sw_v = pd.to_numeric(df_chunk[cols_map["o_sw_v"]], errors="coerce").fillna(0)
    tot = h_sw_v + t_sw_v + o_sw_v
    hs = np.where(tot > 0, (h_sw_v / tot) * 100.0, np.nan)
    ts = np.where(tot > 0, (t_sw_v / tot) * 100.0, np.nan)
    return pd.Series(hs, index=df_chunk.index), pd.Series(ts, index=df_chunk.index)

def apply_shift_statewide_leader(df_chunk: pd.DataFrame,
                                 cols_map: Dict[str, str],
                                 shifts_series: pd.Series,
                                 p_label: str) -> Dict[str, pd.Series]:
    """
    Compute margin_new_{p}, harris_percent_new_{p}, trump_percent_new_{p}
    by shifting in the STATEWIDE LEADER's direction when shift > 0
    (your existing behavior).
    """
    h_pct = pd.to_numeric(df_chunk[cols_map["h_pct"]], errors="coerce")
    t_pct = pd.to_numeric(df_chunk[cols_map["t_pct"]], errors="coerce")
    m     = pd.to_numeric(df_chunk[cols_map["m_col"]], errors="coerce")

    hs, ts = compute_statewide_percents(df_chunk, cols_map)
    s      = pd.to_numeric(shifts_series, errors="coerce")

    h2 = pd.Series(np.nan, index=df_chunk.index)
    t2 = pd.Series(np.nan, index=df_chunk.index)
    m2 = pd.Series(np.nan, index=df_chunk.index)

    valid = (~h_pct.isna()) & (~t_pct.isna()) & (~m.isna()) & (~s.isna()) & (~hs.isna()) & (~ts.isna())

    # Harris statewide leads -> +s moves toward Harris (leader)
    mask_hlead = valid & (hs > ts)
    half = s[mask_hlead] / 2.0
    h2.loc[mask_hlead] = (h_pct[mask_hlead] + half).clip(lower=0, upper=100)
    t2.loc[mask_hlead] = (t_pct[mask_hlead] - half).clip(lower=0, upper=100)
    m2.loc[mask_hlead] = m[mask_hlead] + s[mask_hlead]

    # Trump statewide leads -> +s moves toward Trump (leader)
    mask_tlead = valid & (ts > hs)
    half = (-s[mask_tlead]) / 2.0
    h2.loc[mask_tlead] = (h_pct[mask_tlead] + half).clip(lower=0, upper=100)
    t2.loc[mask_tlead] = (t_pct[mask_tlead] - half).clip(lower=0, upper=100)
    m2.loc[mask_tlead] = m[mask_tlead] - s[mask_tlead]

    return {
        f"margin_new_{p_label}": m2,
        f"harris_percent_new_{p_label}": h2,
        f"trump_percent_new_{p_label}": t2
    }

def apply_shift_statewide_trailer(df_chunk: pd.DataFrame,
                                  cols_map: Dict[str, str],
                                  shifts_series: pd.Series,
                                  p_label: str) -> Dict[str, pd.Series]:
    """
    Compute ..._trailing_{p} by interpreting +shift as moving TOWARD the STATEWIDE TRAILER.
    That is the opposite sign on margin compared to leader mode.
    """
    h_pct = pd.to_numeric(df_chunk[cols_map["h_pct"]], errors="coerce")
    t_pct = pd.to_numeric(df_chunk[cols_map["t_pct"]], errors="coerce")
    m     = pd.to_numeric(df_chunk[cols_map["m_col"]], errors="coerce")

    hs, ts = compute_statewide_percents(df_chunk, cols_map)
    s      = pd.to_numeric(shifts_series, errors="coerce")

    h2 = pd.Series(np.nan, index=df_chunk.index)
    t2 = pd.Series(np.nan, index=df_chunk.index)
    m2 = pd.Series(np.nan, index=df_chunk.index)

    valid = (~h_pct.isna()) & (~t_pct.isna()) & (~m.isna()) & (~s.isna()) & (~hs.isna()) & (~ts.isna())

    # Harris statewide leads -> trailer is Trump; +s moves toward Trump
    mask_hlead = valid & (hs > ts)
    half = s[mask_hlead] / 2.0
    h2.loc[mask_hlead] = (h_pct[mask_hlead] - half).clip(lower=0, upper=100)
    t2.loc[mask_hlead] = (t_pct[mask_hlead] + half).clip(lower=0, upper=100)
    m2.loc[mask_hlead] = m[mask_hlead] - s[mask_hlead]

    # Trump statewide leads -> trailer is Harris; +s moves toward Harris
    mask_tlead = valid & (ts > hs)
    half = s[mask_tlead] / 2.0
    h2.loc[mask_tlead] = (h_pct[mask_tlead] + half).clip(lower=0, upper=100)
    t2.loc[mask_tlead] = (t_pct[mask_tlead] - half).clip(lower=0, upper=100)
    m2.loc[mask_tlead] = m[mask_tlead] + s[mask_tlead]

    return {
        f"margin_new_{p_label}_trailing": m2,
        f"harris_percent_new_{p_label}_trailing": h2,
        f"trump_percent_new_{p_label}_trailing": t2
    }

# =========================
# CHUNKED DRIVER
# =========================
def build_cols_map(df_columns) -> Dict[str, str]:
    """Resolve all required columns once (from the global columns list)."""
    # snapshot key (timestamp) used to group rows into a single statewide snapshot
    snap = find_col(
        df_columns,
        ["timestamp","snapshot_timestamp","snapshot_time","time","datetime","ts"]
    )
    h_pct = require_col(df_columns, ["harris_percent_snapshot","harris_pct_snapshot","harris_pct","harris_percent"], "Harris percent (snapshot)")
    t_pct = require_col(df_columns, ["trump_percent_snapshot","trump_pct_snapshot","trump_pct","trump_percent"], "Trump percent (snapshot)")
    m_col = require_col(df_columns, ["margin_snapshot","snapshot_margin","margin"], "snapshot margin")
    eevp  = require_col(df_columns, ["eevp","pct_in","percent_in","percentin","eevp_numeric"], "% in / EEVP")
    h_v   = require_col(df_columns, ["harris_votes","harris_snapshot_votes","h_votes"], "Harris votes (snapshot)")
    t_v   = require_col(df_columns, ["trump_votes","trump_snapshot_votes","t_votes"], "Trump votes (snapshot)")
    o_v   = require_col(df_columns, ["other_votes","others_votes","other_snapshot_votes","o_votes"], "Other votes (snapshot)")

    final_h = require_col(df_columns, ["final_harris_votes","harris_votes_final"], "Final Harris votes")
    final_t = require_col(df_columns, ["final_trump_votes","trump_votes_final"],   "Final Trump votes")
    final_o = require_col(df_columns, ["final_other_votes","other_votes_final"],   "Final Other votes")

    # statewide % cols (or compute from statewide votes if missing)
    h_sw_pct = find_col(df_columns, ["harris_statewide_percent","harris_percent_statewide","statewide_harris_percent"])
    t_sw_pct = find_col(df_columns, ["trump_statewide_percent","trump_percent_statewide","statewide_trump_percent"])
    h_sw_v = t_sw_v = o_sw_v = None
    if not (h_sw_pct and t_sw_pct):
        h_sw_v = require_col(df_columns, ["harris_votes_statewide","statewide_harris_votes"], "Harris votes statewide")
        t_sw_v = require_col(df_columns, ["trump_votes_statewide","statewide_trump_votes"], "Trump votes statewide")
        o_sw_v = require_col(df_columns, ["other_votes_statewide","statewide_other_votes"], "Other votes statewide")

    return {
        "snap": snap,
        "h_pct": h_pct, "t_pct": t_pct, "m_col": m_col, "eevp": eevp,
        "h_v": h_v, "t_v": t_v, "o_v": o_v,
        "final_h": final_h, "final_t": final_t, "final_o": final_o,
        "h_sw_pct": h_sw_pct, "t_sw_pct": t_sw_pct,
        "h_sw_v": h_sw_v, "t_sw_v": t_sw_v, "o_sw_v": o_sw_v
    }

def compute_statewide_percents(df_chunk: pd.DataFrame, cols_map: Dict[str, str]) -> Tuple[pd.Series, pd.Series]:
    if cols_map["h_sw_pct"] and cols_map["t_sw_pct"]:
        hs = pd.to_numeric(df_chunk[cols_map["h_sw_pct"]], errors="coerce")
        ts = pd.to_numeric(df_chunk[cols_map["t_sw_pct"]], errors="coerce")
        return hs, ts
    # derive from statewide votes
    h_sw_v = pd.to_numeric(df_chunk[cols_map["h_sw_v"]], errors="coerce").fillna(0)
    t_sw_v = pd.to_numeric(df_chunk[cols_map["t_sw_v"]], errors="coerce").fillna(0)
    o_sw_v = pd.to_numeric(df_chunk[cols_map["o_sw_v"]], errors="coerce").fillna(0)
    tot = h_sw_v + t_sw_v + o_sw_v
    hs = np.where(tot > 0, (h_sw_v / tot) * 100.0, np.nan)
    ts = np.where(tot > 0, (t_sw_v / tot) * 100.0, np.nan)
    return pd.Series(hs, index=df_chunk.index), pd.Series(ts, index=df_chunk.index)

def load_checkpoint(path: str) -> int:
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r") as f:
            obj = json.load(f)
        return int(obj.get("rows_done", 0))
    except Exception:
        return 0

def save_checkpoint(path: str, rows_done: int) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"rows_done": rows_done}, f)
    os.replace(tmp, path)
    
def prescan_statewide_finals_by_snapshot(
    input_csv: str,
    snap_col: str,
    final_h_col: str,
    final_t_col: str,
    final_o_col: str,
    chunksize: int = 200_000
) -> Dict[str, Tuple[float, float, float]]:
    """
    One-pass (chunked) prescan over the file to build:
      snapshot_value -> (sum_final_h, sum_final_t, sum_final_o)
    Works regardless of chunking in the main pass.
    """
    out: Dict[str, Tuple[float, float, float]] = {}
    usecols = [snap_col, final_h_col, final_t_col, final_o_col]
    try:
        rdr = pd.read_csv(input_csv, usecols=usecols, chunksize=chunksize, iterator=True)
    except Exception as e:
        print(f"Failed prescan read for statewide finals: {e}", file=sys.stderr)
        raise
    for ch in rdr:
        # coerce numerics
        ch[final_h_col] = pd.to_numeric(ch[final_h_col], errors="coerce").fillna(0.0)
        ch[final_t_col] = pd.to_numeric(ch[final_t_col], errors="coerce").fillna(0.0)
        ch[final_o_col] = pd.to_numeric(ch[final_o_col], errors="coerce").fillna(0.0)
        # sum by snapshot
        g = ch.groupby(snap_col, dropna=False)[[final_h_col, final_t_col, final_o_col]].sum()
        for snap_val, row in g.iterrows():
            h_sum = float(row[final_h_col])
            t_sum = float(row[final_t_col])
            o_sum = float(row[final_o_col])
            if snap_val in out:
                ph, pt, po = out[snap_val]
                out[snap_val] = (ph + h_sum, pt + t_sum, po + o_sum)
            else:
                out[snap_val] = (h_sum, t_sum, o_sum)
    return out


# =========================
# MAIN
# =========================
def main():
    ap = argparse.ArgumentParser(
        description="Chunked snapshots → compute shifts/new margins/percents using STATEWIDE leader/trailer logic. When EEVP==0, use neutralized fallback that zeroes statewide margin."
    )
    ap.add_argument("input_csv", nargs="?", default=DEFAULT_INPUT)
    ap.add_argument("-t", "--table", default=DEFAULT_TABLE)
    ap.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    ap.add_argument("-c", "--chunksize", type=int, default=DEFAULT_CHUNK)
    ap.add_argument("--resume", action="store_true", help="Resume from checkpoint/output if present")
    ap.add_argument("--no-progress", action="store_true", help="Disable progress bar output")
    args = ap.parse_args()

    checkpoint_path = args.output + CHECKPOINT_EXT

    # ----- read lookup table & prep (once) -----
    try:
        tbl_full = pd.read_csv(args.table)
    except Exception as e:
        print(f"Failed to read table CSV '{args.table}': {e}", file=sys.stderr); sys.exit(1)
    tbl, m_lo, m_hi, b_lo, b_hi = prepare_table(tbl_full)
    tbl_cols = tbl.columns



    # ----- prepare to stream input in chunks -----
    try:
        # read only the header to resolve columns
        head_df = pd.read_csv(args.input_csv, nrows=0)
    except Exception as e:
        print(f"Failed to read '{args.input_csv}': {e}", file=sys.stderr); sys.exit(1)

    cols_map = build_cols_map(head_df.columns)
    
    # ----- PRE-SCAN: build global statewide-final totals per snapshot (robust to chunking) -----
    snap_col = cols_map.get("snap")
    final_h_col = cols_map["final_h"]
    final_t_col = cols_map["final_t"]
    final_o_col = cols_map["final_o"]
    statewide_finals_by_snap: Dict[str, Tuple[float, float, float]] = {}
    if snap_col and (snap_col in head_df.columns):
        statewide_finals_by_snap = prescan_statewide_finals_by_snapshot(
            args.input_csv, snap_col, final_h_col, final_t_col, final_o_col
        )
    else:
        # No timestamp column: we’ll fall back later to whole-file totals if needed
        statewide_finals_by_snap = {}


    # determine if we are resuming
    rows_done = 0
    write_header = True
    if args.resume and os.path.exists(args.output):
        rows_done = load_checkpoint(checkpoint_path)
        if os.path.getsize(args.output) > 0:
            write_header = False
        print(f"[resume] rows_done={rows_done}")
    # estimate total rows for progress bar
    try:
        with open(args.input_csv, "r", encoding="utf-8", errors="ignore") as _f:
            total_rows_est = max(0, sum(1 for _ in _f) - 1)
    except Exception:
        total_rows_est = None

    pbar = None
    if not args.no_progress:
        pbar = tqdm(
            total=total_rows_est if total_rows_est is not None else 0,
            initial=rows_done,
            desc="Processing rows",
            unit="rows"
        )

    try:
        reader = pd.read_csv(args.input_csv, chunksize=args.chunksize, iterator=True)
    except Exception as e:
        print(f"Failed to open '{args.input_csv}' in chunks: {e}", file=sys.stderr); sys.exit(1)

    # skip already-processed rows (fast-forward)
    skipped = 0
    if rows_done > 0:
        to_skip_chunks = rows_done // args.chunksize
        rem = rows_done % args.chunksize
        for _ in range(to_skip_chunks):
            try:
                next(reader)
                skipped += args.chunksize
            except StopIteration:
                break
        if rem > 0:
            try:
                pre = next(reader)
                pre = pre.iloc[rem:]
                chunks_iter = [pre]
            except StopIteration:
                chunks_iter = []
        else:
            chunks_iter = []
    else:
        chunks_iter = []

    def chunks():
        for ch in chunks_iter:
            yield ch
        for ch in reader:
            yield ch

    total_rows_written = rows_done

    for df_chunk in chunks():
        # --- core numeric coercions
        for key in ["h_pct", "t_pct", "m_col", "eevp", "h_v", "t_v", "o_v",
                    "final_h", "final_t", "final_o"]:
            col = cols_map[key]
            df_chunk[col] = pd.to_numeric(df_chunk[col], errors="coerce")
        
        #
        # === INSERT START: p-value columns (two per p), appended at the end ===
        # Rule uses margin_snapshot:
        #   Trump col: if margin <= 0 → 100 - p, else p
        #   Harris col: if margin <  0 → p,         else 100 - p
        # === p-value columns (two per p), appended at the end ===
        marg = pd.to_numeric(df_chunk[cols_map["m_col"]], errors="coerce")

        pval_cols = {}
        for p in PVALS:
            p_label = _clean_number(p)
            comp = 100.0 - float(p)
            t_col = f"pval_{p_label}_trump"
            h_col = f"pval_{p_label}_harris"
            pval_cols[t_col] = np.where(marg <= 0, comp, p)
            pval_cols[h_col] = np.where(marg <  0, p,    comp)

        df_chunk = append_cols(df_chunk, pval_cols)
        
                # === OVERRIDES requested ===
        # 1) If EEVP == 100, force margin_{p}_new to final_h - final_t (H−T) for every p
        # 2) If shift_{p}_trump == 0 AND shift_{p}_harris == 0, force
        #    trump_cond_{p}_trump_votes_new  = final_trump_votes
        #    trump_cond_{p}_harris_votes_new = final_harris_votes

        # Build masks/series once
        eevp_vals = pd.to_numeric(df_chunk[cols_map["eevp"]], errors="coerce")
        eevp100   = (eevp_vals >= 100.0)

        final_h_vals = pd.to_numeric(df_chunk[cols_map["final_h"]], errors="coerce").fillna(0.0)
        final_t_vals = pd.to_numeric(df_chunk[cols_map["final_t"]], errors="coerce").fillna(0.0)
        finals_margin_ht = (final_h_vals - final_t_vals)  # H−T

        for p in PVALS:
            p_label = _clean_number(p)

            # ---- (1) margin override when EEVP == 100
            # support both spellings if present in your dataset
            margin_cols = [f"margin_new_{p_label}", f"margin_{p_label}_new"]
            for margin_col in margin_cols:
                if margin_col in df_chunk.columns:
                    m = pd.to_numeric(df_chunk[margin_col], errors="coerce")
                    m.loc[eevp100] = finals_margin_ht.loc[eevp100]
                    df_chunk[margin_col] = m

            # ---- (2) zero-shift → trump_cond_* votes snap to finals
            s_t = pd.to_numeric(
                df_chunk.get(f"shift_{p_label}_trump",  pd.Series(0.0, index=df_chunk.index)),
                errors="coerce"
            ).fillna(0.0)

            s_h = pd.to_numeric(
                df_chunk.get(f"shift_{p_label}_harris", pd.Series(0.0, index=df_chunk.index)),
                errors="coerce"
            ).fillna(0.0)

            zero_shift = (s_t == 0.0) & (s_h == 0.0)

            tc_trump_col  = f"trump_cond_{p_label}_trump_votes_new"
            tc_harris_col = f"trump_cond_{p_label}_harris_votes_new"

            if tc_trump_col in df_chunk.columns:
                v = pd.to_numeric(df_chunk[tc_trump_col], errors="coerce")
                v.loc[zero_shift] = final_t_vals.loc[zero_shift]
                df_chunk[tc_trump_col] = v

            if tc_harris_col in df_chunk.columns:
                v = pd.to_numeric(df_chunk[tc_harris_col], errors="coerce")
                v.loc[zero_shift] = final_h_vals.loc[zero_shift]
                df_chunk[tc_harris_col] = v


        # === INSERT END ===
        # === INSERT START: per-p shift columns via parameters.csv lookup ===
        # For each p in PVALS, we add:
        #   shift_<p>_trump  = table value at row(margin, eevp) and column = EXACT pval_<p>_trump
        #   shift_<p>_harris = table value at row(margin, eevp) and column = EXACT pval_<p>_harris
        # Notes:
        # * margin uses snapshot margin column (cols_map["m_col"])
        # * eevp uses cols_map["eevp"] (0..100)
        # * The chosen parameters column is EXACTly the p-value in the pval_* cell (no reflection)

        # === per-p shift columns via parameters.csv lookup ===
        margin_series = pd.to_numeric(df_chunk[cols_map["m_col"]], errors="coerce")
        pctin_series  = ensure_numeric(df_chunk[cols_map["eevp"]], default=np.nan)

        shift_cols = {}
        for p in PVALS:
            p_label = _clean_number(p)
            pv_t_col = f"pval_{p_label}_trump"
            pv_h_col = f"pval_{p_label}_harris"

            # Trump side
            t_result = pd.Series(np.nan, index=df_chunk.index, dtype=float)
            pv_t_vals = pd.to_numeric(df_chunk[pv_t_col], errors="coerce")
            for pv in pd.unique(pv_t_vals.dropna()):
                try:
                    t_param_col = pick_p_column_exact(tbl_cols, f"{float(pv):.5f}")
                except SystemExit:
                    continue
                sel = (pv_t_vals == pv)
                looked = [
                    lookup_value(tbl, m_lo, m_hi, b_lo, b_hi, t_param_col, mval, eevp)
                    for mval, eevp in zip(margin_series[sel].tolist(), pctin_series[sel].tolist())
                ]
                t_result.loc[sel] = looked

            # Harris side
            h_result = pd.Series(np.nan, index=df_chunk.index, dtype=float)
            pv_h_vals = pd.to_numeric(df_chunk[pv_h_col], errors="coerce")
            for pv in pd.unique(pv_h_vals.dropna()):
                try:
                    h_param_col = pick_p_column_exact(tbl_cols, f"{float(pv):.5f}")
                except SystemExit:
                    continue
                sel = (pv_h_vals == pv)
                looked = [
                    lookup_value(tbl, m_lo, m_hi, b_lo, b_hi, h_param_col, mval, eevp)
                    for mval, eevp in zip(margin_series[sel].tolist(), pctin_series[sel].tolist())
                ]
                h_result.loc[sel] = looked

            shift_cols[f"shift_{p_label}_trump"]  = t_result
            shift_cols[f"shift_{p_label}_harris"] = h_result

        df_chunk = append_cols(df_chunk, shift_cols)
        # ---- FIX: when county EEVP is 100, force all shift_* to 0 so they are not NaN/empty ----
        eevp_vals = pd.to_numeric(df_chunk[cols_map["eevp"]], errors="coerce")
        mask100   = (eevp_vals >= 100.0)

        for p in PVALS:
            p_label = _clean_number(p)
            for side in ("trump", "harris"):
                col = f"shift_{p_label}_{side}"
                if col in df_chunk.columns:
                    s = pd.to_numeric(df_chunk[col], errors="coerce")
                    s.loc[mask100] = 0.0
                    df_chunk[col] = s

        # --- NEW: per-p margin_new by candidate using snapshot margin and the per-row shift ---
        # For each p in PVALS we add:
        #   trump_<p>_margin_new  = (margin_snapshot < 0) ? margin_snapshot - shift_<p>_trump  : margin_snapshot + shift_<p>_trump
        #   harris_<p>_margin_new = (margin_snapshot < 0) ? margin_snapshot - shift_<p>_harris : margin_snapshot + shift_<p>_harris
        margin_series = pd.to_numeric(df_chunk[cols_map["m_col"]], errors="coerce")

        mn_cols = {}
        for p in PVALS:
            p_label = _clean_number(p)
            s_t_col = f"shift_{p_label}_trump"
            s_h_col = f"shift_{p_label}_harris"

            s_t = pd.to_numeric(df_chunk[s_t_col], errors="coerce")
            s_h = pd.to_numeric(df_chunk[s_h_col], errors="coerce")

            mn_cols[f"trump_{p_label}_margin_new"]  = np.where(margin_series < 0, margin_series - s_t, margin_series + s_t)
            mn_cols[f"harris_{p_label}_margin_new"] = np.where(margin_series < 0, margin_series - s_h, margin_series + s_h)

        df_chunk = append_cols(df_chunk, mn_cols)
        
        # --- NEW (corrected rules): per-p conditional percent_new (four per p) ---
        # If margin < 0: shift goes TOWARD Trump; if margin >= 0: shift goes TOWARD Harris.
        # Columns created per p:
        #   trump_cond_<p>_trump_percent_new   (uses shift_<p>_trump)
        #   trump_cond_<p>_harris_percent_new  (uses shift_<p>_trump)
        #   harris_cond_<p>_trump_percent_new  (uses shift_<p>_harris)
        #   harris_cond_<p>_harris_percent_new (uses shift_<p>_harris)
        m  = pd.to_numeric(df_chunk[cols_map["m_col"]],  errors="coerce")
        t0 = pd.to_numeric(df_chunk[cols_map["t_pct"]],  errors="coerce")
        h0 = pd.to_numeric(df_chunk[cols_map["h_pct"]],  errors="coerce")

        cond_cols = {}
        for p in PVALS:
            p_label = _clean_number(p)

            # --- use shift_<p>_trump ---
            s_trump = pd.to_numeric(df_chunk[f"shift_{p_label}_trump"], errors="coerce")
            half_t  = s_trump / 2.0

            # trump_cond_<p>_trump_percent_new:
            #   if m<0 -> t0 + half_t ; else -> t0 - half_t
            t_trump_src = np.where(m < 0, t0 + half_t, t0 - half_t)
            # harris_cond_<p>_trump_percent_new:
            #   if m<0 -> h0 - half_t ; else -> h0 + half_t
            h_trump_src = np.where(m < 0, h0 - half_t, h0 + half_t)

            # --- use shift_<p>_harris ---
            s_harris = pd.to_numeric(df_chunk[f"shift_{p_label}_harris"], errors="coerce")
            half_h   = s_harris / 2.0

            # harris_cond_<p>_trump_percent_new:
            #   if m<0 -> t0 + half_h ; else -> t0 - half_h
            t_harris_src = np.where(m < 0, t0 + half_h, t0 - half_h)
            # harris_cond_<p>_harris_percent_new:
            #   if m<0 -> h0 - half_h ; else -> h0 + half_h
            h_harris_src = np.where(m < 0, h0 - half_h, h0 + half_h)

            # clamp to [0,100]
            cond_cols[f"trump_cond_{p_label}_trump_percent_new"]   = np.clip(t_trump_src,  0.0, 100.0)
            cond_cols[f"trump_cond_{p_label}_harris_percent_new"]  = np.clip(h_trump_src,  0.0, 100.0)
            cond_cols[f"harris_cond_{p_label}_trump_percent_new"]  = np.clip(t_harris_src, 0.0, 100.0)
            cond_cols[f"harris_cond_{p_label}_harris_percent_new"] = np.clip(h_harris_src, 0.0, 100.0)

        df_chunk = append_cols(df_chunk, cond_cols)

        # --- NEW: expected total + conditional votes (after *_cond_*_percent_new) ---
        # total_votes_expected = (trump_votes + harris_votes + other_votes) / (eevp/100)
        # Then per p:
        #   trump_cond_<p>_trump_votes_new  = trump_cond_<p>_trump_percent_new  × total_votes_expected
        #   trump_cond_<p>_harris_votes_new = trump_cond_<p>_harris_percent_new × total_votes_expected
        #   harris_cond_<p>_trump_votes_new  = harris_cond_<p>_trump_percent_new  × total_votes_expected
        #   harris_cond_<p>_harris_votes_new = harris_cond_<p>_harris_percent_new × total_votes_expected

        # Build total_votes_expected (safe divide; NaN where EEVP <= 0 or missing)
        h_snap = pd.to_numeric(df_chunk[cols_map["h_v"]],  errors="coerce").fillna(0.0)
        t_snap = pd.to_numeric(df_chunk[cols_map["t_v"]],  errors="coerce").fillna(0.0)
        o_snap = pd.to_numeric(df_chunk[cols_map["o_v"]],  errors="coerce").fillna(0.0)
        eevp_s = pd.to_numeric(df_chunk[cols_map["eevp"]], errors="coerce")

        snap_total = h_snap + t_snap + o_snap
        denom = eevp_s / 100.0
        with np.errstate(divide="ignore", invalid="ignore"):
            total_votes_expected = np.where(denom > 0, snap_total / denom, np.nan)

        new_vote_cols = {"total_votes_expected": total_votes_expected}

        # For each p, convert *_percent_new (0..100) to votes using total_votes_expected
        for p in PVALS:
            p_label = _clean_number(p)

            # From shift_<p>_trump path
            t_trump_pct = pd.to_numeric(df_chunk[f"trump_cond_{p_label}_trump_percent_new"],  errors="coerce")
            h_trump_pct = pd.to_numeric(df_chunk[f"trump_cond_{p_label}_harris_percent_new"], errors="coerce")

            # From shift_<p>_harris path
            t_harris_pct = pd.to_numeric(df_chunk[f"harris_cond_{p_label}_trump_percent_new"],  errors="coerce")
            h_harris_pct = pd.to_numeric(df_chunk[f"harris_cond_{p_label}_harris_percent_new"], errors="coerce")

            # Convert percent → votes (percent points / 100 * total)
            new_vote_cols[f"trump_cond_{p_label}_trump_votes_new"]   = (t_trump_pct  / 100.0) * total_votes_expected
            new_vote_cols[f"trump_cond_{p_label}_harris_votes_new"]  = (h_trump_pct  / 100.0) * total_votes_expected
            new_vote_cols[f"harris_cond_{p_label}_trump_votes_new"]  = (t_harris_pct / 100.0) * total_votes_expected
            new_vote_cols[f"harris_cond_{p_label}_harris_votes_new"] = (h_harris_pct / 100.0) * total_votes_expected

        # Batch-append to avoid fragmentation warnings
        df_chunk = append_cols(df_chunk, new_vote_cols)



        # === NEW (robust): assign statewide-final totals by mapping from pre-scan dict ===
        H_OUT = "harris_vote_statewide_total_final"
        T_OUT = "trump_vote_statewide_total_final"
        O_OUT = "other_vote_statewide_total_final"

        if snap_col and (snap_col in df_chunk.columns) and statewide_finals_by_snap:
            # Map each row’s snapshot to global statewide sums (correct even if snapshot spans chunks)
            # Ensure snapshot key is treated as string for consistent dict keys
            snap_keys = df_chunk[snap_col].astype(str)
            # Build Series via vectorized lookups
            h_vals = snap_keys.map(lambda k: statewide_finals_by_snap.get(k, (np.nan, np.nan, np.nan))[0])
            t_vals = snap_keys.map(lambda k: statewide_finals_by_snap.get(k, (np.nan, np.nan, np.nan))[1])
            o_vals = snap_keys.map(lambda k: statewide_finals_by_snap.get(k, (np.nan, np.nan, np.nan))[2])
            # after computing h_vals, t_vals, o_vals
            df_chunk = append_cols(df_chunk, {
                H_OUT: h_vals,
                T_OUT: t_vals,
                O_OUT: o_vals,
            })

        else:
            # No timestamp column: fall back to whole-file totals (compute once here if needed)
            # We can compute per-chunk sums, but better is whole-file sums for consistency.
            try:
                # lightweight single pass over just final_* columns for total
                tot_df = pd.read_csv(
                    args.input_csv,
                    usecols=[final_h_col, final_t_col, final_o_col]
                )
                h_sum = pd.to_numeric(tot_df[final_h_col], errors="coerce").fillna(0).sum()
                t_sum = pd.to_numeric(tot_df[final_t_col], errors="coerce").fillna(0).sum()
                o_sum = pd.to_numeric(tot_df[final_o_col], errors="coerce").fillna(0).sum()
            except Exception:
                # last resort: per-chunk sum
                h_sum = pd.to_numeric(df_chunk[final_h_col], errors="coerce").fillna(0).sum()
                t_sum = pd.to_numeric(df_chunk[final_t_col], errors="coerce").fillna(0).sum()
                o_sum = pd.to_numeric(df_chunk[final_o_col], errors="coerce").fillna(0).sum()
                df_chunk = append_cols(df_chunk, {
                    H_OUT: pd.Series(h_sum, index=df_chunk.index),
                    T_OUT: pd.Series(t_sum, index=df_chunk.index),
                    O_OUT: pd.Series(o_sum, index=df_chunk.index),
                })



        # === NEW: statewide final percents and margin (based on the three statewide-final totals) ===
        SW_H_PCT_OUT = "harris_percent_statewide_final"
        SW_T_PCT_OUT = "trump_percent_statewide_final"
        SW_M_OUT     = "margin_statewide_final"  # = Harris% - Trump%

        # compute safely, guarding against divide-by-zero
        sw_h = pd.to_numeric(df_chunk[H_OUT], errors="coerce").fillna(0.0)
        sw_t = pd.to_numeric(df_chunk[T_OUT], errors="coerce").fillna(0.0)
        sw_o = pd.to_numeric(df_chunk[O_OUT], errors="coerce").fillna(0.0)
        sw_tot = sw_h + sw_t + sw_o
        with np.errstate(divide="ignore", invalid="ignore"):
            h_pct_sw = np.where(sw_tot > 0, (sw_h / sw_tot) * 100.0, np.nan)
            t_pct_sw = np.where(sw_tot > 0, (sw_t / sw_tot) * 100.0, np.nan)
        df_chunk[SW_H_PCT_OUT] = h_pct_sw
        df_chunk[SW_T_PCT_OUT] = t_pct_sw
        df_chunk[SW_M_OUT]     = df_chunk[SW_H_PCT_OUT] - df_chunk[SW_T_PCT_OUT]
        
        
        # === NEW: normalized fallback votes (zero-out statewide final margin) ===
        # Uses signed margin_statewide_final (Dem +). We move |margin|/2 from winner → loser,
        # applied to the county's final *total* (H+T+Other). Others stay constant.
        H_NORM_OUT = "harris_normalized_fallback_votes"
        T_NORM_OUT = "trump_normalized_fallback_votes"

        # County final totals
        fh_vals = pd.to_numeric(df_chunk[final_h_col], errors="coerce").fillna(0.0)
        ft_vals = pd.to_numeric(df_chunk[final_t_col], errors="coerce").fillna(0.0)
        fo_vals = pd.to_numeric(df_chunk[final_o_col], errors="coerce").fillna(0.0)
        ctot    = fh_vals + ft_vals + fo_vals

        # margin_statewide_final = Harris% - Trump% (signed, Dem +)
        m_sw = pd.to_numeric(df_chunk[SW_M_OUT], errors="coerce")
        # delta_pct = -(margin)/2   (e.g., margin = -2 → +1% to Harris, −1% to Trump)
        delta_pct   = -m_sw / 2.0
        delta_votes = (delta_pct / 100.0) * ctot

        h_norm = fh_vals + delta_votes
        t_norm = ft_vals - delta_votes

        # Prevent negative votes just in case
        h_norm = np.maximum(h_norm, 0.0)
        t_norm = np.maximum(t_norm, 0.0)

        df_chunk[H_NORM_OUT] = h_norm
        df_chunk[T_NORM_OUT] = t_norm

        # --- OVERLAY: when EEVP<=0, use normalized fallback totals & votes ---
        eevp_vals = pd.to_numeric(df_chunk[cols_map["eevp"]], errors="coerce")
        mask0 = (eevp_vals <= 0) | np.isnan(eevp_vals)

        # Recompute base_tot from normalized fallback vectors (H/T normalized, Others as-is)
        fo_vals = pd.to_numeric(df_chunk[final_o_col], errors="coerce").fillna(0.0)
        base_tot = pd.to_numeric(df_chunk[T_NORM_OUT], errors="coerce").fillna(0.0) \
                 + pd.to_numeric(df_chunk[H_NORM_OUT], errors="coerce").fillna(0.0) \
                 + fo_vals

        # If total_votes_expected exists, replace it with base_tot on fallback rows
        if "total_votes_expected" in df_chunk.columns:
            tve = pd.to_numeric(df_chunk["total_votes_expected"], errors="coerce")
            tve[mask0] = base_tot[mask0]
            df_chunk["total_votes_expected"] = tve

        # For each p, override the conditional vote columns on fallback rows:
        #   *_trump_votes_new  -> trump_normalized_fallback_votes
        #   *_harris_votes_new -> harris_normalized_fallback_votes
        for p in PVALS:
            p_label = _clean_number(p)

            c_t_trump = f"trump_cond_{p_label}_trump_votes_new"
            c_t_harr  = f"trump_cond_{p_label}_harris_votes_new"
            c_h_trump = f"harris_cond_{p_label}_trump_votes_new"
            c_h_harr  = f"harris_cond_{p_label}_harris_votes_new"

            if c_t_trump in df_chunk.columns:
                s = pd.to_numeric(df_chunk[c_t_trump], errors="coerce")
                s[mask0] = df_chunk[T_NORM_OUT][mask0]
                df_chunk[c_t_trump] = s

            if c_t_harr in df_chunk.columns:
                s = pd.to_numeric(df_chunk[c_t_harr], errors="coerce")
                s[mask0] = df_chunk[H_NORM_OUT][mask0]
                df_chunk[c_t_harr] = s

            if c_h_trump in df_chunk.columns:
                s = pd.to_numeric(df_chunk[c_h_trump], errors="coerce")
                s[mask0] = df_chunk[T_NORM_OUT][mask0]
                df_chunk[c_h_trump] = s

            if c_h_harr in df_chunk.columns:
                s = pd.to_numeric(df_chunk[c_h_harr], errors="coerce")
                s[mask0] = df_chunk[H_NORM_OUT][mask0]
                df_chunk[c_h_harr] = s


        # compute totals (snapshot_total, total_expected_fallback) for this chunk
        totals_map = compute_expected_totals_with_fallback(
            df_chunk,
            eevp_col=cols_map["eevp"],
            harris_col=cols_map["h_v"], trump_col=cols_map["t_v"], other_col=cols_map["o_v"],
            final_harris_col=cols_map["final_h"], final_trump_col=cols_map["final_t"], final_other_col=cols_map["final_o"]
        )

        # Skip per-P generation entirely (no shift_*, *_new_*, *_trailing columns)
        # Skip per-P generation entirely (no shift_*, *_new_*, *_trailing columns)
        out_chunk = df_chunk

        # --- WRITE CHUNK (append mode) ---
        out_chunk.to_csv(args.output, index=False, mode="a", header=write_header)
        write_header = False  # write header only once

        # advance checkpoint
        total_rows_written += len(df_chunk)
        save_checkpoint(checkpoint_path, total_rows_written)
        if pbar:
            pbar.update(len(df_chunk))

        # free memory for next loop
        del out_chunk, totals_map


    if pbar:
        pbar.close()

    print(f"Done. Wrote: {args.output}")
    print(f"Total rows: {total_rows_written} (chunked, resumable).")
    print(f"Computed columns for PVALS = {PVALS}")
    print("Per p (leader): shift_{p}, margin_new_{p}, harris_percent_new_{p}, trump_percent_new_{p}, "
          "total_snapshot_{p}, total_expected_{p}, harris_votes_new_{p}, trump_votes_new_{p}")
    print("Per p (trailing): shift_trailing_{p}, margin_new_{p}_trailing, "
          "harris_percent_new_{p}_trailing, trump_percent_new_{p}_trailing, "
          "total_snapshot_{p}_trailing, total_expected_{p}_trailing, "
          "harris_votes_new_{p}_trailing, trump_votes_new_{p}_trailing")
    print(f"Checkpoint saved at: {checkpoint_path}")

if __name__ == "__main__":
    main()
