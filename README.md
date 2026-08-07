# Delta Upgrade Tracker

Tracks the cash upgrade price for both legs of a round trip and pushes a
notification when either drops below your threshold:

- **Outbound**: DL0979, LAX→NYC, 8/16/2026 — Delta One < $800, Premium Select < $500
- **Return**: DL0752, 8/21/2026 — Delta One < $800, Premium Select < $500

## How to check prices (use this — see note below)

```bash
cd ~/delta-upgrade-tracker
source .venv/bin/activate
python3 manual_check.py
```

Open delta.com yourself, look up the trip, and type in the upgrade prices
it shows when prompted. The script logs the prices and texts you via
[ntfy.sh](https://ntfy.sh) if either leg is under your threshold. Nothing
here ever touches delta.com automatically — it's just you browsing
normally, so there's no bot-detection risk.

## Why manual, not automated

`delta_tracker.py` (using Playwright to scrape delta.com automatically)
is still in this repo, but **don't rely on it** — delta.com's bot
protection (Akamai) hard-blocks it, both from cloud hosting (GitHub
Actions, Anthropic's cloud routines — instant block, since those are
well-known datacenter IP ranges) and eventually from a home IP too, after
repeated automated requests during testing triggered an "Access Denied"
block that also affected this project's home connection for a while.
Regular manual browsing was unaffected by that block — only the
automated traffic pattern got flagged.

This isn't a bug to fix — it's deliberate bot detection working as
intended, and getting around it would require fingerprint/IP evasion
techniques that are out of scope. `manual_check.py` sidesteps the whole
problem by never automating the delta.com visit.

## Setup (one-time)

```bash
cp .env.example .env
# edit .env if trip details/thresholds change
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

(`playwright install chromium` only needed if you ever want to try
`delta_tracker.py` again — not needed for `manual_check.py`.)

## Alerts

Push notifications via ntfy.sh — install the app and subscribe to the
topic in `.env` (`NTFY_TOPIC`), or open `https://ntfy.sh/<topic>` in a
browser tab and leave it open.

Alerts are de-duplicated per leg per cabin: once triggered, you won't get
repeat notifications until the price goes back above threshold and then
drops below it again (tracked in `state.json`).
