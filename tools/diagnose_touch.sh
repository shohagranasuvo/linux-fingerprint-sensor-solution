#!/usr/bin/env bash
set -u

BASE_DIR="/home/shohag-rana/goodix-gm168-linux-driver"
BIN_PATH="/usr/local/lib/goodix-gm168/img-capture"
LIB_DIR="/usr/local/lib/goodix-gm168"

[[ ! -x "$BIN_PATH" ]] && BIN_PATH="$BASE_DIR/bin/img-capture"
[[ ! -f "$LIB_DIR/libfprint-2.so.2" ]] && LIB_DIR="$BASE_DIR/bin"
[[ ! -x "$BIN_PATH" ]] && BIN_PATH="$BASE_DIR/src/libfprint/build/examples/img-capture"
[[ ! -f "$LIB_DIR/libfprint-2.so.2" ]] && LIB_DIR="$BASE_DIR/src/libfprint/build/libfprint"

echo "=== [1/5] Killing Stale / Contending Processes ==="
sudo killall -9 img-capture fprintd pam_goodix_verify.py 2>/dev/null || true
sudo systemctl stop fprintd.service 2>/dev/null || true

echo "=== [2/5] Verifying USB Device & Disabling Autosuspend ==="
lsusb -d 27c6:581a
for dev in /sys/bus/usb/devices/*; do
    if [[ -f "$dev/idVendor" && -f "$dev/idProduct" ]]; then
        if [[ "$(cat "$dev/idVendor")" == "27c6" && "$(cat "$dev/idProduct")" == "581a" ]]; then
            echo "Found GM168 at $dev (power/control=$(cat "$dev/power/control"))"
            echo "on" | sudo tee "$dev/power/control" >/dev/null
            sudo chmod -R 0666 "/dev/bus/usb/$(cat "$dev/busnum" | awk '{printf "%03d", $0}')/$(cat "$dev/devnum" | awk '{printf "%03d", $0}')" 2>/dev/null || true
        fi
    fi
done

echo "=== [3/5] Checking PSK & Environment ==="
ls -la /etc/goodix-gm168/psk.bin ~/.config/goodix-gm168/psk.bin 2>/dev/null || true
echo "Using binary: $BIN_PATH"
echo "Using libdir: $LIB_DIR"

echo "=== [4/5] Recent Kernel USB Events ==="
sudo dmesg | grep -iE "27c6|581a|goodix|usb" | tail -n 10

echo "=== [5/5] Standalone Hardware Touch Test (15s Timeout) ==="
echo ">>> PLACE AND HOLD FINGER ON SENSOR NOW <<<"
rm -f /tmp/diag_touch.pgm
LD_LIBRARY_PATH="$LIB_DIR" G_MESSAGES_DEBUG=all LIBUSB_DEBUG=3 \
    timeout 15s stdbuf -oL -eL "$BIN_PATH" /tmp/diag_touch.pgm 2>&1 | tee /tmp/gm168_diag.log

echo "=== Result ==="
if [[ -f /tmp/diag_touch.pgm ]]; then
    ls -lh /tmp/diag_touch.pgm
    echo "SUCCESS: Hardware captured image (/tmp/diag_touch.pgm)."
else
    echo "FAILURE: No image written. Last 20 debug lines:"
    tail -n 20 /tmp/gm168_diag.log
fi
