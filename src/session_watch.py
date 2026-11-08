"""Explain DLNA/Cast sessions that fail without an error.

A TV that cannot reach the stream, and a TV that reached it but could not
play it, look the same from the couch: a spinner, then nothing. The stream
server knows which one happened, so the session can say so.
"""
import socket
from typing import Optional


def local_ip_for(target_ip: str) -> Optional[str]:
    """This machine's address on the route to `target_ip`.

    FluxCast used to advertise whatever address routes to the internet. With
    a VPN, a second network card or a container bridge that is an address the
    TV cannot reach. Connecting a UDP socket sends nothing; it only asks the
    kernel which source address it would use.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((target_ip, 9))
            address = probe.getsockname()[0]
    except (OSError, ValueError):
        return None
    if address.startswith("0.") or address.startswith("127."):
        return None
    return address


def firewall_hint(port: int) -> str:
    """The command that opens `port`, for whichever firewall is running."""
    try:
        from diagnostics import _firewalld_active, _ufw_enabled
        firewalld = _firewalld_active()
        ufw = _ufw_enabled()
    except Exception:  # noqa: BLE001 - a hint must never break the session
        firewalld = ufw = None
    if firewalld:
        return (f"open the port for this session with: sudo firewall-cmd --add-port={port}/tcp "
                f"(add --permanent and run sudo firewall-cmd --reload to keep it)")
    if ufw:
        return f"allow the port with: sudo ufw allow {port}/tcp"
    return f"make sure no firewall blocks TCP port {port} on this computer"


def diagnose(stats: dict, *, tv_ip: str, host: str, port: int,
             tv_state: Optional[str] = None) -> Optional[str]:
    """What most likely went wrong, or None if the TV is fetching normally.

    `stats` is server.request_stats.snapshot(); `tv_state` the last DLNA
    transport state, if known.
    """
    rejected = {ip: n for ip, n in stats.get("rejected", {}).items() if ip != tv_ip}
    if stats.get("requests", 0) == 0:
        if rejected:
            others = ", ".join(sorted(rejected))
            return (
                f"The TV never fetched the stream from {tv_ip}, but {others} tried and was "
                f"refused. If that is the TV, re-run with --host set to this computer's "
                f"address on the TV's network."
            )
        return (
            f"The TV never requested the stream at http://{host}:{port}/, so it cannot "
            "reach this computer. Most likely causes:\n"
            f"  1. A firewall on this computer: {firewall_hint(port)}\n"
            "  2. The TV and this computer are on different networks, or on a guest\n"
            "     Wi-Fi that isolates devices from each other.\n"
            f"  3. A VPN: {host} must be this computer's address on the TV's network\n"
            "     (pass --host <that address> to override)."
        )
    if tv_state in ("STOPPED", "NO_MEDIA_PRESENT") and stats.get("bytes_sent", 0) < 2_000_000:
        kib = stats.get("bytes_sent", 0) // 1024
        return (
            f"The TV fetched the stream ({kib} KiB) and then stopped, so it reached this "
            "computer but did not like what it got. Try the other transport "
            "(--transport hls or --transport progressive-ts), a lighter stream "
            "(--output-res 1280x720 --fps 30), and on Fedora without RPM Fusion check "
            "that `rpm -q openh264` shows the real library, not noopenh264."
        )
    return None
