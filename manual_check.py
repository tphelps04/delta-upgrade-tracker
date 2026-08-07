#!/usr/bin/env python3
"""
Manual-check price evaluator for the Delta upgrade tracker.

You check delta.com yourself in a normal browser (no bot-detection risk —
nothing here ever touches delta.com) and type in the upgrade prices you
see; this logs them and pushes a ntfy alert if either leg's price is
below your threshold. Reuses the same .env config and per-leg alert
de-dup state as delta_tracker.py.
"""

import json
import os
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


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


def req(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise SystemExit(f"Missing required env var: {name} (check your .env file)")
    return val


def load_legs():
    return [
        Leg(
            "outbound",
            req("OUTBOUND_FLIGHT_NUMBER"),
            req("OUTBOUND_FLIGHT_DATE"),
            float(req("OUTBOUND_THRESHOLD_DELTA_ONE")),
            float(req("OUTBOUND_THRESHOLD_PREMIUM")),
        ),
        Leg(
            "return",
            req("RETURN_FLIGHT_NUMBER"),
            req("RETURN_FLIGHT_DATE"),
            float(req("RETURN_THRESHOLD_DELTA_ONE")),
            float(req("RETURN_THRESHOLD_PREMIUM")),
        ),
    ]


def send_alert(subject: str, body: str) -> None:
    ntfy_server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    ntfy_topic = req("NTFY_TOPIC")
    url = f"{ntfy_server.rstrip('/')}/{ntfy_topic}"
    request = urllib.request.Request(
        url,
        data=body.encode("utf-8"),
        headers={"Title": subject, "Priority": "high", "Tags": "airplane"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            resp.read()
    except Exception as exc:
        print(f"Failed to send ntfy alert: {exc}", file=sys.stderr)


def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=2))


def log_entry(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def prompt_price(label: str) -> Optional[float]:
    raw = input(f"{label} (blank to skip): $").strip().replace(",", "").replace("$", "")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        print("  Not a number, skipping.")
        return None


def check_leg(leg: Leg, state: dict, log_path: Path, timestamp: str) -> None:
    print(f"\n--- {leg.label.title()}: {leg.flight_number} ({leg.flight_date}) ---")
    delta_one = prompt_price("Delta One price")
    premium = prompt_price("Premium Select price")

    log_entry(
        log_path,
        {
            "timestamp": timestamp,
            "leg": leg.label,
            "flight_number": leg.flight_number,
            "prices": {"Delta One": delta_one, "Premium Select": premium},
            "source": "manual",
        },
    )

    leg_state = state.setdefault(leg.state_key, {})

    if delta_one is not None and delta_one < leg.threshold_delta_one:
        if not leg_state.get("alerted_delta_one"):
            send_alert(
                f"Delta One upgrade deal! ({leg.label})",
                f"Delta One upgrade for {leg.flight_number} ({leg.flight_date}) "
                f"is now ${delta_one:.0f} — under your ${leg.threshold_delta_one:.0f} threshold.",
            )
            print(f"  ALERT SENT: Delta One ${delta_one:.0f} < ${leg.threshold_delta_one:.0f}")
        leg_state["alerted_delta_one"] = True
    elif delta_one is not None:
        leg_state["alerted_delta_one"] = False
        print(f"  Delta One ${delta_one:.0f} — above threshold, no alert.")

    if premium is not None and premium < leg.threshold_premium:
        if not leg_state.get("alerted_premium"):
            send_alert(
                f"Premium Select upgrade deal! ({leg.label})",
                f"Premium Select upgrade for {leg.flight_number} ({leg.flight_date}) "
                f"is now ${premium:.0f} — under your ${leg.threshold_premium:.0f} threshold.",
            )
            print(f"  ALERT SENT: Premium Select ${premium:.0f} < ${leg.threshold_premium:.0f}")
        leg_state["alerted_premium"] = True
    elif premium is not None:
        leg_state["alerted_premium"] = False
        print(f"  Premium Select ${premium:.0f} — above threshold, no alert.")


def main() -> int:
    legs = load_legs()
    state_path = BASE_DIR / os.environ.get("STATE_FILE", "state.json")
    log_path = BASE_DIR / os.environ.get("LOG_FILE", "logs/price_history.jsonl")
    state = load_state(state_path)
    timestamp = datetime.now(timezone.utc).isoformat()

    print("Enter the upgrade prices you see on delta.com (leave blank to skip a field).")
    for leg in legs:
        check_leg(leg, state, log_path, timestamp)

    save_state(state_path, state)
    print(f"\nDone. Logged to {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
