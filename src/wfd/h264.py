"""Software H.264 encoder selection: x264 when it is installed, OpenH264 when not.

Every pipeline was written against x264. Stock Fedora - and so Fedora Asahi
Remix on Apple Silicon Macs - ships neither GStreamer's x264enc nor ffmpeg's
libx264; both live in RPM Fusion. What Fedora does ship out of the box is
OpenH264, through the fedora-cisco-openh264 repository: GStreamer's
openh264enc (gstreamer1-plugin-openh264) and the libopenh264 encoder in
ffmpeg-free. Apple Silicon also has no VA-API encode driver, so
--wfd-encoder va always ends up on a software encoder there.

OpenH264 encodes Constrained Baseline, which is the only profile FluxCast
advertises in M4 (modes._choose_profile), so it is a drop-in for WFD. It
writes SPS/PPS in front of every IDR on its own, and both muxers FluxCast uses
(ffmpeg's mpegts and GStreamer's mpegtsmux) add the access unit delimiters
that x264 is asked for with aud=1.
"""
import re
import shutil
import subprocess
from typing import Optional

from .config import WFDMediaConfig
from .encoding import _x264_threads
from .gst import _gst_has_element
from .modes import _h264_level_for_mode


# Encoder family -> GStreamer element / ffmpeg codec. Keep the element order in
# step with diagnostics.PORTAL_GST_H264_ENCODERS, which --doctor reports from.
_GST_ELEMENTS = {"x264": "x264enc", "openh264": "openh264enc"}
_FFMPEG_CODECS = {"x264": "libx264", "openh264": "libopenh264"}


def _software_order(preference: str) -> tuple[str, ...]:
    """Encoder families to try, the requested one first.

    "va" is not a software family; when VA-API is missing it falls back the
    same way the default does, x264 first.
    """
    if preference == "openh264":
        return ("openh264", "x264")
    return ("x264", "openh264")


def _gst_pick_h264_encoder(preference: str = "x264") -> Optional[str]:
    """The GStreamer software H.264 element to use, or None if neither exists."""
    for family in _software_order(preference):
        element = _GST_ELEMENTS[family]
        if _gst_has_element(element):
            return element
    return None


def _gst_h264_caps_profile(element: str, profile: str) -> str:
    """Profile name for the caps filter after `element`.

    x264enc's "baseline" output is Constrained Baseline but is labelled
    baseline; openh264enc (like vah264enc) negotiates the exact name.
    """
    if element != "x264enc" and profile == "baseline":
        return "constrained-baseline"
    return profile


def _gst_openh264enc_args(bitrate_kbits: int, gop: int, height: int) -> list[str]:
    """openh264enc settings mirroring what the x264enc pipelines ask for.

    Only properties present since GStreamer 1.20 are used (Ubuntu 24.04's 1.24
    lacks scene-change-detection, for one). bitrate is in bit/s here, not
    kbit/s as on x264enc. Frame skipping stays off: a dropped frame on a WFD
    link shows up as a stutter, an overshoot only as a few extra kilobytes.
    One slice per thread, like x264's sliced threads, also confines a lost
    packet to a band of the picture.
    """
    threads = _x264_threads()
    bitrate = max(1, bitrate_kbits) * 1000
    return [
        "openh264enc",
        f"bitrate={bitrate}",
        f"max-bitrate={bitrate}",
        "rate-control=bitrate",
        "enable-frame-skip=false",
        f"gop-size={gop}",
        f"multi-thread={threads}",
        "slice-mode=n-slices",
        f"num-slices={threads}",
        f"complexity={'low' if height > 1080 else 'medium'}",
    ]


def _gst_h264_encoder_chain(element: str, bitrate_kbits: int, gop: int,
                            height: int) -> list[str]:
    """Encoder element plus settings for the fixed-source gst pipelines.

    The gst-x11 backend and the gst test pattern share one x264enc setup; the
    portal backend builds its own because it probes x264enc's properties.
    """
    if element == "openh264enc":
        return _gst_openh264enc_args(bitrate_kbits, gop, height)
    return [
        "x264enc",
        "tune=zerolatency",
        f"speed-preset={'ultrafast' if height > 1080 else 'veryfast'}",
        f"bitrate={bitrate_kbits}",
        f"key-int-max={gop}",
        "bframes=0",
        "byte-stream=true",
        "aud=true",
        f"threads={_x264_threads()}",
        "sliced-threads=true",
        "vbv-buf-capacity=200",
    ]


_ffmpeg_list_cache: dict[str, Optional[frozenset[str]]] = {}


def _ffmpeg_encoders() -> Optional[frozenset[str]]:
    """Encoder names `ffmpeg -encoders` lists; None when it cannot be asked."""
    if "encoders" in _ffmpeg_list_cache:
        return _ffmpeg_list_cache["encoders"]
    names: Optional[frozenset[str]] = None
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        try:
            result = subprocess.run(
                [ffmpeg, "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                timeout=5.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0:
            # A legend (" V..... = Video") comes first, then " ------", then
            # one " V....D libx264    libx264 H.264 / AVC ..." line per
            # encoder: six flag characters and the name.
            _, separator, listing = result.stdout.partition("------")
            names = frozenset(
                match.group(1)
                for match in re.finditer(r"^\s*[VAS.][F.][S.][X.][B.][D.]\s+(\S+)",
                                         listing if separator else result.stdout, re.M)
                if match.group(1) != "="
            ) or None
    _ffmpeg_list_cache["encoders"] = names
    return names


def _ffmpeg_h264_encoder(preference: str = "x264") -> str:
    """The ffmpeg software H.264 encoder to use.

    When ffmpeg cannot be asked, keep the requested family (x264 by default)
    so the failure, if any, reads the same as it always has.
    """
    order = _software_order(preference)
    available = _ffmpeg_encoders()
    if available is None:
        return _FFMPEG_CODECS[order[0]]
    for family in order:
        codec = _FFMPEG_CODECS[family]
        if codec in available:
            return codec
    return _FFMPEG_CODECS[order[0]]


def _ffmpeg_video_encoder_args(
    config: WFDMediaConfig,
    *,
    height: int,
    gop: int,
    bitrate: str,
    bufsize: str,
    encoder: Optional[str] = None,
) -> list[str]:
    """ffmpeg output options for the WFD H.264 video stream.

    libx264 gets exactly the options every ffmpeg pipeline has always used.
    libopenh264 has no preset/tune/x264-params and picks the level itself
    from the frame size and rate, which lands on the same level
    _h264_level_for_mode would ask for.
    """
    encoder = encoder or _ffmpeg_h264_encoder(config.encoder)
    if encoder == "libopenh264":
        threads = _x264_threads()
        profile = {"baseline": "constrained_baseline"}.get(
            config.h264_profile, config.h264_profile)
        return [
            "-c:v", "libopenh264",
            "-profile:v", profile,
            "-threads", str(threads),
            "-slices", str(threads),
            "-rc_mode", "bitrate",
            "-allow_skip_frames", "0",
            "-pix_fmt", "yuv420p",
            "-r", str(config.fps),
            "-g", str(gop),
            "-b:v", bitrate,
            "-maxrate", bitrate,
        ]
    return [
        "-c:v", "libx264",
        "-preset", "ultrafast" if height > 1080 else "veryfast",
        "-tune", "zerolatency",
        "-threads", str(_x264_threads()),
        "-profile:v", config.h264_profile,
        "-level:v", _h264_level_for_mode(config),
        "-pix_fmt", "yuv420p",
        "-r", str(config.fps),
        "-g", str(gop),
        "-keyint_min", str(gop),
        "-sc_threshold", "0",
        "-bf", "0",
        "-b:v", bitrate,
        "-maxrate", bitrate,
        "-bufsize", bufsize,
        "-x264-params", "repeat-headers=1:aud=1",
    ]
