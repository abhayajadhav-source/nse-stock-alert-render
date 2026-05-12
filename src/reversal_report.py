"""
Trend reversal report — identifies stocks showing potential bullish
or bearish reversals on the daily timeframe.

A stock is flagged when at least N of these signals confirm:
  1. MA crossover    (20 SMA crosses 50 SMA in recent days, price above 50 SMA)
  2. RSI rebound     (RSI was oversold/overbought recently, now reverting)
  3. MACD cross      (MACD line crosses signal line in recent days)
  4. Volume          (recent volume meaningfully above longer-window average)
  5. Pivot structure (higher lows for bullish, lower highs for bearish)

Output: one consolidated email with two tables — bullish and bearish candidates.
"""

from __future__ import annotations

import html
import logging
import smtplib
import time
from dataclasses import dataclass, field
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional

import yfinance as yf

from config import (
    GMAIL_APP_PASSWORD, GMAIL_RECIPIENT, GMAIL_SENDER, IST,
    MA_CROSSOVER_LOOKBACK_DAYS, MACD_CROSSOVER_LOOKBACK_DAYS, MACD_FAST,
    MACD_SIGNAL, MACD_SLOW, REVERSAL_MAX_PER_SECTION,
    REVERSAL_MIN_SIGNALS_REQUIRED, REVERSAL_SUBJECT_PREFIX,
    RSI_BEAR_CONFIRM, RSI_BULL_CONFIRM, RSI_LOOKBACK_DAYS, RSI_OVERBOUGHT,
    RSI_OVERSOLD, RSI_PERIOD, SMA_LONG, SMA_SHORT, SWING_PIVOT_RADIUS,
    SWING_WINDOW_DAYS, VOLUME_CONFIRM_BASE_DAYS, VOLUME_CONFIRM_RATIO,
    VOLUME_CONFIRM_RECENT_DAYS, YF_RETRIES,
)
from stock_list import NSE_STOCKS, get_all_symbols, get_yf_symbol
from technical_indicators import (
    find_pivot_highs, find_pivot_lows, has_crossed_above, has_crossed_below,
    has_higher_lows, has_lower_highs, macd, rsi, sma,
)

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


# ---------------------------------------------------------------------------
# Reversal analysis result
# ---------------------------------------------------------------------------
@dataclass
class ReversalSignal:
    symbol: str
    name: str
    current_price: float
    pct_change_1d: float
    direction: str               # "bullish" or "bearish"
    signal_count: int            # 0-5

    # Individual signal flags
    has_ma_signal:     bool = False
    has_rsi_signal:    bool = False
    has_macd_signal:   bool = False
    has_volume_signal: bool = False
    has_pivot_signal:  bool = False

    # Indicator values for context in the report
    rsi_value:        float = 0.0
    sma_short_value:  float = 0.0
    sma_long_value:   float = 0.0
    macd_histogram:   float = 0.0
    volume_ratio:     float = 0.0

    # Human-readable list of which signals fired
    confirmations: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Per-stock analysis
# ---------------------------------------------------------------------------
def _analyze_stock(symbol: str) -> Optional[ReversalSignal]:
    """
    Pull ~6 months of daily data and run every reversal check.
    Returns None if data is missing or no reversal flag fires.
    Returns the strongest direction (bullish OR bearish) when both somehow
    appear (rare in practice; one usually dominates).
    """
    yf_symbol = get_yf_symbol(symbol)

    for attempt in range(YF_RETRIES + 1):
        try:
            ticker = yf.Ticker(yf_symbol)
            # 6 months gives enough history for 50 SMA + indicator warm-up
            hist = ticker.history(period="6mo", interval="1d")
            if hist.empty or len(hist) < SMA_LONG + 10:
                return None
            break
        except Exception as e:
            if attempt < YF_RETRIES:
                time.sleep(1 + attempt)
                continue
            logger.warning("Reversal fetch failed for %s: %s", symbol, e)
            return None

    close   = hist["Close"]
    volume  = hist["Volume"]
    current = float(close.iloc[-1])
    prev    = float(close.iloc[-2])
    pct_1d  = ((current - prev) / prev) * 100 if prev > 0 else 0.0

    # --- Compute indicators (used by both bull and bear branches) ---
    sma_s        = sma(close, SMA_SHORT)
    sma_l        = sma(close, SMA_LONG)
    rsi_series   = rsi(close, RSI_PERIOD)
    macd_line, signal_line, histogram = macd(close, MACD_FAST, MACD_SLOW, MACD_SIGNAL)

    rsi_now      = float(rsi_series.iloc[-1]) if pd.notna_safe(rsi_series.iloc[-1]) else 50.0
    sma_s_now    = float(sma_s.iloc[-1])      if not pd.isna(sma_s.iloc[-1])      else current
    sma_l_now    = float(sma_l.iloc[-1])      if not pd.isna(sma_l.iloc[-1])      else current
    hist_now     = float(histogram.iloc[-1])  if not pd.isna(histogram.iloc[-1])  else 0.0

    # Volume confirmation: recent-window avg vs base-window avg
    recent_vol_avg = volume.iloc[-VOLUME_CONFIRM_RECENT_DAYS:].mean()
    base_vol_avg   = volume.iloc[-VOLUME_CONFIRM_BASE_DAYS:].mean()
    vol_ratio      = (recent_vol_avg / base_vol_avg) if base_vol_avg > 0 else 0.0

    # --- Bullish reversal checks ---
    bull = ReversalSignal(
        symbol=symbol,
        name=NSE_STOCKS.get(symbol, {}).get("name", symbol),
        current_price=current,
        pct_change_1d=pct_1d,
        direction="bullish",
        signal_count=0,
        rsi_value=rsi_now,
        sma_short_value=sma_s_now,
        sma_long_value=sma_l_now,
        macd_histogram=hist_now,
        volume_ratio=vol_ratio,
    )

    # Signal 1 — Golden cross (20 SMA crosses above 50 SMA) AND price above 50 SMA
    if has_crossed_above(sma_s, sma_l, MA_CROSSOVER_LOOKBACK_DAYS) and current > sma_l_now:
        bull.has_ma_signal = True
        bull.signal_count += 1
        bull.confirmations.append(f"Golden cross (20>50 SMA), price > 50 SMA")

    # Signal 2 — RSI was oversold within lookback window AND has climbed back above confirm level
    recent_rsi = rsi_series.iloc[-RSI_LOOKBACK_DAYS:]
    if (recent_rsi.min() < RSI_OVERSOLD) and (rsi_now > RSI_BULL_CONFIRM):
        bull.has_rsi_signal = True
        bull.signal_count += 1
        bull.confirmations.append(f"RSI {rsi_now:.0f} (was oversold, now climbing)")

    # Signal 3 — MACD bullish cross with positive histogram
    if has_crossed_above(macd_line, signal_line, MACD_CROSSOVER_LOOKBACK_DAYS) and hist_now > 0:
        bull.has_macd_signal = True
        bull.signal_count += 1
        bull.confirmations.append(f"MACD bull cross (hist +{hist_now:.2f})")

    # Signal 4 — Volume confirmation: recent 5-day avg >= 1.5x base 20-day avg
    if vol_ratio >= VOLUME_CONFIRM_RATIO:
        bull.has_volume_signal = True
        bull.signal_count += 1
        bull.confirmations.append(f"Volume {vol_ratio:.1f}x base")

    # Signal 5 — Higher lows pattern in recent swing structure
    swing_window = close.iloc[-SWING_WINDOW_DAYS:]
    lows = find_pivot_lows(swing_window, radius=SWING_PIVOT_RADIUS)
    if has_higher_lows(lows, min_count=3):
        bull.has_pivot_signal = True
        bull.signal_count += 1
        bull.confirmations.append("Higher lows pattern")

    # --- Bearish reversal checks ---
    bear = ReversalSignal(
        symbol=symbol,
        name=NSE_STOCKS.get(symbol, {}).get("name", symbol),
        current_price=current,
        pct_change_1d=pct_1d,
        direction="bearish",
        signal_count=0,
        rsi_value=rsi_now,
        sma_short_value=sma_s_now,
        sma_long_value=sma_l_now,
        macd_histogram=hist_now,
        volume_ratio=vol_ratio,
    )

    if has_crossed_below(sma_s, sma_l, MA_CROSSOVER_LOOKBACK_DAYS) and current < sma_l_now:
        bear.has_ma_signal = True
        bear.signal_count += 1
        bear.confirmations.append(f"Death cross (20<50 SMA), price < 50 SMA")

    if (recent_rsi.max() > RSI_OVERBOUGHT) and (rsi_now < RSI_BEAR_CONFIRM):
        bear.has_rsi_signal = True
        bear.signal_count += 1
        bear.confirmations.append(f"RSI {rsi_now:.0f} (was overbought, now falling)")

    if has_crossed_below(macd_line, signal_line, MACD_CROSSOVER_LOOKBACK_DAYS) and hist_now < 0:
        bear.has_macd_signal = True
        bear.signal_count += 1
        bear.confirmations.append(f"MACD bear cross (hist {hist_now:.2f})")

    if vol_ratio >= VOLUME_CONFIRM_RATIO:
        bear.has_volume_signal = True
        bear.signal_count += 1
        bear.confirmations.append(f"Volume {vol_ratio:.1f}x base")

    highs = find_pivot_highs(swing_window, radius=SWING_PIVOT_RADIUS)
    if has_lower_highs(highs, min_count=3):
        bear.has_pivot_signal = True
        bear.signal_count += 1
        bear.confirmations.append("Lower highs pattern")

    # Return whichever direction meets the threshold; if both, pick the stronger
    if bull.signal_count >= REVERSAL_MIN_SIGNALS_REQUIRED and bull.signal_count >= bear.signal_count:
        return bull
    if bear.signal_count >= REVERSAL_MIN_SIGNALS_REQUIRED:
        return bear
    return None


# Small helper because pd.isna can fail on certain types in older pandas
def _import_pandas_safe():
    import pandas as pd
    return pd

pd = _import_pandas_safe()
# Monkey-patched safe-NaN check used above
class _PdHelper:
    @staticmethod
    def notna_safe(v):
        try:
            return not pd.isna(v)
        except Exception:
            return False
# expose at module level for _analyze_stock above
pd.notna_safe = _PdHelper.notna_safe  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Email rendering
# ---------------------------------------------------------------------------
def _e(text: str) -> str:
    return html.escape(text or "")


def _row_html(s: ReversalSignal, color: str) -> str:
    sign = "+" if s.pct_change_1d >= 0 else ""
    confirmations = " · ".join(s.confirmations)
    return f"""
    <tr style="border-bottom:1px solid #e5e7eb;">
      <td style="padding:8px;font-weight:600;vertical-align:top;">{_e(s.symbol)}</td>
      <td style="padding:8px;color:#6b7280;vertical-align:top;">{_e(s.name)}</td>
      <td style="padding:8px;text-align:right;font-weight:600;vertical-align:top;">
        ₹{s.current_price:,.2f}<br>
        <span style="color:{color};font-weight:500;font-size:12px;">{sign}{s.pct_change_1d:.2f}%</span>
      </td>
      <td style="padding:8px;text-align:center;font-weight:600;vertical-align:top;color:{color};">
        {s.signal_count}/5
      </td>
      <td style="padding:8px;font-size:12px;color:#374151;vertical-align:top;">{_e(confirmations)}</td>
      <td style="padding:8px;vertical-align:top;">
        <a href="https://in.tradingview.com/chart/?symbol=NSE:{s.symbol}"
           style="color:#1d4ed8;text-decoration:none;font-size:13px;">📈 TV</a>
      </td>
    </tr>
    """


def _table_html(signals: List[ReversalSignal], title: str, color: str) -> str:
    if not signals:
        return f'<p style="color:#6b7280;font-style:italic;">No {title.lower()} candidates today.</p>'

    header = f"""
    <h3 style="margin:18px 0 8px 0;color:{color};">{title} ({len(signals)})</h3>
    <table style="width:100%;border-collapse:collapse;font-size:14px;
                  font-family:-apple-system,Segoe UI,Roboto,sans-serif;">
      <thead>
        <tr style="background:#f3f4f6;color:#374151;text-align:left;">
          <th style="padding:8px;">Symbol</th>
          <th style="padding:8px;">Name</th>
          <th style="padding:8px;text-align:right;">Price</th>
          <th style="padding:8px;text-align:center;">Signals</th>
          <th style="padding:8px;">Confirmations</th>
          <th style="padding:8px;">Chart</th>
        </tr>
      </thead>
      <tbody>
    """
    body = "".join(_row_html(s, color) for s in signals)
    return header + body + "</tbody></table>"


def _table_text(signals: List[ReversalSignal], title: str) -> str:
    if not signals:
        return f"{title}: none\n"

    lines = [f"{title} ({len(signals)}):"]
    for s in signals:
        sign = "+" if s.pct_change_1d >= 0 else ""
        lines.append(
            f"  {s.symbol:12s}  {s.signal_count}/5  ₹{s.current_price:>9,.2f}  "
            f"({sign}{s.pct_change_1d:5.2f}%)  {s.name}"
        )
        for c in s.confirmations:
            lines.append(f"      • {c}")
    return "\n".join(lines)


def _send_reversal_email(
    bulls: List[ReversalSignal], bears: List[ReversalSignal], total_scanned: int
) -> bool:
    if not all([GMAIL_SENDER, GMAIL_APP_PASSWORD, GMAIL_RECIPIENT]):
        logger.error("Gmail credentials missing")
        return False

    now = datetime.now(IST).strftime("%d %b %Y · %H:%M IST")
    summary = (
        f"{len(bulls)} bullish reversal · {len(bears)} bearish reversal "
        f"· {total_scanned} stocks analysed"
    )

    html_body = f"""
    <html><body style="background:#f9fafb;padding:16px;margin:0;
                       font-family:-apple-system,Segoe UI,Roboto,sans-serif;">
      <div style="max-width:780px;margin:0 auto;">
        <div style="background:#1f2937;color:#fff;padding:14px 16px;border-radius:8px;
                    margin-bottom:12px;">
          <div style="font-size:18px;font-weight:600;">🔄 NSE Trend Reversal Report</div>
          <div style="font-size:13px;opacity:.8;margin-top:2px;">{_e(now)}</div>
          <div style="font-size:12px;opacity:.7;margin-top:2px;">{_e(summary)}</div>
        </div>

        <div style="background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:14px;">
          {_table_html(bulls, "🚀 Bullish Reversals", "#16a34a")}
          {_table_html(bears, "📉 Bearish Reversals", "#dc2626")}
        </div>

        <div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;
                    padding:10px 14px;margin-top:12px;font-size:12px;color:#9a3412;">
          <strong>Methodology:</strong> Each candidate has ≥
          {REVERSAL_MIN_SIGNALS_REQUIRED}/5 signals confirmed:
          MA crossover (20/50 SMA) · RSI rebound (oversold/overbought reversion) ·
          MACD line/signal cross · Volume surge ({VOLUME_CONFIRM_RATIO}x base) ·
          Pivot structure (higher lows / lower highs).
        </div>

        <div style="text-align:center;font-size:12px;color:#9ca3af;margin-top:12px;">
          Daily-timeframe analysis · For information only · Not investment advice
        </div>
      </div>
    </body></html>
    """

    text_body = (
        f"NSE Trend Reversal Report\n{now}\n{summary}\n"
        + "=" * 70 + "\n\n"
        + _table_text(bulls, "Bullish Reversals") + "\n\n"
        + _table_text(bears, "Bearish Reversals") + "\n\n"
        + "=" * 70
        + f"\nMethodology: candidates have >= {REVERSAL_MIN_SIGNALS_REQUIRED}/5 signals confirmed.\n"
        + "\nFor information only · Not investment advice\n"
    )

    msg            = MIMEMultipart("alternative")
    msg["Subject"] = f"{REVERSAL_SUBJECT_PREFIX} {summary}"
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
# Public entry point
# ---------------------------------------------------------------------------
def run_reversal_report() -> dict:
    """Scan the universe, classify, and send the report."""
    logger.info("=" * 60)
    logger.info("Starting trend-reversal scan at %s IST",
                datetime.now(IST).strftime("%H:%M:%S"))
    logger.info("=" * 60)

    symbols = get_all_symbols()
    logger.info("Universe: %d stocks", len(symbols))

    bulls: List[ReversalSignal] = []
    bears: List[ReversalSignal] = []
    total_ok = 0

    for i, symbol in enumerate(symbols, 1):
        if i % 25 == 0:
            logger.info("Progress: %d/%d", i, len(symbols))
        result = _analyze_stock(symbol)
        total_ok += 1 if result is not None or True else 0  # counted below
        if result is None:
            continue
        if result.direction == "bullish":
            bulls.append(result)
        else:
            bears.append(result)
        logger.info(
            "✓ %s — %s reversal, %d/5 signals: %s",
            symbol, result.direction, result.signal_count,
            ", ".join(result.confirmations),
        )

    # Rank by signal strength (more confirmations first), then by 1-day move
    bulls.sort(key=lambda s: (s.signal_count, s.pct_change_1d), reverse=True)
    bears.sort(key=lambda s: (s.signal_count, -s.pct_change_1d), reverse=True)
    bulls = bulls[:REVERSAL_MAX_PER_SECTION]
    bears = bears[:REVERSAL_MAX_PER_SECTION]

    logger.info("Bullish: %d  ·  Bearish: %d  ·  Total scanned: %d",
                len(bulls), len(bears), len(symbols))

    sent = _send_reversal_email(bulls, bears, len(symbols))
    if sent:
        logger.info("📤 Reversal report sent")
    else:
        logger.error("✗ Reversal email failed")

    return {
        "total_scanned": len(symbols),
        "bullish":       len(bulls),
        "bearish":       len(bears),
        "email_sent":    sent,
    }
