#!/usr/bin/env bash
# ==============================================================================
# Goodix GM168 Linux Driver & DRFM Biometric Suite - Automated Installer
# Supports: Xiaomi RedmiBook Pro 14S / 15, Xiaomi Mi NoteBook Ultra
# USB Devices: 27c6:581a, 27c6:589a
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || echo "")"
TEMP_CLONE=""

if [[ $EUID -ne 0 ]]; then
    echo "⚠️  This installer requires administrative privileges."
    echo "   Re-running with sudo..."
    exec sudo bash "$0" "$@"
fi

TARGET_USER="${PKEXEC_UID:+$(id -nu "$PKEXEC_UID" 2>/dev/null || true)}"
if [[ -z "$TARGET_USER" || "$TARGET_USER" == "root" ]]; then
    TARGET_USER="${SUDO_USER:-}"
fi
if [[ -z "$TARGET_USER" || "$TARGET_USER" == "root" ]]; then
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

# Resolve source directory (prefer local repo, then deployed dir, then clone)
if [[ -f "${SCRIPT_DIR}/src/dense_matcher.py" ]]; then
    SRC_ROOT="$SCRIPT_DIR"
elif [[ -f "${SCRIPT_DIR}/dense_matcher.py" ]]; then
    SRC_ROOT="$SCRIPT_DIR"
elif [[ -f "${TARGET_HOME}/goodix-gm168-linux-driver/src/dense_matcher.py" ]]; then
    SRC_ROOT="${TARGET_HOME}/goodix-gm168-linux-driver"
elif [[ -f "/usr/local/lib/goodix-gm168/dense_matcher.py" ]]; then
    SRC_ROOT="/usr/local/lib/goodix-gm168"
else
    echo "📥 Downloading driver package from GitHub..."
    TEMP_CLONE="$(mktemp -d /tmp/goodix-gm168-XXXXXX)"
    git clone --depth 1 https://github.com/shohagranasuvo/linux-fingerprint-sensor-solution.git "$TEMP_CLONE"
    SRC_ROOT="$TEMP_CLONE"
    trap 'rm -rf "$TEMP_CLONE"' EXIT
fi

# 1. Install prerequisites via apt with strict candidate validation
echo "[1/7] Installing system dependencies..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq || true

pkg_available() {
    local cand
    cand="$(apt-cache policy "$1" 2>/dev/null | awk '/Candidate:/ {print $2}')"
    [[ -n "$cand" && "$cand" != "(none)" ]]
}

DEPS=()
for pkg in python3 python3-pip python3-gi gir1.2-gtk-4.0 gir1.2-gdkpixbuf-2.0 python3-opencv python3-numpy libpixman-1-0 libpam-runtime udev; do
    pkg_available "$pkg" && DEPS+=("$pkg")
done

# Dynamic ABI transition packages
for group in "libgusb2t64 libgusb2a libgusb2" "libglib2.0-0t64 libglib2.0-0" "libssl3t64 libssl3"; do
    for candidate in $group; do
        if pkg_available "$candidate"; then
            DEPS+=("$candidate")
            break
        fi
    done
done

if [[ ${#DEPS[@]} -gt 0 ]]; then
    apt-get install -y -qq "${DEPS[@]}" >/dev/null 2>&1 || apt-get install -y "${DEPS[@]}" || true
fi

# 2. Create system directories
echo "[2/7] Creating system directories..."
mkdir -p /usr/local/lib/goodix-gm168/src \
         /usr/local/lib/goodix-gm168/bin \
         /var/lib/goodix-gm168/templates \
         /etc/goodix-gm168 \
         /usr/share/pam-configs \
         /etc/udev/rules.d \
         /usr/share/applications

chmod 755 /usr/local/lib/goodix-gm168 /etc/goodix-gm168 /var/lib/goodix-gm168

# 3. Install core libraries, scripts, and self-copy installer
echo "[3/7] Deploying DRFM modules, capture binaries, and libraries..."
MOD_SRC="${SRC_ROOT}/src"
[[ ! -d "$MOD_SRC" ]] && MOD_SRC="$SRC_ROOT"

safe_install() {
    local mode="$1"
    local src="$2"
    local dst="$3"
    local real_src real_dst
    real_src="$(readlink -f "$src" 2>/dev/null || echo "$src")"
    real_dst="$(readlink -f "$dst" 2>/dev/null || echo "$dst")"
    if [[ "$real_src" != "$real_dst" ]]; then
        install -m "$mode" "$src" "$dst"
    fi
}

safe_install 755 "${BASH_SOURCE[0]}" /usr/local/lib/goodix-gm168/install.sh
for pyfile in dense_matcher.py goodix_daemon.py fingerprint_gui.py pam_goodix_verify.py; do
    if [[ -f "${MOD_SRC}/${pyfile}" ]]; then
        safe_install 755 "${MOD_SRC}/${pyfile}" "/usr/local/lib/goodix-gm168/${pyfile}"
        safe_install 755 "${MOD_SRC}/${pyfile}" "/usr/local/lib/goodix-gm168/src/${pyfile}"
    fi
done
safe_install 755 "/usr/local/lib/goodix-gm168/pam_goodix_verify.py" /usr/local/bin/pam_goodix_verify.py

cat << 'EOF' > /usr/local/bin/goodix-enroll
#!/usr/bin/env bash
exec python3 /usr/local/lib/goodix-gm168/fingerprint_gui.py "$@"
EOF
chmod 755 /usr/local/bin/goodix-enroll

touch /tmp/goodix-pam.log && chmod 666 /tmp/goodix-pam.log 2>/dev/null || true

BIN_SRC="${SRC_ROOT}/bin"
[[ ! -d "$BIN_SRC" ]] && BIN_SRC="$SRC_ROOT"
if [[ -f "${BIN_SRC}/img-capture" ]]; then
    safe_install 755 "${BIN_SRC}/img-capture" /usr/local/lib/goodix-gm168/img-capture
    safe_install 755 "${BIN_SRC}/img-capture" /usr/local/lib/goodix-gm168/bin/img-capture
fi
if ls "${BIN_SRC}"/libfprint-2.so* >/dev/null 2>&1; then
    if [[ "$(readlink -f "${BIN_SRC}")" != "$(readlink -f /usr/local/lib/goodix-gm168)" ]]; then
        cp -df "${BIN_SRC}"/libfprint-2.so* /usr/local/lib/goodix-gm168/ 2>/dev/null || true
    fi
    if [[ "$(readlink -f "${BIN_SRC}")" != "$(readlink -f /usr/local/lib/goodix-gm168/bin)" ]]; then
        cp -df "${BIN_SRC}"/libfprint-2.so* /usr/local/lib/goodix-gm168/bin/ 2>/dev/null || true
    fi
fi

# Bridge user site-packages (e.g. python3.14 cv2/numpy) into system dist-packages & /usr/local/lib/goodix-gm168
for u in /home/*; do
    [[ -d "$u/.local/lib" ]] || continue
    chmod o+x "$u" "$u/.local" "$u/.local/lib" 2>/dev/null || true
    for sp in "$u"/.local/lib/python3*/site-packages; do
        [[ -d "$sp" ]] || continue
        chmod -R o+rX "$sp" 2>/dev/null || true
        pyver="$(basename "$(dirname "$sp")")"
        mkdir -p "/usr/local/lib/${pyver}/dist-packages"
        echo "$sp" > "/usr/local/lib/${pyver}/dist-packages/goodix-deps.pth" 2>/dev/null || true
    done
done

if ! python3 -c "import cv2, numpy" >/dev/null 2>&1; then
    python3 -m pip install --break-system-packages opencv-python-headless numpy >/dev/null 2>&1 || true
fi

# 4. Install udev access rules
echo "[4/7] Configuring USB udev access permissions..."
if [[ -f "${SRC_ROOT}/udev/60-goodix-gm168.rules" ]]; then
    install -m 644 "${SRC_ROOT}/udev/60-goodix-gm168.rules" /etc/udev/rules.d/60-goodix-gm168.rules
    udevadm control --reload-rules || true
    udevadm trigger || true
fi

# 5. Migrate templates & cryptographic keys
echo "[5/7] Synchronizing biometric templates & keys..."
USER_CONFIG="${TARGET_HOME}/.config/goodix-gm168"
mkdir -p "${USER_CONFIG}/templates"
chown -R "${TARGET_USER}:${TARGET_USER}" "${USER_CONFIG}" 2>/dev/null || true

if [[ ! -f /etc/goodix-gm168/psk.bin ]]; then
    for candidate_psk in \
        "${USER_CONFIG}/psk.bin" \
        "${TARGET_HOME}/goodix_unseal/psk.bin" \
        /home/*/.config/goodix-gm168/psk.bin \
        /home/*/goodix_unseal/psk.bin; do
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

if [[ -d "${USER_CONFIG}/templates" ]]; then
    mkdir -p "/var/lib/goodix-gm168/templates/${TARGET_USER}" "/var/lib/goodix-gm168/templates/root"
    cp -r "${USER_CONFIG}/templates"/* "/var/lib/goodix-gm168/templates/${TARGET_USER}/" 2>/dev/null || true
    cp -r "${USER_CONFIG}/templates"/* "/var/lib/goodix-gm168/templates/root/" 2>/dev/null || true
    chmod -R a+rX /var/lib/goodix-gm168/templates 2>/dev/null || true
fi

# 6. Install desktop launcher
echo "[6/7] Creating desktop applications & menu shortcuts..."
if [[ -f "${SRC_ROOT}/desktop/goodix-fingerprint.desktop" ]]; then
    install -m 644 "${SRC_ROOT}/desktop/goodix-fingerprint.desktop" /usr/share/applications/goodix-fingerprint.desktop
    if [[ -d "${TARGET_HOME}/Desktop" ]]; then
        install -m 755 -o "$TARGET_USER" -g "$TARGET_USER" \
            "${SRC_ROOT}/desktop/goodix-fingerprint.desktop" "${TARGET_HOME}/Desktop/Goodix-Fingerprint.desktop"
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

echo "   Disabling and masking conflicting fprintd..."
systemctl stop fprintd.service 2>/dev/null || true
systemctl disable fprintd.service 2>/dev/null || true
systemctl mask fprintd.service 2>/dev/null || true

if [[ -f /etc/pam.d/gdm-fingerprint ]]; then
    sed -i 's/^auth\s\+required\s\+pam_fprintd\.so/# auth required pam_fprintd.so (disabled for goodix-drfm)/g' /etc/pam.d/gdm-fingerprint 2>/dev/null || true
fi

pam-auth-update --disable fprintd --package 2>/dev/null || true
pam-auth-update --enable goodix-drfm --package

echo "================================================================="
echo "✅ Goodix GM168 DRFM Biometric Driver successfully installed!"
echo "⚠️  NOTE: System 'fprintd' is masked to avoid USB contention."
echo "    Do NOT use GNOME Settings or 'fprintd-enroll'."
echo "    Enroll fingers using 'goodix-enroll' or Fingerprint Manager GUI."
echo "================================================================="
