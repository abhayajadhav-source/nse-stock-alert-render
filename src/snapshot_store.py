"""
Snapshot persistence — stores latest results from each scanner run.

Why "snapshots"? We always keep only the latest run per report type.
The dashboard shows current state, not historical. If you want history
later, replace these UPSERTs with INSERTs and add a 'snapshot_at' index.

Tables (auto-created on first use):
  - intraday_snapshot   : latest intraday alerts
  - momentum_snapshot   : latest 30-day gainers/losers
  - reversal_snapshot   : latest trend-reversal candidates
  - meta                : freshness timestamps per report type
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List

import psycopg2

from config import DATABASE_URL

logger = logging.getLogger(__name__)


@contextmanager
def _conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
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
    """Idempotent — safe to call on every run."""
    with _conn() as c:
        with c.cursor() as cur:
            # One unified table per report. payload is JSON for flexibility —
            # we can change the fields in Python without migrating SQL.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    report_type   TEXT        PRIMARY KEY,
                    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    payload       JSONB       NOT NULL
                );
            """)


def save_snapshot(report_type: str, items: List[Dict[str, Any]],
                  extra: Dict[str, Any] = None) -> None:
    """
    Replace the latest snapshot for a given report type.

    Args:
      report_type: 'intraday' | 'momentum' | 'reversal'
      items:       list of dicts (one per stock entry)
      extra:       optional dict for headers/totals/etc. (merged into payload)
    """
    _ensure_schema()
    payload = {
        "items":         items,
        "total_items":   len(items),
        "generated_at":  datetime.utcnow().isoformat() + "Z",
    }
    if extra:
        payload.update(extra)

    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("""
                INSERT INTO snapshots (report_type, updated_at, payload)
                VALUES (%s, NOW(), %s)
                ON CONFLICT (report_type)
                DO UPDATE SET updated_at = NOW(), payload = EXCLUDED.payload;
            """, (report_type, json.dumps(payload)))
    logger.info("Saved %s snapshot with %d items", report_type, len(items))


def load_snapshot(report_type: str) -> Dict[str, Any]:
    """Read the latest snapshot. Returns {} if missing."""
    _ensure_schema()
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT updated_at, payload FROM snapshots WHERE report_type = %s",
                (report_type,),
            )
            row = cur.fetchone()
            if not row:
                return {}
            updated_at, payload = row
            return {
                "report_type": report_type,
                "updated_at":  updated_at.isoformat(),
                **payload,
            }


def load_all_snapshots() -> Dict[str, Any]:
    """Read all snapshots — one big response for the dashboard."""
    _ensure_schema()
    out = {}
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT report_type, updated_at, payload FROM snapshots")
            for report_type, updated_at, payload in cur.fetchall():
                out[report_type] = {
                    "updated_at": updated_at.isoformat(),
                    **payload,
                }
    return out
