#!/bin/bash
# Standalone diagnostic script to isolate permission and timeout issues.

export GOODIX_GM168_DIR="/etc/goodix-gm168"
export LD_LIBRARY_PATH="/usr/local/lib/goodix-gm168"
export GM168_DUMP_FRAMES="1"
export GM168_DUMP_DIR="/tmp"

BIN="/usr/local/lib/goodix-gm168/img-capture"

echo "=== 1. Testing Standalone (User Privileges) ==="
$BIN /tmp/test_user.pgm
echo "User Exit Code: $?"

echo -e "\n=== 2. Testing with Timeout Wrapper (User Privileges) ==="
timeout 15s $BIN /tmp/test_timeout.pgm
echo "Timeout Exit Code: $?"

echo -e "\n=== 3. Testing with Root Privileges (sudo) ==="
sudo -E timeout 15s $BIN /tmp/test_root.pgm
echo "Root Exit Code: $?"
