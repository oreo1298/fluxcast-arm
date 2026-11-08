# FluxCast on Asahi Linux (Apple Silicon Macs)

This fork of [FluxCast](https://github.com/IlyaP358/fluxcast) runs on
**Fedora Asahi Remix** on Apple Silicon Macs (M1/M2, e.g. the M1 MacBook Air),
and on any other Fedora release, without needing third-party repositories.

FluxCast is pure Python, so the CPU architecture was never the problem. These
things were:

| Problem on Asahi / Fedora | What this fork does |
|---|---|
| Every pipeline needed **x264**, which Fedora only ships through RPM Fusion | Falls back to **OpenH264** (GStreamer `openh264enc`, ffmpeg `libopenh264`), which Fedora ships through its Cisco OpenH264 repository. New `--wfd-encoder openh264` option. |
| Apple Silicon has **no hardware H.264 encoder** under Linux (no VA-API encode) | `--wfd-encoder va` falls back to software automatically; CPU encoding is fast enough on M1 for 1080p30. |
| No Fedora install path (only .deb, AUR, AppImage for x86_64) | `install-asahi.sh`: one script that installs everything with `dnf` and `pip`. |
| `--doctor` assumed x264 | Accepts either encoder, and adds a **platform** row on Apple Silicon that names the model, the Wi-Fi driver and the page size, and explains the Wi-Fi Direct caveat below. |

## Install

Open a terminal (Konsole on KDE) and run:

```bash
git clone https://github.com/oreo1298/fluxcast-arm.git
```

```bash
cd fluxcast-arm
```

```bash
./install-asahi.sh
```

Run it as your normal user. It asks for your password through `sudo` when it
needs root, then:

1. checks that this is Fedora and shows the Mac model it detected
2. enables Fedora's `fedora-cisco-openh264` repository
3. **asks whether to enable RPM Fusion** for x264 and the full ffmpeg build.
   Saying yes is recommended: upstream FluxCast is tested on x264. Saying no
   keeps you on Fedora's own repositories, and FluxCast encodes with OpenH264.
4. installs the system packages (GStreamer, PipeWire capture, NetworkManager,
   wpa_supplicant, tray libraries and so on)
5. installs FluxCast into `/opt/fluxcast`, with `/usr/bin/fluxcast`, an app
   menu entry and the Wi-Fi Direct D-Bus policy
6. installs the Python packages Fedora does not ship (`upnpclient`,
   `pychromecast`, and others if missing) into `/opt/fluxcast/venv`
7. runs `fluxcast --doctor` so you can see straight away what works

Options:

| Option | Effect |
|---|---|
| `--rpmfusion` / `--no-rpmfusion` | Answer the RPM Fusion question up front |
| `-y`, `--yes` | Accept every default (including RPM Fusion) without asking |
| `--skip-packages` | Only (re)install FluxCast itself, no `dnf` |
| `--dry-run` | Print every command that would change the system, change nothing |

## Use it

From the app menu, start **FluxCast**. A tray icon appears; pick your TV
there. From a terminal:

```bash
fluxcast --tray
```

Or cast once from the terminal. It asks which screen and which TV:

```bash
fluxcast
```

On KDE Plasma and GNOME, the desktop shows a screen-sharing dialog the first
time. Pick the screen (or a window) to send to the TV.

On **GNOME**, tray icons only show up with the AppIndicator extension:

```bash
sudo dnf install gnome-shell-extension-appindicator
```

Then log out and back in.

## Check Wi-Fi Direct before your first cast

Miracast (WFD, FluxCast's main mode) needs **Wi-Fi Direct (P2P)** on your Wi-Fi
chip. M1/M2 Macs use a Broadcom chip driven by `brcmfmac`, and whether
Wi-Fi Direct works on that chip under Asahi is **not confirmed**. Run:

```bash
fluxcast --doctor
```

and look at these rows:

- `platform`: your Mac model, `wlan0=brcmfmac` and the 16K page size
- `iw P2P`: **ok** means the kernel offers Wi-Fi Direct. Any other status
  means it does not on this chip.
- `wpa_supplicant P2P`: whether the supplicant can drive it

To see the raw capability yourself, run this and look for `P2P-client`,
`P2P-GO` or `P2P-device` in the "Supported interface modes" list:

```bash
iw phy | grep -A 12 "Supported interface modes"
```

If Wi-Fi Direct is not available, you still have two options:

1. **DLNA over your normal Wi-Fi.** Most smart TVs support it and no Wi-Fi
   Direct is needed:

   ```bash
   fluxcast --protocol dlna
   ```

   Or Google Cast, on TVs that have it (LG's 2022 OLEDs got it with webOS 24):

   ```bash
   fluxcast --protocol cast
   ```

2. **A USB Wi-Fi adapter that supports P2P**, plugged in through a USB-C
   adapter, for Miracast. Point FluxCast at it with
   `--wfd-interface <name>`; `iw dev` lists the interface names. The upstream
   README lists adapters people have confirmed.

## Performance

Encoding runs on the CPU. An M1 handles 1080p30 comfortably with either
encoder. If the picture stutters or lags, lower the load:

```bash
fluxcast --output-res 1280x720 --fps 30
```

- The M1 Air's panel is 2560×1600 (16:10). FluxCast scales it to the TV's mode
  and adds thin bars at the top and bottom instead of stretching it.
- x264 gives a slightly better picture for the same bitrate; OpenH264 is the
  fallback. Pick one explicitly with `--wfd-encoder x264` or
  `--wfd-encoder openh264` (or in the tray's Settings window).
- Running on battery is fine; plug in for long sessions.

## LG TVs (and other TVs that are not Samsung) over DLNA and Cast

Upstream FluxCast's DLNA and Cast modes were only tested on Samsung TVs. On
LG webOS TVs such as the C2, the TV would find the stream and then give up
before showing anything. This fork fixes the causes found so far:

- **DLNA:** LG answers the "here is the stream" command only after it has
  opened the stream (up to ~15 s), then refuses "Play" with UPnP error 701
  while it is still getting ready. FluxCast used to send Play immediately and
  give up on the error, while still printing "Casting started". It now waits,
  retries until the TV is ready, and reports the TV's real state
  (`TV state: PLAYING`).
- **DLNA formats:** FluxCast asks the TV which formats it plays and labels the
  stream with one the TV lists. It falls back from HLS to progressive MPEG-TS
  when the TV does not list HLS.
- **Cast:** FluxCast now starts Google's Default Media Receiver on the TV
  before sending the stream. Otherwise the app already in front (the TV's own
  home screen, Spotify, YouTube...) receives it and ignores it (upstream
  issue #174).
- **Wrong address:** with a VPN or a second network, FluxCast used to give
  the TV an address it cannot reach. It now serves on the address that routes
  to the TV.
- **Silent failures:** after about 20 seconds FluxCast says whether the TV
  never fetched the stream (firewall, isolated guest Wi-Fi, VPN) or fetched it
  and stopped (format), and what to try.

Casts started from the tray log to `/tmp/fluxcast-cast.log`; from a terminal,
the same lines are printed there. If an LG TV still will not play, that log is
what to look at (or send).

## Firewall

Fedora uses firewalld. For Miracast, FluxCast opens TCP port 7236 for the
length of a session and closes it afterwards. You may get a password prompt
for that the first time. DLNA and Cast need the TV to fetch the stream from
FluxCast's HTTP port, 8080. If FluxCast says "The TV never requested the
stream", allow that port:

```bash
sudo firewall-cmd --add-port=8080/tcp
```

## Updating, Fedora upgrades, removing

Update to the latest version of this repository:

```bash
git pull && ./install-asahi.sh
```

Run `./install-asahi.sh` again after **upgrading Fedora to a new release**.
The Python packages in `/opt/fluxcast/venv` are tied to the Python version, and
the installer rebuilds them when that changes.

Remove FluxCast. Add `--purge` to also delete your settings; system packages
are left installed:

```bash
./uninstall-asahi.sh
```

## Troubleshooting

| Symptom | Try |
|---|---|
| `--doctor`: "portal backend is missing ... an H.264 encoder" | `sudo dnf install gstreamer1-plugin-openh264 openh264` (or re-run the installer) |
| OpenH264 errors such as "Failed to create encoder" | Fedora installed the `noopenh264` stub: `sudo dnf swap noopenh264 openh264` |
| TV connects but stays black | `fluxcast --wfd-aosp-pmt-pid` (Android-based TVs/boxes), or `--wfd-encoder x264` if you have RPM Fusion |
| DLNA/Cast: "The TV never requested the stream" | Open port 8080 (see Firewall), make sure the TV is not on a guest Wi-Fi, or pass `--host <this Mac's LAN IP>` |
| DLNA/Cast: TV fetched the stream, then stopped | Try `--transport progressive-ts` or `--transport hls`, and `--output-res 1280x720` |
| No tray icon on GNOME | Install `gnome-shell-extension-appindicator`, log out and in |
| Device name not shown on the TV | Make sure you are in the `wheel` group: `id -nG` |
| `fluxcast` fails after a Fedora upgrade | Re-run `./install-asahi.sh` |

Upstream's full flag reference is in
[documentation/DOCUMENTATION.md](documentation/DOCUMENTATION.md).

## What has been tested

- The unit test suite (391 tests, including new ones for the OpenH264
  paths, the Apple Silicon `--doctor` row and the DLNA/Cast start-up).
- The DLNA fixes, run end to end: FluxCast's real `main()` against a fake
  renderer that behaves the way LG webOS is reported to (slow answer to the
  stream URL, UPnP 701 while transitioning, strict format matching). The old
  code reproduces "connects, then nothing plays"; the new code reaches
  PLAYING and the fake TV reads the stream continuously. The same harness
  checks the HLS-to-TS fallback, serving on the right address behind a
  "VPN", and the explanation when the TV cannot reach the computer.
- The OpenH264 GStreamer pipelines FluxCast generates, run for real: the
  portal pipeline with a 2560×1600 source sent over RTP/UDP, plus gst-x11 and
  the DLNA HLS chain. They produce Constrained Baseline H.264 that decodes
  cleanly, with SPS/PPS on every keyframe and an access unit delimiter on every
  frame.
- `install-asahi.sh` and `uninstall-asahi.sh`: install, upgrade, reinstall and
  removal in a container with `dnf` stubbed out, and `shellcheck`.

Not yet tested: a real M1 Mac with a real TV (including a real LG C2 over
DLNA and Cast), and in particular Wi-Fi Direct on the built-in Broadcom chip. If you try it, `fluxcast --doctor` output from your
Mac is the most useful thing to report.
