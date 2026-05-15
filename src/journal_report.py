"""
End-of-day journal report — runs after market close.

Pulls today's alerts from Postgres, scores how they performed, builds
a daily summary email with winners/losers, market context, and reflection
prompts. Helps build the review discipline that distinguishes profitable
traders from screen-watchers.
"""

from __future__ import annotations

import html
import json
import logging
import smtplib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional

import psycopg2
import yfinance as yf

from config import (
    DATABASE_URL, EMAIL_SUBJECT_PREFIX, GMAIL_APP_PASSWORD,
    GMAIL_RECIPIENT, GMAIL_SENDER, IST,
)
from snapshot_store import save_snapshot
from stock_list import NSE_STOCKS, get_yf_symbol

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# Subject prefix for filtering in Gmail
JOURNAL_SUBJECT_PREFIX = "[NSE Journal]"

# How far back to look for today's alerts (handles late-evening runs)
ALERT_LOOKBACK_HOURS = 12

# Number of trading days for trailing performance (winners/losers list)
TRAILING_PERFORMANCE_DAYS = 5

# Cap rows per section
MAX_WINNERS = 10
MAX_LOSERS  = 10
MAX_WATCHLIST = 15


# ---------------------------------------------------------------------------
# Postgres helpers
# ---------------------------------------------------------------------------
@contextmanager
def _conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    c = psycopg2.connect(DATABASE_URL)
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class AlertOutcome:
    """An alert that was triggered today, and how the stock performed since."""
    symbol: str
    name: str
    source: str               # 'intraday', 'reversal', 'momentum'
    signal: str               # human-readable signal description
    alerted_at: datetime
    alerted_price: float
    current_price: float
    pct_move_since_alert: float
    direction_expected: str   # 'up' or 'down' or 'neutral'
    direction_actual:   str
    paid_off: bool            # did it move in the expected direction?


@dataclass
class MarketContext:
    """Today's broad-market snapshot."""
    nifty_pct:      Optional[float] = None
    bank_nifty_pct: Optional[float] = None
    nifty_close:    Optional[float] = None
    bank_nifty_close: Optional[float] = None
    advancing:      int = 0     # stocks from our universe that closed positive
    declining:      int = 0
    unchanged:      int = 0


# ---------------------------------------------------------------------------
# Pull today's alerts from snapshot history
# ---------------------------------------------------------------------------
def _fetch_todays_alerts() -> List[dict]:
    """
    Read the latest snapshot from each report type. Each snapshot represents
    "what was flagged today" — we treat its items as today's alerts.

    Returns a flat list of alert dicts with source attribution.
    """
    cutoff = datetime.utcnow() - timedelta(hours=ALERT_LOOKBACK_HOURS)
    alerts = []

    try:
        with _conn() as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT report_type, updated_at, payload
                    FROM snapshots
                    WHERE updated_at >= %s
                """, (cutoff,))
                for report_type, updated_at, payload in cur.fetchall():
                    items = payload.get("items", []) if isinstance(payload, dict) else []
                    for item in items:
                        alerts.append({
                            "source":     report_type,
                            "updated_at": updated_at,
                            **item,
                        })
    except psycopg2.errors.UndefinedTable:
        logger.warning("snapshots table doesn't exist yet")
        return []
    except Exception as e:
        logger.exception("Failed to fetch today's alerts: %s", e)
        return []

    logger.info("Found %d alerts from today's snapshots", len(alerts))
    return alerts


# ---------------------------------------------------------------------------
# Score each alert: did it move in the expected direction?
# ---------------------------------------------------------------------------
def _classify_alert_direction(alert: dict) -> str:
    """
    Infer the directional bias of an alert.
    Returns 'up', 'down', or 'neutral'.
    """
    source = alert.get("source")

    if source == "intraday":
        # Intraday: gap up / breakout up / new 52w high = bullish
        if alert.get("is_gap_up") or alert.get("is_breakout_up") or alert.get("is_new_52w_high"):
            return "up"
        if alert.get("is_gap_down") or alert.get("is_breakout_down") or alert.get("is_new_52w_low"):
            return "down"
        return "neutral"

    if source == "reversal":
        direction = alert.get("direction", "")
        if direction == "bullish": return "up"
        if direction == "bearish": return "down"
        return "neutral"

    if source == "momentum":
        # Already-moved stocks; momentum continuation expected in same direction
        if alert.get("direction") == "gainer": return "up"
        if alert.get("direction") == "loser":  return "down"
        return "neutral"

    return "neutral"


def _describe_signal(alert: dict) -> str:
    """Human-readable description of why this stock was alerted."""
    source = alert.get("source")

    if source == "intraday":
        tags = []
        if alert.get("is_new_52w_high"):  tags.append("New 52W High")
        if alert.get("is_new_52w_low"):   tags.append("New 52W Low")
        if alert.get("is_gap_up"):        tags.append(f"Gap +{alert.get('gap_pct', 0):.1f}%")
        if alert.get("is_gap_down"):      tags.append(f"Gap {alert.get('gap_pct', 0):.1f}%")
        if alert.get("is_breakout_up"):   tags.append("Breakout up")
        if alert.get("is_breakout_down"): tags.append("Breakout down")
        if alert.get("has_volume_spike"):
            tags.append(f"Vol {alert.get('volume_ratio', 0):.1f}x")
        return " · ".join(tags) or "Intraday signal"

    if source == "reversal":
        direction = alert.get("direction", "").title()
        return f"{direction} reversal ({alert.get('signal_count', 0)}/5 signals)"

    if source == "momentum":
        direction = alert.get("direction", "")
        pct = alert.get("pct_change", 0)
        return f"30D {direction}: {pct:+.1f}%"

    return "Signal"


def _score_alerts(alerts: List[dict]) -> List[AlertOutcome]:
    """
    For each alert, fetch the current price and determine if the move
    has played out in the expected direction.

    "Paid off" criteria:
      - Bullish signal: stock is up >= 0.5% from alert price
      - Bearish signal: stock is down >= 0.5%
      - Neutral: skipped (not scored)
    """
    outcomes: List[AlertOutcome] = []

    # Dedupe — same stock may have alerts from multiple sources today; keep
    # the strongest one (intraday wins because it's freshest)
    source_priority = {"intraday": 0, "reversal": 1, "momentum": 2}
    seen: dict[str, dict] = {}
    for alert in alerts:
        sym = alert.get("symbol")
        if not sym:
            continue
        existing = seen.get(sym)
        if existing is None or source_priority.get(alert["source"], 9) < source_priority.get(existing["source"], 9):
            seen[sym] = alert

    for symbol, alert in seen.items():
        direction_expected = _classify_alert_direction(alert)
        alerted_price = float(alert.get("price") or alert.get("end_price") or 0)
        if alerted_price <= 0:
            continue

        # Fetch current price
        try:
            ticker = yf.Ticker(get_yf_symbol(symbol))
            hist = ticker.history(period="2d", interval="1d")
            if hist.empty:
                continue
            current_price = float(hist["Close"].iloc[-1])
        except Exception as e:
            logger.warning("Price refresh failed for %s: %s", symbol, e)
            continue

        pct_move = ((current_price - alerted_price) / alerted_price) * 100

        # Determine actual direction
        if pct_move > 0.5:    actual = "up"
        elif pct_move < -0.5: actual = "down"
        else:                  actual = "neutral"

        # Did it pay off?
        paid_off = False
        if direction_expected == "up" and pct_move >= 0.5:
            paid_off = True
        elif direction_expected == "down" and pct_move <= -0.5:
            paid_off = True

        outcomes.append(AlertOutcome(
            symbol=symbol,
            name=NSE_STOCKS.get(symbol, {}).get("name", symbol),
            source=alert["source"],
            signal=_describe_signal(alert),
            alerted_at=alert.get("updated_at") or datetime.utcnow(),
            alerted_price=alerted_price,
            current_price=current_price,
            pct_move_since_alert=pct_move,
            direction_expected=direction_expected,
            direction_actual=actual,
            paid_off=paid_off,
        ))

    return outcomes


# ---------------------------------------------------------------------------
# Market context — Nifty / Bank Nifty / breadth
# ---------------------------------------------------------------------------
def _fetch_market_context() -> MarketContext:
    """Pull Nifty/Bank Nifty moves and broader breadth."""
    ctx = MarketContext()

    # Indices
    for name, sym, attr_close, attr_pct in [
        ("Nifty 50",    "^NSEI",     "nifty_close",      "nifty_pct"),
        ("Bank Nifty",  "^NSEBANK",  "bank_nifty_close", "bank_nifty_pct"),
    ]:
        try:
            t = yf.Ticker(sym)
            hist = t.history(period="2d", interval="1d")
            if hist.empty or len(hist) < 2:
                continue
            today = float(hist["Close"].iloc[-1])
            prev  = float(hist["Close"].iloc[-2])
            pct   = ((today - prev) / prev) * 100
            setattr(ctx, attr_close, today)
            setattr(ctx, attr_pct, pct)
        except Exception as e:
            logger.warning("Index fetch failed for %s: %s", sym, e)

    # Breadth — quick scan of our universe
    from stock_list import get_all_symbols
    symbols = get_all_symbols()
    advancing = declining = unchanged = 0
    # Sample 50 to keep the fetch fast (full universe would take 5+ min)
    for symbol in symbols[:50]:
        try:
            t = yf.Ticker(get_yf_symbol(symbol))
            hist = t.history(period="2d", interval="1d")
            if hist.empty or len(hist) < 2:
                continue
            today = float(hist["Close"].iloc[-1])
            prev  = float(hist["Close"].iloc[-2])
            pct = ((today - prev) / prev) * 100
            if   pct >  0.2: advancing += 1
            elif pct < -0.2: declining += 1
            else:             unchanged += 1
        except Exception:
            continue

    ctx.advancing = advancing
    ctx.declining = declining
    ctx.unchanged = unchanged
    return ctx


# ---------------------------------------------------------------------------
# Top movers from alert universe (today's winners/losers)
# ---------------------------------------------------------------------------
def _split_winners_losers(outcomes: List[AlertOutcome]) -> tuple[list, list]:
    """Sort outcomes by move magnitude, return top winners and top losers."""
    by_move = sorted(outcomes, key=lambda o: o.pct_move_since_alert, reverse=True)
    winners = [o for o in by_move if o.pct_move_since_alert > 0.5][:MAX_WINNERS]
    losers  = [o for o in by_move if o.pct_move_since_alert < -0.5][-MAX_LOSERS:]
    losers.reverse()  # Show biggest decliner first
    return winners, losers


# ---------------------------------------------------------------------------
# Tomorrow's watchlist — stocks with earnings/results upcoming
# ---------------------------------------------------------------------------
def _fetch_tomorrows_watchlist(outcomes: List[AlertOutcome]) -> List[dict]:
    """
    Fetch upcoming earnings dates for today's alert stocks.

    yfinance's `calendar` property gives earnings dates. We're looking
    specifically for results due in the next 1-3 trading days, since those
    will move prices the most.
    """
    watchlist = []
    tomorrow = datetime.now(IST).date() + timedelta(days=1)
    in_3_days = datetime.now(IST).date() + timedelta(days=3)

    # Dedupe by symbol
    symbols = list({o.symbol for o in outcomes})[:30]   # cap to keep fetch fast

    for symbol in symbols:
        try:
            t = yf.Ticker(get_yf_symbol(symbol))
            cal = t.calendar
            if not cal:
                continue

            # yfinance returns a dict like {"Earnings Date": [datetime]}
            earnings_dates = cal.get("Earnings Date") or []
            if not isinstance(earnings_dates, list):
                earnings_dates = [earnings_dates]

            for ed in earnings_dates:
                if ed is None:
                    continue
                # Normalize to date
                if hasattr(ed, "date"):
                    ed_date = ed.date()
                else:
                    ed_date = ed
                if tomorrow <= ed_date <= in_3_days:
                    watchlist.append({
                        "symbol": symbol,
                        "name":   NSE_STOCKS.get(symbol, {}).get("name", symbol),
                        "date":   ed_date.strftime("%d %b %Y (%a)"),
                    })
                    break
        except Exception as e:
            logger.debug("Calendar fetch failed for %s: %s", symbol, e)
            continue

    return watchlist[:MAX_WATCHLIST]


# ---------------------------------------------------------------------------
# Trading-day stats — overall scorecard
# ---------------------------------------------------------------------------
def _compute_scorecard(outcomes: List[AlertOutcome]) -> dict:
    """Aggregate stats: hit rate, total alerts, breakdown by source."""
    total_directional = [o for o in outcomes if o.direction_expected in ("up", "down")]
    paid = [o for o in total_directional if o.paid_off]

    by_source = {}
    for o in total_directional:
        s = by_source.setdefault(o.source, {"total": 0, "paid": 0})
        s["total"] += 1
        if o.paid_off:
            s["paid"] += 1

    return {
        "total_alerts":      len(outcomes),
        "directional":       len(total_directional),
        "paid_off":          len(paid),
        "hit_rate":          (len(paid) / len(total_directional) * 100) if total_directional else 0,
        "by_source":         by_source,
    }


# ---------------------------------------------------------------------------
# HTML / text rendering
# ---------------------------------------------------------------------------
def _e(text: str) -> str:
    return html.escape(text or "")


def _outcome_row_html(o: AlertOutcome, color: str) -> str:
    sign  = "+" if o.pct_move_since_alert >= 0 else ""
    badge = "✅" if o.paid_off else ("❌" if o.direction_expected != "neutral" else "•")
    return f"""
    <tr style="border-bottom:1px solid #f3f4f6;">
      <td style="padding:8px;font-weight:600;font-family:ui-monospace,monospace;">{_e(o.symbol)}</td>
      <td style="padding:8px;color:#6b7280;font-size:13px;">{_e(o.name)}</td>
      <td style="padding:8px;font-size:12px;color:#374151;">{_e(o.signal)}</td>
      <td style="padding:8px;text-align:right;font-weight:500;">₹{o.alerted_price:,.2f}</td>
      <td style="padding:8px;text-align:right;font-weight:600;">₹{o.current_price:,.2f}</td>
      <td style="padding:8px;text-align:right;color:{color};font-weight:700;">
        {sign}{o.pct_move_since_alert:.2f}%
      </td>
      <td style="padding:8px;text-align:center;font-size:16px;">{badge}</td>
    </tr>
    """


def _outcomes_table_html(outcomes: List[AlertOutcome], title: str, color: str) -> str:
    if not outcomes:
        return f'<p style="color:#6b7280;font-style:italic;margin:10px 0;">No {title.lower()} today.</p>'

    header = f"""
    <h3 style="margin:18px 0 8px 0;color:{color};">{title} ({len(outcomes)})</h3>
    <table style="width:100%;border-collapse:collapse;font-size:14px;
                  font-family:-apple-system,Segoe UI,Roboto,sans-serif;">
      <thead>
        <tr style="background:#f3f4f6;color:#374151;text-align:left;">
          <th style="padding:8px;">Symbol</th>
          <th style="padding:8px;">Name</th>
          <th style="padding:8px;">Signal</th>
          <th style="padding:8px;text-align:right;">Alert Price</th>
          <th style="padding:8px;text-align:right;">Close</th>
          <th style="padding:8px;text-align:right;">Move</th>
          <th style="padding:8px;text-align:center;">Hit</th>
        </tr>
      </thead>
      <tbody>
    """
    return header + "".join(_outcome_row_html(o, color) for o in outcomes) + "</tbody></table>"


def _build_html_email(
    outcomes:   List[AlertOutcome],
    winners:    List[AlertOutcome],
    losers:     List[AlertOutcome],
    watchlist:  List[dict],
    market:     MarketContext,
    scorecard:  dict,
) -> str:
    now = datetime.now(IST).strftime("%d %b %Y · %A")

    # Market header tile
    def idx_tile(label, pct, close):
        if pct is None:
            return f'<div style="opacity:.7;">{_e(label)}: <em>n/a</em></div>'
        color = "#16a34a" if pct >= 0 else "#dc2626"
        sign  = "+" if pct >= 0 else ""
        return f"""
          <div>
            <strong>{_e(label)}</strong>:
            {close:,.0f}
            <span style="color:{color};font-weight:600;">({sign}{pct:.2f}%)</span>
          </div>
        """

    market_html = f"""
      <div style="background:#1f2937;color:#fff;padding:14px 16px;border-radius:8px;
                  margin-bottom:14px;font-size:13px;">
        <div style="font-size:17px;font-weight:600;margin-bottom:6px;">📔 Trading Journal — {_e(now)}</div>
        <div style="display:flex;flex-wrap:wrap;gap:18px;opacity:.92;">
          {idx_tile("Nifty 50",   market.nifty_pct,      market.nifty_close      or 0)}
          {idx_tile("Bank Nifty", market.bank_nifty_pct, market.bank_nifty_close or 0)}
          <div>
            <strong>Breadth (sample)</strong>:
            <span style="color:#86efac;">↑{market.advancing}</span> ·
            <span style="color:#fca5a5;">↓{market.declining}</span> ·
            <span style="opacity:.8;">↔{market.unchanged}</span>
          </div>
        </div>
      </div>
    """

    # Scorecard
    sc = scorecard
    hit_color = "#16a34a" if sc["hit_rate"] >= 50 else ("#dc2626" if sc["hit_rate"] < 30 else "#ca8a04")
    by_source_html = ""
    for src, s in sc["by_source"].items():
        rate = (s["paid"] / s["total"] * 100) if s["total"] else 0
        by_source_html += (
            f'<span style="background:#f3f4f6;padding:3px 8px;border-radius:99px;margin-right:6px;font-size:12px;">'
            f'{_e(src)}: {s["paid"]}/{s["total"]} ({rate:.0f}%)</span>'
        )

    scorecard_html = f"""
      <div style="background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:14px;margin-bottom:14px;">
        <div style="font-size:15px;font-weight:600;margin-bottom:8px;">📊 Today's Scorecard</div>
        <div style="display:flex;gap:24px;flex-wrap:wrap;font-size:14px;margin-bottom:8px;">
          <div><strong>Total alerts:</strong> {sc["total_alerts"]}</div>
          <div><strong>Directional:</strong> {sc["directional"]}</div>
          <div><strong>Paid off:</strong> {sc["paid_off"]}</div>
          <div><strong>Hit rate:</strong>
            <span style="color:{hit_color};font-weight:700;">{sc["hit_rate"]:.0f}%</span>
          </div>
        </div>
        {f'<div style="margin-top:6px;">{by_source_html}</div>' if by_source_html else ''}
      </div>
    """

    # Winners + Losers
    movers_html = f"""
      <div style="background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:14px;margin-bottom:14px;">
        {_outcomes_table_html(winners, "🚀 Today's Winners (from alerts)", "#16a34a")}
        {_outcomes_table_html(losers,  "📉 Today's Losers (from alerts)",  "#dc2626")}
      </div>
    """

    # Tomorrow's watchlist
    if watchlist:
        watchlist_rows = "".join(
            f'<tr style="border-bottom:1px solid #f3f4f6;">'
            f'<td style="padding:8px;font-weight:600;font-family:ui-monospace,monospace;">{_e(w["symbol"])}</td>'
            f'<td style="padding:8px;color:#6b7280;">{_e(w["name"])}</td>'
            f'<td style="padding:8px;color:#374151;">{_e(w["date"])}</td>'
            f'<td style="padding:8px;">'
            f'<a href="https://in.tradingview.com/chart/?symbol=NSE:{w["symbol"]}" '
            f'style="color:#1d4ed8;text-decoration:none;font-size:13px;">📈 TV</a>'
            f'</td></tr>'
            for w in watchlist
        )
        watchlist_html = f"""
          <div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:14px;margin-bottom:14px;">
            <div style="font-size:15px;font-weight:600;margin-bottom:8px;color:#9a3412;">
              📅 Earnings/Events Next 3 Days (from today's alerts)
            </div>
            <table style="width:100%;border-collapse:collapse;font-size:14px;">
              <thead>
                <tr style="text-align:left;color:#6b7280;font-size:12px;">
                  <th style="padding:6px 8px;">Symbol</th>
                  <th style="padding:6px 8px;">Name</th>
                  <th style="padding:6px 8px;">Date</th>
                  <th style="padding:6px 8px;"></th>
                </tr>
              </thead>
              <tbody>{watchlist_rows}</tbody>
            </table>
          </div>
        """
    else:
        watchlist_html = ""

    # Reflection prompts — 3 randomly selected from a pool
    import random
    prompt_pool = [
        "Which alert today taught you something new about a stock you didn't expect?",
        "Did you act on any alert today? If so — what was the outcome vs your thesis?",
        "Look at the losers list: is there a common signal type that's underperforming this week?",
        "Are there sectors disproportionately represented in today's winners or losers?",
        "If you held positions today, did you cut losers fast or let winners run?",
        "Which earnings event in the next 3 days is on your watchlist? What's your bias?",
        "Looking at the broader market move today, were your individual alerts genuine outperformers or just riding the tide?",
        "Were the highest-conviction alerts (e.g., 5/5 reversal signals) actually the best performers? Or the lower-conviction ones?",
        "What's one stock you wish you'd looked at more carefully this week?",
        "Did you over-trade today? How many positions did you actually need to take?",
    ]
    prompts = random.sample(prompt_pool, k=min(3, len(prompt_pool)))
    prompts_html = "".join(f'<li style="margin:6px 0;">{_e(p)}</li>' for p in prompts)

    reflection_html = f"""
      <div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:14px;margin-bottom:14px;">
        <div style="font-size:15px;font-weight:600;margin-bottom:8px;color:#1e40af;">
          🪞 Reflection Prompts
        </div>
        <ul style="margin:0;padding-left:20px;font-size:14px;color:#1e3a8a;line-height:1.6;">
          {prompts_html}
        </ul>
      </div>
    """

    return f"""
    <html><body style="background:#f9fafb;padding:16px;margin:0;
                       font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#111827;">
      <div style="max-width:760px;margin:0 auto;">
        {market_html}
        {scorecard_html}
        {movers_html}
        {watchlist_html}
        {reflection_html}
        <div style="text-align:center;font-size:12px;color:#9ca3af;margin-top:12px;">
          Automated end-of-day journal · Hit = stock moved ≥0.5% in signal direction since alert<br>
          Personal review tool · Not investment advice
        </div>
      </div>
    </body></html>
    """


def _build_text_email(
    outcomes:   List[AlertOutcome],
    winners:    List[AlertOutcome],
    losers:     List[AlertOutcome],
    watchlist:  List[dict],
    market:     MarketContext,
    scorecard:  dict,
) -> str:
    """Plain-text fallback."""
    now = datetime.now(IST).strftime("%d %b %Y · %A")
    lines = [f"TRADING JOURNAL — {now}", "=" * 60, ""]

    # Market
    lines.append("MARKET CONTEXT")
    if market.nifty_pct is not None:
        lines.append(f"  Nifty 50:   {market.nifty_close:,.0f}  ({market.nifty_pct:+.2f}%)")
    if market.bank_nifty_pct is not None:
        lines.append(f"  Bank Nifty: {market.bank_nifty_close:,.0f}  ({market.bank_nifty_pct:+.2f}%)")
    lines.append(f"  Breadth (sampled 50): up {market.advancing}, down {market.declining}, flat {market.unchanged}")
    lines.append("")

    # Scorecard
    sc = scorecard
    lines.append("SCORECARD")
    lines.append(f"  Total alerts: {sc['total_alerts']}  Directional: {sc['directional']}  "
                 f"Paid off: {sc['paid_off']}  Hit rate: {sc['hit_rate']:.0f}%")
    for src, s in sc["by_source"].items():
        rate = (s["paid"] / s["total"] * 100) if s["total"] else 0
        lines.append(f"    {src}: {s['paid']}/{s['total']} ({rate:.0f}%)")
    lines.append("")

    # Winners
    lines.append(f"WINNERS ({len(winners)})")
    for o in winners:
        lines.append(f"  {o.symbol:12s}  {o.pct_move_since_alert:+6.2f}%  ₹{o.alerted_price:>8.2f} → ₹{o.current_price:>8.2f}  ({o.signal})")
    lines.append("")

    # Losers
    lines.append(f"LOSERS ({len(losers)})")
    for o in losers:
        lines.append(f"  {o.symbol:12s}  {o.pct_move_since_alert:+6.2f}%  ₹{o.alerted_price:>8.2f} → ₹{o.current_price:>8.2f}  ({o.signal})")
    lines.append("")

    # Watchlist
    if watchlist:
        lines.append("EARNINGS / EVENTS NEXT 3 DAYS")
        for w in watchlist:
            lines.append(f"  {w['symbol']:12s}  {w['date']}  {w['name']}")
        lines.append("")

    lines.append("=" * 60)
    lines.append("Personal review tool · Not investment advice")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Email send
# ---------------------------------------------------------------------------
def _send_journal_email(html_body: str, text_body: str, subject_suffix: str) -> bool:
    if not all([GMAIL_SENDER, GMAIL_APP_PASSWORD, GMAIL_RECIPIENT]):
        logger.error("Gmail credentials missing")
        return False

    msg            = MIMEMultipart("alternative")
    msg["Subject"] = f"{JOURNAL_SUBJECT_PREFIX} {subject_suffix}"
    msg["From"]    = GMAIL_SENDER
    msg["To"]      = GMAIL_RECIPIENT
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html",  "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        logger.error("Journal email send failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def run_journal_report() -> dict:
    """Build today's journal: scorecard, winners/losers, watchlist, market context."""
    logger.info("=" * 60)
    logger.info("Starting end-of-day journal at %s IST",
                datetime.now(IST).strftime("%H:%M:%S"))
    logger.info("=" * 60)

    alerts = _fetch_todays_alerts()
    if not alerts:
        logger.info("No alerts today — sending lightweight 'quiet day' summary")
        market = _fetch_market_context()
        # Even on quiet days, send market context so you have continuity
        outcomes = []
        winners = []
        losers = []
        watchlist = []
        scorecard = {"total_alerts": 0, "directional": 0, "paid_off": 0,
                     "hit_rate": 0, "by_source": {}}
    else:
        logger.info("Scoring %d alerts", len(alerts))
        outcomes = _score_alerts(alerts)
        winners, losers = _split_winners_losers(outcomes)
        market = _fetch_market_context()
        watchlist = _fetch_tomorrows_watchlist(outcomes)
        scorecard = _compute_scorecard(outcomes)

    html_body = _build_html_email(outcomes, winners, losers, watchlist, market, scorecard)
    text_body = _build_text_email(outcomes, winners, losers, watchlist, market, scorecard)

    # Compact subject for at-a-glance email list
    suffix_parts = []
    if scorecard["directional"] > 0:
        suffix_parts.append(f"{scorecard['hit_rate']:.0f}% hit rate")
        suffix_parts.append(f"{scorecard['paid_off']}/{scorecard['directional']}")
    if winners:
        top = winners[0]
        suffix_parts.append(f"🚀{top.symbol} {top.pct_move_since_alert:+.1f}%")
    suffix = " · ".join(suffix_parts) or "quiet day"

    sent = _send_journal_email(html_body, text_body, suffix)
    if sent:
        logger.info("📤 Journal sent")
    else:
        logger.error("✗ Journal email failed")

    # Save snapshot for the dashboard
    save_snapshot("journal", [
        {
            "symbol":            o.symbol,
            "name":              o.name,
            "source":            o.source,
            "signal":            o.signal,
            "alerted_price":     o.alerted_price,
            "current_price":     o.current_price,
            "pct_move":          round(o.pct_move_since_alert, 2),
            "expected":          o.direction_expected,
            "actual":            o.direction_actual,
            "paid_off":          o.paid_off,
        } for o in outcomes
    ], {
        "report_time":     datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
        "scorecard":       scorecard,
        "market":          {
            "nifty_pct":      market.nifty_pct,
            "bank_nifty_pct": market.bank_nifty_pct,
            "advancing":      market.advancing,
            "declining":      market.declining,
        },
        "winners_count":   len(winners),
        "losers_count":    len(losers),
        "watchlist_count": len(watchlist),
    })

    return {
        "total_alerts":  len(outcomes),
        "winners":       len(winners),
        "losers":        len(losers),
        "watchlist":     len(watchlist),
        "hit_rate":      scorecard["hit_rate"],
        "email_sent":    sent,
    }
