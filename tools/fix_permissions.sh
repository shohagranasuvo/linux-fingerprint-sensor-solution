#!/bin/bash
# Apply robust USB permissions using both 'plugdev' group and systemd 'uaccess' tag.

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root."
  exit 1
fi

# Determine the actual non-root user
TARGET_USER="${SUDO_USER:-${PKEXEC_UID:+$(id -nu "$PKEXEC_UID")}}"
if [ -z "$TARGET_USER" ] || [ "$TARGET_USER" == "root" ]; then
    TARGET_USER=$(logname 2>/dev/null || who | awk '{print $1}' | head -n1)
fi

echo "Configuring plugdev group for user: $TARGET_USER"
getent group plugdev >/dev/null || groupadd -r plugdev
if [ -n "$TARGET_USER" ] && [ "$TARGET_USER" != "root" ]; then
    usermod -aG plugdev "$TARGET_USER"
fi

echo "Installing udev rule..."
cat << 'EOF' > /etc/udev/rules.d/99-goodix-gm168.rules
# Grant access via group (plugdev), open permissions (0666), and active seat tag (uaccess)
SUBSYSTEM=="usb", ATTRS{idVendor}=="27c6", ATTRS{idProduct}=="581a", MODE="0666", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb", ATTRS{idVendor}=="27c6", ATTRS{idProduct}=="589a", MODE="0666", GROUP="plugdev", TAG+="uaccess"
EOF

echo "Reloading udev rules..."
udevadm control --reload-rules
udevadm trigger

# Apply immediately to existing device node without requiring a physical replug
echo "Applying runtime permissions..."
DEVICE_PATH=$(lsusb -d 27c6:581a | awk '{print "/dev/bus/usb/"$2"/"$4}' | sed 's/://')
if [ -n "$DEVICE_PATH" ] && [ -e "$DEVICE_PATH" ]; then
    chmod 666 "$DEVICE_PATH"
    chown root:plugdev "$DEVICE_PATH"
fi

echo "Done. You may need to log out and log back in for the 'plugdev' group change to take full effect."
