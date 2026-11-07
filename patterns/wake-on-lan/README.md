# Pattern: Wake-on-LAN via a LAN-Resident Sender

> **Placeholders:** every MAC (`aa:bb:cc:dd:ee:0x`), IP, hostname, mesh address, and path in this folder is fictional. Replace them with your own in `wol.py`'s CONFIG block (or the environment variables it names).

## What it is

A small Wake-on-LAN tool (`wol.py`) plus the discipline around it: wake a sleeping wired machine (a desktop, a TV) from an agent that does **not** live on the home LAN, by having an always-on device that *does* live there send the magic packet — and then proving the machine actually woke by asking its real service, not by trusting that the packet left.

## Why a LAN-resident sender is mandatory

A magic packet is a link-local UDP broadcast. Broadcasts do not route — not through your router, not through a mesh VPN, not through an exit node. A cloud VM, a CI runner, or an agent host on another network can craft a perfect packet and it will still never arrive. This is the single fact the whole pattern hangs on: **the sender must already be inside the LAN.** Everything else is choosing which resident device sends.

## Sender options

| Sender | How | Notes |
|---|---|---|
| **Android TV box / streaming box over ADB** (reference sender) | Push the 102-byte packet file once, then blast it with the box's built-in `nc -u` to the subnet broadcast and the target's unicast IP | Already awake, already wired, already on the LAN for other duties (in the reference home it doubles as the RSSI mesh's scan point — see [`../rssi-presence-mesh/`](../rssi-presence-mesh/)). Reachable from the agent host over a mesh-VPN port forward to its ADB port. |
| **Any LAN host** (`--direct`) | Plain Python UDP sockets with `SO_BROADCAST` | Use when the tool runs on a machine already on the LAN (a home server, a NAS, the target subnet's router host). |
| **ESP32-class node** | A tiny firmware sketch that listens for a wake request and emits the packet | The most "infrastructure" option: no OS, no ADB lane to keep alive. Worth it if no other always-on resident exists. |
| **Router admin UI** | Many routers have a built-in WoL page | Manual and unauditable — fine as a one-off, useless as an agent lane. |

The reference sender's quirks, hard-won: toybox `nc -u -w 1` **blocks ~20 s per call after the packet leaves** — that wait is nc's, not a failure, so budget for it (3 rounds × 4 destinations ≈ a few minutes) rather than "fixing" it. Blast **broadcast and unicast, ports 9 and 7, 3 rounds**: NICs in deep sleep states are inconsistent about which copy they honor, and the extra copies cost nothing.

## The target registry

Targets are a dict in `wol.py`'s CONFIG: short name → `{mac, ip, verify, ...}`. Principles:

- **The MAC is the identity.** LAN IPs are DHCP and drift; the unicast copy uses the last-known IP, but the broadcast copy addressed to the MAC is the one that has to land.
- **Unproven targets are labeled unproven.** The reference registry carries a TV entry marked untested with no verifier, next to a PC entry verified live. A registry that pretends every entry works is worse than no registry.
- Adding a target is a CONFIG edit, never a code change.

## Verification discipline

**Packet delivery is not wakefulness.** `--verify` proves the wake by the machine's real service: for the desktop, an SSH round-trip returning its hostname, polled for up to ~90 s (a cold boot is not instant). Targets without a service to interrogate report "packet delivery only" — say so, don't upgrade the claim. And if the sender lane is down, the tool **abstains loudly** (exit 2, a printed reason): a wake tool that silently "succeeds" teaches every caller to trust a fiction.

## Target-side requirements (Windows desktop)

WoL is a property of the *sleeping* machine's configuration as much as the packet:

- **NIC settings:** in Device Manager, the Ethernet adapter must have "Allow this device to wake the computer" and "Only allow a magic packet to wake the computer" enabled; the driver-level Wake-on-Magic-Packet property must be on.
- **Fast Startup breaks WoL from shutdown.** With Fast Startup enabled, Windows "shutdown" is a hybrid hibernate (S4/S5 hybrid) from which most NICs will not wake. WoL works reliably from **sleep (S3)** and usually from hibernate; if wake-from-full-shutdown matters, disable Fast Startup or accept sleep as the wakeable state.
- **BIOS/UEFI:** "Wake on LAN" / "Power on by PCI-E" must be enabled, and **ErP / deep-sleep power modes disabled** — ErP cuts standby power to the NIC entirely, and no packet wakes a NIC with no power.
- Wired Ethernet is the reliable path. WoWLAN (wake over Wi-Fi) exists but is adapter- and driver-dependent; don't design around it.

## The rules that keep this safe

- **Operator-invoked, never automated.** A human, or an agent acting on a human's explicit say-so, runs the wake. No scheduler, health check, or sampler may call it. In the reference home the PC is also an RSSI sampling point — its sampler **abstains while it sleeps**, by design; the mesh judges that leg on its poll markers, never on sample age. Waking a machine just to keep a graph pretty burns power and trains the whole system to lie about what "asleep" means.
- **One wake per request.** Repeated blasting inside one invocation is the tool's own 3-round pattern; re-invoking in a loop because verify is slow is not.
- **Report what the evidence supports:** "packets sent", "answered SSH at T+40 s", or "no answer in 90 s — likely a target-side WoL setting" are three different claims. Keep them separate.

## Bonus gotcha: the software-Off Wi-Fi radio

Found while bringing the reference PC up as an RSSI scan point (companion pattern): its Wi-Fi adapter was present, disconnected — and its radio switched **Off in software**, so `netsh wlan show networks` returned nothing and the sampler read as dead. The fix is remote and reversible: over the same SSH lane, the Windows radio-management API (`Windows.Devices.Radios.Radio.SetStateAsync(On)`) turns the radio on **without associating** — the machine stays on Ethernet, the adapter just scans. If a scan-based tool reports a Windows box as radio-dead, check radio state (`netsh wlan show interfaces` → "Software Off") before blaming the tool.

## Install it with Muse

Give this to your Muse (or any coding agent):

> Add Wake-on-LAN from `patterns/wake-on-lan/` to my smart-home stack. Read the pattern README first, especially why the sender must live on my LAN. Help me pick my sender: if I have an always-on Android TV box you can reach over ADB, use it as the sender; otherwise run `wol.py --direct` from a host already on my LAN. Set my subnet broadcast address and my sender's reachability (mesh address / forward command, or direct ADB) in `wol.py`'s CONFIG, then register each wake target by its wired MAC and last-known IP. Before the first real wake, walk me through the target-side checklist — NIC wake settings, Fast Startup, BIOS ErP — and mark any target we haven't proven as untested in the registry. Then do one live test wake of my desktop with `--verify` and show me the SSH proof it answered. Do not wire this into any schedule, poll, or sampler: it stays operator-invoked only.

## Files

```text
patterns/wake-on-lan/
├── README.md   # this pattern doc
└── wol.py      # registry + sender lanes (ADB Android / --direct) + SSH verify
```
