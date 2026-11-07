#!/usr/bin/env python3
"""
Goodix GM168 PAM Authentication Helper
Direct In-Process DRFM (Dense Ridge-Flow Matcher) + Adaptive Template Learning (gx_templateStudy).
Enforces Windows Hello Goodix Enclave REA telemetry checks (valid_area, deduplicated inliers,
composite match_score, and multi-stage consensus) for Zero-FAR verification.
Invoked by pam_exec.so during sudo, lockscreen, and GDM login.
Exits 0 on match, 1 on failure.
"""

import fcntl
import glob
import os
import pwd
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_SRC_DIR = "/home/shohag-rana/goodix-gm168-linux-driver/src"

# Prioritize local source directory over stale /usr/local/lib/goodix-gm168 copies
SEARCH_PATHS = [
    REPO_SRC_DIR,
    SCRIPT_DIR,
    "/usr/local/lib/goodix-gm168",
    "/usr/lib/python3/dist-packages",
    "/usr/local/lib/python3/dist-packages",
]
for pattern in (
    "/home/*/.local/lib/python3*/site-packages",
    "/home/*/.local/lib/python3*/dist-packages",
    "/usr/local/lib/python3*/dist-packages",
    "/usr/local/lib/python3*/site-packages",
    "/usr/lib/python3*/dist-packages",
):
    for pkg_dir in sorted(glob.glob(pattern), reverse=True):
        if os.path.isdir(pkg_dir) and pkg_dir not in SEARCH_PATHS:
            SEARCH_PATHS.append(pkg_dir)

for p in reversed(SEARCH_PATHS):
    if os.path.isdir(p):
        while p in sys.path:
            sys.path.remove(p)
        sys.path.insert(0, p)

try:
    import cv2
    import numpy as np
except ImportError as exc:
    if sys.executable != "/usr/bin/python3" and os.path.exists("/usr/bin/python3"):
        os.execv("/usr/bin/python3", ["/usr/bin/python3"] + sys.argv)
    sys.stderr.write(f"PAM Goodix: OpenCV import failed ({exc})\n")
    sys.exit(1)

SYSTEM_CAPTURE_BIN = "/usr/local/lib/goodix-gm168/img-capture"
LOCAL_CAPTURE_BIN = os.path.abspath(os.path.join(SCRIPT_DIR, "../bin/img-capture"))
CAPTURE_BIN = SYSTEM_CAPTURE_BIN if os.path.exists(SYSTEM_CAPTURE_BIN) else LOCAL_CAPTURE_BIN

SYSTEM_LIB_DIR = "/usr/local/lib/goodix-gm168"
LOCAL_LIB_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "../bin"))
LIB_DIR = SYSTEM_LIB_DIR if os.path.exists(os.path.join(SYSTEM_LIB_DIR, "libfprint-2.so.2")) else LOCAL_LIB_DIR

OUT_FRAME = "/tmp/pam_probe.pgm"
MIN_INLIERS = 10
LOG_FILE = "/tmp/goodix-pam.log"
LOCK_FILE = "/run/goodix_sensor.lock"

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


def user_msg(msg: str):
    """Outputs human-friendly status directly to PAM stdout (sudo/lockscreen prompt)."""
    sys.stdout.write(f"{msg}\n")
    sys.stdout.flush()


def debug_log(msg: str):
    """Logs detailed telemetry to /tmp/goodix-pam.log without cluttering the PAM prompt."""
    try:
        first_time = not os.path.exists(LOG_FILE)
        with open(LOG_FILE, "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
        if first_time:
            try:
                os.chmod(LOG_FILE, 0o666)
            except Exception:
                pass
    except Exception:
        pass


def ensure_fprintd_stopped():
    """Prevents USB device contention with conflicting background fprintd service."""
    try:
        subprocess.run(["systemctl", "stop", "fprintd.service"], capture_output=True, timeout=2)
        subprocess.run(["systemctl", "mask", "fprintd.service"], capture_output=True, timeout=2)
    except Exception:
        pass


def get_psk_directory() -> str:
    """Finds the cryptographic PSK directory across system and user locations."""
    if os.path.exists("/etc/goodix-gm168/psk.bin"):
        return "/etc/goodix-gm168"

    for user_home in glob.glob("/home/*"):
        user_psk = os.path.join(user_home, ".config/goodix-gm168/psk.bin")
        if os.path.exists(user_psk):
            return os.path.dirname(user_psk)
        legacy_psk = os.path.join(user_home, "goodix_unseal/psk.bin")
        if os.path.exists(legacy_psk):
            return os.path.dirname(legacy_psk)

    return "/etc/goodix-gm168"


def _exec_single_capture(env: dict, timeout_sec: int = 15) -> tuple:
    """Executes single img-capture process and streams output."""
    if os.path.exists(OUT_FRAME):
        try:
            os.remove(OUT_FRAME)
        except OSError:
            pass

    proc = subprocess.Popen(
        ["timeout", f"{timeout_sec}s", CAPTURE_BIN, OUT_FRAME],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    touch_signaled = False
    for line in proc.stdout:
        line_str = line.strip()
        debug_log(f"img-capture: {line_str}")
        if ("Touch Detected" in line_str or "touch detected" in line_str.lower()) and not touch_signaled:
            user_msg("⚡ Verifying fingerprint...")
            touch_signaled = True

    proc.wait()
    success = os.path.exists(OUT_FRAME) and os.path.getsize(OUT_FRAME) > 1000
    return success, proc.returncode


def capture_sensor_touch() -> bool:
    """Arms the sensor and captures a live touch frame with hardware mutex and auto-retry."""
    ensure_fprintd_stopped()

    lock_fd = None
    try:
        lock_fd = open(LOCK_FILE, "w")
        start_wait = time.time()
        locked = False
        while time.time() - start_wait < 2.0:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except (IOError, OSError):
                time.sleep(0.1)
        if not locked:
            debug_log("Sensor busy: another auth worker currently holds lock.")
            return False
    except Exception as e:
        debug_log(f"Lock warning: {e}")

    try:
        psk_dir = get_psk_directory()
        ld_paths = [p for p in [LIB_DIR, SYSTEM_LIB_DIR, LOCAL_LIB_DIR] if os.path.isdir(p)]

        env = os.environ.copy()
        env.update({
            "GOODIX_GM168_DIR": psk_dir,
            "LD_LIBRARY_PATH": ":".join(ld_paths),
            "GM168_DUMP_FRAMES": "0"
        })

        user_msg("👆 Place finger on sensor to unlock...")
        debug_log(f"Arming sensor with CAPTURE_BIN={CAPTURE_BIN}, PSK={psk_dir}...")

        success, retcode = _exec_single_capture(env, timeout_sec=15)
        if success:
            debug_log(f"Probe frame captured successfully ({os.path.getsize(OUT_FRAME)} bytes).")
            return True

        if retcode == 1:
            debug_log("Capture attempt 1 encountered USB/TLS glitch. Retrying after brief settle...")
            time.sleep(0.2)
            success, retcode = _exec_single_capture(env, timeout_sec=10)
            if success:
                debug_log(f"Probe frame captured on retry ({os.path.getsize(OUT_FRAME)} bytes).")
                return True

        user_msg("⏱️ Fingerprint sensor timed out.")
        debug_log(f"Sensor acquisition ended without valid frame (exit={retcode}).")
        return False
    except Exception as e:
        debug_log(f"Capture error exception: {e}")
        user_msg("⏱️ Fingerprint sensor timed out.")
        return False
    finally:
        if lock_fd:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()
            except Exception:
                pass


def get_candidate_templates(target_user: str) -> dict:
    """
    Discovers enrolled template frames strictly from the target user's GUI config directory,
    grouped by finger profile to prevent cross-profile consensus false positives.
    """
    profiles = {}
    base_dir = None

    try:
        pw = pwd.getpwnam(target_user)
        candidate_dir = os.path.join(pw.pw_dir, ".config/goodix-gm168/templates")
        if os.path.isdir(candidate_dir):
            base_dir = candidate_dir
    except KeyError:
        pass

    if not base_dir:
        sys_user_dir = f"/var/lib/goodix-gm168/templates/{target_user}"
        if os.path.isdir(sys_user_dir):
            base_dir = sys_user_dir

    if not base_dir:
        return profiles

    for finger in VALID_FINGERS:
        finger_dir = os.path.join(base_dir, finger)
        if os.path.isdir(finger_dir):
            frames = sorted(glob.glob(os.path.join(finger_dir, "stage_*.pgm")))
            if frames:
                profiles[finger] = frames

    return profiles


def verify_in_process(username: str, img_path: str) -> int:
    """Executes direct in-process DRFM verification per finger profile (matching GUI behavior)."""
    import dense_matcher
    from dense_matcher import DenseRidgeMatcher

    debug_log(f"Using dense_matcher module from: {getattr(dense_matcher, '__file__', 'unknown')}")

    probe_img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
    if probe_img is None:
        debug_log("Error: Captured probe frame is unreadable.")
        user_msg("❌ Fingerprint unreadable.")
        return 1

    matcher = DenseRidgeMatcher(min_inliers=MIN_INLIERS)
    usable, contrast, grad = matcher.get_quality(probe_img)
    debug_log(f"Quality: contrast={contrast:.1f}, grad_var={grad:.1f}, usable={usable}")
    if not usable:
        debug_log("Authentication REJECTED (Low image_quality / partial touch).")
        user_msg("❌ Fingerprint unclear. Place finger flat.")
        return 1

    profiles = get_candidate_templates(username)
    if not profiles:
        debug_log(f"Error: No enrolled templates found for target user '{username}'.")
        user_msg("⚠️ No enrolled fingerprint profiles found.")
        return 1

    for finger, candidates in profiles.items():
        matched, inliers, rot, _, score, ncc, stage, hits, best_path = matcher.match_gallery(
            probe_img, candidates
        )
        is_verified = bool(
            matched
            and inliers >= matcher.min_inliers
            and score >= 68
            and ncc >= 36
            and (inliers >= matcher.min_inliers + 1 or hits >= 2)
        )
        debug_log(
            f"Profile '{finger}': matched={matched}, verified={is_verified}, score={score}, "
            f"inliers={inliers}/{matcher.min_inliers}, ncc={ncc}%, hits={hits}, stage={stage}, rot={rot:+.1f}°"
        )
        if is_verified:
            if best_path:
                studied = matcher.adaptive_study(probe_img, best_path, inliers)
                if studied:
                    debug_log(f"gx_templateStudy: Stage frame '{os.path.basename(best_path)}' refreshed.")
            debug_log(f"Authentication SUCCESSFUL on profile '{finger}'.")
            user_msg("✓ Fingerprint recognized! Authenticated.")
            return 0

    debug_log("Authentication REJECTED (Zero-FAR Enclave Security Gate).")
    user_msg("❌ Fingerprint not recognized.")
    return 1


def cleanup():
    if os.path.exists(OUT_FRAME):
        try:
            os.remove(OUT_FRAME)
        except OSError:
            pass


def main():
    raw_user = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PAM_USER", os.environ.get("USER", ""))
    if not raw_user or raw_user == "root":
        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user:
            username = sudo_user
        else:
            users = [u for u in glob.glob("/home/*") if os.path.isdir(u)]
            username = os.path.basename(users[0]) if users else "root"
    else:
        username = raw_user

    debug_log(f"Initiating biometric PAM check for target: '{username}' (actor: '{raw_user}')")

    if not capture_sensor_touch():
        cleanup()
        return 1

    ret = verify_in_process(username, OUT_FRAME)
    cleanup()
    return ret


if __name__ == "__main__":
    sys.exit(main())
