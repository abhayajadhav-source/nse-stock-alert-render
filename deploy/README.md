# Oracle Cloud Always Free deploy notes (scanner repo)

Repo-side half of the Render -> Oracle migration for the cron scanners.
See `nse-dashboard-api/deploy/README.md` for the API service, Caddy, env
file, and deploy shim -- this repo only needs a venv and the crontab
below, since the scanners are invoked directly by cron rather than
running as a long-lived systemd service.

## What's actually in `render.yaml`

Four separate cron jobs share one Postgres database, not one hourly job:

| Job | `RUN_MODE` | Schedule (UTC) | IST | Entry point |
|---|---|---|---|---|
| Intraday scanner | *(default)* | `*/30 3-10 * * 1-5` | ~09:00-16:00, every 30 min | `src/scan_once.py` |
| Momentum report | `momentum` | `0 12 * * 1-5` | 17:30 | `src/scan_once.py` |
| Reversal report | `reversal` | `30 12 * * 1-5` | 18:00 | `src/scan_once.py` |
| Journal report | `journal` | `30 10 * * 1-5` | 16:00 | `src/scan_once.py` |

`deploy/nse-scanners.cron` reproduces all four as one crontab, appending
to (not replacing) whatever's already installed.

## Install (on the VM, after the venv exists per runbook Step 5)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

( crontab -l 2>/dev/null; cat deploy/nse-scanners.cron ) | crontab -
crontab -l   # sanity-check the merge didn't clobber deploy.sh's line
```

All four jobs read `/etc/nse/api.env` for `DATABASE_URL`, `GMAIL_SENDER`,
`GMAIL_APP_PASSWORD`, `GMAIL_RECIPIENT`, `TZ` -- same file the API
service uses (see the API repo's `deploy/api.env.example`).

## Journal restore

Before the first run, restore `trading_journal` from the dump taken off
Render (runbook Pre-flight + Step 4):

```bash
psql "$DATABASE_URL" -f journal_dump.sql
```

Everything else (`snapshots`, `analyst_cache`, `fundamentals_cache`)
regenerates from the first scanner tick -- don't bother restoring those.
