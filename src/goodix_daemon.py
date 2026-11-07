#!/usr/bin/env python3
"""
Goodix GM168 Native Biometric Service Daemon
Runs as a lightweight background service to provide high-speed (<=45ms)
DRFM + Adaptive Learning (gx_templateStudy) authentication for PAM, sudo, and lockscreens.
"""

import glob
import os
import pwd
import socket
import struct
import sys

# Bootstrap site-packages
for user_home in glob.glob("/home/*"):
    for py_ver in ["python3.14", "python3.13", "python3.12", "python3.11", "python3.10"]:
        pkg_dir = os.path.join(user_home, ".local/lib", py_ver, "site-packages")
        if os.path.isdir(pkg_dir) and pkg_dir not in sys.path:
            sys.path.insert(0, pkg_dir)

if "/usr/local/lib/goodix-gm168" not in sys.path:
    sys.path.insert(0, "/usr/local/lib/goodix-gm168")

import cv2
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from dense_matcher import DenseRidgeMatcher

SOCKET_PATH = "/run/goodix_gm168.sock"
GLOBAL_TEMPLATES_DIR = "/var/lib/goodix-gm168/templates"
MIN_INLIERS = 10

VALID_FINGERS = (
    "right-index-finger",
    "right-middle-finger",
    "right-ring-finger",
    "right-thumb",
    "right-little-finger",
    "left-index-finger",
    "left-middle-finger",
    "left-thumb",
    "left-ring-finger",
    "left-little-finger",
)


def get_templates_for_user(username: str) -> dict:
    target = "shohag-rana" if username in ("root", "", None) else username
    profiles = {}
    base_dir = None

    try:
        pw = pwd.getpwnam(target)
        user_conf = os.path.join(pw.pw_dir, ".config/goodix-gm168/templates")
        if os.path.isdir(user_conf):
            base_dir = user_conf
    except KeyError:
        pass

    if not base_dir:
        sys_dir = os.path.join(GLOBAL_TEMPLATES_DIR, target)
        if os.path.isdir(sys_dir):
            base_dir = sys_dir

    if not base_dir:
        return profiles

    for finger in VALID_FINGERS:
        finger_dir = os.path.join(base_dir, finger)
        if os.path.isdir(finger_dir):
            frames = sorted(glob.glob(os.path.join(finger_dir, "stage_*.pgm")))
            if frames:
                profiles[finger] = frames

    return profiles


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

            profiles = get_templates_for_user(username)
            if not profiles:
                print(f"[AUTH] No enrolled templates found for user: {username}")
                conn.sendall(struct.pack("!III", 0, 0, 0))
                conn.close()
                continue

            is_match = False
            best_inliers = 0
            best_stage = -1
            matched_path = None
            matched_finger = None

            for finger, templates in profiles.items():
                matched, inliers, _, _, _, _, stage, _, best_path = matcher.match_gallery(
                    probe_img, templates
                )
                if inliers > best_inliers:
                    best_inliers = inliers
                    best_stage = stage
                if matched:
                    is_match = True
                    best_inliers = inliers
                    best_stage = stage
                    matched_path = best_path
                    matched_finger = finger
                    break

            studied = 0
            if is_match and matched_path:
                if matcher.adaptive_study(probe_img, matched_path, best_inliers):
                    studied = 1
                    print(f"[AUTH SUCCESS] User={username}, Finger={matched_finger}, Inliers={best_inliers}, Stage={best_stage} [gx_templateStudy: Enriched ✓]")
                else:
                    print(f"[AUTH SUCCESS] User={username}, Finger={matched_finger}, Inliers={best_inliers}, Stage={best_stage}")
            else:
                print(f"[AUTH REJECT] User={username}, MaxInliers={best_inliers}")

            conn.sendall(struct.pack("!III", 1 if is_match else 0, best_inliers, studied))
        except Exception as e:
            print(f"[ERROR] Exception handling request: {e}")
        finally:
            conn.close()


if __name__ == "__main__":
    main()
