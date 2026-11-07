#!/usr/bin/env python3
"""Wi-Fi health watch — the scan series moonlights as AP monitoring.

The TV-box leg hears both home APs every poll. That makes the series
an AP-health feed the router itself never provides:

  - a home AP absent from the scan points across the recent rows
    while rows are flowing (the extender fell off / a router radio
    died);
  - a home AP's latest RSSI collapsed (<= -80 dBm at a fixed point
    that normally reads strong = the AP moved, dropped to one bar,
    or something is shielding it);
  - the scan sample stream itself stale (the sampler's state file
    says why).

Writes wifi-health-state.json {ts, ok, issues[]}. A watchdog consumes
it with a two-tick persistence rule before escalating — one bad poll
is weather, two is a defect. Never raises.

Usage: python3 wifi_health.py   (idempotent; safe to hook)
"""
import json
import os
import sys
from datetime import datetime, timezone

# ------------------------------------------------------------------
# CONFIG — adapt these. Every value can also be set by environment.
# ------------------------------------------------------------------
MESH_DIR = os.path.expanduser(os.environ.get(
    "RSSI_MESH_DIR", os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "..")))
SERIES = os.path.join(MESH_DIR, "rssi-series.jsonl")
SHIELD_STATE = os.path.join(MESH_DIR, "shield-rssi-state.json")
OUT = os.path.join(MESH_DIR, "wifi-health-state.json")
# The scan leg's point names (must match shield_rssi_sampler's APS).
SHIELD_POINTS = ("shield-ap-main", "shield-ap-extender")
ABSENT_ROWS = 6          # consecutive flowing rows w/o the point
COLLAPSE_DBM = -80


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_iso(s):
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except Exception:
        return None


def load_json(p):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return {}


def main() -> int:
    now = datetime.now(timezone.utc)
    issues = []

    rows = []
    try:
        with open(SERIES) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        continue
    except FileNotFoundError:
        pass
    shield_rows = [r for r in rows
                   if any(k in (r.get("points") or {})
                          for k in SHIELD_POINTS)][-ABSENT_ROWS:]

    if len(shield_rows) >= ABSENT_ROWS:
        for k in SHIELD_POINTS:
            if all(k not in (r.get("points") or {}) for r in shield_rows):
                issues.append(
                    f"{k}: absent from the scan points across "
                    f"the last {ABSENT_ROWS} flowing rows — AP missing "
                    f"(extender down / router radio off?)")
            else:
                latest = [r["points"][k] for r in shield_rows
                          if k in (r.get("points") or {})][-1]
                if latest <= COLLAPSE_DBM:
                    issues.append(
                        f"{k}: latest RSSI {latest} dBm at the fixed "
                        f"scan point — AP signal collapsed")
    st = load_json(SHIELD_STATE)
    sample_ts = parse_iso(st.get("last_sample_ts"))
    if sample_ts and (now - sample_ts).total_seconds() > 1800:
        issues.append(
            f"scan-sample-stale: newest scan sample is "
            f"{int((now - sample_ts).total_seconds() // 60)} min old "
            f"(sampler status {st.get('last_status')})")

    out = {"ts": now_iso(), "ok": not issues, "issues": issues,
           "shield_rows_checked": len(shield_rows)}
    with open(OUT, "w") as f:
        json.dump(out, f)
    if issues:
        for i in issues:
            print(f"wifi-health: ISSUE {i}")
    else:
        print("wifi-health: ok (both home APs visible at the scan "
              "point, no collapse)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
