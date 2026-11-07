#!/usr/bin/env python3
"""Wake-on-LAN via a LAN-resident sender.

Magic packets are link-local broadcasts: they do not route, so the
sender must be ON the target's LAN. An agent VM in the cloud is not —
no matter how hard it retries, it can never deliver one. The default
sender here is an always-on Android device (a streaming TV box) driven
over its ADB lane: the packet file is pushed once, then toybox
`nc -u` blasts it to the subnet broadcast address and the target's
unicast IP, ports 9 and 7, for 3 rounds. `--direct` sends from this
host instead (use when the script itself runs on the LAN).

Targets live in the TARGETS registry below (CONFIG). Waking is
operator-invoked, never automated: a human or an agent acting on a
human's say-so runs this. Never wake a machine just to sample it —
sleeping machines are a legitimate state, and other automations must
abstain around them, not reach for this tool.

Usage: wol.py <target> [--direct] [--verify]
Exit: 0 = packets sent (and verify passed if asked); abstains loudly
(exit 2) when the sender lane is down — never silently "sent".
"""
import os
import subprocess
import sys
import time

# ------------------------------------------------------------------
# CONFIG — adapt these. Key values can also be set by environment.
# ------------------------------------------------------------------
# Your targets: short name -> wake identity. MAC is the identity (LAN
# IPs are usually DHCP; the unicast copy uses the last-known IP).
# A target may name a verifier: "ssh" means prove wakefulness by an
# SSH round-trip (see verify_ssh), with an optional expected substring
# of the remote hostname. Placeholders — replace with your own.
TARGETS = {
    # A wired desktop. WoL verified live in the reference install:
    # woke from sleep and answered SSH within ~a minute.
    "pc": {"mac": "aa:bb:cc:dd:ee:01", "ip": "192.168.1.50",
           "verify": "ssh", "verify_user": "you",
           "verify_expect": "desktop"},
    # A smart TV. WoL-capable class; untested in the reference
    # install, hence no verifier — packet delivery only.
    "tv": {"mac": "aa:bb:cc:dd:ee:02", "ip": "192.168.1.60",
           "verify": None},
}
BROADCAST = os.environ.get("WOL_BROADCAST", "192.168.1.255")
ADB = os.environ.get("WOL_ADB", "adb")
# The sender Android device's mesh/VPN address, and the local port
# forwarded to its ADB port (5555). If your sender is directly
# reachable over ADB, point WOL_FORWARD_CMD at a no-op and set
# WOL_SENDER_REACHABLE=1 so the forwarder step is skipped.
SENDER_MESH_ADDR = os.environ.get("WOL_SENDER_MESH_ADDR", "100.x.y.z")
FWD_PORT = int(os.environ.get("WOL_FWD_PORT", "5556"))
FORWARD_CMD = os.environ.get(
    "WOL_FORWARD_CMD", "~/bin/mesh-forward.py {port} {addr}:5555")
PACKET_PATH_LOCAL = os.environ.get("WOL_PACKET_PATH", "/tmp/wol.bin")
PACKET_PATH_REMOTE = "/sdcard/wol.bin"
# SSH used by the verifier; the target may sit behind the same kind
# of forward as the sender (VERIFY_FORWARD_CMD, empty = direct).
SSH_KEY = os.path.expanduser(os.environ.get(
    "WOL_SSH_KEY", "~/.ssh/id_ed25519"))
VERIFY_FWD_PORT = int(os.environ.get("WOL_VERIFY_FWD_PORT", "2222"))
VERIFY_TARGET_MESH_ADDR = os.environ.get(
    "WOL_VERIFY_TARGET_MESH_ADDR", "")
VERIFY_FORWARD_CMD = os.environ.get(
    "WOL_VERIFY_FORWARD_CMD", "~/bin/mesh-forward.py {port} {addr}:22")


def packet(mac: str) -> bytes:
    raw = bytes.fromhex(mac.replace(":", ""))
    return b"\xff" * 6 + raw * 16


def run(cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout)


def start_forward(port: int, addr: str, template: str):
    cmd = template.format(port=port, addr=addr)
    if cmd.strip():
        subprocess.Popen(cmd.split(), stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        time.sleep(3)


def send_via_sender(pkt: bytes):
    """Push the packet to the LAN-resident Android sender and return
    its ADB serial, or False if the lane is down (abstain loudly)."""
    open(PACKET_PATH_LOCAL, "wb").write(pkt)
    start_forward(FWD_PORT, SENDER_MESH_ADDR, FORWARD_CMD)
    s = f"127.0.0.1:{FWD_PORT}"
    run([ADB, "connect", s], timeout=30)
    if "device" not in run([ADB, "-s", s, "get-state"], 30).stdout:
        print("wol: abstain — sender ADB lane down")
        return False
    run([ADB, "-s", s, "push", PACKET_PATH_LOCAL, PACKET_PATH_REMOTE],
        30)
    return s


def blast_sender(s: str, ip: str):
    # toybox nc -w blocks ~20 s per call after sending; that is
    # normal — the packet leaves immediately, the wait is nc's.
    for _ in range(3):
        for dst in (f"{BROADCAST} 9", f"{BROADCAST} 7",
                    f"{ip} 9", f"{ip} 7"):
            try:
                run([ADB, "-s", s, "shell",
                     f"nc -u -w 1 {dst} < {PACKET_PATH_REMOTE}"],
                    timeout=45)
            except subprocess.TimeoutExpired:
                pass  # packet left before the wait; the wait is nc's


def blast_direct(pkt: bytes, ip: str):
    import socket
    for _ in range(3):
        for host in (BROADCAST, ip):
            for port in (9, 7):
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.sendto(pkt, (host, port))
                sock.close()
        time.sleep(1)


def verify_ssh(t: dict) -> bool:
    """Wakefulness is proven by the machine's real service answering,
    never by packet delivery. Poll SSH for up to ~90 s."""
    port = VERIFY_FWD_PORT
    if VERIFY_TARGET_MESH_ADDR:
        start_forward(port, VERIFY_TARGET_MESH_ADDR,
                      VERIFY_FORWARD_CMD)
        host = "127.0.0.1"
    else:
        host = t["ip"]
        port = 22
    expect = t.get("verify_expect", "")
    for _ in range(6):
        r = run(["ssh", "-p", str(port), "-i", SSH_KEY,
                 "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
                 f"{t.get('verify_user', 'you')}@{host}", "hostname"],
                timeout=30)
        if r.returncode == 0 and r.stdout.strip() and (
                not expect or expect in r.stdout):
            return True
        time.sleep(10)
    return False


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1 or args[0] not in TARGETS:
        print(f"usage: wol.py <{'|'.join(TARGETS)}> [--direct] "
              "[--verify]")
        return 2
    t = TARGETS[args[0]]
    pkt = packet(t["mac"])
    if "--direct" in sys.argv:
        blast_direct(pkt, t["ip"])
        print(f"wol: sent direct -> {args[0]}")
    else:
        s = send_via_sender(pkt)
        if not s:
            return 2
        blast_sender(s, t["ip"])
        print(f"wol: sent via sender -> {args[0]}")
    if "--verify" in sys.argv:
        if t.get("verify") == "ssh":
            ok = verify_ssh(t)
            print("wol: verify",
                  "PASS (target answered SSH)" if ok else "FAIL")
            return 0 if ok else 1
        print("wol: no verifier for this target; packet delivery only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
