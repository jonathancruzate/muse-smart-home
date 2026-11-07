#!/usr/bin/env python3
"""RF votes — anonymous, household-level presence corroboration.

PATTERN EXCERPT. In the reference home this function lives inside a
larger presence-corroboration evaluator that first forms a verdict
from person-grade sources (phone geofence zones, location sharing)
and only then consults these votes. The votes are reproduced here
standalone so you can wire them into your own evaluator the same
way. The standing rules are the pattern, not the plumbing:

  CORROBORATION ONLY. RF votes may support a home verdict reached by
  other means, and may be logged as a CHALLENGE against an
  away-leaning verdict. They never fire, block, or reverse a mode
  decision, never feed a gate, and never outweigh a person-source
  (GPS / location-sharing) reading for any individual. They are
  ANONYMOUS and HOUSEHOLD-LEVEL: RF can say "the house is active",
  never "a specific person is home" — and nothing here attempts
  who / pose / vitals, which RSSI cannot do.

Three signals, with honest weights encoded in RF_WEIGHT (the numbers
exist so the distinction is configuration, not prose):

  - link_rf_activity (0.15): device-state transition rate /
    motion_level from any LAN-activity processor you run (in the
    reference home: a roll-call-driven antenna feed -> processor
    chain). The dynamic signal. Optional input — point
    LINK_STATE at its JSON ({ts, transitions_in_window,
    motion_level, window_min, presence, presence_score,
    anchors_answering}) or leave unset and the signal abstains.
  - rssi_variance (0.15): RSSI range/anomaly at the fixed points of
    rssi-series.jsonl. Variance is the signal — bodies moving
    through the room disturb multipath and a fixed receiver sees its
    AP links wobble. A FLAT series abstains explicitly: fixed
    always-on-linked points at rest carry ~no occupancy evidence.
    Points with a MATURE learned baseline (baseline/rssi_baseline.py:
    >= 200 samples across >= 4 days) vote on ANOMALY vs their own
    per-hour baseline range instead of the raw threshold — "unusual
    for this point at this hour" — while immature points keep the
    raw-range rule.
  - link_rf_presence_flag (0.0): the processor's presence boolean.
    Its anchor-answer rate is dominated by always-on devices, so the
    flag is infrastructure liveness, not occupancy evidence. It is
    recorded for trend-watching and NEVER supports or challenges a
    verdict.

A frozen or absent feed abstains and is a defect to flag, never a
fact to act on: a processor state older than 45 min abstains.

Usage: import rf_votes() from your evaluator, or run standalone to
print the current votes as JSON.
"""
import json
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ------------------------------------------------------------------
# CONFIG — adapt these. Every value can also be set by environment.
# ------------------------------------------------------------------
MESH_DIR = os.path.expanduser(os.environ.get(
    "RSSI_MESH_DIR", os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "..")))
RSSI_SERIES = os.path.join(MESH_DIR, "rssi-series.jsonl")
RSSI_BASELINE = os.path.join(MESH_DIR, "rssi-baseline.json")
# Optional LAN-activity processor state (see docstring). Empty = the
# two link_* signals abstain.
LINK_STATE = os.environ.get("RSSI_LINK_STATE", "")
LOCAL_TZ = ZoneInfo(os.environ.get("RSSI_MESH_TZ", "UTC"))

# Honest RF weights (see module docstring). The two dynamic signals
# are corroborate/challenge-only at 0.15; the flat presence flag is
# 0.0 by design. Nothing here can outvote, block, or reverse a
# person-source decision.
RF_WEIGHT = {"link_rf_activity": 0.15,
             "rssi_variance": 0.15,
             "link_rf_presence_flag": 0.0}
RF_STALE_SECS = 2700        # processor state older than 45 min abstains
RSSI_RANGE_DB = 4.0         # per-point range that counts as disturbed
MOTION_MIN = 0.15           # processor motion_level floor for "active"
TRANSITIONS_MIN = 3         # ...or >= this many transitions in window


def load_json(path, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def rf_votes():
    """Compute the anonymous household RF votes. Returns (votes, lines).

    Each vote: {signal, vote, weight, basis, source_ts}. vote is
    "home-activity" (dynamic signals only), "home-flag" (the
    near-zero presence flag), or "abstain"."""
    votes, lines = [], []
    now = datetime.now(timezone.utc)

    # --- LAN-activity processor state (optional input) ---------------
    st = load_json(LINK_STATE, None) if LINK_STATE else None
    st_ts = parse_iso(st.get("ts")) if st else None
    fresh = bool(st_ts and (now - st_ts).total_seconds() <= RF_STALE_SECS)
    if not st:
        for sig in ("link_rf_activity", "link_rf_presence_flag"):
            votes.append({"signal": sig, "vote": "abstain",
                          "weight": RF_WEIGHT[sig],
                          "basis": "no LAN-activity processor state — "
                                   "signal not configured or chain has "
                                   "not produced a sample",
                          "source_ts": None})
        lines.append("RF link votes: abstain — no processor state")
    elif not fresh:
        age = int((now - st_ts).total_seconds() // 60) if st_ts else -1
        for sig in ("link_rf_activity", "link_rf_presence_flag"):
            votes.append({"signal": sig, "vote": "abstain",
                          "weight": RF_WEIGHT[sig],
                          "basis": f"processor state is stale "
                                   f"({age} min old; >{RF_STALE_SECS // 60} "
                                   f"min abstains) — a frozen feed is a "
                                   f"defect, never a fact",
                          "source_ts": st.get("ts")})
        lines.append(f"RF link votes: abstain — processor state stale "
                     f"({age} min)")
    else:
        trans = st.get("transitions_in_window") or 0
        motion = st.get("motion_level") or 0
        active = trans >= TRANSITIONS_MIN or motion >= MOTION_MIN
        votes.append({
            "signal": "link_rf_activity",
            "vote": "home-activity" if active else "abstain",
            "weight": RF_WEIGHT["link_rf_activity"],
            "basis": (f"{trans} device-state transitions and motion_level "
                      f"{motion} in the last {st.get('window_min', 60)} min "
                      f"(thresholds: >={TRANSITIONS_MIN} transitions or "
                      f">={MOTION_MIN} motion) — anonymous household "
                      f"dynamics, corroborate/challenge only"),
            "source_ts": st.get("ts")})
        votes.append({
            "signal": "link_rf_presence_flag",
            "vote": "home-flag" if st.get("presence") else "abstain",
            "weight": RF_WEIGHT["link_rf_presence_flag"],
            "basis": (f"processor presence={st.get('presence')} "
                      f"(score {st.get('presence_score')}, anchors "
                      f"{st.get('anchors_answering')}) — anchor answers "
                      f"are dominated by always-on devices, so this flag "
                      f"is infrastructure liveness at weight 0.0 and "
                      f"never supports a verdict"),
            "source_ts": st.get("ts")})
        lines.append(f"RF link activity: {'home-activity' if active else 'abstain'} "
                     f"(transitions {trans}, motion {motion}); presence flag "
                     f"recorded at weight 0.0")

    # --- Fixed-point RSSI variance / baseline anomaly ----------------
    rows = []
    try:
        with open(RSSI_SERIES) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        continue
    except FileNotFoundError:
        pass
    rows = rows[-12:]
    ranges, last6 = {}, {}
    for k in set().union(*[set(r.get("points", {})) for r in rows]) if rows else set():
        vals = [r["points"][k] for r in rows
                if isinstance(r.get("points", {}).get(k), (int, float))]
        if len(vals) >= 3:
            ranges[k] = max(vals) - min(vals)
            last6[k] = vals[-6:]
    if not ranges:
        votes.append({"signal": "rssi_variance", "vote": "abstain",
                      "weight": RF_WEIGHT["rssi_variance"],
                      "basis": "insufficient RSSI series (<3 usable "
                               "samples per fixed point)",
                      "source_ts": rows[-1].get("ts") if rows else None})
        lines.append("RF RSSI vote: abstain — insufficient series")
    else:
        # Baseline anomaly for MATURE points; raw range for the rest.
        # A mature point whose current window is normal FOR ITS HOUR
        # abstains even if the raw range crosses the old threshold —
        # evenings are legitimately wobbly.
        baseline = load_json(RSSI_BASELINE, {}) or {}
        hour_key = str(datetime.now(LOCAL_TZ).hour)
        disturbed_by, method_notes = {}, {}
        for k, rng in ranges.items():
            bp = (baseline.get("points") or {}).get(k) or {}
            bh = (bp.get("hours") or {}).get(hour_key) or {}
            if (bp.get("mature") and bh.get("range_mean") is not None
                    and len(last6.get(k, [])) >= 4):
                cur = max(last6[k]) - min(last6[k])
                thresh = bh["range_mean"] + 2.5 * max(bh.get("range_std") or 0.0, 1.0)
                disturbed_by[k] = cur > thresh
                method_notes[k] = (f"baseline-anomaly: last-6 range "
                                   f"{cur:.0f} dB vs hour-{hour_key} normal "
                                   f"{bh['range_mean']:.0f}±{bh.get('range_std') or 0:.0f} "
                                   f"(fires >{thresh:.0f})")
            else:
                disturbed_by[k] = rng >= RSSI_RANGE_DB
                method_notes[k] = f"raw-range {rng:.0f} dB (threshold {RSSI_RANGE_DB:.0f})"
        disturbed_points = [k for k, d in disturbed_by.items() if d]
        disturbed = bool(disturbed_points)
        worst = disturbed_points[0] if disturbed_points else max(ranges, key=ranges.get)
        votes.append({
            "signal": "rssi_variance",
            "vote": "home-activity" if disturbed else "abstain",
            "weight": RF_WEIGHT["rssi_variance"],
            "basis": (f"RSSI over last {len(rows)} samples: "
                      + "; ".join(f"{k}: {method_notes[k]}"
                                  for k in sorted(ranges))
                      + (f" — disturbance at {', '.join(disturbed_points)} "
                         f"is multipath movement evidence"
                         if disturbed else
                         " — nothing beyond normal for each point's "
                         "method: flat-or-normal series abstains rather "
                         "than voting")),
            "source_ts": rows[-1].get("ts")})
        lines.append(f"RF RSSI variance: {'home-activity' if disturbed else 'abstain'} "
                     f"(worst range {ranges[worst]:.0f} dB at {worst})")
    return votes, lines


def main() -> int:
    votes, lines = rf_votes()
    for line in lines:
        print(f"  {line}")
    print(json.dumps({"rf_votes": votes,
                      "rf_active": any(v["vote"] == "home-activity"
                                       for v in votes),
                      "corroboration_only": True}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
