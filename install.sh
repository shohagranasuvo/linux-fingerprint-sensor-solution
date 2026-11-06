#!/usr/bin/env bash
# ==============================================================================
# Goodix GM168 Linux Driver & DRFM Biometric Suite - Automated Installer
# Supports: Xiaomi RedmiBook Pro 14S / 15, Xiaomi Mi NoteBook Ultra
# USB Devices: 27c6:581a, 27c6:589a
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || echo "")"
TEMP_CLONE=""

# Ensure script runs with root privileges
if [[ $EUID -ne 0 ]]; then
    echo "⚠️  This installer requires administrative privileges."
    echo "   Re-running with sudo..."
    exec sudo bash "$0" "$@"
fi

# Determine target interactive user
TARGET_USER="${SUDO_USER:-}"
if [[ -z "$TARGET_USER" || "$TARGET_USER" == "root" ]]; then
    # Discover the primary active non-root desktop user
    TARGET_USER="$(logname 2>/dev/null || who | awk '{print $1}' | head -n1 || echo "")"
    if [[ -z "$TARGET_USER" || "$TARGET_USER" == "root" ]]; then
        TARGET_USER="$(ls -d /home/* 2>/dev/null | head -n1 | xargs -r basename || echo "root")"
    fi
fi
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"

echo "================================================================="
echo "   Goodix GM168 DRFM Biometric Driver & PAM Engine Installer    "
echo "   (Zero-FAR Windows Hello Enclave Parity for Linux)            "
echo "================================================================="
echo "Target User: ${TARGET_USER} (Home: ${TARGET_HOME})"

# If executed via pipe (curl | bash), clone repository into temporary directory
if [[ -z "$SCRIPT_DIR" || ! -f "${SCRIPT_DIR}/src/dense_matcher.py" ]]; then
    echo "📥 Downloading driver package from GitHub..."
    TEMP_CLONE="$(mktemp -d /tmp/goodix-gm168-XXXXXX)"
    git clone --depth 1 https://github.com/shohagranasuvo/goodix-gm168-linux-driver.git "$TEMP_CLONE"
    SCRIPT_DIR="$TEMP_CLONE"
    trap 'rm -rf "$TEMP_CLONE"' EXIT
fi

# 1. Install prerequisites via apt
echo "[1/7] Installing system dependencies..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq || true

DEPS=()
for pkg in python3 python3-pip python3-gi gir1.2-gtk-4.0 gir1.2-gdkpixbuf-2.0 libglib2.0-0 libglib2.0-0t64 libgusb2 libgusb2a libpixman-1-0 libssl3 libssl3t64 libpam-runtime udev; do
    if apt-cache policy "$pkg" 2>/dev/null | grep -E -q "Candidate:\s+[0-9]"; then
        DEPS+=("$pkg")
    fi
done

if [[ ${#DEPS[@]} -gt 0 ]]; then
    apt-get install -y -qq "${DEPS[@]}" >/dev/null 2>&1 || apt-get install -y "${DEPS[@]}" || true
fi

# Ensure opencv and numpy are available
if ! python3 -c "import cv2, numpy" >/dev/null 2>&1; then
    apt-get install -y -qq python3-opencv python3-numpy >/dev/null 2>&1 || true
fi

# 2. Create system directories
echo "[2/7] Creating system directories..."
mkdir -p /usr/local/lib/goodix-gm168 \
         /var/lib/goodix-gm168/templates \
         /etc/goodix-gm168 \
         /usr/share/pam-configs \
         /etc/udev/rules.d \
         /usr/share/applications

chmod 755 /usr/local/lib/goodix-gm168 /etc/goodix-gm168 /var/lib/goodix-gm168

# 3. Install core libraries and Python modules
echo "[3/7] Deploying DRFM modules, capture binaries, and libraries..."
install -m 755 "${SCRIPT_DIR}/src/dense_matcher.py" /usr/local/lib/goodix-gm168/dense_matcher.py
install -m 755 "${SCRIPT_DIR}/src/goodix_daemon.py" /usr/local/lib/goodix-gm168/goodix_daemon.py
install -m 755 "${SCRIPT_DIR}/src/fingerprint_gui.py" /usr/local/lib/goodix-gm168/fingerprint_gui.py
install -m 755 "${SCRIPT_DIR}/src/pam_goodix_verify.py" /usr/local/lib/goodix-gm168/pam_goodix_verify.py
install -m 755 "${SCRIPT_DIR}/src/pam_goodix_verify.py" /usr/local/bin/pam_goodix_verify.py

if [[ -f "${SCRIPT_DIR}/bin/img-capture" ]]; then
    install -m 755 "${SCRIPT_DIR}/bin/img-capture" /usr/local/lib/goodix-gm168/img-capture
fi
if ls "${SCRIPT_DIR}/bin"/libfprint-2.so* >/dev/null 2>&1; then
    cp -d "${SCRIPT_DIR}/bin"/libfprint-2.so* /usr/local/lib/goodix-gm168/
fi

# Ensure clean system Python delegation for OpenCV / NumPy
rm -rf /usr/local/lib/goodix-gm168/cv2 /usr/local/lib/goodix-gm168/numpy
for u in /home/*; do
    for v in python3.14 python3.13 python3.12 python3.11 python3.10; do
        if [[ -d "$u/.local/lib/$v/site-packages" ]] && [[ -d "/usr/local/lib/$v/dist-packages" ]]; then
            echo "$u/.local/lib/$v/site-packages" > "/usr/local/lib/$v/dist-packages/goodix-deps.pth" 2>/dev/null || true
        fi
    done
done

# 4. Install udev access rules
echo "[4/7] Configuring USB udev access permissions..."
if [[ -f "${SCRIPT_DIR}/udev/60-goodix-gm168.rules" ]]; then
    install -m 644 "${SCRIPT_DIR}/udev/60-goodix-gm168.rules" /etc/udev/rules.d/60-goodix-gm168.rules
    udevadm control --reload-rules || true
    udevadm trigger || true
fi

# 5. Migrate templates & cryptographic keys
echo "[5/7] Synchronizing biometric templates & keys..."
USER_CONFIG="${TARGET_HOME}/.config/goodix-gm168"
mkdir -p "${USER_CONFIG}/templates"
chown -R "${TARGET_USER}:${TARGET_USER}" "${TARGET_HOME}/.config" 2>/dev/null || true

# Look for PSK in common locations
if [[ ! -f /etc/goodix-gm168/psk.bin ]]; then
    for candidate_psk in \
        "${USER_CONFIG}/psk.bin" \
        "${TARGET_HOME}/goodix_unseal/psk.bin" \
        "/home/*/.config/goodix-gm168/psk.bin" \
        "/home/*/goodix_unseal/psk.bin"; do
        if [[ -f "$candidate_psk" ]]; then
            cp "$candidate_psk" /etc/goodix-gm168/psk.bin
            chmod 644 /etc/goodix-gm168/psk.bin
            echo "   ✓ Imported PSK from ${candidate_psk}"
            break
        fi
    done
else
    chmod 644 /etc/goodix-gm168/psk.bin 2>/dev/null || true
fi

# Synchronize existing templates
if [[ -d "${USER_CONFIG}/templates" ]]; then
    mkdir -p "/var/lib/goodix-gm168/templates/${TARGET_USER}"
    mkdir -p "/var/lib/goodix-gm168/templates/root"
    cp -r "${USER_CONFIG}/templates"/* "/var/lib/goodix-gm168/templates/${TARGET_USER}/" 2>/dev/null || true
    cp -r "${USER_CONFIG}/templates"/* "/var/lib/goodix-gm168/templates/root/" 2>/dev/null || true
    chmod 755 /var/lib/goodix-gm168/templates
    find /var/lib/goodix-gm168/templates -type d -exec chmod 755 {} + 2>/dev/null || true
    find /var/lib/goodix-gm168/templates -type f -exec chmod 644 {} + 2>/dev/null || true
fi

# 6. Install desktop launcher
echo "[6/7] Creating desktop applications & menu shortcuts..."
if [[ -f "${SCRIPT_DIR}/desktop/goodix-fingerprint.desktop" ]]; then
    install -m 644 "${SCRIPT_DIR}/desktop/goodix-fingerprint.desktop" /usr/share/applications/goodix-fingerprint.desktop
    if [[ -d "${TARGET_HOME}/Desktop" ]]; then
        install -m 755 -o "$TARGET_USER" -g "$TARGET_USER" \
            "${SCRIPT_DIR}/desktop/goodix-fingerprint.desktop" "${TARGET_HOME}/Desktop/Goodix-Fingerprint.desktop"
    fi
fi

# 7. Configure PAM System Authentication
echo "[7/7] Enabling DRFM Linux PAM authentication stack..."
cat << 'EOF' > /usr/share/pam-configs/goodix-drfm
Name: Goodix GM168 DRFM Biometric Authentication
Default: yes
Priority: 260
Auth-Type: Primary
Auth:
	[success=end default=ignore] pam_exec.so quiet stdout /usr/local/bin/pam_goodix_verify.py
EOF

# Disable conflicting fprintd if present and activate goodix-drfm
pam-auth-update --disable fprintd --package 2>/dev/null || true
pam-auth-update --enable goodix-drfm --package

echo "================================================================="
echo "✅ Goodix GM168 DRFM Biometric Driver successfully installed!"
echo "================================================================="
echo "Next Steps:"
echo " 1. Open the Biometric Manager:"
echo "    - From Desktop: Click 'Goodix Fingerprint Suite'"
echo "    - From Terminal: python3 /usr/local/lib/goodix-gm168/fingerprint_gui.py"
echo " 2. Click '2. 16-Stage Enroll' to register your finger(s)."
echo " 3. Test System Auth:"
echo "    - Run 'sudo ls' in a terminal and touch the sensor to unlock!"
echo "================================================================="
