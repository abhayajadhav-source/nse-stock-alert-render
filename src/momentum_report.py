"""
30-day momentum report — daily summary of NSE stocks with large moves.

Runs once per day (separate cron from the intraday scanner). Always
runs regardless of market hours, because it operates on closing prices
that are already final.

Output:
  1. One email with two tables: top gainers and top losers (no fundamentals)
  2. Snapshot saved to Postgres for the dashboard (WITH fundamentals:
     PE, EPS, ROE, 3Y profit growth, FII holding %)
"""

from __future__ import annotations

import html
import logging
import smtplib
import time
from dataclasses import dataclass
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional

import yfinance as yf

from config import (
    GMAIL_APP_PASSWORD, GMAIL_RECIPIENT, GMAIL_SENDER, IST,
    MOMENTUM_GAIN_THRESHOLD, MOMENTUM_LOOKBACK_DAYS, MOMENTUM_LOSS_THRESHOLD,
    MOMENTUM_MAX_PER_SECTION, MOMENTUM_SUBJECT_PREFIX, YF_RETRIES,
)
from fundamentals_fetcher import get_fundamentals
from snapshot_store import save_snapshot
from stock_list import NSE_STOCKS, get_all_symbols, get_yf_symbol

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class MomentumRow:
    symbol: str
    name: str
    start_price: float
    end_price:   float
    pct_change:  float
    start_date:  str
    end_date:    str


# ---------------------------------------------------------------------------
# Price fetching — 30-day window (unchanged)
# ---------------------------------------------------------------------------
def _fetch_momentum_row(symbol: str) -> Optional[MomentumRow]:
    """Fetch ~30 trading days of bars and compute % change from first to last close."""
    yf_symbol = get_yf_symbol(symbol)

    for attempt in range(YF_RETRIES + 1):
        try:
            ticker = yf.Ticker(yf_symbol)
            # "2mo" gives ~42 trading days — plenty for a 30-day window
            hist = ticker.history(period="2mo", interval="1d")
            if hist.empty or len(hist) < 2:
                return None

            anchor_idx = max(0, len(hist) - 1 - MOMENTUM_LOOKBACK_DAYS)
            start = hist.iloc[anchor_idx]
            end   = hist.iloc[-1]

            start_price = float(start["Close"])
            end_price   = float(end["Close"])
            if start_price <= 0:
                return None

            pct = ((end_price - start_price) / start_price) * 100
            name = NSE_STOCKS.get(symbol, {}).get("name", symbol)

            return MomentumRow(
                symbol=symbol,
                name=name,
                start_price=start_price,
                end_price=end_price,
                pct_change=pct,
                start_date=hist.index[anchor_idx].strftime("%d %b %Y"),
                end_date=hist.index[-1].strftime("%d %b %Y"),
            )
        except Exception as e:
            if attempt < YF_RETRIES:
                time.sleep(1 + attempt)
                continue
            logger.warning("Momentum fetch failed for %s: %s", symbol, e)
            return None
    return None


def _classify(rows: List[MomentumRow]) -> tuple[List[MomentumRow], List[MomentumRow]]:
    """Split into gainers and losers based on configured thresholds."""
    gainers = [r for r in rows if r.pct_change >= MOMENTUM_GAIN_THRESHOLD]
    losers  = [r for r in rows if r.pct_change <= MOMENTUM_LOSS_THRESHOLD]
    gainers.sort(key=lambda r: r.pct_change, reverse=True)
    losers.sort(key=lambda r: r.pct_change)
    return gainers[:MOMENTUM_MAX_PER_SECTION], losers[:MOMENTUM_MAX_PER_SECTION]


# ---------------------------------------------------------------------------
# Email rendering — UNCHANGED (no fundamentals in email per your spec)
# ---------------------------------------------------------------------------
def _e(text: str) -> str:
    return html.escape(text or "")


def _table_html(rows: List[MomentumRow], title: str, color: str) -> str:
    if not rows:
        return f'<p style="color:#6b7280;font-style:italic;">No {title.lower()}.</p>'

    header = f"""
    <h3 style="margin:18px 0 8px 0;color:{color};">{title} ({len(rows)})</h3>
    <table style="width:100%;border-collapse:collapse;font-size:14px;
                  font-family:-apple-system,Segoe UI,Roboto,sans-serif;">
      <thead>
        <tr style="background:#f3f4f6;color:#374151;text-align:left;">
          <th style="padding:8px;">Symbol</th>
          <th style="padding:8px;">Name</th>
          <th style="padding:8px;text-align:right;">Start ₹</th>
          <th style="padding:8px;text-align:right;">Now ₹</th>
          <th style="padding:8px;text-align:right;">Change</th>
          <th style="padding:8px;">Chart</th>
        </tr>
      </thead>
      <tbody>
    """

    body_rows = []
    for r in rows:
        sign = "+" if r.pct_change >= 0 else ""
        body_rows.append(f"""
        <tr style="border-bottom:1px solid #e5e7eb;">
          <td style="padding:8px;font-weight:600;">{_e(r.symbol)}</td>
          <td style="padding:8px;color:#6b7280;">{_e(r.name)}</td>
          <td style="padding:8px;text-align:right;color:#6b7280;">{r.start_price:,.2f}</td>
          <td style="padding:8px;text-align:right;font-weight:600;">{r.end_price:,.2f}</td>
          <td style="padding:8px;text-align:right;color:{color};font-weight:600;">
            {sign}{r.pct_change:.2f}%
          </td>
          <td style="padding:8px;">
            <a href="https://in.tradingview.com/chart/?symbol=NSE:{r.symbol}"
               style="color:#1d4ed8;text-decoration:none;">📈 TV</a>
          </td>
        </tr>
        """)

    return header + "".join(body_rows) + "</tbody></table>"


def _table_text(rows: List[MomentumRow], title: str) -> str:
    if not rows:
        return f"{title}: none\n"

    lines = [f"{title} ({len(rows)}):"]
    for r in rows:
        sign = "+" if r.pct_change >= 0 else ""
        lines.append(
            f"  {r.symbol:12s}  {sign}{r.pct_change:6.2f}%  "
            f"₹{r.start_price:>9,.2f} → ₹{r.end_price:>9,.2f}  {r.name}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Email send (unchanged)
# ---------------------------------------------------------------------------
def _send_momentum_email(gainers: List[MomentumRow],
                        losers: List[MomentumRow],
                        total_scanned: int) -> bool:
    if not all([GMAIL_SENDER, GMAIL_APP_PASSWORD, GMAIL_RECIPIENT]):
        logger.error("Gmail credentials missing")
        return False

    now = datetime.now(IST).strftime("%d %b %Y · %H:%M IST")

    sample = (gainers + losers)[:1]
    period = ""
    if sample:
        period = f"{sample[0].start_date} → {sample[0].end_date}"

    summary = (
        f"{len(gainers)} gainers (≥ +{MOMENTUM_GAIN_THRESHOLD:.0f}%) · "
        f"{len(losers)} losers (≤ {MOMENTUM_LOSS_THRESHOLD:.0f}%) · "
        f"{total_scanned} stocks scanned"
    )

    html_body = f"""
    <html><body style="background:#f9fafb;padding:16px;margin:0;
                       font-family:-apple-system,Segoe UI,Roboto,sans-serif;">
      <div style="max-width:760px;margin:0 auto;">
        <div style="background:#1f2937;color:#fff;padding:14px 16px;border-radius:8px;
                    margin-bottom:12px;">
          <div style="font-size:18px;font-weight:600;">📊 NSE 30-Day Momentum Report</div>
          <div style="font-size:13px;opacity:.8;margin-top:2px;">{_e(now)}</div>
          <div style="font-size:12px;opacity:.7;margin-top:2px;">{_e(period)} · {_e(summary)}</div>
        </div>

        <div style="background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:14px;">
          {_table_html(gainers, "🚀 Top Gainers", "#16a34a")}
          {_table_html(losers,  "📉 Top Losers",  "#dc2626")}
        </div>

        <div style="text-align:center;font-size:12px;color:#9ca3af;margin-top:16px;">
          Generated automatically · For information only · Not investment advice
        </div>
      </div>
    </body></html>
    """

    text_body = (
        f"NSE 30-Day Momentum Report\n{now}\n{period}\n{summary}\n"
        + "=" * 70 + "\n\n"
        + _table_text(gainers, "Top Gainers (>= +10%)") + "\n\n"
        + _table_text(losers,  "Top Losers (<= -7%)") + "\n\n"
        + "=" * 70 + "\nAutomated report · Not investment advice\n"
    )

    msg            = MIMEMultipart("alternative")
    msg["Subject"] = f"{MOMENTUM_SUBJECT_PREFIX} {summary}"
    msg["From"]    = GMAIL_SENDER
    msg["To"]      = GMAIL_RECIPIENT
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html",  "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.starttls()
            server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        return True
    except smtplib.SMTPAuthenticationError as e:
        logger.error("Gmail auth failed: %s", e)
        return False
    except Exception as e:
        logger.error("Gmail send failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Snapshot builder — NOW WITH FUNDAMENTALS for dashboard
# ---------------------------------------------------------------------------
def _build_snapshot_items(gainers: List[MomentumRow],
                          losers: List[MomentumRow]) -> List[dict]:
    """
    Convert MomentumRow objects into JSON-friendly dicts.

    Attaches 5 fundamentals from fundamentals_fetcher (PE, EPS, ROE, 3Y profit
    growth, FII holding %). Fundamentals are cached for 7 days in Postgres, so
    the first run after a fresh cache takes longer (~3-5 min) but subsequent
    daily runs are near-instant for the fundamentals part.

    Missing values are stored as None (rendered as "—" on the dashboard).
    """
    items = []
    all_rows = list(gainers) + list(losers)
    total = len(all_rows)
    logger.info("Attaching fundamentals to %d momentum rows...", total)

    for i, r in enumerate(all_rows, 1):
        if i % 5 == 0:
            logger.info("  Fundamentals progress: %d/%d", i, total)
        # Determine direction from the source list
        direction = "gainer" if r in gainers else "loser"
        fund = get_fundamentals(r.symbol)
        items.append({
            "symbol":               r.symbol,
            "name":                 r.name,
            "direction":            direction,
            "pct_change":           round(r.pct_change, 2),
            "start_price":          round(r.start_price, 2),
            "end_price":            round(r.end_price, 2),
            "start_date":           r.start_date,
            "end_date":             r.end_date,
            # Fundamentals (any may be None)
            "pe_ratio":             round(fund.pe_ratio, 2) if fund.pe_ratio is not None else None,
            "eps":                  round(fund.eps, 2) if fund.eps is not None else None,
            "roe_pct":              round(fund.roe_pct, 2) if fund.roe_pct is not None else None,
            "profit_growth_3y_pct": round(fund.profit_growth_3y_pct, 2) if fund.profit_growth_3y_pct is not None else None,
            "fii_holding_pct":      round(fund.fii_holding_pct, 2) if fund.fii_holding_pct is not None else None,
        })
    return items


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def run_momentum_report() -> dict:
    """Fetch + classify + send + save snapshot (with fundamentals for dashboard)."""
    logger.info("=" * 60)
    logger.info("Starting 30-day momentum report at %s IST",
                datetime.now(IST).strftime("%H:%M:%S"))
    logger.info("=" * 60)

    symbols = get_all_symbols()
    logger.info("Universe: %d stocks", len(symbols))

    rows: List[MomentumRow] = []
    for i, symbol in enumerate(symbols, 1):
        if i % 25 == 0:
            logger.info("Progress: %d/%d", i, len(symbols))
        row = _fetch_momentum_row(symbol)
        if row is not None:
            rows.append(row)

    gainers, losers = _classify(rows)
    logger.info("Gainers: %d  ·  Losers: %d  ·  Total scanned: %d",
                len(gainers), len(losers), len(rows))

    # --- Save snapshot for the dashboard (before email so it's saved even if email fails) ---
    # This is where fundamentals get attached. With 7-day Postgres cache, this is fast
    # after the first run; first run may take 3-5 min as it warms the cache.
    snapshot_items = _build_snapshot_items(gainers, losers)
    period = ""
    if gainers or losers:
        sample = (gainers + losers)[0]
        period = f"{sample.start_date} → {sample.end_date}"
    save_snapshot("momentum", snapshot_items, {
        "gainers_count":     len(gainers),
        "losers_count":      len(losers),
        "total_scanned":     len(rows),
        "gain_threshold":    MOMENTUM_GAIN_THRESHOLD,
        "loss_threshold":    MOMENTUM_LOSS_THRESHOLD,
        "period":            period,
        "report_time":       datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
    })

    sent = _send_momentum_email(gainers, losers, len(rows))
    if sent:
        logger.info("📤 Momentum report sent")
    else:
        logger.error("✗ Momentum email failed")

    return {
        "total_scanned": len(rows),
        "gainers":       len(gainers),
        "losers":        len(losers),
        "email_sent":    sent,
    }
