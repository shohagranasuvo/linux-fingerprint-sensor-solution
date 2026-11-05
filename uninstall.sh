#!/usr/bin/env bash
# Goodix GM168 Linux Driver - Uninstaller
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "⚠️ Administrative privileges required. Re-running with sudo..."
    exec sudo bash "$0" "$@"
fi

echo "Removing Goodix GM168 DRFM Biometric Service..."

# Revert PAM configuration
if command -v pam-auth-update >/dev/null 2>&1; then
    pam-auth-update --disable goodix-drfm --package 2>/dev/null || true
    rm -f /usr/share/pam-configs/goodix-drfm
    pam-auth-update --package || true
fi

# Remove system files
rm -f /usr/local/bin/pam_goodix_verify.py
rm -rf /usr/local/lib/goodix-gm168
rm -f /etc/udev/rules.d/60-goodix-gm168.rules
rm -f /usr/share/applications/goodix-fingerprint.desktop
rm -f /home/*/Desktop/Goodix-Fingerprint.desktop

udevadm control --reload-rules || true
udevadm trigger || true

echo "✓ Uninstallation complete. System restored to standard configuration."
