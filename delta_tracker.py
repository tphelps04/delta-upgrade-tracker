#!/usr/bin/env python3
"""
Delta upgrade price tracker.

Looks up a round-trip on delta.com by confirmation number + name (the
public "Find Your Trip" flow — no account login/password involved), reads
the cash upgrade price shown for Delta One and Premium Select on each
flight leg, and pushes a notification (via ntfy.sh — no password needed)
when either drops below your threshold for that leg.

IMPORTANT — bot detection: delta.com actively blocks automated traffic.
This script does NOT attempt to solve CAPTCHAs or bypass any "verify
you're human" challenge. If it detects one, it stops immediately, saves
a screenshot + HTML snapshot to logs/, and sends you a fallback alert
telling you to check manually. Expect it to eventually get blocked —
that's a hard limit of the site, not a bug to route around.

Selectors are written defensively (role/label/text based) since I could
not always live-inspect delta.com's current DOM while building this.
`select_flight_segment()` in particular — which picks the right leg out
of a round-trip itinerary — is unverified against a real two-flight
screen. If it can't tell the legs apart, run with HEADLESS=false, watch
where it gets stuck, and adjust the selectors accordingly.
"""

import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BLOCK_INDICATORS = [
    "pardon our interruption",
    "verify you are a human",
    "verify you're human",
    "access denied",
    "px-captcha",
    "are you a robot",
    "unusual traffic",
]

SEGMENT_ACTION_SELECTOR = (
    'a:has-text("Upgrade"), button:has-text("Upgrade"), '
    'a:has-text("Seats"), button:has-text("Seats"), '
    'a:has-text("Change Seats")'
)


@dataclass
class Leg:
    label: str
    flight_number: str
    flight_date: str
    threshold_delta_one: float
    threshold_premium: float

    @property
    def state_key(self) -> str:
        return f"leg_{self.flight_number}"


@dataclass
class Config:
    confirmation: str
    first_name: str
    last_name: str
    legs: List[Leg]
    ntfy_server: str
    ntfy_topic: str
    headless: bool
    state_file: Path
    log_file: Path
    stop_at: datetime
    launchd_plist: Path

    @classmethod
    def from_env(cls) -> "Config":
        def req(name: str) -> str:
            val = os.environ.get(name)
            if not val:
                raise SystemExit(f"Missing required env var: {name} (check your .env file)")
            return val

        legs = [
            Leg(
                label="outbound",
                flight_number=req("OUTBOUND_FLIGHT_NUMBER"),
                flight_date=req("OUTBOUND_FLIGHT_DATE"),
                threshold_delta_one=float(req("OUTBOUND_THRESHOLD_DELTA_ONE")),
                threshold_premium=float(req("OUTBOUND_THRESHOLD_PREMIUM")),
            ),
            Leg(
                label="return",
                flight_number=req("RETURN_FLIGHT_NUMBER"),
                flight_date=req("RETURN_FLIGHT_DATE"),
                threshold_delta_one=float(req("RETURN_THRESHOLD_DELTA_ONE")),
                threshold_premium=float(req("RETURN_THRESHOLD_PREMIUM")),
            ),
        ]

        return cls(
            confirmation=req("DELTA_CONFIRMATION"),
            first_name=req("DELTA_FIRST_NAME"),
            last_name=req("DELTA_LAST_NAME"),
            legs=legs,
            ntfy_server=os.environ.get("NTFY_SERVER", "https://ntfy.sh"),
            ntfy_topic=req("NTFY_TOPIC"),
            headless=os.environ.get("HEADLESS", "true").lower() != "false",
            state_file=BASE_DIR / os.environ.get("STATE_FILE", "state.json"),
            log_file=BASE_DIR / os.environ.get("LOG_FILE", "logs/price_history.jsonl"),
            stop_at=datetime.fromisoformat(req("STOP_AT")),
            launchd_plist=Path(
                os.environ.get(
                    "LAUNCHD_PLIST",
                    str(Path.home() / "Library/LaunchAgents/com.tiffany.delta-upgrade-tracker.plist"),
                )
            ),
        )


def is_blocked(page: Page) -> bool:
    try:
        content = page.content().lower()
    except Exception:
        return False
    return any(indicator in content for indicator in BLOCK_INDICATORS)


def save_debug_snapshot(page: Page, label: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    debug_dir = BASE_DIR / "logs" / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(debug_dir / f"{ts}_{label}.png"), full_page=True)
    except Exception:
        pass
    try:
        (debug_dir / f"{ts}_{label}.html").write_text(page.content())
    except Exception:
        pass


def find_trip(page: Page, config: Config) -> bool:
    """Navigate delta.com's public trip lookup. Returns True if the trip
    details page loaded successfully."""
    try:
        page.goto("https://www.delta.com/mytrips/find", wait_until="domcontentloaded", timeout=45000)
    except Exception as exc:
        # Network failure (DNS, connection refused, etc.) — treat like a
        # block so the caller falls back to a manual-check alert instead
        # of crashing the whole run.
        print(f"Failed to reach delta.com: {exc}", file=sys.stderr)
        return False

    if is_blocked(page):
        return False

    # This is an Angular SPA — the form isn't in the DOM (or isn't
    # interactive) right after domcontentloaded. Wait for the confirmation
    # field to actually be visible before touching anything.
    try:
        page.wait_for_selector('input[name="confirmationNo"]', state="visible", timeout=20000)
    except PlaywrightTimeoutError:
        save_debug_snapshot(page, "find_trip_form_not_found")
        return False

    if is_blocked(page):
        return False

    def fill_first(selectors, value):
        for sel in selectors:
            try:
                locator = page.locator(sel).first
                if locator.count() > 0 and locator.is_visible():
                    locator.fill(value)
                    return True
            except Exception:
                continue
        return False

    confirmation_filled = fill_first(
        [
            'input[name="confirmationNo"]',
            'input[aria-label="Confirmation Number"]',
            'input[placeholder*="ex." i]',
        ],
        config.confirmation,
    )
    first_name_filled = fill_first(
        [
            'input[name="firstName"]',
            'input[aria-label="First Name"]',
        ],
        config.first_name,
    )
    last_name_filled = fill_first(
        [
            'input[name="lastName"]',
            'input[aria-label="Last Name"]',
        ],
        config.last_name,
    )

    if not (confirmation_filled and first_name_filled and last_name_filled):
        save_debug_snapshot(page, "find_trip_form_not_found")
        return False

    submitted = False
    for sel in ['#findTripSearch', 'button:has-text("Search")', 'button[type="submit"]']:
        try:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible():
                btn.click()
                submitted = True
                break
        except Exception:
            continue

    if not submitted:
        save_debug_snapshot(page, "find_trip_submit_not_found")
        return False

    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except PlaywrightTimeoutError:
        pass

    if is_blocked(page):
        return False

    return True


def select_flight_segment(page: Page, flight_number: str) -> bool:
    """From the trip details page (which may show one or two flight
    segments for a round trip), open the seat/upgrade screen for the
    specific leg matching flight_number. Falls back to 'just one
    Upgrade/Seats control on the page' if segment-specific matching
    doesn't find anything (handles the single-leg-shown case)."""
    digits = re.sub(r"\D", "", flight_number)
    variants = {v for v in (digits, digits.lstrip("0")) if v}

    for variant in variants:
        try:
            container = page.locator(f':has-text("{variant}")').filter(
                has=page.locator(SEGMENT_ACTION_SELECTOR)
            ).last
            if container.count() > 0:
                action = container.locator(SEGMENT_ACTION_SELECTOR).first
                if action.is_visible():
                    action.click()
                    try:
                        page.wait_for_load_state("networkidle", timeout=30000)
                    except PlaywrightTimeoutError:
                        pass
                    return not is_blocked(page)
        except Exception:
            continue

    # Fallback: exactly one Upgrade/Seats control visible — single-segment view.
    try:
        actions = page.locator(SEGMENT_ACTION_SELECTOR)
        if actions.count() == 1 and actions.first.is_visible():
            actions.first.click()
            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except PlaywrightTimeoutError:
                pass
            return not is_blocked(page)
    except Exception:
        pass

    save_debug_snapshot(page, f"segment_{flight_number}_not_found")
    return False


def segment_actions_visible(page: Page) -> bool:
    try:
        return page.locator(SEGMENT_ACTION_SELECTOR).count() > 0
    except Exception:
        return False


def scrape_upgrade_prices(page: Page) -> dict:
    """Text-based scrape: look for cabin names and the nearest dollar
    amount in the page text. More resilient to markup changes than fixed
    CSS selectors, at the cost of precision — verify against the debug
    HTML snapshot if a price looks wrong."""
    text = page.inner_text("body")
    results = {"Delta One": None, "Premium Select": None}

    for cabin in list(results.keys()):
        # Look for the cabin name followed within ~200 chars by a $amount.
        pattern = re.compile(re.escape(cabin) + r".{0,200}?\$([\d,]+(?:\.\d{2})?)", re.IGNORECASE | re.DOTALL)
        match = pattern.search(text)
        if match:
            results[cabin] = float(match.group(1).replace(",", ""))

    return results


def send_alert(config: Config, subject: str, body: str) -> None:
    url = f"{config.ntfy_server.rstrip('/')}/{config.ntfy_topic}"
    req = urllib.request.Request(
        url,
        data=body.encode("utf-8"),
        headers={"Title": subject, "Priority": "high", "Tags": "airplane"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except Exception as exc:
        # Don't let a notification failure crash the whole run — log and move on.
        print(f"Failed to send ntfy alert: {exc}", file=sys.stderr)


def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"last_blocked_alert": None, "stopped": False}


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=2))


def log_price_check(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def stop_tracking(config: Config, state: dict) -> None:
    """Past the tracking window: notify once, unload the launchd job so it
    stops running itself, and mark state so we don't repeat this."""
    if not state.get("stopped"):
        flights = " / ".join(f"DL{leg.flight_number} ({leg.flight_date})" for leg in config.legs)
        send_alert(
            config,
            "Delta tracker: tracking window ended",
            f"Stopped tracking upgrade prices for {flights} — past the "
            f"{config.stop_at.isoformat()} cutoff.",
        )
        state["stopped"] = True
        save_state(config.state_file, state)

    if config.launchd_plist.exists():
        try:
            subprocess.run(["launchctl", "unload", str(config.launchd_plist)], check=False)
        except Exception as exc:
            print(f"Failed to unload launchd job: {exc}", file=sys.stderr)


def handle_blocked_or_failed(config: Config, state: dict, page: Page, reason: str) -> None:
    save_debug_snapshot(page, "failure")
    print(f"Check failed: {reason}", file=sys.stderr)

    last_alert = state.get("last_blocked_alert")
    now = datetime.now(timezone.utc)
    should_alert = True
    if last_alert:
        hours_since = (now - datetime.fromisoformat(last_alert)).total_seconds() / 3600
        should_alert = hours_since >= 6  # don't spam if it stays blocked

    if should_alert:
        send_alert(
            config,
            "Delta tracker: manual check needed",
            f"Automated check failed ({reason}), confirmation {config.confirmation}. "
            f"Please check delta.com manually. See logs/debug/ for a snapshot.",
        )
        state["last_blocked_alert"] = now.isoformat()


def check_thresholds(config: Config, state: dict, leg: Leg, prices: dict) -> None:
    leg_state = state.setdefault(leg.state_key, {})
    delta_one = prices.get("Delta One")
    premium = prices.get("Premium Select")

    if delta_one is not None and delta_one < leg.threshold_delta_one:
        if not leg_state.get("alerted_delta_one"):
            send_alert(
                config,
                f"Delta One upgrade deal! ({leg.label})",
                f"Delta One upgrade for DL{leg.flight_number} ({leg.flight_date}) "
                f"is now ${delta_one:.0f} — under your ${leg.threshold_delta_one:.0f} threshold.",
            )
            leg_state["alerted_delta_one"] = True
    else:
        leg_state["alerted_delta_one"] = False

    if premium is not None and premium < leg.threshold_premium:
        if not leg_state.get("alerted_premium"):
            send_alert(
                config,
                f"Premium Select upgrade deal! ({leg.label})",
                f"Premium Select upgrade for DL{leg.flight_number} ({leg.flight_date}) "
                f"is now ${premium:.0f} — under your ${leg.threshold_premium:.0f} threshold.",
            )
            leg_state["alerted_premium"] = True
    else:
        leg_state["alerted_premium"] = False


def main() -> int:
    config = Config.from_env()
    state = load_state(config.state_file)
    timestamp = datetime.now(timezone.utc).isoformat()

    if datetime.now(timezone.utc) >= config.stop_at:
        stop_tracking(config, state)
        print(f"[{timestamp}] Past tracking window ({config.stop_at.isoformat()}) — stopping.")
        return 0

    any_leg_ok = False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=config.headless)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
        )
        page = context.new_page()

        try:
            if not find_trip(page, config):
                handle_blocked_or_failed(config, state, page, "trip lookup failed or blocked")
                save_state(config.state_file, state)
                return 1

            for i, leg in enumerate(config.legs):
                if not select_flight_segment(page, leg.flight_number):
                    handle_blocked_or_failed(
                        config, state, page,
                        f"could not reach upgrade screen for {leg.label} leg (DL{leg.flight_number})",
                    )
                    continue

                prices = scrape_upgrade_prices(page)
                record = {
                    "timestamp": timestamp,
                    "leg": leg.label,
                    "flight_number": leg.flight_number,
                    "prices": prices,
                }
                log_price_check(config.log_file, record)
                print(
                    f"[{timestamp}] {leg.label} DL{leg.flight_number}: "
                    f"Delta One {prices['Delta One']}  Premium Select {prices['Premium Select']}"
                )
                check_thresholds(config, state, leg, prices)
                any_leg_ok = True

                if i < len(config.legs) - 1:
                    try:
                        page.go_back(wait_until="networkidle", timeout=20000)
                    except Exception:
                        pass
                    if not segment_actions_visible(page):
                        find_trip(page, config)
        finally:
            browser.close()

    save_state(config.state_file, state)
    return 0 if any_leg_ok else 1


if __name__ == "__main__":
    sys.exit(main())
