#!/usr/bin/env bash
# install-asahi.sh - install FluxCast on Fedora Asahi Remix (Apple Silicon Macs).
#
# Works on any other Fedora too (aarch64 or x86_64). Run it from a clone, as
# your normal user - it asks for your password through sudo when it needs it:
#
#     git clone https://github.com/oreo1298/fluxcast-arm.git
#     cd fluxcast-arm
#     ./install-asahi.sh
#
# What it does, in order:
#   1. checks this is Fedora and that sudo works
#   2. enables Fedora's Cisco OpenH264 repository (an H.264 encoder that needs
#      no third-party repository)
#   3. optionally enables RPM Fusion (free) for x264 and the full ffmpeg -
#      upstream FluxCast is tested on x264; asked unless you pass
#      --rpmfusion / --no-rpmfusion
#   4. installs the system packages FluxCast uses with dnf
#   5. copies FluxCast to /opt/fluxcast and adds /usr/bin/fluxcast, the app
#      menu entry, the icon and the wpa_supplicant D-Bus policy (meta/install.sh)
#   6. puts the Python packages Fedora does not ship into /opt/fluxcast/venv
#   7. reloads D-Bus and runs `fluxcast --doctor`
#
# Re-run it to update after a `git pull`, or after upgrading Fedora to a new
# release (the venv is tied to the Python version). ./uninstall-asahi.sh
# removes everything it installed.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPDIR=/opt/fluxcast
VENV="$APPDIR/venv"

ASSUME_YES=0
RPMFUSION=ask        # ask | yes | no
SKIP_PACKAGES=0
DRY_RUN=0
FORCE=0

usage() {
    cat <<'EOF'
Usage: ./install-asahi.sh [options]

  -y, --yes          answer yes to every question (enables RPM Fusion unless
                     --no-rpmfusion is also given)
      --rpmfusion    enable RPM Fusion free and use x264 + full ffmpeg
      --no-rpmfusion stay on Fedora's own repositories (OpenH264 encoder)
      --skip-packages do not install system packages, only FluxCast itself
      --dry-run      print the commands that change the system instead of
                     running them
      --force        continue on a distribution that is not Fedora
  -h, --help         show this help
EOF
}

while (($#)); do
    case "$1" in
        -y|--yes) ASSUME_YES=1 ;;
        --rpmfusion) RPMFUSION=yes ;;
        --no-rpmfusion) RPMFUSION=no ;;
        --skip-packages) SKIP_PACKAGES=1 ;;
        --dry-run) DRY_RUN=1 ;;
        --force) FORCE=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

if [[ -t 1 ]]; then
    BOLD=$'\e[1m'; RED=$'\e[31m'; GREEN=$'\e[32m'; YELLOW=$'\e[33m'; BLUE=$'\e[34m'; RESET=$'\e[0m'
else
    BOLD=""; RED=""; GREEN=""; YELLOW=""; BLUE=""; RESET=""
fi

step() { printf '\n%s==>%s %s%s%s\n' "$BLUE$BOLD" "$RESET" "$BOLD" "$*" "$RESET"; }
info() { printf '    %s\n' "$*"; }
ok()   { printf '    %sok%s %s\n' "$GREEN" "$RESET" "$*"; }
warn() { printf '    %swarning:%s %s\n' "$YELLOW$BOLD" "$RESET" "$*" >&2; WARNINGS+=("$*"); }
die()  { printf '\n%serror:%s %s\n' "$RED$BOLD" "$RESET" "$*" >&2; exit 1; }
WARNINGS=()

# Run a command as root (directly when already root, else through sudo).
# With --dry-run it is only printed.
as_root() {
    local shown
    printf -v shown '%q ' "$@"
    if ((DRY_RUN)); then
        printf '    [dry-run] %s\n' "${shown% }"
        return 0
    fi
    printf '    + %s\n' "${shown% }"
    if ((EUID == 0)); then
        "$@"
    else
        sudo "$@"
    fi
}

ask() {  # ask "question" default(Y|N)
    local prompt="$1" default="$2" reply
    if ((ASSUME_YES)); then
        return 0
    fi
    if [[ ! -t 0 ]]; then
        [[ "$default" == Y ]]
        return
    fi
    if [[ "$default" == Y ]]; then
        read -r -p "    $prompt [Y/n] " reply || reply=""
        [[ -z "$reply" || "$reply" =~ ^[Yy] ]]
    else
        read -r -p "    $prompt [y/N] " reply || reply=""
        [[ "$reply" =~ ^[Yy] ]]
    fi
}

have_rpm() { rpm -q "$1" >/dev/null 2>&1; }

# The user FluxCast is being installed for: whoever ran sudo, if anyone.
if ((EUID == 0)) && [[ -n "${SUDO_USER:-}" ]]; then
    TARGET_USER="$SUDO_USER"
else
    TARGET_USER="$(id -un)"
fi

run_as_user() {
    if ((EUID == 0)) && [[ "$TARGET_USER" != root ]]; then
        sudo -u "$TARGET_USER" -H "$@"
    else
        "$@"
    fi
}

# ---------------------------------------------------------------------------
step "Checking this system"

[[ -f "$REPO/src/main.py" && -f "$REPO/meta/install.sh" ]] \
    || die "run this script from a FluxCast checkout (src/main.py not found next to it)"

if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
fi
case " ${ID:-} ${ID_LIKE:-} " in
    *" fedora "*) ok "${PRETTY_NAME:-Fedora}" ;;
    *)
        if ((FORCE)); then
            warn "${PRETTY_NAME:-this distribution} is not Fedora; continuing because of --force"
        else
            die "${PRETTY_NAME:-this distribution} is not Fedora. This installer uses dnf;
       see README.md for other distributions, or pass --force to try anyway."
        fi
        ;;
esac

ARCH="$(uname -m)"
APPLE=0
if [[ -r /proc/device-tree/compatible ]] && tr '\0' '\n' </proc/device-tree/compatible | grep -q '^apple,'; then
    APPLE=1
    MODEL="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
    ok "${MODEL:-Apple Silicon Mac} ($ARCH, $(($(getconf PAGESIZE) / 1024))K pages)"
else
    info "architecture: $ARCH (not an Apple Silicon Mac - that is fine, any Fedora works)"
fi

if ((SKIP_PACKAGES == 0 && DRY_RUN == 0)); then
    command -v dnf >/dev/null 2>&1 || die "dnf not found"
fi
if ((SKIP_PACKAGES)); then
    command -v python3 >/dev/null 2>&1 || die "python3 not found (and --skip-packages was given)"
fi

if ((EUID != 0)) && ((DRY_RUN == 0)); then
    command -v sudo >/dev/null 2>&1 || die "sudo not found; run this script as root instead"
    info "some steps need root; sudo may ask for your password"
    sudo -v || die "sudo failed"
fi

DNF5=0
if dnf --version 2>/dev/null | grep -qi dnf5; then
    DNF5=1
fi
if ((DNF5)); then
    SKIP_UNAVAILABLE=(--skip-unavailable)
    CONFIG_MANAGER_PKG=dnf5-plugins
else
    SKIP_UNAVAILABLE=(--setopt=strict=0)
    CONFIG_MANAGER_PKG=dnf-plugins-core
fi

# ---------------------------------------------------------------------------
if ((SKIP_PACKAGES)); then
    step "Skipping system packages (--skip-packages)"
else
    step "Enabling the Fedora Cisco OpenH264 repository"
    if dnf repolist --enabled 2>/dev/null | grep -q '^fedora-cisco-openh264'; then
        ok "already enabled"
    else
        enabled=0
        for attempt in 1 2; do
            if ((DNF5)); then
                as_root dnf config-manager setopt fedora-cisco-openh264.enabled=1 && enabled=1
            else
                as_root dnf config-manager --set-enabled fedora-cisco-openh264 && enabled=1
            fi
            ((enabled)) && break
            # config-manager lives in a plugin package that may be missing.
            if ((attempt == 1)); then
                as_root dnf install -y "$CONFIG_MANAGER_PKG" || true
            fi
        done
        ((enabled)) || warn "could not enable fedora-cisco-openh264; the OpenH264 encoder may be unavailable"
    fi

    step "RPM Fusion (x264 encoder and full ffmpeg)"
    if have_rpm rpmfusion-free-release; then
        ok "RPM Fusion free is already enabled"
        [[ "$RPMFUSION" == no ]] || RPMFUSION=yes
    fi
    if [[ "$RPMFUSION" == ask ]]; then
        info "Fedora itself ships OpenH264, which FluxCast now falls back to. Upstream"
        info "FluxCast is tested on x264, which comes from the RPM Fusion repository."
        if ask "Enable RPM Fusion free and install x264 + full ffmpeg? (recommended)" Y; then
            RPMFUSION=yes
        else
            RPMFUSION=no
        fi
    fi
    if [[ "$RPMFUSION" == yes ]]; then
        if ! have_rpm rpmfusion-free-release; then
            as_root dnf install -y \
                "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm" \
                || die "could not enable RPM Fusion; re-run with --no-rpmfusion to use OpenH264 instead"
        fi
        if have_rpm ffmpeg-free; then
            # RPM Fusion's own documented way to replace Fedora's ffmpeg build.
            as_root dnf swap -y ffmpeg-free ffmpeg --allowerasing \
                || warn "could not swap ffmpeg-free for RPM Fusion's ffmpeg; OpenH264 will be used by ffmpeg"
        elif ! have_rpm ffmpeg; then
            as_root dnf install -y ffmpeg || warn "could not install RPM Fusion's ffmpeg"
        fi
        as_root dnf install -y gstreamer1-plugins-ugly \
            || warn "could not install gstreamer1-plugins-ugly (x264enc); OpenH264 will be used"
    else
        info "staying on Fedora's repositories; FluxCast will encode with OpenH264"
    fi

    step "Installing system packages"
    REQUIRED=(
        python3 python3-gobject gtk3 glib2
        gstreamer1 gstreamer1-plugins-base gstreamer1-plugins-good gstreamer1-plugins-bad-free
        NetworkManager wpa_supplicant iw
    )
    # ffmpeg-free conflicts with RPM Fusion's ffmpeg; only ask for it when no
    # ffmpeg is installed at all.
    if ! command -v ffmpeg >/dev/null 2>&1 && ! have_rpm ffmpeg; then
        REQUIRED+=(ffmpeg-free)
    fi
    OPTIONAL=(
        # Wi-Fi Direct: P2P group DHCP, wifi plugin, ip(8)
        NetworkManager-wifi dnsmasq iproute
        # capture: KDE/GNOME portal, PipeWire into GStreamer, wlroots (Sway), X11
        xdg-desktop-portal pipewire-gstreamer wf-recorder xrandr
        # audio: pactl for the monitor source; AAC encoder
        pulseaudio-utils gstreamer1-plugin-libav
        # H.264 without RPM Fusion
        gstreamer1-plugin-openh264
        # tray icon, notifications, settings window
        python3-cairo libappindicator-gtk3 libayatana-appindicator-gtk3 libnotify python3-tkinter
        # Python packages Fedora does ship, so pip has less to fetch
        python3-pip python3-pillow python3-dbus-next python3-xlib python3-lxml python3-requests
        python3-zeroconf python3-protobuf python3-ifaddr python3-dateutil
    )
    as_root dnf install -y "${REQUIRED[@]}" || die "installing the required packages failed"
    as_root dnf install -y "${SKIP_UNAVAILABLE[@]}" "${OPTIONAL[@]}" \
        || warn "some optional packages failed to install; --doctor below shows what is missing"

    # Fedora resolves libopenh264 to a no-op stub (noopenh264) unless the real
    # library from the Cisco repository is installed.
    if ! have_rpm openh264; then
        if have_rpm noopenh264; then
            as_root dnf swap -y noopenh264 openh264 \
                || warn "could not replace the noopenh264 stub with openh264"
        else
            as_root dnf install -y openh264 \
                || warn "could not install openh264 from fedora-cisco-openh264"
        fi
    fi
fi

# ---------------------------------------------------------------------------
step "Installing FluxCast to $APPDIR"
VERSION_STAMP="$(git -C "$REPO" describe --tags --always 2>/dev/null || echo unknown)"
VERSION_STAMP="${VERSION_STAMP#v} (fluxcast-arm, branch: $(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown))"
if [[ -f "$APPDIR/main.py" ]]; then
    # Upgrade: drop the old code so removed modules do not linger; keep the venv.
    as_root find "$APPDIR" -mindepth 1 -maxdepth 1 ! -name venv -exec rm -rf {} +
fi
# The stamp is computed here, as you, because git refuses to read a checkout
# owned by another user when run as root.
as_root env SRCDIR="$REPO" FLUXCAST_VERSION="$VERSION_STAMP" bash "$REPO/meta/install.sh"

# ---------------------------------------------------------------------------
step "Installing Python packages into $VENV"
PYVER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if [[ -x "$VENV/bin/python3" ]] \
        && [[ "$("$VENV/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" == "$PYVER" ]]; then
    ok "reusing the existing venv (Python $PYVER)"
else
    if [[ -e "$VENV" ]]; then
        info "the venv was made for another Python version; recreating it"
        as_root rm -rf "$VENV"
    fi
    # --system-site-packages: PyGObject and anything dnf installed above come
    # from Fedora; pip only adds what is missing.
    as_root python3 -m venv --system-site-packages "$VENV"
fi

CORE_REQS=()
EXTRA_REQS=()
while IFS= read -r line; do
    line="${line%%#*}"
    line="${line//[[:space:]]/}"
    [[ -z "$line" ]] && continue
    case "${line,,}" in
        pygobject*) ;;                                 # from python3-gobject
        upnpclient*|pychromecast*) EXTRA_REQS+=("$line") ;;  # DLNA / Cast only
        *) CORE_REQS+=("$line") ;;
    esac
done <"$REPO/requirements.txt"

PIP=("$VENV/bin/python3" -m pip install --disable-pip-version-check --no-cache-dir
     --root-user-action=ignore)
as_root "${PIP[@]}" "${CORE_REQS[@]}" \
    || die "pip could not install ${CORE_REQS[*]} (needed for screen capture and the tray)"
as_root "${PIP[@]}" "${EXTRA_REQS[@]}" \
    || warn "pip could not install ${EXTRA_REQS[*]}; DLNA/Cast will not work, Miracast (WFD) still will"
if command -v restorecon >/dev/null 2>&1; then
    as_root restorecon -R "$APPDIR" || true
fi

# ---------------------------------------------------------------------------
step "Finishing up"
# dbus-daemon/dbus-broker only reads the new wpa_supplicant policy on reload.
as_root systemctl reload dbus || warn "could not reload D-Bus; reboot before casting"
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    as_root gtk-update-icon-cache -q -f /usr/share/icons/hicolor || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    as_root update-desktop-database -q /usr/share/applications || true
fi

if id -nG "$TARGET_USER" 2>/dev/null | tr ' ' '\n' | grep -qx wheel; then
    ok "$TARGET_USER is in the wheel group (needed for the Wi-Fi Direct D-Bus policy)"
else
    warn "$TARGET_USER is not in the wheel group, so FluxCast cannot set the Wi-Fi Direct device name; fix with: sudo usermod -aG wheel $TARGET_USER (then log out and back in)"
fi
if command -v systemctl >/dev/null 2>&1 && ! systemctl is-active --quiet NetworkManager 2>/dev/null; then
    warn "NetworkManager is not running; Miracast (WFD) needs it"
fi

if ((DRY_RUN == 0)); then
    step "Checking the result (fluxcast --doctor)"
    run_as_user /usr/bin/fluxcast --doctor || true
fi

if ((DRY_RUN)); then
    printf '\n%sDry run finished; nothing was changed.%s\n\n' "$GREEN$BOLD" "$RESET"
else
    printf '\n%sFluxCast is installed.%s\n\n' "$GREEN$BOLD" "$RESET"
fi
cat <<EOF
  Start it from your app menu ("FluxCast"), or from a terminal:

      fluxcast --tray                              # tray icon
      fluxcast                                     # Miracast (WFD), pick screen and TV
      fluxcast --protocol dlna --transport hls     # DLNA over your normal Wi-Fi

  Re-check your setup at any time with:  fluxcast --doctor
  Update later with:                      git pull && ./install-asahi.sh
  Remove with:                            ./uninstall-asahi.sh
EOF
if ((APPLE)); then
    cat <<'EOF'

  Apple Silicon notes (see ASAHI.md):
    - Encoding runs on the CPU (x264 or OpenH264); Asahi has no hardware encoder.
    - Miracast needs Wi-Fi Direct. If the "iw P2P" row above is not "ok", the
      built-in Broadcom Wi-Fi does not offer it under Asahi: use DLNA, or a
      USB Wi-Fi adapter that supports P2P.
EOF
fi
if ((${#WARNINGS[@]})); then
    printf '\n%sWarnings:%s\n' "$YELLOW$BOLD" "$RESET"
    for w in "${WARNINGS[@]}"; do
        printf '  - %s\n' "$w"
    done
fi
