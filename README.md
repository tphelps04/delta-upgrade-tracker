# Delta Upgrade Tracker

Watches the cash upgrade price for both legs of a round trip on delta.com
and pushes a notification when either drops below your threshold:

- **Outbound**: DL0979, LAX→NYC, 8/16/2026 — Delta One < $800, Premium Select < $500
- **Return**: DL0752, 8/21/2026 — Delta One < $800, Premium Select < $500

## How it works

Uses delta.com's public "Find Your Trip" lookup (confirmation number + name
— no account password involved) to open your reservation, then opens the
seat/upgrade screen for each flight leg in turn and reads the prices shown.
Every check is logged to `logs/price_history.jsonl` (tagged by leg) so you
can see price movement over time. Alerts go out via
[ntfy.sh](https://ntfy.sh) push notifications — no email/password involved.

Runs on a schedule via macOS `launchd` and automatically stops itself
(unloads its own scheduled job) after `STOP_AT` in `.env`.

## Setup

1. **Configure.** `.env` already has your trip details, thresholds, and
   ntfy topic filled in. Edit it directly if anything changes.

2. **Verify with a visible test run** any time you change the scraping
   logic (set `HEADLESS=false` in `.env` first):

   ```bash
   cd ~/delta-upgrade-tracker
   .venv/bin/python3 delta_tracker.py
   ```

   If it can't find a form field or can't tell the two flight legs apart,
   it saves a screenshot + HTML snapshot to `logs/debug/` — that's the
   place to look (or send to me) to fix selectors.

3. Set `HEADLESS=true` before relying on the scheduled run (no visible
   browser window, required for unattended background runs).

## Scheduling (launchd)

```bash
cp com.tiffany.delta-upgrade-tracker.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.tiffany.delta-upgrade-tracker.plist
```

Check status:

```bash
launchctl list | grep delta-upgrade-tracker
```

Stop it:

```bash
launchctl unload ~/Library/LaunchAgents/com.tiffany.delta-upgrade-tracker.plist
```

Logs from each scheduled run land in `logs/launchd.out.log` and
`logs/launchd.err.log`. Current interval is set in the `.plist` file
(`StartInterval`, in seconds).

**Your Mac needs to be powered on and you logged in** for scheduled checks
to happen — launchd is per-user and doesn't run while the machine is off.
If it's asleep with the lid closed, checks may be missed (not reliably
caught up).

## Important limitations

- **Bot detection**: delta.com actively blocks automated browsers (Akamai).
  This script will not try to solve CAPTCHAs or evade detection — it stops,
  alerts you to check manually, and waits for the next scheduled run. It
  already got a hard "Access Denied" once during testing after repeated
  rapid checks — that's why the interval was backed off from 30 min to 4
  hours. There's no guarantee it stays unblocked; if it keeps failing,
  the fallback is checking delta.com yourself.
- **Two-leg selection is unverified live.** `select_flight_segment()` in
  `delta_tracker.py` tries to match each flight's segment on the itinerary
  page by flight number, but this hasn't been confirmed against a real
  two-flight screen yet (built while backing off from the block above).
  The first real test is the next scheduled run — check
  `logs/price_history.jsonl` afterward to confirm both `outbound` and
  `return` entries show sane prices, not the same price for both.
- **Alert de-duplication**: once a threshold is crossed for a given leg,
  you won't get repeat notifications every check — it only re-alerts if
  the price goes back above threshold and then drops below it again.
