"""
Configuration for the NSE Stock News Alert System (Render edition).
All thresholds and runtime parameters live here.
"""

import os
from datetime import time as dtime
from zoneinfo import ZoneInfo


# ---------------------------------------------------------------------------
# SECRETS
# ---------------------------------------------------------------------------
GMAIL_SENDER       = os.getenv("GMAIL_SENDER", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
GMAIL_RECIPIENT    = os.getenv("GMAIL_RECIPIENT", "")
DATABASE_URL       = os.getenv("DATABASE_URL", "")

# ---------------------------------------------------------------------------
# MARKET HOURS (IST)
# ---------------------------------------------------------------------------
IST = ZoneInfo("Asia/Kolkata")
SCAN_START_TIME = dtime(9, 0)
SCAN_END_TIME   = dtime(15, 45)

FORCE_RUN = os.getenv("FORCE_RUN", "false").lower() == "true"

# RUN_MODE controls what the entry script does:
#   "test"      → send test email and exit
#   "momentum"  → 30-day gainers/losers report
#   "reversal"  → trend reversal report (NEW)
#   (empty)     → default: intraday scanner
RUN_MODE = os.getenv("RUN_MODE", "").lower()

# ---------------------------------------------------------------------------
# GAP DETECTION (intraday scanner)
# ---------------------------------------------------------------------------
GAP_UP_THRESHOLD   = 2.0
GAP_DOWN_THRESHOLD = -2.0

INTRADAY_MOVE_THRESHOLD = 3.0
VOLUME_SPIKE_MULTIPLIER = 2.0

# ---------------------------------------------------------------------------
# 52-WEEK HIGH/LOW (intraday scanner)
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
MOMENTUM_LOOKBACK_DAYS    = 30
MOMENTUM_GAIN_THRESHOLD   = 10.0
MOMENTUM_LOSS_THRESHOLD   = -7.0
MOMENTUM_MAX_PER_SECTION  = 30

# ---------------------------------------------------------------------------
# TREND REVERSAL DETECTION (NEW)
# ---------------------------------------------------------------------------
# Moving average periods — classic short/long combination for crossovers.
SMA_SHORT = 20   # 20-day SMA
SMA_LONG  = 50   # 50-day SMA
# Look-back window (trading days) for detecting a fresh crossover.
# Smaller = only the freshest signals; larger = catches lagging crossovers too.
MA_CROSSOVER_LOOKBACK_DAYS = 5

# RSI — Relative Strength Index settings.
RSI_PERIOD          = 14
RSI_OVERSOLD        = 30    # below this = oversold zone
RSI_OVERBOUGHT      = 70    # above this = overbought zone
RSI_BULL_CONFIRM    = 40    # RSI must climb back above this to confirm bull reversal
RSI_BEAR_CONFIRM    = 60    # RSI must drop below this to confirm bear reversal
RSI_LOOKBACK_DAYS   = 10    # how recently the oversold/overbought touch happened

# MACD — Moving Average Convergence Divergence settings.
MACD_FAST   = 12
MACD_SLOW   = 26
MACD_SIGNAL = 9
MACD_CROSSOVER_LOOKBACK_DAYS = 5

# Volume confirmation — recent N-day vol vs longer-window vol.
VOLUME_CONFIRM_RECENT_DAYS = 5
VOLUME_CONFIRM_BASE_DAYS   = 20
VOLUME_CONFIRM_RATIO       = 1.5   # 1.5x base average

# Swing-low/high pattern — requires N higher lows or lower highs.
SWING_WINDOW_DAYS  = 30
SWING_PIVOT_RADIUS = 3   # a pivot is a local min/max over +/- 3 days

# Minimum signal count to be flagged. We have 5 signals; 3 = strong confirmation.
REVERSAL_MIN_SIGNALS_REQUIRED = 3

# Hard cap on rows per section in the email
REVERSAL_MAX_PER_SECTION = 25

# ---------------------------------------------------------------------------
# SCANNING BEHAVIOUR (intraday scanner)
# ---------------------------------------------------------------------------
ALERT_COOLDOWN_SECONDS = 7200
YF_RETRIES             = 2
MAX_ALERTS_PER_CYCLE   = 15

# ---------------------------------------------------------------------------
# EMAIL BEHAVIOUR
# ---------------------------------------------------------------------------
EMAIL_BATCH_MODE       = True
EMAIL_SUBJECT_PREFIX   = "[NSE Alert]"
MOMENTUM_SUBJECT_PREFIX = "[NSE Momentum]"
REVERSAL_SUBJECT_PREFIX = "[NSE Reversal]"
