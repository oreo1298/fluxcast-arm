#!/usr/bin/env bash
# uninstall-asahi.sh - remove what install-asahi.sh installed.
#
# Removes /opt/fluxcast (including its Python venv), /usr/bin/fluxcast, the app
# menu entry, the icon and the wpa_supplicant D-Bus policy. System packages,
# RPM Fusion and the OpenH264 repository are left alone, since other software
# may use them; the commands to remove those are printed at the end.
# Your tray settings in ~/.config/fluxcast are kept unless you pass --purge.

set -euo pipefail

ASSUME_YES=0
PURGE=0
while (($#)); do
    case "$1" in
        -y|--yes) ASSUME_YES=1 ;;
        --purge) PURGE=1 ;;
        -h|--help)
            echo "Usage: ./uninstall-asahi.sh [-y|--yes] [--purge]"
            echo "  --purge  also delete your settings in ~/.config/fluxcast"
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

as_root() {
    local shown
    printf -v shown '%q ' "$@"
    printf '    + %s\n' "${shown% }"
    if ((EUID == 0)); then "$@"; else sudo "$@"; fi
}

PATHS=(
    /opt/fluxcast
    /usr/bin/fluxcast
    /usr/share/applications/fluxcast.desktop
    /usr/share/icons/hicolor/512x512/apps/fluxcast.png
    /usr/share/dbus-1/system.d/zz-dev.fluxcast.wpa-supplicant.conf
)

if [[ -n "${SUDO_USER:-}" ]]; then
    USER_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
else
    USER_HOME="$HOME"
fi
CONFIG_DIR="${USER_HOME}/.config/fluxcast"

present=()
for path in "${PATHS[@]}"; do
    if [[ -e "$path" ]]; then
        present+=("$path")
    fi
done
if ((PURGE)) && [[ -d "$CONFIG_DIR" ]]; then
    present+=("$CONFIG_DIR")
fi

if ((${#present[@]} == 0)); then
    echo "FluxCast does not appear to be installed; nothing to remove."
    exit 0
fi

echo "This removes:"
printf '    %s\n' "${present[@]}"
if ((ASSUME_YES == 0)); then
    read -r -p "Continue? [y/N] " reply || reply=""
    [[ "$reply" =~ ^[Yy] ]] || { echo "Nothing removed."; exit 0; }
fi

for path in "${present[@]}"; do
    if [[ "$path" == "$CONFIG_DIR" ]]; then
        rm -rf -- "$CONFIG_DIR"     # yours, no root needed
        echo "    removed $CONFIG_DIR"
    else
        as_root rm -rf -- "$path"
    fi
done

as_root systemctl reload dbus 2>/dev/null || true
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    as_root gtk-update-icon-cache -q -f /usr/share/icons/hicolor || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    as_root update-desktop-database -q /usr/share/applications || true
fi

echo
echo "FluxCast is removed."
if ((PURGE == 0)) && [[ -d "$CONFIG_DIR" ]]; then
    echo "Your settings are still in $CONFIG_DIR (re-run with --purge to delete them)."
fi
cat <<'EOF'

The system packages it installed were left in place. To remove the ones only
FluxCast needed, for example:

    sudo dnf remove gstreamer1-plugin-openh264 wf-recorder

and, if you enabled RPM Fusion only for FluxCast:

    sudo dnf swap ffmpeg ffmpeg-free --allowerasing
    sudo dnf remove gstreamer1-plugins-ugly rpmfusion-free-release
EOF
