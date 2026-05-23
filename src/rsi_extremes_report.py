"""
RSI Extremes scanner — daily report of NSE stocks at overbought / oversold extremes.

Mirrors the structure of momentum_report.py:
  - Runs once per day (separate cron from intraday)
  - Operates on closing prices (always runs regardless of market hours)
  - Sends one email + saves snapshot to Postgres for the dashboard

Signal logic:
  - RSI(14) on daily timeframe using Wilder's smoothing (TradingView-compatible)
  - INCLUDE if RSI >= 70 AND volume_ratio > 1.5 (overbought with conviction)
  - INCLUDE if RSI <= 30 AND volume_ratio > 1.5 (oversold with conviction)
  - TIER as "extreme" if RSI >= 80 or <= 20

Why volume confirmation: RSI alone produces noise during trending markets
(a stock can sit above 70 for weeks). Pairing with volume > 1.5× avg means
we only flag conviction moves — typically 5-15 names per day vs 30+ without.
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
    YF_RETRIES,
)
from snapshot_store import save_snapshot
from stock_list import NSE_STOCKS, get_all_symbols, get_yf_symbol

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# ---------------------------------------------------------------------------
# Tunable thresholds
# ---------------------------------------------------------------------------
RSI_PERIOD              = 14
RSI_OVERBOUGHT          = 70.0
RSI_OVERSOLD            = 30.0
RSI_EXTREME_OVERBOUGHT  = 80.0
RSI_EXTREME_OVERSOLD    = 20.0

VOLUME_AVG_WINDOW       = 20      # 20-day average volume baseline
VOLUME_RATIO_MIN        = 1.5     # current volume must be >= 1.5× the 20-day avg
HISTORY_PERIOD          = "3mo"   # ~60-65 trading days — enough for RSI(14) + 20-day vol avg

RSI_SUBJECT_PREFIX      = "[NSE RSI Extremes]"
RSI_MAX_PER_SECTION     = 25      # cap items per direction so emails stay short


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class RsiExtremeRow:
    symbol:                    str
    name:                      str
    direction:                 str            # "overbought" | "oversold"
    tier:                      str            # "extreme" | "standard"
    rsi:                       float
    price:                     float
    pct_change_1d:             float
    volume_ratio:              float
    sma_50:                    float
    distance_from_sma_50_pct:  float          # +ve = above SMA50, -ve = below
    bar_date:                  str            # date of the last bar


# ---------------------------------------------------------------------------
# RSI computation — Wilder's smoothing (matches TradingView/standard charting)
# ---------------------------------------------------------------------------
def _wilder_rsi(closes: list[float], period: int = RSI_PERIOD) -> Optional[float]:
    """
    RSI using Wilder's smoothing. This is what TradingView, Yahoo, and most
    charting platforms show — NOT the simpler EMA-based RSI some libraries use.

    Returns the latest RSI value, or None if there isn't enough data.
    """
    if len(closes) < period + 1:
        return None

    # First gain/loss — simple averages of the first `period` deltas
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        if delta >= 0:
            gains  += delta
        else:
            losses += -delta
    avg_gain = gains / period
    avg_loss = losses / period

    # Wilder smoothing for the remainder
    for i in range(period + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gain = delta  if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


# ---------------------------------------------------------------------------
# Per-symbol fetch + analyse
# ---------------------------------------------------------------------------
def _fetch_rsi_row(symbol: str) -> Optional[RsiExtremeRow]:
    """
    Fetch ~3 months of daily bars, compute RSI(14) + 20-day vol avg, and
    return a row IFF the symbol meets the RSI + volume criteria.

    Returns None for stocks that don't meet the threshold — caller filters
    these out implicitly.
    """
    yf_symbol = get_yf_symbol(symbol)

    for attempt in range(YF_RETRIES + 1):
        try:
            ticker = yf.Ticker(yf_symbol)
            hist = ticker.history(period=HISTORY_PERIOD, interval="1d")
            if hist.empty or len(hist) < RSI_PERIOD + 5:
                return None

            closes  = [float(c) for c in hist["Close"].tolist()]
            volumes = [float(v) for v in hist["Volume"].tolist()]

            rsi = _wilder_rsi(closes, RSI_PERIOD)
            if rsi is None:
                return None

            # Quick exit if not at an extreme — saves doing further work
            is_ob = rsi >= RSI_OVERBOUGHT
            is_os = rsi <= RSI_OVERSOLD
            if not (is_ob or is_os):
                return None

            # Volume confirmation
            if len(volumes) < VOLUME_AVG_WINDOW + 1:
                return None
            recent_vol  = volumes[-1]
            avg_vol     = sum(volumes[-(VOLUME_AVG_WINDOW + 1):-1]) / VOLUME_AVG_WINDOW
            if avg_vol <= 0:
                return None
            volume_ratio = recent_vol / avg_vol
            if volume_ratio < VOLUME_RATIO_MIN:
                return None

            # Build the row
            end_price = closes[-1]
            prev_close = closes[-2] if len(closes) >= 2 else end_price
            pct_change_1d = ((end_price - prev_close) / prev_close * 100) if prev_close > 0 else 0.0

            # 50-day SMA (use whatever's available if we have less than 50 bars)
            sma_window = min(50, len(closes))
            sma_50 = sum(closes[-sma_window:]) / sma_window
            distance_pct = ((end_price - sma_50) / sma_50 * 100) if sma_50 > 0 else 0.0

            # Direction + tier
            if is_ob:
                direction = "overbought"
                tier = "extreme" if rsi >= RSI_EXTREME_OVERBOUGHT else "standard"
            else:
                direction = "oversold"
                tier = "extreme" if rsi <= RSI_EXTREME_OVERSOLD else "standard"

            name = NSE_STOCKS.get(symbol, {}).get("name", symbol)

            return RsiExtremeRow(
                symbol                    = symbol,
                name                      = name,
                direction                 = direction,
                tier                      = tier,
                rsi                       = round(rsi, 2),
                price                     = round(end_price, 2),
                pct_change_1d             = round(pct_change_1d, 2),
                volume_ratio              = round(volume_ratio, 2),
                sma_50                    = round(sma_50, 2),
                distance_from_sma_50_pct  = round(distance_pct, 2),
                bar_date                  = hist.index[-1].strftime("%d %b %Y"),
            )
        except Exception as e:
            err_str = str(e).lower()
            # Yahoo's "Too Many Requests" needs a longer backoff than other errors —
            # use 5/10/15s instead of the default 1/2/3s
            if "too many requests" in err_str or "rate limited" in err_str:
                if attempt < YF_RETRIES:
                    backoff = 5 * (attempt + 1)
                    logger.info("Rate limited on %s; sleeping %ds before retry", symbol, backoff)
                    time.sleep(backoff)
                    continue
                logger.warning("RSI fetch failed for %s after retries: %s", symbol, e)
                return None
            if attempt < YF_RETRIES:
                time.sleep(1 + attempt)
                continue
            logger.warning("RSI fetch failed for %s: %s", symbol, e)
            return None
    return None


def _classify(rows: List[RsiExtremeRow]) -> tuple[List[RsiExtremeRow], List[RsiExtremeRow]]:
    """
    Split into overbought + oversold, with extreme tier first within each,
    then by absolute distance from SMA50 (most stretched at top).
    """
    overbought = [r for r in rows if r.direction == "overbought"]
    oversold   = [r for r in rows if r.direction == "oversold"]

    # Sort: extreme tier first, then by RSI (highest for OB, lowest for OS)
    overbought.sort(key=lambda r: (r.tier != "extreme", -r.rsi))
    oversold.sort  (key=lambda r: (r.tier != "extreme",  r.rsi))

    return overbought[:RSI_MAX_PER_SECTION], oversold[:RSI_MAX_PER_SECTION]


# ---------------------------------------------------------------------------
# Email rendering
# ---------------------------------------------------------------------------
def _e(text: str) -> str:
    return html.escape(text or "")


def _table_html(rows: List[RsiExtremeRow], title: str, color: str) -> str:
    if not rows:
        return f'<p style="color:#6b7280;font-style:italic;">No {title.lower()}.</p>'

    header = f"""
    <h3 style="margin:18px 0 8px 0;color:{color};">{title} ({len(rows)})</h3>
    <table style="width:100%;border-collapse:collapse;font-size:13px;
                  font-family:-apple-system,Segoe UI,Roboto,sans-serif;">
      <thead>
        <tr style="background:#f3f4f6;color:#374151;text-align:left;">
          <th style="padding:8px;">Symbol</th>
          <th style="padding:8px;">Tier</th>
          <th style="padding:8px;text-align:right;">RSI</th>
          <th style="padding:8px;text-align:right;">Price ₹</th>
          <th style="padding:8px;text-align:right;">1D %</th>
          <th style="padding:8px;text-align:right;">Vol</th>
          <th style="padding:8px;text-align:right;">vs SMA50</th>
          <th style="padding:8px;">Chart</th>
        </tr>
      </thead>
      <tbody>
    """

    body_rows = []
    for r in rows:
        sign_1d = "+" if r.pct_change_1d >= 0 else ""
        sign_sma = "+" if r.distance_from_sma_50_pct >= 0 else ""
        tier_badge = ""
        if r.tier == "extreme":
            tier_badge = (
                f'<span style="background:{color};color:#fff;padding:2px 6px;'
                f'border-radius:3px;font-size:10px;font-weight:600;">EXTREME</span>'
            )
        else:
            tier_badge = (
                '<span style="background:#e5e7eb;color:#374151;padding:2px 6px;'
                'border-radius:3px;font-size:10px;">Standard</span>'
            )

        change_color = "#16a34a" if r.pct_change_1d >= 0 else "#dc2626"

        body_rows.append(f"""
        <tr style="border-bottom:1px solid #e5e7eb;">
          <td style="padding:8px;font-weight:600;">
            {_e(r.symbol)}
            <div style="color:#9ca3af;font-size:11px;font-weight:400;">{_e(r.name)}</div>
          </td>
          <td style="padding:8px;">{tier_badge}</td>
          <td style="padding:8px;text-align:right;color:{color};font-weight:600;">{r.rsi:.1f}</td>
          <td style="padding:8px;text-align:right;">{r.price:,.2f}</td>
          <td style="padding:8px;text-align:right;color:{change_color};font-weight:600;">
            {sign_1d}{r.pct_change_1d:.2f}%
          </td>
          <td style="padding:8px;text-align:right;">{r.volume_ratio:.1f}x</td>
          <td style="padding:8px;text-align:right;color:#6b7280;">
            {sign_sma}{r.distance_from_sma_50_pct:.1f}%
          </td>
          <td style="padding:8px;">
            <a href="https://in.tradingview.com/chart/?symbol=NSE:{r.symbol}"
               style="color:#1d4ed8;text-decoration:none;">📈 TV</a>
          </td>
        </tr>
        """)

    return header + "".join(body_rows) + "</tbody></table>"


def _table_text(rows: List[RsiExtremeRow], title: str) -> str:
    if not rows:
        return f"{title}: none\n"

    lines = [f"{title} ({len(rows)}):"]
    for r in rows:
        tier_marker = " [EXTREME]" if r.tier == "extreme" else ""
        sign_1d = "+" if r.pct_change_1d >= 0 else ""
        lines.append(
            f"  {r.symbol:12s}  RSI {r.rsi:5.1f}{tier_marker:11s}  "
            f"₹{r.price:>9,.2f}  {sign_1d}{r.pct_change_1d:6.2f}%  "
            f"vol {r.volume_ratio:>4.1f}x  {r.name}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Email send
# ---------------------------------------------------------------------------
def _send_rsi_email(overbought: List[RsiExtremeRow],
                    oversold:   List[RsiExtremeRow],
                    total_scanned: int) -> bool:
    if not all([GMAIL_SENDER, GMAIL_APP_PASSWORD, GMAIL_RECIPIENT]):
        logger.error("Gmail credentials missing")
        return False

    now = datetime.now(IST).strftime("%d %b %Y · %H:%M IST")

    extreme_ob = sum(1 for r in overbought if r.tier == "extreme")
    extreme_os = sum(1 for r in oversold   if r.tier == "extreme")

    summary = (
        f"{len(overbought)} overbought ({extreme_ob} extreme) · "
        f"{len(oversold)} oversold ({extreme_os} extreme) · "
        f"{total_scanned} stocks scanned"
    )

    html_body = f"""
    <html><body style="background:#f9fafb;padding:16px;margin:0;
                       font-family:-apple-system,Segoe UI,Roboto,sans-serif;">
      <div style="max-width:820px;margin:0 auto;">
        <div style="background:#1f2937;color:#fff;padding:14px 16px;border-radius:8px;
                    margin-bottom:12px;">
          <div style="font-size:18px;font-weight:600;">🌡️ NSE RSI Extremes Report</div>
          <div style="font-size:13px;opacity:.8;margin-top:2px;">{_e(now)}</div>
          <div style="font-size:12px;opacity:.7;margin-top:2px;">
            Daily RSI({RSI_PERIOD}) ≥{int(RSI_OVERBOUGHT)} or ≤{int(RSI_OVERSOLD)},
            volume &gt; {VOLUME_RATIO_MIN:.1f}× the 20-day avg · {_e(summary)}
          </div>
        </div>

        <div style="background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:14px;">
          {_table_html(overbought, "🔴 Overbought (RSI ≥ 70 + Volume)", "#dc2626")}
          {_table_html(oversold,   "🟢 Oversold (RSI ≤ 30 + Volume)",  "#16a34a")}
        </div>

        <div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;
                    padding:12px;margin-top:12px;font-size:12px;color:#1e40af;">
          <strong>Reading the signals:</strong><br>
          • <strong>Overbought</strong> on conviction volume often precedes mean-reversion,
            but can also extend in strong trends — confirm with price action.<br>
          • <strong>Oversold</strong> on conviction volume often marks capitulation lows,
            but can extend in waterfall declines — confirm with price action.<br>
          • <strong>Extreme</strong> tier (RSI ≥80 or ≤20) is rare and usually marks
            the most stretched moves — worth deeper inspection.
        </div>

        <div style="text-align:center;font-size:12px;color:#9ca3af;margin-top:16px;">
          Generated automatically · For information only · Not investment advice
        </div>
      </div>
    </body></html>
    """

    text_body = (
        f"NSE RSI Extremes Report\n{now}\n{summary}\n"
        + "=" * 70 + "\n\n"
        + _table_text(overbought, f"Overbought (RSI >= {int(RSI_OVERBOUGHT)} + vol > {VOLUME_RATIO_MIN}x)") + "\n\n"
        + _table_text(oversold,   f"Oversold (RSI <= {int(RSI_OVERSOLD)} + vol > {VOLUME_RATIO_MIN}x)") + "\n\n"
        + "=" * 70 + "\nAutomated report · Not investment advice\n"
    )

    msg            = MIMEMultipart("alternative")
    msg["Subject"] = f"{RSI_SUBJECT_PREFIX} {summary}"
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
# Snapshot builder
# ---------------------------------------------------------------------------
def _build_snapshot_items(overbought: List[RsiExtremeRow],
                          oversold:   List[RsiExtremeRow]) -> List[dict]:
    """Convert rows into JSON-friendly dicts for the dashboard."""
    items = []
    for r in overbought + oversold:
        items.append({
            "symbol":                   r.symbol,
            "name":                     r.name,
            "direction":                r.direction,
            "tier":                     r.tier,
            "rsi":                      r.rsi,
            "price":                    r.price,
            "pct_change_1d":            r.pct_change_1d,
            "volume_ratio":             r.volume_ratio,
            "sma_50":                   r.sma_50,
            "distance_from_sma_50_pct": r.distance_from_sma_50_pct,
            "bar_date":                 r.bar_date,
        })
    return items


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def run_rsi_extremes_report() -> dict:
    """Fetch + classify + send + save snapshot."""
    logger.info("=" * 60)
    logger.info("Starting RSI extremes scan at %s IST",
                datetime.now(IST).strftime("%H:%M:%S"))
    logger.info("=" * 60)

    symbols = get_all_symbols()
    logger.info("Universe: %d stocks · RSI(%d) thresholds: %.0f/%.0f · vol > %.1fx",
                len(symbols), RSI_PERIOD, RSI_OVERBOUGHT, RSI_OVERSOLD, VOLUME_RATIO_MIN)

    rows: List[RsiExtremeRow] = []
    # Pace requests at ~150ms apart to stay under Yahoo's per-second throttle.
    # For a 126-stock universe this adds ~19s to the run (total still under 1 min).
    INTER_REQUEST_DELAY = 0.15

    for i, symbol in enumerate(symbols, 1):
        if i % 25 == 0:
            logger.info("Progress: %d/%d (%d extremes so far)", i, len(symbols), len(rows))
        row = _fetch_rsi_row(symbol)
        if row is not None:
            rows.append(row)
        time.sleep(INTER_REQUEST_DELAY)

    overbought, oversold = _classify(rows)
    extreme_ob = sum(1 for r in overbought if r.tier == "extreme")
    extreme_os = sum(1 for r in oversold   if r.tier == "extreme")

    logger.info(
        "Overbought: %d (%d extreme)  ·  Oversold: %d (%d extreme)  ·  Total flagged: %d",
        len(overbought), extreme_ob, len(oversold), extreme_os, len(rows),
    )

    # --- Save snapshot for the dashboard (before email so it persists even if email fails) ---
    snapshot_items = _build_snapshot_items(overbought, oversold)
    save_snapshot("rsi_extremes", snapshot_items, {
        "overbought_count":          len(overbought),
        "oversold_count":            len(oversold),
        "extreme_overbought_count":  extreme_ob,
        "extreme_oversold_count":    extreme_os,
        "total_flagged":             len(rows),
        "total_scanned":             len(symbols),
        "rsi_period":                RSI_PERIOD,
        "rsi_overbought_threshold":  RSI_OVERBOUGHT,
        "rsi_oversold_threshold":    RSI_OVERSOLD,
        "rsi_extreme_ob_threshold":  RSI_EXTREME_OVERBOUGHT,
        "rsi_extreme_os_threshold":  RSI_EXTREME_OVERSOLD,
        "volume_ratio_min":          VOLUME_RATIO_MIN,
        "report_time":               datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
    })

    sent = _send_rsi_email(overbought, oversold, len(rows))
    if sent:
        logger.info("📤 RSI extremes report sent")
    else:
        logger.error("✗ RSI extremes email failed")

    return {
        "total_scanned":      len(symbols),
        "total_flagged":      len(rows),
        "overbought":         len(overbought),
        "oversold":           len(oversold),
        "extreme_overbought": extreme_ob,
        "extreme_oversold":   extreme_os,
        "email_sent":         sent,
    }
