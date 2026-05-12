"""
Configuration for the NSE Stock News Alert System (Render edition).
All thresholds and runtime parameters live here so you can tune
behaviour without touching the rest of the codebase.
"""

import os
from datetime import time as dtime
from zoneinfo import ZoneInfo


# ---------------------------------------------------------------------------
# SECRETS — loaded from Render environment variables
# ---------------------------------------------------------------------------
GMAIL_SENDER       = os.getenv("GMAIL_SENDER", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
GMAIL_RECIPIENT    = os.getenv("GMAIL_RECIPIENT", "")

# Postgres connection string — auto-injected when DB is linked in render.yaml
DATABASE_URL = os.getenv("DATABASE_URL", "")

# ---------------------------------------------------------------------------
# MARKET HOURS (Indian Standard Time)
# ---------------------------------------------------------------------------
IST = ZoneInfo("Asia/Kolkata")
SCAN_START_TIME = dtime(9, 0)
SCAN_END_TIME   = dtime(15, 45)

# Skip market-hours check entirely if set to "true".
FORCE_RUN = os.getenv("FORCE_RUN", "false").lower() == "true"

# RUN_MODE controls what the entry script does:
#   "test"        → send a test email and exit
#   "momentum"    → run 30-day gainers/losers report (always, no market check)
#   (empty)       → default: run the 30-min intraday scanner
RUN_MODE = os.getenv("RUN_MODE", "").lower()

# ---------------------------------------------------------------------------
# GAP DETECTION THRESHOLDS (intraday scanner)
# ---------------------------------------------------------------------------
GAP_UP_THRESHOLD   = 2.0
GAP_DOWN_THRESHOLD = -2.0

INTRADAY_MOVE_THRESHOLD = 3.0
VOLUME_SPIKE_MULTIPLIER = 2.0

# ---------------------------------------------------------------------------
# 52-WEEK HIGH/LOW DETECTION (intraday scanner)
# ---------------------------------------------------------------------------
NEAR_52W_HIGH_PCT      = 2.0
NEAR_52W_LOW_PCT       = 2.0
MIN_DAILY_MOVE_FOR_52W = 1.0

# ---------------------------------------------------------------------------
# NEWS FILTER
# ---------------------------------------------------------------------------
NEWS_LOOKBACK_HOURS = 24

HIGH_PRIORITY_KEYWORDS = [
    "buyback", "bonus", "split", "dividend", "rights issue",
    "results", "profit", "loss", "revenue", "earnings", "guidance",
    "beats estimates", "misses estimates",
    "acquisition", "acquires", "merger", "demerger", "stake", "deal",
    "partnership", "joint venture", "JV",
    "SEBI", "RBI", "investigation", "raid", "fraud", "ban", "penalty",
    "approval", "license", "tender", "order win", "contract",
    "upgrade", "downgrade", "target price", "rating", "broker",
    "block deal", "bulk deal", "promoter",
    "production", "expansion", "capex", "shutdown", "strike",
    "CEO", "MD", "resigns", "appointed", "appoints",
    "52-week high", "52 week high", "52-week low", "52 week low",
    "all-time high", "record high", "lifetime high",
]

# ---------------------------------------------------------------------------
# 30-DAY MOMENTUM REPORT
# ---------------------------------------------------------------------------
# Window of trading days to look back. 21 trading days ≈ 30 calendar days.
MOMENTUM_LOOKBACK_DAYS = 30

# Stock qualifies as a "gainer" if it's up at least this much vs N days ago
MOMENTUM_GAIN_THRESHOLD = 10.0   # +10%

# Stock qualifies as a "loser" if it's down at least this much
MOMENTUM_LOSS_THRESHOLD = -7.0   # -7%

# Hard cap on rows per section (gainers/losers) in the email — keeps it readable
MOMENTUM_MAX_PER_SECTION = 30

# ---------------------------------------------------------------------------
# SCANNING BEHAVIOUR (intraday scanner)
# ---------------------
