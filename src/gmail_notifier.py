"""Gmail SMTP notification sender."""

from __future__ import annotations

import html
import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Tuple

from config import (
    EMAIL_SUBJECT_PREFIX, GMAIL_APP_PASSWORD, GMAIL_RECIPIENT, GMAIL_SENDER, IST,
)
from news_fetcher import NewsItem
from stock_analyzer import StockData, get_movement_emoji, get_signal_tags
from stock_list import NSE_STOCKS

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def _e(text: str) -> str:
    return html.escape(text or "")


def _stock_card_html(stock: StockData, news_items: List[NewsItem]) -> str:
    emoji  = get_movement_emoji(stock)
    name   = NSE_STOCKS.get(stock.symbol, {}).get("name", stock.symbol)
    tags   = " · ".join(get_signal_tags(stock)) or "Tracking"
    change = stock.total_pct_change
    sign   = "+" if change >= 0 else ""
    color  = "#16a34a" if change >= 0 else "#dc2626"

    if stock.is_new_52w_high:
        border_color = "#16a34a"
    elif stock.is_new_52w_low:
        border_color = "#dc2626"
    else:
        border_color = "#e5e7eb"

    news_html = ""
    if news_items:
        items_html = []
        for item in news_items[:5]:
            badge = "🔥 " if item.is_high_priority else "• "
            headline = item.title.rsplit(" - ", 1)[0] if " - " in item.title else item.title
            headline = headline[:160] + ("…" if len(headline) > 160 else "")
            items_html.append(
                f'<li style="margin:4px 0;">'
                f'{badge}<a href="{_e(item.link)}" style="color:#1d4ed8;text-decoration:none;">'
                f'{_e(headline)}</a> '
                f'<span style="color:#6b7280;font-size:12px;">({_e(item.source)})</span>'
                f'</li>'
            )
        news_html = (
            f'<div style="margin-top:10px;">'
            f'<strong>📰 News ({len(news_items)})</strong>'
            f'<ul style="margin:6px 0 0 0;padding-left:18px;font-size:14px;">'
            + "".join(items_html) +
            f'</ul></div>'
        )

    return f"""
    <div style="border:2px solid {border_color};border-radius:8px;padding:14px;margin:10px 0;
                background:#ffffff;font-family:-apple-system,Segoe UI,Roboto,sans-serif;">
      <div style="display:flex;justify-content:space-between;align-items:baseline;">
        <div style="font-size:16px;font-weight:600;">
          {emoji} <span style="color:#111827;">{_e(stock.symbol)}</span>
          <span style="color:#6b7280;font-weight:400;">— {_e(name)}</span>
        </div>
        <div style="font-size:16px;color:{color};font-weight:600;">
          ₹{stock.current_price:,.2f} ({sign}{change:.2f}%)
        </div>
      </div>
      <div style="margin-top:4px;font-size:13px;color:#6b7280;">{_e(tags)}</div>
      <div style="margin-top:4px;font-size:12px;color:#9ca3af;">
        52W range: ₹{stock.low_52w:,.2f} — ₹{stock.high_52w:,.2f}
      </div>
      {news_html}
      <div style="margin-top:10px;font-size:13px;">
        📈
        <a href="https://www.nseindia.com/get-quotes/equity?symbol={stock.symbol}"
           style="color:#1d4ed8;text-decoration:none;margin-right:8px;">NSE</a> ·
        <a href="https://in.tradingview.com/chart/?symbol=NSE:{stock.symbol}"
           style="color:#1d4ed8;text-decoration:none;margin-left:8px;">TradingView</a>
      </div>
    </div>
    """


def _stock_card_text(stock: StockData, news_items: List[NewsItem]) -> str:
    name   = NSE_STOCKS.get(stock.symbol, {}).get("name", stock.symbol)
    tags   = " | ".join(get_signal_tags(stock)) or "Tracking"
    change = stock.total_pct_change
    sign   = "+" if change >= 0 else ""

    lines = [
        f"{stock.symbol} — {name}",
        f"₹{stock.current_price:,.2f}  ({sign}{change:.2f}%)",
        f"  {tags}",
        f"  52W range: ₹{stock.low_52w:,.2f} — ₹{stock.high_52w:,.2f}",
    ]
    if news_items:
        lines.append("  News:")
        for item in news_items[:5]:
            flag = "[!] " if item.is_high_priority else "    "
            headline = item.title.rsplit(" - ", 1)[0] if " - " in item.title else item.title
            lines.append(f"  {flag}{headline[:120]}")
            lines.append(f"        {item.link}")
    lines.append(f"  NSE: https://www.nseindia.com/get-quotes/equity?symbol={stock.symbol}")
    return "\n".join(lines)


def _send_email(subject: str, html_body: str, text_body: str) -> bool:
    if not all([GMAIL_SENDER, GMAIL_APP_PASSWORD, GMAIL_RECIPIENT]):
        logger.error("Gmail credentials missing")
        return False

    msg            = MIMEMultipart("alternative")
    msg["Subject"] = subject
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
        logger.error("Gmail auth failed — check App Password: %s", e)
        return False
    except Exception as e:
        logger.error("Gmail send failed: %s", e)
        return False


def send_batch_alert(alerts: List[Tuple[StockData, List[NewsItem]]]) -> bool:
    if not alerts:
        return True

    now   = datetime.now(IST).strftime("%H:%M IST · %d %b %Y")
    count = len(alerts)

    gap_ups       = sum(1 for s, _ in alerts if s.is_gap_up)
    gap_downs     = sum(1 for s, _ in alerts if s.is_gap_down)
    new_52w_highs = sum(1 for s, _ in alerts if s.is_new_52w_high)
    new_52w_lows  = sum(1 for s, _ in alerts if s.is_new_52w_low)

    summary_lines = [f"{count} stock(s) flagged"]
    if new_52w_highs: summary_lines.append(f"{new_52w_highs} new 52W high")
    if new_52w_lows:  summary_lines.append(f"{new_52w_lows} new 52W low")
    if gap_ups:       summary_lines.append(f"{gap_ups} gap up")
    if gap_downs:     summary_lines.append(f"{gap_downs} gap down")
    summary = " · ".join(summary_lines)

    cards_html = "".join(_stock_card_html(s, n) for s, n in alerts)
    html_body  = f"""
    <html><body style="background:#f9fafb;padding:16px;margin:0;
                       font-family:-apple-system,Segoe UI,Roboto,sans-serif;">
      <div style="max-width:680px;margin:0 auto;">
        <div style="background:#1f2937;color:#fff;padding:14px 16px;border-radius:8px;
                    margin-bottom:12px;">
          <div style="font-size:18px;font-weight:600;">📊 NSE Stock Alerts</div>
          <div style="font-size:13px;opacity:.8;margin-top:2px;">{_e(now)} · {_e(summary)}</div>
        </div>
        {cards_html}
        <div style="text-align:center;font-size:12px;color:#9ca3af;margin-top:16px;">
          Automated alert · For information only · Not investment advice
        </div>
      </div>
    </body></html>
    """

    text_body = (
        f"NSE Stock Alerts — {now}\n"
        f"{summary}\n"
        + "=" * 60 + "\n\n"
        + "\n\n".join(_stock_card_text(s, n) for s, n in alerts)
        + "\n\n" + "=" * 60
        + "\nAutomated alert · Not investment advice\n"
    )

    subject = f"{EMAIL_SUBJECT_PREFIX} {summary}"
    return _send_email(subject, html_body, text_body)


def send_test_email() -> bool:
    html_body = """
    <html><body style="font-family:sans-serif;padding:20px;">
      <h2>✅ NSE Stock Alert System — Test</h2>
      <p>Render cron + Postgres + Gmail wired up correctly.
         You'll receive alerts here during NSE market hours.</p>
    </body></html>
    """
    text_body = "NSE Alert System Test — Render cron working."
    return _send_email(f"{EMAIL_SUBJECT_PREFIX} Test message", html_body, text_body)
