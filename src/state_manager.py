"""
Alert-state persistence — Postgres edition for Render.

Render's free cron jobs can't use a persistent disk, so we store the
cooldown state in a tiny Postgres table. The table has one row per
(symbol, signal_type) pair with a 'last_alerted_at' timestamp.

Why Postgres rather than a file? Render's free 1 GB Postgres survives
across container restarts, doesn't expire, and is overkill for our
~100-row workload — but it's there for free and rock solid.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional

import psycopg2

from config import ALERT_COOLDOWN_SECONDS, DATABASE_URL

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------
@contextmanager
def _conn():
    """
    Open a Postgres connection for the duration of one operation.
    Since cron runs are short-lived (30-90 sec), we don't need a pool.
    """
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set — Postgres binding missing")
    connection = psycopg2.connect(DATABASE_URL)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _ensure_schema() -> None:
    """Create the alerts table on first run. Idempotent."""
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS alert_state (
                    symbol           TEXT        NOT NULL,
                    signal_type      TEXT        NOT NULL,
                    last_alerted_at  TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (symbol, signal_type)
                );
            """)
            # Index on time helps the cleanup query stay fast even at scale
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_alert_state_time
                ON alert_state (last_alerted_at);
            """)


# ---------------------------------------------------------------------------
# Public API — same signatures as the file-based version
# ---------------------------------------------------------------------------
def should_send_alert(symbol: str, signal_type: str) -> bool:
    """True if we should alert — False if same signal sent within cooldown."""
    _ensure_schema()
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT last_alerted_at FROM alert_state "
                "WHERE symbol = %s AND signal_type = %s",
                (symbol, signal_type),
            )
            row = cur.fetchone()
            if row is None:
                return True
            last: datetime = row[0]
            age = (datetime.now(last.tzinfo) - last).total_seconds()
            return age >= ALERT_COOLDOWN_SECONDS


def mark_alert_sent(symbol: str, signal_type: str) -> None:
    """Record (or refresh) the alert timestamp for (symbol, signal_type)."""
    _ensure_schema()
    with _conn() as c:
        with c.cursor() as cur:
            # ON CONFLICT updates the timestamp if the row already exists
            cur.execute("""
                INSERT INTO alert_state (symbol, signal_type, last_alerted_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (symbol, signal_type)
                DO UPDATE SET last_alerted_at = NOW();
            """, (symbol, signal_type))


def cleanup_old_entries(max_age_seconds: int = 86400) -> None:
    """Drop rows older than max_age_seconds. Defaults to 1 day."""
    _ensure_schema()
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "DELETE FROM alert_state "
                "WHERE last_alerted_at < NOW() - INTERVAL %s",
                (f"{max_age_seconds} seconds",),
            )
            if cur.rowcount > 0:
                logger.info("Cleaned %d stale state rows", cur.rowcount)
