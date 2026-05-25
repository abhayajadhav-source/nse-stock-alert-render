"""
Fundamentals fetcher — pulls PE, EPS, ROE, 3Y avg profit growth, FII holding %.

Design philosophy:
  - Yahoo Finance for the 4 stable metrics (one .info call + one .income_stmt call)
  - NSE scraper for FII holding % (fragile, may break — graceful fallback)
  - Postgres cache with 7-day TTL since fundamentals don't change daily
  - Never raises — always returns a Fundamentals object with None for missing fields

The 7-day TTL is intentional:
  - PE/EPS update at quarterly results, not daily — caching saves ~250 API calls/day
  - FII holdings disclosed quarterly on NSE — no point fetching more than weekly
  - Reduces Yahoo rate-limit pressure (we already pace at 150ms in scanners)
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import psycopg2
import requests
import yfinance as yf

from config import IST, YF_RETRIES
from stock_list import get_yf_symbol

logger = logging.getLogger(__name__)

CACHE_TTL_HOURS = 7 * 24   # 7 days
NSE_TIMEOUT_SEC = 8
DATABASE_URL = os.getenv("DATABASE_URL", "")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class Fundamentals:
    symbol: str
    pe_ratio:                Optional[float] = None
    eps:                     Optional[float] = None
    roe_pct:                 Optional[float] = None    # already in %, not decimal
    profit_growth_3y_pct:    Optional[float] = None    # CAGR %, can be negative
    fii_holding_pct:         Optional[float] = None    # %
    fetched_at:              Optional[str]   = None    # ISO string


# ---------------------------------------------------------------------------
# Postgres cache
# ---------------------------------------------------------------------------
def _ensure_cache_table() -> None:
    """Create the fundamentals_cache table if it doesn't exist."""
    if not DATABASE_URL:
        return
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS fundamentals_cache (
                        symbol                VARCHAR(32) PRIMARY KEY,
                        pe_ratio              NUMERIC,
                        eps                   NUMERIC,
                        roe_pct               NUMERIC,
                        profit_growth_3y_pct  NUMERIC,
                        fii_holding_pct       NUMERIC,
                        fetched_at            TIMESTAMP WITH TIME ZONE NOT NULL
                    )
                """)
    except Exception as e:
        logger.warning("Could not ensure fundamentals_cache table: %s", e)


def _cache_get(symbol: str) -> Optional[Fundamentals]:
    """Return cached Fundamentals if fresh, else None."""
    if not DATABASE_URL:
        return None
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT pe_ratio, eps, roe_pct, profit_growth_3y_pct,
                           fii_holding_pct, fetched_at
                    FROM fundamentals_cache
                    WHERE symbol = %s
                """, (symbol,))
                row = cur.fetchone()
                if not row:
                    return None
                fetched_at = row[5]
                age_hours = (datetime.now(IST) - fetched_at).total_seconds() / 3600
                if age_hours > CACHE_TTL_HOURS:
                    return None  # expired
                return Fundamentals(
                    symbol               = symbol,
                    pe_ratio             = float(row[0]) if row[0] is not None else None,
                    eps                  = float(row[1]) if row[1] is not None else None,
                    roe_pct              = float(row[2]) if row[2] is not None else None,
                    profit_growth_3y_pct = float(row[3]) if row[3] is not None else None,
                    fii_holding_pct      = float(row[4]) if row[4] is not None else None,
                    fetched_at           = fetched_at.isoformat(),
                )
    except Exception as e:
        logger.warning("Cache read failed for %s: %s", symbol, e)
        return None


def _cache_set(f: Fundamentals) -> None:
    """Upsert a fundamentals record."""
    if not DATABASE_URL:
        return
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO fundamentals_cache
                        (symbol, pe_ratio, eps, roe_pct, profit_growth_3y_pct,
                         fii_holding_pct, fetched_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol) DO UPDATE SET
                        pe_ratio             = EXCLUDED.pe_ratio,
                        eps                  = EXCLUDED.eps,
                        roe_pct              = EXCLUDED.roe_pct,
                        profit_growth_3y_pct = EXCLUDED.profit_growth_3y_pct,
                        fii_holding_pct      = EXCLUDED.fii_holding_pct,
                        fetched_at           = EXCLUDED.fetched_at
                """, (
                    f.symbol, f.pe_ratio, f.eps, f.roe_pct,
                    f.profit_growth_3y_pct, f.fii_holding_pct,
                    datetime.now(IST),
                ))
    except Exception as e:
        logger.warning("Cache write failed for %s: %s", f.symbol, e)


# ---------------------------------------------------------------------------
# Yahoo Finance — PE, EPS, ROE, 3Y profit growth
# ---------------------------------------------------------------------------
def _fetch_from_yahoo(symbol: str) -> dict:
    """
    Pull PE, EPS, ROE from .info, then compute 3-year profit growth from
    annual income statements.

    Returns dict with keys: pe_ratio, eps, roe_pct, profit_growth_3y_pct.
    Each may be None if Yahoo doesn't have that field.
    """
    out = {"pe_ratio": None, "eps": None, "roe_pct": None, "profit_growth_3y_pct": None}
    yf_symbol = get_yf_symbol(symbol)

    for attempt in range(YF_RETRIES + 1):
        try:
            ticker = yf.Ticker(yf_symbol)

            # --- .info call (fastest path) ---
            info = ticker.info or {}
            out["pe_ratio"] = info.get("trailingPE")
            out["eps"]      = info.get("trailingEps")
            roe = info.get("returnOnEquity")
            # Yahoo returns ROE as a decimal (e.g. 0.18 = 18%); we want %.
            if roe is not None:
                out["roe_pct"] = roe * 100 if abs(roe) < 5 else roe

            # --- 3-year profit growth — separate API call ---
            # Use income_stmt (yearly). Newer fields first.
            try:
                income = ticker.income_stmt
                if income is not None and not income.empty:
                    # "Net Income" row, columns = years (most recent first)
                    if "Net Income" in income.index:
                        net_income_series = income.loc["Net Income"].dropna()
                        # Need at least 3+ years of data for a 3Y CAGR
                        if len(net_income_series) >= 4:
                            latest = float(net_income_series.iloc[0])   # most recent year
                            earlier = float(net_income_series.iloc[3])  # 3 years ago
                            # Avoid div-by-zero or sign flip nonsense
                            if earlier > 0 and latest > 0:
                                cagr = ((latest / earlier) ** (1 / 3) - 1) * 100
                                out["profit_growth_3y_pct"] = cagr
                            elif earlier > 0 and latest <= 0:
                                # Went from profit to loss — assign large negative
                                out["profit_growth_3y_pct"] = -100.0
                            # If earlier was negative, CAGR is mathematically meaningless;
                            # leave as None rather than report a misleading number.
            except Exception as e:
                logger.debug("Income statement fetch failed for %s: %s", symbol, e)

            return out

        except Exception as e:
            err_str = str(e).lower()
            if "too many requests" in err_str and attempt < YF_RETRIES:
                backoff = 5 * (attempt + 1)
                logger.info("Rate limited on fundamentals for %s; sleeping %ds", symbol, backoff)
                time.sleep(backoff)
                continue
            if attempt < YF_RETRIES:
                time.sleep(1 + attempt)
                continue
            logger.warning("Fundamentals fetch failed for %s: %s", symbol, e)
            return out
    return out


# ---------------------------------------------------------------------------
# NSE scraper — FII holding %
# ---------------------------------------------------------------------------
# WARNING: This is the fragile part. NSE changes their site sometimes, blocks
# requests without proper headers, and rate-limits aggressively. Wrap in
# try/except and always tolerate None.
#
# Approach: fetch the corporate-info shareholding endpoint. It returns a JSON
# response with quarterly shareholding patterns, including the FII column.
# If anything fails (404, 403, JSON parse error, missing field), return None.

NSE_BASE = "https://www.nseindia.com"
NSE_SHAREHOLDING_URL = (
    f"{NSE_BASE}/api/corporate-share-holdings-master"
    "?index=equities&symbol={symbol}"
)
NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{NSE_BASE}/companies-listing/corporate-filings-shareholding",
}

# Module-level session to share cookies across requests in one cron run
_nse_session: Optional[requests.Session] = None


def _get_nse_session() -> Optional[requests.Session]:
    """
    NSE requires you to "visit" the homepage first to get cookies, then use those
    cookies for API calls. We do this once per cron run and reuse the session.
    """
    global _nse_session
    if _nse_session is not None:
        return _nse_session
    try:
        s = requests.Session()
        s.headers.update(NSE_HEADERS)
        # Warm up — get cookies
        s.get(NSE_BASE, timeout=NSE_TIMEOUT_SEC)
        _nse_session = s
        return s
    except Exception as e:
        logger.warning("NSE session warm-up failed: %s", e)
        return None


def _fetch_fii_pct(symbol: str) -> Optional[float]:
    """
    Scrape FII holding % from NSE's shareholding API.

    Returns:
      - float: FII percentage if found
      - None:  on any failure (NSE blocked, JSON malformed, field missing, etc.)
    """
    session = _get_nse_session()
    if session is None:
        return None

    url = NSE_SHAREHOLDING_URL.format(symbol=symbol)
    try:
        resp = session.get(url, timeout=NSE_TIMEOUT_SEC)
        if resp.status_code != 200:
            logger.debug("NSE FII HTTP %d for %s", resp.status_code, symbol)
            return None

        data = resp.json()

        # NSE's shareholding response shape varies. We try several known paths.
        # Most common path: top-level list of quarterly snapshots, each with
        # category-wise breakdown including FII (Foreign Institutional Investors).
        if isinstance(data, list) and data:
            # Most recent quarter is usually first
            latest = data[0]
        elif isinstance(data, dict) and "data" in data:
            inner = data["data"]
            if isinstance(inner, list) and inner:
                latest = inner[0]
            else:
                latest = inner
        elif isinstance(data, dict):
            latest = data
        else:
            return None

        # Search for FII field — names vary across NSE responses
        candidate_keys = [
            "fii", "FII", "fiiHolding", "fii_holding",
            "foreignInstitutions", "foreignInstitutionalInvestors",
            "fpi", "FPI",   # FPI (Foreign Portfolio Investors) often used instead of FII
            "fpiHolding",
        ]
        if isinstance(latest, dict):
            for k in candidate_keys:
                if k in latest and latest[k] is not None:
                    try:
                        return float(latest[k])
                    except (TypeError, ValueError):
                        continue

            # Last resort — look for a category list inside the record
            categories = latest.get("categories") or latest.get("shareholding") or []
            if isinstance(categories, list):
                for cat in categories:
                    if not isinstance(cat, dict):
                        continue
                    name = (cat.get("category") or cat.get("name") or "").lower()
                    if "foreign" in name and "institut" in name:
                        try:
                            return float(cat.get("percentage") or cat.get("pct"))
                        except (TypeError, ValueError):
                            continue

        return None

    except Exception as e:
        logger.debug("NSE FII fetch failed for %s: %s", symbol, e)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_fundamentals(symbol: str, use_cache: bool = True) -> Fundamentals:
    """
    Fetch all 5 fundamentals for a symbol, using cache where possible.

    Always returns a Fundamentals object — fields may be None if data is
    unavailable. Never raises.

    Args:
      symbol:    NSE ticker (e.g. "RELIANCE")
      use_cache: If True, check Postgres cache first (7-day TTL)
    """
    if use_cache:
        cached = _cache_get(symbol)
        if cached is not None:
            return cached

    # Cache miss — fetch fresh
    y = _fetch_from_yahoo(symbol)
    fii = _fetch_fii_pct(symbol)

    fundamentals = Fundamentals(
        symbol               = symbol,
        pe_ratio             = y.get("pe_ratio"),
        eps                  = y.get("eps"),
        roe_pct              = y.get("roe_pct"),
        profit_growth_3y_pct = y.get("profit_growth_3y_pct"),
        fii_holding_pct      = fii,
        fetched_at           = datetime.now(IST).isoformat(),
    )

    # Save to cache (even if some fields are None — we don't want to refetch
    # the same dead fields every run for 7 days)
    _cache_set(fundamentals)
    return fundamentals


# Ensure table exists on module import (no-op if already present)
_ensure_cache_table()
