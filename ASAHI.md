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
   fluxcast --protocol dlna --transport hls
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

## Firewall

Fedora uses firewalld. For Miracast, FluxCast opens TCP port 7236 for the
length of a session and closes it afterwards. You may get a password prompt
for that the first time. If DLNA connects but the TV never starts playing,
allow FluxCast's HTTP port:

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
| No tray icon on GNOME | Install `gnome-shell-extension-appindicator`, log out and in |
| Device name not shown on the TV | Make sure you are in the `wheel` group: `id -nG` |
| `fluxcast` fails after a Fedora upgrade | Re-run `./install-asahi.sh` |

Upstream's full flag reference is in
[documentation/DOCUMENTATION.md](documentation/DOCUMENTATION.md).

## What has been tested

- The unit test suite (366 tests, including new ones for the OpenH264
  paths and the Apple Silicon `--doctor` row).
- The OpenH264 GStreamer pipelines FluxCast generates, run for real: the
  portal pipeline with a 2560×1600 source sent over RTP/UDP, plus gst-x11 and
  the DLNA HLS chain. They produce Constrained Baseline H.264 that decodes
  cleanly, with SPS/PPS on every keyframe and an access unit delimiter on every
  frame.
- `install-asahi.sh` and `uninstall-asahi.sh`: install, upgrade, reinstall and
  removal in a container with `dnf` stubbed out, and `shellcheck`.

Not yet tested: a real M1 Mac with a real TV, and in particular Wi-Fi Direct on
the built-in Broadcom chip. If you try it, `fluxcast --doctor` output from your
Mac is the most useful thing to report.
