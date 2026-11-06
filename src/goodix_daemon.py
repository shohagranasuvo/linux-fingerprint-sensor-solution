#!/usr/bin/env python3
"""
Goodix GM168 Native Biometric Service Daemon
Runs as a lightweight background service to provide high-speed (<=45ms)
DRFM + Adaptive Learning (gx_templateStudy) authentication for PAM, sudo, and lockscreens.
"""

import glob
import os
import sys

# Bootstrap site-packages
for user_home in glob.glob("/home/*"):
    for py_ver in ["python3.14", "python3.13", "python3.12", "python3.11", "python3.10"]:
        pkg_dir = os.path.join(user_home, ".local/lib", py_ver, "site-packages")
        if os.path.isdir(pkg_dir) and pkg_dir not in sys.path:
            sys.path.insert(0, pkg_dir)

if "/usr/local/lib/goodix-gm168" not in sys.path:
    sys.path.insert(0, "/usr/local/lib/goodix-gm168")

import socket
import struct
import cv2
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from dense_matcher import DenseRidgeMatcher

SOCKET_PATH = "/run/goodix_gm168.sock"
GLOBAL_TEMPLATES_DIR = "/var/lib/goodix-gm168/templates"
FALLBACK_USER_TEMPLATES = os.path.expanduser("~/.config/goodix-gm168/templates")
MIN_INLIERS = 4


def get_templates_for_user(username: str):
    target = "shohag-rana" if username in ("root", "", None) else username
    candidates = []

    sys_dir = os.path.join(GLOBAL_TEMPLATES_DIR, target)
    if os.path.isdir(sys_dir):
        candidates.extend(glob.glob(os.path.join(sys_dir, "*.pgm")))
        candidates.extend(glob.glob(os.path.join(sys_dir, "*", "*.pgm")))

    user_home = os.path.expanduser(f"~{target}")
    if os.path.isdir(user_home):
        user_conf = os.path.join(user_home, ".config/goodix-gm168/templates")
        if os.path.isdir(user_conf):
            candidates.extend(glob.glob(os.path.join(user_conf, "*", "*.pgm")))

    if not candidates and os.path.isdir(FALLBACK_USER_TEMPLATES):
        candidates.extend(glob.glob(os.path.join(FALLBACK_USER_TEMPLATES, "*", "*.pgm")))

    return sorted(list(set(candidates)))


def main():
    if os.path.exists(SOCKET_PATH):
        try:
            os.unlink(SOCKET_PATH)
        except OSError:
            pass

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o666)
    server.listen(5)

    matcher = DenseRidgeMatcher(min_inliers=MIN_INLIERS)
    print(f"Goodix GM168 Biometric Daemon listening on {SOCKET_PATH}...")

    while True:
        try:
            conn, _ = server.accept()
        except KeyboardInterrupt:
            break
        except Exception:
            continue

        try:
            header = conn.recv(8)
            if len(header) < 8:
                conn.close()
                continue
            user_len, img_len = struct.unpack("!II", header)
            username = conn.recv(user_len).decode("utf-8", errors="ignore")

            buf = bytearray()
            while len(buf) < img_len:
                chunk = conn.recv(min(8192, img_len - len(buf)))
                if not chunk:
                    break
                buf.extend(chunk)

            nparr = np.frombuffer(buf, np.uint8)
            probe_img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)

            if probe_img is None:
                conn.sendall(struct.pack("!III", 0, 0, 0))
                conn.close()
                continue

            templates = get_templates_for_user(username)
            if not templates:
                print(f"[AUTH] No enrolled templates found for user: {username}")
                conn.sendall(struct.pack("!III", 0, 0, 0))
                conn.close()
                continue

            is_match, inliers, rot, _, _, _, stage, hits, best_path = matcher.match_gallery(probe_img, templates)

            studied = 0
            if is_match and best_path:
                if matcher.adaptive_study(probe_img, best_path, inliers):
                    studied = 1
                    print(f"[AUTH SUCCESS] User={username}, Inliers={inliers}, Stage={stage} [gx_templateStudy: Enriched ✓]")
                else:
                    print(f"[AUTH SUCCESS] User={username}, Inliers={inliers}, Stage={stage}")
            else:
                print(f"[AUTH REJECT] User={username}, Inliers={inliers}")

            conn.sendall(struct.pack("!III", 1 if is_match else 0, inliers, studied))
        except Exception as e:
            print(f"[ERROR] Exception handling request: {e}")
        finally:
            conn.close()


if __name__ == "__main__":
    main()
