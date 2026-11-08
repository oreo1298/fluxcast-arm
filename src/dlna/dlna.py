import re
import sys
import time
from typing import Callable, Optional
from urllib.parse import urlsplit

try:
    import upnpclient
except ImportError:
    print("[FluxCast] ERROR: upnpclient is not installed. "
          "Run: pip install upnpclient")
    sys.exit(1)


HLS_PROTOCOL_INFO = "http-get:*:application/x-mpegurl:*"
# Everything after the MIME type; kept apart so the MIME can follow the TV.
TS_DLNA_PROFILE = "AVC_TS_MP_HD_AAC"
TS_DLNA_PARAMS = (
    "DLNA.ORG_OP=01;"
    "DLNA.ORG_CI=0;"
    "DLNA.ORG_FLAGS=01700000000000000000000000000000"
)
TS_PROTOCOL_INFO = f"http-get:*:video/mpeg:DLNA.ORG_PN={TS_DLNA_PROFILE};{TS_DLNA_PARAMS}"

# MIME types renderers list for MPEG-TS and for HLS, in order of preference.
# LG webOS wants video/mpeg for TS (it is what Samsung gets too).
_TS_MIMES = ("video/mpeg", "video/mp2t", "video/vnd.dlna.mpeg-tts")
_HLS_MIMES = ("application/x-mpegurl", "application/vnd.apple.mpegurl",
              "audio/mpegurl", "audio/x-mpegurl")

# UPnP AVTransport error codes worth acting on.
UPNP_TRANSITION_NOT_AVAILABLE = 701
UPNP_ILLEGAL_MIME = 714
UPNP_RESOURCE_NOT_FOUND = 716

# LG webOS answers SetAVTransportURI/Play only once it has opened the stream,
# 8-15 s for live content, and rejects Play with 701 while it is still
# TRANSITIONING. Waiting this long before giving up is what makes LG work.
PLAY_RETRY_SECONDS = 25.0
TRANSITION_WAIT_SECONDS = 15.0


def _protocol_info(stream_url: str) -> str:
    path = urlsplit(stream_url).path
    if path.endswith(".m3u8"):
        return HLS_PROTOCOL_INFO
    return TS_PROTOCOL_INFO


def upnp_error_code(exc: BaseException) -> Optional[int]:
    """The UPnP error code of a failed action, if the device sent one."""
    if isinstance(exc, upnpclient.soap.SOAPError) and exc.args:
        try:
            return int(exc.args[0])
        except (TypeError, ValueError):
            return None
    return None


def describe_error(exc: BaseException) -> str:
    """One line a user can act on, instead of a bare exception repr."""
    code = upnp_error_code(exc)
    if code is not None:
        desc = exc.args[1] if len(exc.args) > 1 else ""
        hints = {
            UPNP_TRANSITION_NOT_AVAILABLE: "the TV was still switching state",
            UPNP_ILLEGAL_MIME: "the TV does not accept this stream type",
            UPNP_RESOURCE_NOT_FOUND: "the TV could not open the stream URL",
        }
        hint = f" - {hints[code]}" if code in hints else ""
        return f"UPnP error {code} ({desc}){hint}"
    name = type(exc).__name__
    if "Timeout" in name:
        return "the TV did not answer in time"
    if "ConnectionError" in name:
        return "could not reach the TV's control port"
    return str(exc) or name


def sink_protocol_info(device) -> list[str]:
    """What the renderer says it can play (ConnectionManager.GetProtocolInfo).

    Empty when the TV has no ConnectionManager or does not answer; callers
    then keep the defaults, which is how FluxCast behaved before.
    """
    try:
        manager = device["ConnectionManager"]
    except (KeyError, AttributeError, TypeError):
        return []
    try:
        result = manager.GetProtocolInfo()
    except Exception:  # noqa: BLE001 - any failure means "unknown"
        return []
    sink = result.get("Sink") or ""
    return [entry.strip() for entry in sink.split(",") if entry.strip()]


def _split_protocol_info(entry: str) -> Optional[tuple[str, str, str, str]]:
    parts = entry.split(":", 3)
    if len(parts) != 4:
        return None
    protocol, network, mime, extra = parts
    return protocol.strip().lower(), network.strip(), mime.strip(), extra.strip()


def _profile_name(extra: str) -> str:
    match = re.search(r"DLNA\.ORG_PN=([^;]+)", extra)
    return match.group(1) if match else ""


def accepts_hls(sink: list[str]) -> Optional[bool]:
    """True/False when the TV listed its formats, None when it did not say."""
    if not sink:
        return None
    for entry in sink:
        parsed = _split_protocol_info(entry)
        if parsed and parsed[0] == "http-get" and parsed[2].lower() in _HLS_MIMES:
            return True
    return False


def choose_protocol_info(stream_url: str, sink: list[str]) -> str:
    """protocolInfo for the DIDL <res>, matched to what the TV accepts.

    Keeps FluxCast's long-standing value whenever the TV lists it (or lists
    nothing), so renderers that already worked see no change. Otherwise it
    takes the MIME type, and if needed the DLNA profile name, from the TV's own
    list: some renderers (LG webOS among them) refuse a SetAVTransportURI whose
    protocolInfo is not one they advertise.
    """
    default = _protocol_info(stream_url)
    entries = [p for p in (_split_protocol_info(e) for e in sink)
               if p and p[0] == "http-get"]
    if not entries:
        return default

    if default == HLS_PROTOCOL_INFO:
        for wanted in _HLS_MIMES:
            for _, _, mime, _ in entries:
                if mime.lower() == wanted:
                    return f"http-get:*:{mime}:*"
        return default

    ts = [(mime, extra) for _, _, mime, extra in entries if mime.lower() in _TS_MIMES]
    if not ts:
        return default
    # MIME first (video/mpeg is what LG webOS and Samsung want), profile second.
    for wanted in _TS_MIMES:
        listed = [(mime, extra) for mime, extra in ts if mime.lower() == wanted]
        if not listed:
            continue
        mime = listed[0][0]
        if wanted == "video/mpeg" and any(
                _profile_name(extra) == TS_DLNA_PROFILE for _, extra in listed):
            return default
        if any(extra == "*" for _, extra in listed):
            # A wildcard accepts any MPEG-TS: say nothing about the profile.
            return f"http-get:*:{mime}:{TS_DLNA_PARAMS}"
        # Borrow the closest H.264 + AAC profile the TV names, the 188-byte
        # (_ISO) variant first since that is the packet size FluxCast sends.
        names = [_profile_name(extra) for _, extra in listed]
        names = [n for n in names if n] or [""]
        name = min(names, key=lambda n: (0 if n.startswith("AVC_TS") else 1,
                                         0 if "AAC" in n else 1,
                                         0 if n.endswith("_ISO") else 1))
        profile = f"DLNA.ORG_PN={name};" if name else ""
        return f"http-get:*:{mime}:{profile}{TS_DLNA_PARAMS}"
    return default


def content_features(protocol_info: str) -> str:
    """The contentFeatures.dlna.org value matching a protocolInfo."""
    parsed = _split_protocol_info(protocol_info)
    if not parsed or parsed[3] == "*":
        return TS_DLNA_PARAMS
    return parsed[3]


def protocol_mime(protocol_info: str) -> str:
    parsed = _split_protocol_info(protocol_info)
    return parsed[2] if parsed else "video/mpeg"


def discover_devices(timeout: int = 5) -> list:
    print(f"[FluxCast] Searching for UPnP/DLNA Cast devices (timeout={timeout}s)…")
    devices = upnpclient.discover(timeout=timeout)
    renderers = [d for d in devices if hasattr(d, "AVTransport")]
    
    unique_renderers = {}
    for r in renderers:
        if r.udn not in unique_renderers:
            unique_renderers[r.udn] = r
            
    return list(unique_renderers.values())


def prompt_device(devices: list, device_name: Optional[str] = None):
    if not devices:
        print("[FluxCast] ERROR: No DLNA renderers found on the network.")
        print("[FluxCast] Make sure the TV is ON and connected to the same Wi-Fi/LAN.")
        sys.exit(1)

    if device_name is not None:
        needle = device_name.lower()
        for dev in devices:
            if dev.friendly_name.lower() == needle:
                return dev
        print(f"[FluxCast] WARNING: Device '{device_name}' not found; falling back to picker.")

    print("\n[FluxCast] Found DLNA device(s):")
    for i, dev in enumerate(devices):
        name = dev.friendly_name
        model = dev.model_name
        print(f"  [{i}] {name}  ({model})")

    default_idx = 0
    for i, dev in enumerate(devices):
        if "samsung" in dev.model_name.lower() or "samsung" in dev.friendly_name.lower():
            default_idx = i
            break

    raw = input(f"Select device [{default_idx}]: ").strip()
    try:
        idx = int(raw) if raw else default_idx
        return devices[idx]
    except (ValueError, IndexError):
        print(f"[FluxCast] Invalid choice, using device {default_idx}.")
        return devices[default_idx]


def _build_didl_metadata(stream_url: str, protocol_info: Optional[str] = None) -> str:
    """
    DIDL-Lite XML metadata payload for DLNA AVTransport.
    """
    from xml.sax.saxutils import escape, quoteattr

    info = protocol_info or _protocol_info(stream_url)
    didl = f"""<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" 
    xmlns:dc="http://purl.org/dc/elements/1.1/" 
    xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" 
    xmlns:sec="http://www.sec.co.kr/">
        <item id="1" parentID="0" restricted="1">
            <upnp:class>object.item.videoItem</upnp:class>
            <dc:title>FluxCast Screen Mirroring</dc:title>
            <res protocolInfo={quoteattr(info)}>{escape(stream_url)}</res>
        </item>
    </DIDL-Lite>"""
    return didl


def transport_state(device) -> tuple[Optional[str], Optional[str]]:
    """(CurrentTransportState, CurrentTransportStatus), or (None, None)."""
    try:
        info = device.AVTransport.GetTransportInfo(InstanceID=0)
    except Exception:  # noqa: BLE001 - polling must never kill the cast
        return None, None
    return info.get("CurrentTransportState"), info.get("CurrentTransportStatus")


def _wait_while_transitioning(device, timeout: float,
                              sleep: Callable[[float], None] = time.sleep) -> Optional[str]:
    deadline = time.monotonic() + timeout
    state, _ = transport_state(device)
    while state == "TRANSITIONING" and time.monotonic() < deadline:
        sleep(0.5)
        state, _ = transport_state(device)
    return state


def start_cast(device, stream_url: str, protocol_info: Optional[str] = None,
               sleep: Callable[[float], None] = time.sleep) -> bool:
    """Hand the stream to the TV and start it. Returns True if the TV took it.

    The order and the patience are what LG webOS needs: it may take 8-15 s to
    answer SetAVTransportURI for a live stream, it refuses Play with UPnP 701
    while still TRANSITIONING, and some renderers start playing on their own.
    """
    print(f"[FluxCast] Sending stream URL to {device.friendly_name} via UPnP/DLNA…")
    info = protocol_info or _protocol_info(stream_url)
    if info != _protocol_info(stream_url):
        print(f"[FluxCast] Using the TV's own format label: {info}")
    didl_metadata = _build_didl_metadata(stream_url, info)

    try:
        device.AVTransport.Stop(InstanceID=0)
        sleep(0.3)
    except Exception:  # noqa: BLE001 - nothing was playing
        pass

    print("[FluxCast] Waiting for the TV to open the stream (LG TVs can take ~15 s)…")
    started = time.monotonic()
    deadline = started + PLAY_RETRY_SECONDS
    while True:
        try:
            device.AVTransport.SetAVTransportURI(
                InstanceID=0,
                CurrentURI=stream_url,
                CurrentURIMetaData=didl_metadata,
            )
            break
        except Exception as exc:  # noqa: BLE001
            if (upnp_error_code(exc) == UPNP_TRANSITION_NOT_AVAILABLE
                    and time.monotonic() < deadline):
                sleep(1.0)
                continue
            print(f"[FluxCast] ERROR: The TV refused the stream URL: {describe_error(exc)}")
            return False
    print(f"[FluxCast] TV accepted the stream URL ({time.monotonic() - started:.1f}s).")

    state = _wait_while_transitioning(device, TRANSITION_WAIT_SECONDS, sleep)
    if state == "PLAYING":
        print("[FluxCast] The TV started playing by itself.")
        return True

    deadline = time.monotonic() + PLAY_RETRY_SECONDS
    attempts = 0
    while True:
        attempts += 1
        try:
            device.AVTransport.Play(InstanceID=0, Speed="1")
            break
        except Exception as exc:  # noqa: BLE001
            if (upnp_error_code(exc) == UPNP_TRANSITION_NOT_AVAILABLE
                    and time.monotonic() < deadline):
                if attempts == 1:
                    print("[FluxCast] TV is still getting ready (UPnP 701); retrying Play…")
                sleep(1.0)
                continue
            state, _ = transport_state(device)
            if state in ("PLAYING", "TRANSITIONING"):
                # Answered late or with an error, but it is starting anyway.
                print(f"[FluxCast] Play returned {describe_error(exc)}, "
                      f"but the TV reports {state}; carrying on.")
                return True
            print(f"[FluxCast] ERROR: The TV would not start playback: {describe_error(exc)}")
            return False
    print("[FluxCast] Play sent. The TV should open its native video player.")
    return True


def stop_cast(device: Optional[object]) -> None:
    if device is None:
        return
    try:
        device.AVTransport.Stop(InstanceID=0)
    except Exception:
        pass
