import sys
import time
import uuid
from typing import Callable, Optional

try:
    import pychromecast
    from pychromecast.config import APP_MEDIA_RECEIVER
    from pychromecast.const import CAST_TYPE_CHROMECAST
    from pychromecast.discovery import discover_chromecasts
    from pychromecast.models import CastInfo, HostServiceInfo
except ImportError:
    print("[FluxCast] ERROR: pychromecast is not installed. "
          "Run: pip install pychromecast")
    sys.exit(1)


class _MediaListener:
    #Surface receiver-side playback state so failures stop being silent hangs.
    def __init__(self):
        self._last = None

    def new_media_status(self, status):
        key = (status.player_state, status.idle_reason)
        if key == self._last:
            return
        self._last = key
        if status.player_state == "IDLE" and status.idle_reason:
            print(f"[FluxCast] TV playback: IDLE (reason={status.idle_reason})")
            if status.idle_reason == "ERROR":
                print("[FluxCast] The TV stopped playback with an error. The receiver "
                      "doesn't report why, but this usually means a video segment "
                      "arrived late because the encoder couldn't keep up under load.")
                print("[FluxCast] Try a lighter setting, e.g. --fps 20 or "
                      "--output-res 960x540, then restart the cast (Ctrl+C to stop).")
        else:
            print(f"[FluxCast] TV playback: {status.player_state}")

    def load_media_failed(self, item, error_code):
        print(f"[FluxCast] ERROR: TV failed to load media (error_code={error_code})")


class _ConnectionListener:
    #Make connection drops / retries visible.
    def __init__(self):
        self._last = None

    def new_connection_status(self, status):
        if status.status == self._last:
            return
        self._last = status.status
        print(f"[FluxCast] Cast connection: {status.status}")


def _direct_chromecast(host: str, port: int, device_uuid, model: str | None,
                       name: str | None, cast_type: str | None,
                       manufacturer: str | None):
    cast_info = CastInfo(
        services={HostServiceInfo(host, port)},
        uuid=device_uuid,
        model_name=model,
        friendly_name=name,
        host=host,
        port=port,
        cast_type=cast_type or CAST_TYPE_CHROMECAST,
        manufacturer=manufacturer,
    )
    return pychromecast.Chromecast(cast_info=cast_info)


def discover_devices(timeout: int = 10) -> list:
    print(f"[FluxCast] Searching for Cast devices (timeout={timeout}s)…")
    cast_infos, browser = discover_chromecasts(timeout=timeout)
    browser.stop_discovery()
    return [
        _direct_chromecast(
            info.host, info.port, info.uuid,
            info.model_name, info.friendly_name,
            info.cast_type, info.manufacturer,
        )
        for info in cast_infos
        if info.host
    ]


def connect_by_ip(ip: str, port: int = 8009):
    print(f"[FluxCast] Connecting directly to Cast device at {ip}:{port}…")
    fake_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"{ip}:{port}")
    cast = _direct_chromecast(ip, port, fake_uuid, "Chromecast", ip,
                              CAST_TYPE_CHROMECAST, None)
    try:
        cast.wait(timeout=10)
    except pychromecast.RequestTimeout:
        cast.disconnect()
        print(f"[FluxCast] ERROR: No Cast device responded at {ip}:{port}.")
        print("[FluxCast] Make sure the TV is ON, on the same network, "
              "and Cast is enabled in Settings → General → External Device Manager.")
        sys.exit(1)
    print(f"[FluxCast] Connected: {cast.cast_info.friendly_name}")
    return cast


def prompt_device(devices: list, device_name: Optional[str] = None):
    if not devices:
        print("[FluxCast] ERROR: No Cast devices found on the network.")
        print("[FluxCast] TIP: Use --tv-ip <IP> to connect directly, e.g.:")
        print("[FluxCast]      python main.py --protocol cast --tv-ip 192.168.100.XXX")
        sys.exit(1)

    if device_name is not None:
        needle = device_name.lower()
        for cc in devices:
            if (cc.cast_info.friendly_name or "").lower() == needle:
                return cc
        print(f"[FluxCast] WARNING: Device '{device_name}' not found; falling back to picker.")

    print("\n[FluxCast] Found Cast device(s):")
    for i, cc in enumerate(devices):
        name = cc.cast_info.friendly_name or cc.cast_info.host
        model = cc.cast_info.model_name or "Unknown"
        host = cc.cast_info.host
        print(f"  [{i}] {name}  ({model})  —  {host}")

    default_idx = 0
    for i, cc in enumerate(devices):
        if "samsung" in (cc.cast_info.model_name or "").lower():
            default_idx = i
            break

    raw = input(f"Select device [{default_idx}]: ").strip()
    try:
        idx = int(raw) if raw else default_idx
        return devices[idx]
    except (ValueError, IndexError):
        print(f"[FluxCast] Invalid choice, using device {default_idx}.")
        return devices[default_idx]


RECEIVER_LAUNCH_TIMEOUT = 20.0
MEDIA_SESSION_TIMEOUT = 20.0


def _wait_for_app(device, app_id: str, timeout: float,
                  sleep: Callable[[float], None] = time.sleep) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if device.app_id == app_id:
            return True
        sleep(0.25)
    return device.app_id == app_id


def _launch_media_receiver(device, sleep: Callable[[float], None] = time.sleep) -> bool:
    """Put Google's Default Media Receiver in front before sending LOAD.

    pychromecast only launches it when the running app lacks the media
    namespace. Apps that have it - a TV's own home screen or player, Spotify,
    YouTube - receive the LOAD instead and ignore it, so the TV never asks for
    the stream while FluxCast reported success (upstream #174).
    """
    if device.app_id == APP_MEDIA_RECEIVER:
        return True
    running = device.app_display_name or device.app_id or "nothing"
    print(f"[FluxCast] Starting the TV's media receiver (currently running: {running})…")
    try:
        device.start_app(APP_MEDIA_RECEIVER, timeout=RECEIVER_LAUNCH_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - pychromecast raises several kinds
        print(f"[FluxCast] WARNING: the TV did not confirm starting the media receiver "
              f"({type(exc).__name__}: {exc}).")
    if _wait_for_app(device, APP_MEDIA_RECEIVER, 10.0, sleep):
        return True
    print("[FluxCast] WARNING: the TV is still running "
          f"{device.app_display_name or device.app_id or 'another app'}; the stream "
          "may be ignored. Close other casting apps on the TV and try again.")
    return False


def start_cast(device, stream_url: str, sleep: Callable[[float], None] = time.sleep) -> bool:
    """Load the stream on the TV. Returns True once a media session is active."""
    try:
        device.wait(timeout=15)
    except pychromecast.RequestTimeout:
        print(f"[FluxCast] ERROR: {device.cast_info.friendly_name} did not answer on its "
              "Cast port within 15s.")
        return False
    device.register_connection_listener(_ConnectionListener())
    mc = device.media_controller
    mc.register_status_listener(_MediaListener())
    _launch_media_receiver(device, sleep)

    is_hls = stream_url.endswith(".m3u8")
    content_type = "application/x-mpegURL" if is_hls else "video/mpeg"
    # The receiver's HLS player defaults vary between Cast implementations
    # (Google TV, LG, Vizio); say outright that the segments are MPEG-TS.
    media_info = {"hlsVideoSegmentFormat": "mpeg2_ts"} if is_hls else None
    mc.play_media(stream_url, content_type, title="FluxCast", stream_type="LIVE",
                  media_info=media_info)
    mc.block_until_active(timeout=MEDIA_SESSION_TIMEOUT)
    if not mc.session_active_event.is_set():
        print(f"[FluxCast] ERROR: {device.cast_info.friendly_name} did not start a media "
              f"session within {MEDIA_SESSION_TIMEOUT:.0f}s, so it did not accept the stream.")
        return False
    print(f"[FluxCast] Cast started → {device.cast_info.friendly_name}")
    print("[FluxCast] Waiting for the TV to report playback status (watch lines below)…")
    return True


def stop_cast(device: Optional[object]) -> None:
    if device is None:
        return
    try:
        device.media_controller.stop()
        device.disconnect(timeout=5)
    except Exception:
        pass
