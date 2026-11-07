# Pattern: RSSI Presence Mesh

> **Placeholders:** every BSSID (`aa:bb:cc:dd:ee:0x`), endpoint, account, and path in this folder is fictional. Replace them with your own in each script's CONFIG block (or the environment variables it names).

## What it is

A mesh of *fixed Wi-Fi observation points you already own* — purifiers, a TV box, a robot vacuum, a desktop — each contributing its access-point RSSI to one shared time series. Bodies moving through a room disturb multipath propagation, so a fixed receiver sees its AP links wobble; that wobble, scored honestly, is a coarse household-activity signal that can **corroborate** presence systems built on stronger evidence.

It is deliberately the free rung of Wi-Fi sensing: **RSSI-class only**. It supports "the house has been active over the last hour" — never who is home, never which person, never pose, breathing, or vitals. Anything finer needs channel state information (CSI), and CSI is not reachable from consumer operating systems: Android exposes no CSI API at any permission level (it is a receiver-PHY export gated by radio firmware — Espressif's `esp_wifi_set_csi`, Nexmon-patched drivers, patched mt76, and similar are the real doors). If you want that rung, the companion project below is equally blunt about where its ceiling is.

## Architecture

```text
 fixed points (samplers/, one leg each, all riding one 300 s poll)
   vendor cloud feed ──► rssi_series_sampler.py    (AP-link RSSI)
   Android TV scan   ──► shield_rssi_sampler.py    (scan RSSI via ADB)
   robot vacuum RPC  ──► roborock_rssi_sampler.py  (GET_NETWORK_INFO)
   Windows PC scan   ──► pc_rssi_sampler.py        (netsh via SSH)
                                │
                                ▼
                     rssi-series.jsonl   {ts, source, points:{name: dBm}}
                                │
              ┌─────────────────┼──────────────────┐
              ▼                 ▼                  ▼
   baseline/rssi_baseline.py   votes/rf_votes.py   samplers/wifi_health.py
   per-point/hour norms;       variance/anomaly    AP absence/collapse
   maturity ≥200 samples       votes @ 0.15/0.15,  detection off the
   across ≥4 days              presence flag 0.0   same scan view
```

Every sampler follows the same contract, which is most of the pattern:

- **Abstain, never fabricate.** A dead lane prints an abstain line and exits 0. A sampler must never break the poll it rides, and a frozen feed is a defect to surface — never a stale fact downstream.
- **Dedupe on the source's own signature** (feed timestamp, scan contents, (RSSI, state)), keyed against *that source's* last row — the series is interleaved, so the file's last line is not yours.
- **Freshness markers on every run** (`last_poll_utc` / `last_sample_ts` / `last_status` in a per-leg state file). Dedupe means a quiet room legitimately appends no rows, so row-absence cannot tell "flat" from "dead" — the markers can, and a watchdog should read them (two-tick persistence before escalating).

## The tricks worth stealing

**The Ethernet scan trick (TV box, and again on the PC).** A wired Android TV box's Wi-Fi radio is unassociated — its connected-link RSSI reads −127 and says so honestly in every sample. But the radio still *scans*, and scan results carry strong per-AP RSSI for your own APs. A two-file telemetry app on the box (foreground service, boot autostart, in-app log rotation; samples at 2 Hz to a JSONL the host pulls over ADB) turns a streaming box into a fixed RF observation point *without giving up Ethernet*. A Windows desktop's idle Wi-Fi adapter does the same via `netsh wlan show networks mode=bssid` (signal % → dBm ≈ pct/2 − 100). Honest cadence note: OS scan throttling means new scan data lands every ~30 s to a few minutes — perfect for occupancy *trends*, useless for motion *transients*.

**The robot is a surveyor.** Roborock vacuums answer `GET_NETWORK_INFO` on their local channel with SSID/BSSID/frequency/RSSI. Docked, the robot is one more fixed point (on your IoT band, which the other legs may not see). Cleaning, it is a *mobile* point: log every poll with robot state and cleaned area (`roborock-rf-survey.jsonl`), and a pose-join against its map later yields a per-room radio map that calibrates all the fixed points.

**Zone attribution.** A point's wobble localizes to where the point *sits*: the TV box's points are living-room points. In the reference home, Shield-point range ≥ 4 dB feeds the living-room area-occupancy signal directly, while the household vote stays in the corroboration layer. Keep the two scopes separate — zone evidence and household evidence are different claims.

**Wi-Fi health for free.** The scan leg hears your APs every poll: an AP missing across the recent flowing rows, or a fixed point's RSSI collapsing past −80 dBm, is an extender/router failure your router will never report. `wifi_health.py` writes `{ok, issues[]}` for a watchdog to consume.

## Scoring: from raw range to learned baselines

Day one, the RSSI vote is a raw range rule: per point, max−min over the recent window ≥ 4 dB counts as disturbed. That works, but it can't tell a busy-but-empty evening from occupancy. So the baseline learner runs from the start: per point, per local hour, the normal value distribution and the normal 6-sample range. A point is **mature at ≥ 200 samples across ≥ 4 distinct days**; from then its vote is *anomaly* — the last-6 range must exceed its own hour's normal range mean + 2.5σ — and a mature point that is normal *for its hour* abstains even if the raw range crosses 4 dB. Immature points keep the raw rule until they grow up.

## The honesty contract (do not strip this)

Encoded in `votes/rf_votes.py` as weights, not just prose:

| Signal | Weight | Role |
|---|---|---|
| LAN-activity dynamics (transitions/motion) | 0.15 | corroborate / challenge |
| RSSI variance-or-anomaly | 0.15 | corroborate / challenge |
| Processor presence flag | **0.0** | trend-watching only — always-on anchors make it infrastructure liveness, never occupancy evidence |

RF votes may support a verdict that person-grade sources (geofence, location sharing) reached, and may be logged as a *challenge* against an away-leaning verdict. They never fire, block, or reverse a mode decision, never feed a gate, and never outweigh a person-source reading. A flat series abstains. A stale feed abstains and gets flagged as a defect. Verified dead ends on the reference hardware, so you don't re-chase them: no Wi-Fi RTT (802.11mc) and no Wi-Fi Aware on the 2019 Shield Pro, and no CSI from Android, ever.

## Companion project

The TV-box telemetry app lives in its own repo — **[shield-wifi-sense](https://github.com/jonathancruzate/shield-wifi-sense)** — APK source, build script (project-local toolchain, no Android Studio), and the host sampler this pattern generalizes.

## Install it with Muse

Give this to your Muse (or any coding agent):

> Add the RSSI presence mesh from `patterns/rssi-presence-mesh/` to my smart-home stack. Start with the legs my hardware supports: (1) if any of my always-on devices already report AP-link RSSI in a feed you poll, wire `samplers/rssi_series_sampler.py` to that feed's state file; (2) for my Android TV box, install the shield-wifi-sense app from its repo, then set my AP BSSIDs and ADB endpoint in `samplers/shield_rssi_sampler.py`'s CONFIG and schedule it on the same 300 s poll; (3) if I have a Roborock, point `samplers/roborock_rssi_sampler.py` at my local-lane tools directory. Run `baseline/rssi_baseline.py` on the same poll, and wire `votes/rf_votes.py` into my presence evaluator as corroborate/challenge-only votes at the encoded weights — do not give RF any path to fire, block, or reverse a mode decision. Set `RSSI_MESH_TZ` to my timezone. After the first poll, show me the series rows each leg appended, with the actual RSSI values, and each leg's freshness state file. Then add `samplers/wifi_health.py` to the poll and show me its first state file.

## Files

```text
patterns/rssi-presence-mesh/
├── README.md
├── samplers/
│   ├── rssi_series_sampler.py    # leg 1: vendor-feed AP-link RSSI
│   ├── shield_rssi_sampler.py    # leg 2: Android TV scan RSSI (ADB)
│   ├── roborock_rssi_sampler.py  # leg 3: robot GET_NETWORK_INFO
│   ├── pc_rssi_sampler.py        # leg 4: Windows netsh scan (SSH)
│   └── wifi_health.py            # AP absence/collapse watch
├── baseline/
│   └── rssi_baseline.py          # per-point/hour learner
├── votes/
│   └── rf_votes.py               # corroboration votes + weights
└── samples/
    └── rssi-series.example.jsonl # the shared series format
```
