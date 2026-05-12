"""
Single-shot entry point — Render's cron service runs this once, then exits.

Branches on RUN_MODE env var:
  "test"      → send a test email and exit
  "momentum"  → 30-day gainers/losers report
  "reversal"  → trend reversal report (daily timeframe)
  (default)   → intraday scanner (gap/breakout/52w/volume)
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from typing import List, Tuple

from config import (
    EMAIL_BATCH_MODE, FORCE_RUN, IST, MAX_ALERTS_PER_CYCLE, RUN_MODE,
    SCAN_END_TIME, SCAN_START_TIME,
)
from gmail_notifier import send_batch_alert, send_test_email
from momentum_report import run_momentum_report
from news_fetcher import NewsItem, fetch_news_for_stock
from reversal_report import run_reversal_report
from state_manager import cleanup_old_entries, mark_alert_sent, should_send_alert
from stock_analyzer import StockData, fetch_stock_data
from stock_list import get_all_symbols

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Intraday scanner helpers
# ---------------------------------------------------------------------------
def is_market_hours() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    return SCAN_START_TIME <= now.time() <= SCAN_END_TIME


def get_primary_signal(data: StockData) -> str:
    if data.is_new_52w_high:  return "new_52w_high"
    if data.is_new_52w_low:   return "new_52w_low"
    if data.is_gap_up:        return "gap_up"
    if data.is_gap_down:      return "gap_down"
    if data.is_breakout_up:   return "breakout_up"
    if data.is_breakout_down: return "breakout_down"
    if data.is_near_52w_high: return "near_52w_high"
    if data.is_near_52w_low:  return "near_52w_low"
    if data.has_volume_spike: return "volume_spike"
    return "none"


def filter_significant_stocks(symbols: List[str]) -> List[StockData]:
    significant = []
    for i, symbol in enumerate(symbols, 1):
        if i % 25 == 0:
            logger.info("Progress: %d/%d", i, len(symbols))
        data = fetch_stock_data(symbol)
        if data is None:
            continue
        if data.is_significant:
            significant.append(data)
            extras = []
            if data.is_new_52w_high:  extras.append("NEW 52W HIGH")
            if data.is_new_52w_low:   extras.append("NEW 52W LOW")
            if data.is_near_52w_high: extras.append(f"near 52w high ({data.pct_from_52w_high:+.2f}%)")
            if data.is_near_52w_low:  extras.append(f"near 52w low ({data.pct_from_52w_low:+.2f}%)")
            extra_str = (" | " + " | ".join(extras)) if extras else ""
            logger.info("✓ %s — gap=%.2f%% intraday=%.2f%% vol=%.1fx%s",
                        symbol, data.gap_pct, data.intraday_pct,
                        data.volume_ratio, extra_str)
    return significant


def rank_alerts(stocks: List[StockData]) -> List[StockData]:
    def score(s: StockData) -> float:
        val = (abs(s.gap_pct) * 2.0 + abs(s.intraday_pct) * 1.5
               + (s.volume_ratio if s.volume_ratio > 1 else 0) * 0.5)
        if s.is_new_52w_high or s.is_new_52w_low:
            val += 10.0
        elif s.is_near_52w_high or s.is_near_52w_low:
            val += 3.0
        return val
    return sorted(stocks, key=score, reverse=True)


def run_intraday_scan() -> dict:
    logger.info("=" * 60)
    logger.info("Starting intraday scan at %s IST",
                datetime.now(IST).strftime("%H:%M:%S"))
    logger.info("=" * 60)

    symbols = get_all_symbols()
    logger.info("Universe: %d stocks", len(symbols))

    significant = filter_significant_stocks(symbols)
    logger.info("Significant stocks: %d", len(significant))

    ranked   = rank_alerts(significant)
    to_alert = ranked[:MAX_ALERTS_PER_CYCLE]

    alerts_to_send: List[Tuple[StockData, List[NewsItem]]] = []
    for stock in to_alert:
        signal = get_primary_signal(stock)
        if not should_send_alert(stock.symbol, signal):
            logger.info("⏭  %s — cooldown active", stock.symbol)
            continue
        news_items = fetch_news_for_stock(stock.symbol)
        alerts_to_send.append((stock, news_items))

    sent = 0
    if alerts_to_send and EMAIL_BATCH_MODE:
        if send_batch_alert(alerts_to_send):
            for stock, _ in alerts_to_send:
                mark_alert_sent(stock.symbol, get_primary_signal(stock))
            sent = len(alerts_to_send)
            logger.info("📤 Sent batch email with %d alerts", sent)
        else:
            logger.error("✗ Batch email failed")

    cleanup_old_entries()
    return {
        "total_scanned":  len(symbols),
        "significant":    len(significant),
        "alerts_sent":    sent,
    }


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------
def main() -> int:
    # --- Test mode ---
    if RUN_MODE == "test":
        logger.info("RUN_MODE=test → sending test email")
        ok = send_test_email()
        return 0 if ok else 1

    # --- Momentum mode (30-day gainers/losers) ---
    if RUN_MODE == "momentum":
        logger.info("RUN_MODE=momentum → running 30-day momentum report")
        try:
            stats = run_momentum_report()
            logger.info("Momentum complete: %s", stats)
            return 0
        except Exception as e:
            logger.exception("Momentum report failed: %s", e)
            return 1

    # --- Reversal mode (trend reversal candidates) ---
    if RUN_MODE == "reversal":
        logger.info("RUN_MODE=reversal → running trend-reversal report")
        try:
            stats = run_reversal_report()
            logger.info("Reversal complete: %s", stats)
            return 0
        except Exception as e:
            logger.exception("Reversal report failed: %s", e)
            return 1

    # --- Default: intraday scanner, gated by market hours ---
    if not FORCE_RUN and not is_market_hours():
        logger.info("Outside market hours — skipping intraday scan")
        return 0

    try:
        stats = run_intraday_scan()
        logger.info("Intraday scan complete: %s", stats)
        return 0
    except Exception as e:
        logger.exception("Intraday scan failed: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
