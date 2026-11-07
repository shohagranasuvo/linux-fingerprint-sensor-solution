#!/usr/bin/env bash
set -euo pipefail

PACKAGE_NAME="goodix-gm168-linux-driver"
VERSION="1.1.1"
ARCH="amd64"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${ROOT_DIR}/packaging/${PACKAGE_NAME}_${VERSION}_${ARCH}"

rm -rf "$BUILD_DIR"
mkdir -p "${BUILD_DIR}/DEBIAN" \
         "${BUILD_DIR}/usr/local/lib/goodix-gm168" \
         "${BUILD_DIR}/usr/local/bin" \
         "${BUILD_DIR}/etc/udev/rules.d" \
         "${BUILD_DIR}/usr/share/applications" \
         "${BUILD_DIR}/usr/share/pam-configs"

# 1. Control file
cat << EOF > "${BUILD_DIR}/DEBIAN/control"
Package: ${PACKAGE_NAME}
Version: ${VERSION}
Section: admin
Priority: optional
Architecture: ${ARCH}
Depends: python3, python3-opencv, python3-numpy, python3-gi, gir1.2-gtk-4.0, libglib2.0-0 | libglib2.0-0t64, libgusb2 | libgusb2t64 | libgusb2a, libssl3 | libssl3t64, libpam-runtime
Maintainer: Shohag Rana Suvo <shohagranasuvo@gmail.com>
Description: Goodix GM168 DRFM Linux Biometric Driver & PAM Stack
 Complete Linux driver and Dense Ridge-Flow Matcher (DRFM) biometric engine
 for Goodix GM168 (27c6:581a, 27c6:589a) fingerprint sensors found in
 Xiaomi RedmiBook Pro 14S, RedmiBook Pro 15, and Mi NoteBook Ultra laptops.
 Includes Windows Hello Enclave parity, PAM system authentication (sudo, GDM,
 lockscreen), and a GTK4 management suite.
EOF

# 2. Post-installation hook
cat << 'EOF' > "${BUILD_DIR}/DEBIAN/postinst"
#!/usr/bin/env bash
set -e

chmod 755 /usr/local/bin/pam_goodix_verify.py
chmod 755 /usr/local/lib/goodix-gm168/img-capture 2>/dev/null || true
udevadm control --reload-rules || true
udevadm trigger || true

# Dynamic sysfs power state initialization for Goodix GM168 sensors
for dev in /sys/bus/usb/devices/*; do
    if [ -f "$dev/idVendor" ] && [ "$(cat "$dev/idVendor" 2>/dev/null)" = "27c6" ]; then
        if [ -f "$dev/idProduct" ]; then
            prod="$(cat "$dev/idProduct" 2>/dev/null)"
            if [ "$prod" = "581a" ] || [ "$prod" = "589a" ]; then
                echo "on" > "$dev/power/control" 2>/dev/null || true
                echo "-1" > "$dev/power/autosuspend" 2>/dev/null || true
                echo "1" > "$dev/power/persist" 2>/dev/null || true
            fi
        fi
    fi
done

systemctl stop fprintd.service 2>/dev/null || true
systemctl disable fprintd.service 2>/dev/null || true
systemctl mask fprintd.service 2>/dev/null || true

if [ -f /etc/pam.d/gdm-fingerprint ]; then
    sed -i 's/^auth\s\+required\s\+pam_fprintd\.so/# auth required pam_fprintd.so (disabled for goodix-drfm)/g' /etc/pam.d/gdm-fingerprint 2>/dev/null || true
fi

if command -v pam-auth-update >/dev/null 2>&1; then
    pam-auth-update --disable fprintd --package 2>/dev/null || true
    pam-auth-update --enable goodix-drfm --package || true
fi

# Ensure clean system Python delegation for OpenCV / NumPy
rm -rf /usr/local/lib/goodix-gm168/cv2 /usr/local/lib/goodix-gm168/numpy
for u in /home/*; do
    for v in python3.14 python3.13 python3.12 python3.11 python3.10; do
        if [ -d "$u/.local/lib/$v/site-packages" ] && [ -d "/usr/local/lib/$v/dist-packages" ]; then
            echo "$u/.local/lib/$v/site-packages" > "/usr/local/lib/$v/dist-packages/goodix-deps.pth" 2>/dev/null || true
        fi
    done
done

# Add desktop icon for active users
for user_home in /home/*; do
    if [ -d "$user_home/Desktop" ]; then
        user_name="$(basename "$user_home")"
        cp /usr/share/applications/goodix-fingerprint.desktop "$user_home/Desktop/Goodix-Fingerprint.desktop" 2>/dev/null || true
        chown "$user_name:$user_name" "$user_home/Desktop/Goodix-Fingerprint.desktop" 2>/dev/null || true
        chmod +x "$user_home/Desktop/Goodix-Fingerprint.desktop" 2>/dev/null || true
    fi
done

exit 0
EOF
chmod 755 "${BUILD_DIR}/DEBIAN/postinst"

# 3. Post-removal hook
cat << 'EOF' > "${BUILD_DIR}/DEBIAN/postrm"
#!/usr/bin/env bash
set -e
if command -v pam-auth-update >/dev/null 2>&1; then
    pam-auth-update --disable goodix-drfm --package 2>/dev/null || true
    pam-auth-update --enable fprintd --package 2>/dev/null || true
fi
udevadm control --reload-rules || true
udevadm trigger || true
exit 0
EOF
chmod 755 "${BUILD_DIR}/DEBIAN/postrm"

# 4. Copy payload files
install -m 755 "${ROOT_DIR}/src/dense_matcher.py" "${BUILD_DIR}/usr/local/lib/goodix-gm168/"
install -m 755 "${ROOT_DIR}/src/goodix_daemon.py" "${BUILD_DIR}/usr/local/lib/goodix-gm168/"
install -m 755 "${ROOT_DIR}/src/fingerprint_gui.py" "${BUILD_DIR}/usr/local/lib/goodix-gm168/"
install -m 755 "${ROOT_DIR}/src/pam_goodix_verify.py" "${BUILD_DIR}/usr/local/lib/goodix-gm168/"
install -m 755 "${ROOT_DIR}/src/pam_goodix_verify.py" "${BUILD_DIR}/usr/local/bin/"
cat << 'EOF' > "${BUILD_DIR}/usr/local/bin/goodix-enroll"
#!/usr/bin/env bash
exec /usr/bin/python3 /usr/local/lib/goodix-gm168/fingerprint_gui.py "$@"
EOF
chmod 755 "${BUILD_DIR}/usr/local/bin/goodix-enroll"

install -m 755 "${ROOT_DIR}/uninstall.sh" "${BUILD_DIR}/usr/local/lib/goodix-gm168/"
install -m 755 "${ROOT_DIR}/bin/img-capture" "${BUILD_DIR}/usr/local/lib/goodix-gm168/"
cp -d "${ROOT_DIR}/bin"/libfprint-2.so* "${BUILD_DIR}/usr/local/lib/goodix-gm168/"
install -m 644 "${ROOT_DIR}/udev/60-goodix-gm168.rules" "${BUILD_DIR}/etc/udev/rules.d/"
install -m 644 "${ROOT_DIR}/desktop/goodix-fingerprint.desktop" "${BUILD_DIR}/usr/share/applications/"

cat << 'EOF' > "${BUILD_DIR}/usr/share/pam-configs/goodix-drfm"
Name: Goodix GM168 DRFM Biometric Authentication
Default: yes
Priority: 260
Auth-Type: Primary
Auth:
	[success=end default=ignore] pam_exec.so quiet stdout /usr/local/bin/pam_goodix_verify.py
EOF

# Build package
dpkg-deb --build --root-owner-group "$BUILD_DIR" "${ROOT_DIR}/packaging/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"
echo "✅ Successfully built: ${ROOT_DIR}/packaging/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"
