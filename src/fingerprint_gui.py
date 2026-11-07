#!/usr/bin/env python3
"""
Goodix GM168 (27c6:581a) Biometric Suite
Dedicated Engine: Dense Ridge-Flow Matcher (DRFM) + Adaptive Template Study (gx_templateStudy)
"""

import fcntl
import glob
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

import cv2
import gi
import numpy as np

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)

from dense_matcher import DenseRidgeMatcher


def find_psk_dir() -> str:
    if os.path.exists("/etc/goodix-gm168/psk.bin"):
        return "/etc/goodix-gm168"
    user_home = os.path.expanduser("~")
    for p in [os.path.join(user_home, ".config/goodix-gm168"), os.path.join(user_home, "goodix_unseal")]:
        if os.path.exists(os.path.join(p, "psk.bin")):
            return p
    return "/etc/goodix-gm168"


def find_capture_bin() -> str:
    system_bin = "/usr/local/lib/goodix-gm168/img-capture"
    if os.path.exists(system_bin):
        return system_bin
    local_bin = os.path.abspath(os.path.join(CURRENT_DIR, "../bin/img-capture"))
    if os.path.exists(local_bin):
        return local_bin
    return os.path.join(CURRENT_DIR, "libfprint/build/examples/img-capture")


def find_lib_dir() -> str:
    system_lib = "/usr/local/lib/goodix-gm168"
    if os.path.exists(os.path.join(system_lib, "libfprint-2.so.2")):
        return system_lib
    local_lib = os.path.abspath(os.path.join(CURRENT_DIR, "../bin"))
    if os.path.exists(os.path.join(local_lib, "libfprint-2.so.2")):
        return local_lib
    return os.path.join(CURRENT_DIR, "libfprint/build/libfprint")


def ensure_fprintd_stopped():
    """Prevents USB device contention with conflicting background fprintd service."""
    try:
        subprocess.run(["systemctl", "stop", "fprintd.service"], capture_output=True, timeout=2)
        subprocess.run(["systemctl", "mask", "fprintd.service"], capture_output=True, timeout=2)
    except Exception:
        pass


def acquire_sensor_lock(timeout_sec: float = 3.0):
    """Acquires exclusive hardware lock to prevent USB contention with PAM or daemon workers."""
    for lock_path in ("/run/goodix_sensor.lock", "/tmp/goodix_sensor.lock"):
        lock_fd = None
        try:
            lock_fd = open(lock_path, "w")
            try:
                os.chmod(lock_path, 0o666)
            except Exception:
                pass
            start_wait = time.time()
            while time.time() - start_wait < timeout_sec:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return lock_fd
                except (IOError, OSError):
                    time.sleep(0.1)
            lock_fd.close()
            return None
        except PermissionError:
            if lock_fd:
                try:
                    lock_fd.close()
                except Exception:
                    pass
            continue
        except Exception:
            if lock_fd:
                try:
                    lock_fd.close()
                except Exception:
                    pass
            return None
    return None


def release_sensor_lock(lock_fd):
    """Safely unlocks and closes the hardware lock file descriptor."""
    if lock_fd:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            lock_fd.close()
        except Exception:
            pass


PSK_SYSTEM_PATH = "/etc/goodix-gm168/psk.bin"
PREVIEW_SYSTEM_PATH = "/etc/goodix-gm168/preview.pgm"
PREVIEW_TMP_PATH = "/tmp/gm168_latest_preview.pgm"
TEMPLATES_BASE_DIR = os.path.expanduser("~/.config/goodix-gm168/templates")

FINGERS = [
    ("right-index-finger", "Right Index (Profile 1 - Flat Center)"),
    ("right-middle-finger", "Right Index (Profile 2 - Angled / Side Roll)"),
    ("right-ring-finger", "Right Index (Profile 3 - Tip / Reach)"),
    ("right-thumb", "Right Thumb (Profile 1 - Standard Flat)"),
    ("right-little-finger", "Right Thumb (Profile 2 - Angled / Roll)"),
    ("left-index-finger", "Left Index (Profile 1 - Flat Center)"),
    ("left-middle-finger", "Left Index (Profile 2 - Angled / Reach)"),
    ("left-thumb", "Left Thumb (Profile 1 - Standard Flat)"),
    ("left-ring-finger", "Left Hand (Alternate Angle Slot)"),
    ("left-little-finger", "Left Hand (Backup Angle Slot)"),
]

ZONES = {
    1: {"name": "Center Pad", "icon": "🎯", "stages": "1–4", "guide": "Place flat center pad firmly on sensor."},
    2: {"name": "Fingertip", "icon": "👆", "stages": "5–8", "guide": "Tilt finger forward (~30°) using the tip."},
    3: {"name": "Left Edge", "icon": "👈", "stages": "9–12", "guide": "Roll finger slightly to the left side edge."},
    4: {"name": "Right Edge", "icon": "👉", "stages": "13–16", "guide": "Roll finger slightly to the right side edge."}
}


def get_zone_for_stage(stage_1_based: int) -> int:
    return (stage_1_based - 1) // 4 + 1 if stage_1_based <= 16 else 4


class ModernBiometricApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="com.goodix.gm168.biometric")
        self.win = None
        self.proc = None
        self.busy = False
        self.suppress_preview_poll = False
        self.last_preview_mtime = 0
        self.last_preview_file = ""
        self.enrolled_set = set()
        self.active_zone = 1
        self.threshold_value = 6
        self.dense_matcher = DenseRidgeMatcher(min_inliers=6)
        os.makedirs(TEMPLATES_BASE_DIR, exist_ok=True)

    def do_activate(self):
        win = Gtk.ApplicationWindow(application=self)
        win.set_title("Goodix GM168 • DRFM Biometric Engine")
        win.set_default_size(940, 840)
        self.win = win

        css = Gtk.CssProvider()
        css.load_from_data(b"""
            window {
                background-color: #0b0f19;
                color: #e2e8f0;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Inter", sans-serif;
            }
            .card {
                background-color: #131b2e;
                border-radius: 12px;
                padding: 12px 16px;
                border: 1px solid #1e293b;
            }
            .header-title {
                font-size: 18px;
                font-weight: 800;
                color: #38bdf8;
                letter-spacing: -0.3px;
            }
            .header-subtitle {
                font-size: 11px;
                color: #94a3b8;
            }
            .badge-pill {
                font-size: 11px;
                font-weight: 600;
                padding: 4px 10px;
                border-radius: 20px;
                background-color: #1e293b;
                color: #94a3b8;
                border: 1px solid #334155;
            }
            .badge-ok {
                color: #4ade80;
                border-color: #166534;
                background-color: #052e16;
            }
            .badge-accent {
                color: #38bdf8;
                border-color: #075985;
                background-color: #082f49;
            }
            .action-banner {
                border-radius: 10px;
                padding: 12px 16px;
                min-height: 44px;
            }
            .banner-title { font-size: 14px; font-weight: 700; }
            .banner-subtitle { font-size: 11px; opacity: 0.92; }
            .banner-idle { background: #1e293b; color: #94a3b8; border: 1px solid #334155; }
            .banner-waiting { background: #1e1b4b; color: #c7d2fe; border: 1px solid #4338ca; }
            .banner-ready { background: #064e3b; color: #a7f3d0; border: 1px solid #059669; }
            .banner-scanning { background: #713f12; color: #fef08a; border: 1px solid #ca8a04; }
            .banner-lift { background: #1e3a8a; color: #bfdbfe; border: 1px solid #2563eb; }
            .banner-success { background: #064e3b; color: #4ade80; border: 1px solid #16a34a; }
            .banner-error { background: #7f1d1d; color: #fca5a5; border: 1px solid #dc2626; }
            .zone-box {
                border-radius: 8px;
                padding: 8px 10px;
                font-size: 11px;
                border: 1px solid #1e293b;
                background-color: #0b0f19;
                color: #94a3b8;
            }
            .zone-active {
                background-color: #2563eb;
                color: #ffffff;
                font-weight: 700;
                border-color: #60a5fa;
            }
            .btn-enroll {
                background: #16a34a;
                color: #ffffff;
                font-weight: 700;
                border-radius: 8px;
                padding: 8px 14px;
                border: none;
            }
            .btn-enroll:hover { background: #22c55e; }
            .btn-dense {
                background: linear-gradient(135deg, #059669, #0284c7);
                color: #ffffff;
                font-weight: 700;
                border-radius: 8px;
                padding: 8px 16px;
                border: none;
            }
            .btn-dense:hover { background: linear-gradient(135deg, #047857, #0369a1); }
            .btn-verify {
                background: #334155;
                color: #f1f5f9;
                font-weight: 600;
                border-radius: 8px;
                padding: 8px 12px;
                border: 1px solid #475569;
            }
            .btn-verify:hover { background: #475569; }
            .btn-action {
                background: #1e293b;
                color: #e2e8f0;
                font-weight: 600;
                border-radius: 8px;
                padding: 8px 14px;
                border: 1px solid #334155;
            }
            .btn-action:hover { background: #334155; }
            .btn-danger {
                background: #dc2626;
                color: #ffffff;
                font-weight: 600;
                border-radius: 8px;
                padding: 8px 12px;
                border: none;
            }
            .btn-danger:hover { background: #ef4444; }
            .slider-card {
                background-color: #0f172a;
                border: 1px solid #1e293b;
                border-radius: 10px;
                padding: 10px 14px;
            }
            .badge-thresh {
                background: #1e293b;
                color: #38bdf8;
                border: 1px solid #0284c7;
                border-radius: 6px;
                padding: 3px 10px;
                font-size: 11px;
                font-weight: 700;
            }
            .btn-preset {
                background: #1e293b;
                color: #cbd5e1;
                font-size: 11px;
                font-weight: 600;
                border-radius: 6px;
                padding: 4px 10px;
                border: 1px solid #334155;
            }
            .btn-preset:hover { background: #334155; color: #f8fafc; }
            .preview-card {
                background-color: #0b0f19;
                border: 1px solid #1e293b;
                border-radius: 10px;
                padding: 10px;
            }
            .preview-img-frame {
                background-color: #020617;
                border-radius: 8px;
                border: 1px solid #1e293b;
                padding: 4px;
            }
            progressbar trough {
                min-height: 10px;
                border-radius: 5px;
                background-color: #1e293b;
            }
            progressbar progress {
                min-height: 10px;
                border-radius: 5px;
                background: linear-gradient(90deg, #38bdf8, #22c55e);
            }
            textview {
                font-family: ui-monospace, SFMono-Regular, "Cascadia Code", monospace;
                font-size: 11px;
                background-color: #060911;
                color: #94a3b8;
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_margin_top(10)
        root.set_margin_bottom(10)
        root.set_margin_start(12)
        root.set_margin_end(12)
        win.set_child(root)

        # 1. Header Card
        hdr_card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        hdr_card.add_css_class("card")
        hdr_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        hdr_vbox.set_hexpand(True)

        title = Gtk.Label(label="Goodix GM168 Biometric Suite", xalign=0)
        title.add_css_class("header-title")
        subtitle = Gtk.Label(
            label="Verification Engine: Dense Ridge-Flow Matcher (DRFM) + Adaptive Template Study",
            xalign=0
        )
        subtitle.add_css_class("header-subtitle")

        badges_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.badge_usb = Gtk.Label(label="USB: Checking...")
        self.badge_usb.add_css_class("badge-pill")
        self.badge_psk = Gtk.Label(label="PSK: Checking...")
        self.badge_psk.add_css_class("badge-pill")
        self.badge_drfm = Gtk.Label(label="DRFM Engine: Active ✓")
        self.badge_drfm.add_css_class("badge-pill")
        self.badge_drfm.add_css_class("badge-accent")
        self.badge_study = Gtk.Label(label="Adaptive Learning: Enabled ✓")
        self.badge_study.add_css_class("badge-pill")
        self.badge_study.add_css_class("badge-ok")

        badges_box.append(self.badge_usb)
        badges_box.append(self.badge_psk)
        badges_box.append(self.badge_drfm)
        badges_box.append(self.badge_study)

        hdr_vbox.append(title)
        hdr_vbox.append(subtitle)
        hdr_vbox.append(badges_box)

        self.install_btn = Gtk.Button(label="🛠️ Deploy Driver")
        self.install_btn.add_css_class("btn-action")
        self.install_btn.connect("clicked", self.on_install_driver)

        hdr_card.append(hdr_vbox)
        hdr_card.append(self.install_btn)
        root.append(hdr_card)

        # 2. Profile Selection Card
        finger_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        finger_card.add_css_class("card")

        f_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        f_lbl = Gtk.Label(label="Active Profile:", xalign=0)
        self.finger_combo = Gtk.ComboBoxText()
        for fid, fname in FINGERS:
            self.finger_combo.append(fid, fname)
        self.finger_combo.set_active(0)
        self.finger_combo.set_hexpand(True)

        self.del_single_btn = Gtk.Button(label="🗑️ Delete")
        self.del_single_btn.add_css_class("btn-danger")
        self.del_single_btn.connect("clicked", self.on_delete_single)

        self.del_all_btn = Gtk.Button(label="Clear All")
        self.del_all_btn.add_css_class("btn-action")
        self.del_all_btn.connect("clicked", self.on_delete_all)

        f_row.append(f_lbl)
        f_row.append(self.finger_combo)
        f_row.append(self.del_single_btn)
        f_row.append(self.del_all_btn)
        finger_card.append(f_row)

        self.enrolled_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        finger_card.append(self.enrolled_box)
        root.append(finger_card)

        # 3. Action Banner
        self.banner_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.banner_card.add_css_class("action-banner")
        self.banner_card.add_css_class("banner-idle")

        self.banner_title_lbl = Gtk.Label(label="Ready. Standby.", xalign=0)
        self.banner_title_lbl.add_css_class("banner-title")
        self.banner_sub_lbl = Gtk.Label(
            label="Click '1. Preview', '2. 16-Stage Enroll', or '3. DRFM Verify' to begin.",
            xalign=0
        )
        self.banner_sub_lbl.add_css_class("banner-subtitle")

        self.banner_card.append(self.banner_title_lbl)
        self.banner_card.append(self.banner_sub_lbl)
        root.append(self.banner_card)

        # 4. Multi-Zone Coach & Controls
        coach_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        coach_card.add_css_class("card")

        self.zone_widgets = {}
        zone_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for zid in (1, 2, 3, 4):
            z = ZONES[zid]
            zb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            zb.add_css_class("zone-box")
            zb.set_hexpand(True)
            zb.append(Gtk.Label(label=f"{z['icon']} Stages {z['stages']}", xalign=0.5))
            zb.append(Gtk.Label(label=z['name'], xalign=0.5))
            zone_row.append(zb)
            self.zone_widgets[zid] = zb
        coach_card.append(zone_row)

        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        self.progress.set_text("Stage 0 / 16 (0%)")
        coach_card.append(self.progress)

        # Sensitivity Slider
        slider_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        slider_card.add_css_class("slider-card")

        slider_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        stitle = Gtk.Label(label="DRFM Inlier Sensitivity Calibration", xalign=0)
        stitle.set_hexpand(True)
        self.thresh_badge = Gtk.Label(label="Threshold: 6 Inliers (🔵 Balanced)")
        self.thresh_badge.add_css_class("badge-thresh")
        slider_header.append(stitle)
        slider_header.append(self.thresh_badge)
        slider_card.append(slider_header)

        scale_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.thresh_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 3.0, 15.0, 1.0)
        self.thresh_scale.set_value(6.0)
        self.thresh_scale.set_draw_value(True)
        self.thresh_scale.set_hexpand(True)
        self.thresh_scale.connect("value-changed", self.on_threshold_changed)
        scale_row.append(self.thresh_scale)

        preset_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_rel = Gtk.Button(label="🟢 Sensitive (4)")
        btn_rel.add_css_class("btn-preset")
        btn_rel.connect("clicked", lambda _b: self.thresh_scale.set_value(4.0))

        btn_bal = Gtk.Button(label="🔵 Balanced (6)")
        btn_bal.add_css_class("btn-preset")
        btn_bal.connect("clicked", lambda _b: self.thresh_scale.set_value(6.0))

        btn_str = Gtk.Button(label="🔒 Strict (8)")
        btn_str.add_css_class("btn-preset")
        btn_str.connect("clicked", lambda _b: self.thresh_scale.set_value(8.0))

        preset_row.append(btn_rel)
        preset_row.append(btn_bal)
        preset_row.append(btn_str)
        scale_row.append(preset_row)
        slider_card.append(scale_row)
        coach_card.append(slider_card)

        # Core Action Buttons
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self.test_btn = Gtk.Button(label="📷 1. Preview")
        self.test_btn.add_css_class("btn-action")
        self.test_btn.connect("clicked", self.on_test_touch)

        self.enroll_btn = Gtk.Button(label="👆 2. 16-Stage Enroll")
        self.enroll_btn.add_css_class("btn-enroll")
        self.enroll_btn.set_hexpand(True)
        self.enroll_btn.connect("clicked", self.on_enroll)

        self.dense_btn = Gtk.Button(label="⚡ 3. DRFM Verify (Adaptive)")
        self.dense_btn.add_css_class("btn-dense")
        self.dense_btn.set_hexpand(True)
        self.dense_btn.connect("clicked", self.on_match_dense)

        self.verify_btn = Gtk.Button(label="🔑 4. System Auth")
        self.verify_btn.add_css_class("btn-verify")
        self.verify_btn.connect("clicked", self.on_verify_fprintd)

        self.stop_btn = Gtk.Button(label="🛑 Stop")
        self.stop_btn.add_css_class("btn-danger")
        self.stop_btn.set_sensitive(False)
        self.stop_btn.connect("clicked", self.on_stop)

        btn_row.append(self.test_btn)
        btn_row.append(self.enroll_btn)
        btn_row.append(self.dense_btn)
        btn_row.append(self.verify_btn)
        btn_row.append(self.stop_btn)
        coach_card.append(btn_row)
        root.append(coach_card)

        # 5. Split Bottom: Log & Visualizer
        bottom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        bottom_box.set_vexpand(True)

        log_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        log_card.add_css_class("card")
        log_card.set_hexpand(True)
        log_card.set_vexpand(True)
        log_card.append(Gtk.Label(label="Biometric Engine Log:", xalign=0))

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log_buf = self.log_view.get_buffer()
        scroll.set_child(self.log_view)
        log_card.append(scroll)
        bottom_box.append(log_card)

        prev_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        prev_card.add_css_class("card")
        prev_card.add_css_class("preview-card")
        prev_card.set_size_request(400, -1)

        img_frame = Gtk.Box()
        img_frame.add_css_class("preview-img-frame")
        img_frame.set_halign(Gtk.Align.CENTER)
        self.img_widget = Gtk.Picture()
        self.img_widget.set_size_request(380, 220)
        self.img_widget.set_vexpand(True)
        img_frame.append(self.img_widget)
        prev_card.append(img_frame)

        self.metric_kps_lbl = Gtk.Label(label="Dense Keypoints: Standby", xalign=0.5)
        self.metric_kps_lbl.add_css_class("badge-pill")
        self.metric_inliers_lbl = Gtk.Label(label="Inliers: Standby", xalign=0.5)
        self.metric_inliers_lbl.add_css_class("badge-pill")
        self.metric_rot_lbl = Gtk.Label(label="Rotation: 0.0°", xalign=0.5)
        self.metric_rot_lbl.add_css_class("badge-pill")

        prev_card.append(self.metric_kps_lbl)
        prev_card.append(self.metric_inliers_lbl)
        prev_card.append(self.metric_rot_lbl)
        bottom_box.append(prev_card)
        root.append(bottom_box)

        self.set_active_zone(1)
        self.refresh_status()
        self.start_preview_file_watcher()
        win.present()

    def set_banner(self, banner_class, title, subtitle):
        def _do():
            for c in ("banner-idle", "banner-waiting", "banner-ready", "banner-scanning",
                      "banner-lift", "banner-success", "banner-error"):
                self.banner_card.remove_css_class(c)
            self.banner_card.add_css_class(banner_class)
            self.banner_title_lbl.set_text(title)
            self.banner_sub_lbl.set_text(subtitle)
            return False
        GLib.idle_add(_do)

    def set_active_zone(self, zone_id):
        def _do():
            self.active_zone = zone_id
            for zid, w in self.zone_widgets.items():
                if zid == zone_id:
                    w.add_css_class("zone-active")
                else:
                    w.remove_css_class("zone-active")
            return False
        GLib.idle_add(_do)

    def append_log(self, text):
        def _do():
            ts = time.strftime("[%H:%M:%S] ")
            end_iter = self.log_buf.get_end_iter()
            self.log_buf.insert(end_iter, ts + text + "\n")
            mark = self.log_buf.create_mark(None, self.log_buf.get_end_iter(), False)
            self.log_view.scroll_to_mark(mark, 0.0, True, 0.0, 1.0)
            return False
        GLib.idle_add(_do)

    def set_progress(self, stage, total=16, text=None):
        def _do():
            frac = max(0.0, min(1.0, float(stage) / float(total)))
            self.progress.set_fraction(frac)
            self.progress.set_text(text or f"Stage {stage} / {total} ({int(frac * 100)}%)")
            return False
        GLib.idle_add(_do)

    def set_busy(self, busy: bool):
        self.busy = bool(busy)

        def _do():
            self.install_btn.set_sensitive(not busy)
            self.test_btn.set_sensitive(not busy)
            self.enroll_btn.set_sensitive(not busy)
            self.dense_btn.set_sensitive(not busy)
            self.verify_btn.set_sensitive(not busy)
            self.del_single_btn.set_sensitive(not busy)
            self.del_all_btn.set_sensitive(not busy)
            self.stop_btn.set_sensitive(busy)
            return False
        GLib.idle_add(_do)

    def display_numpy_image(self, bgr_img: np.ndarray):
        def _do():
            try:
                rgb = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2RGB) if len(bgr_img.shape) == 3 else cv2.cvtColor(bgr_img, cv2.COLOR_GRAY2RGB)
                h, w, ch = rgb.shape
                stride = ch * w
                pixbuf = GdkPixbuf.Pixbuf.new_from_data(rgb.tobytes(), GdkPixbuf.Colorspace.RGB, False, 8, w, h, stride)
                self.img_widget.set_paintable(Gdk.Texture.new_for_pixbuf(pixbuf))
            except Exception as e:
                self.append_log(f"Display error: {e}")
            return False
        GLib.idle_add(_do)

    def start_preview_file_watcher(self):
        def _poll():
            if self.suppress_preview_poll:
                return True
            candidates = [PREVIEW_SYSTEM_PATH, PREVIEW_TMP_PATH, "/tmp/finger_live_capture.pgm"]
            latest_pgms = sorted(glob.glob("/tmp/gm168_*_fpimg.pgm"), key=os.path.getmtime)
            if latest_pgms:
                candidates.append(latest_pgms[-1])

            best_file, best_mtime = None, 0
            for path in candidates:
                if os.path.exists(path):
                    try:
                        mt = os.path.getmtime(path)
                        if mt > best_mtime:
                            best_mtime, best_file = mt, path
                    except Exception:
                        pass

            if best_file and (best_mtime > self.last_preview_mtime or best_file != self.last_preview_file):
                self.last_preview_mtime = best_mtime
                self.last_preview_file = best_file
                self.load_preview_image(best_file)
            return True

        GLib.timeout_add(250, _poll)

    def load_preview_image(self, pgm_path: str):
        try:
            raw = cv2.imread(pgm_path, cv2.IMREAD_GRAYSCALE)
            if raw is None:
                return
            enh, mask = self.dense_matcher.preprocess(raw)
            kp, _ = self.dense_matcher.orb.detectAndCompute(enh, mask)
            vis = cv2.cvtColor(enh, cv2.COLOR_GRAY2BGR)
            for k in (kp or []):
                cv2.circle(vis, (int(round(k.pt[0])), int(round(k.pt[1]))), 2, (0, 255, 64), 1)
            self.display_numpy_image(vis)
            GLib.idle_add(lambda: self.metric_kps_lbl.set_text(f"Dense Features: {len(kp) if kp else 0}") or False)
        except Exception:
            pass

    def refresh_status(self):
        def _worker():
            lsusb = subprocess.run(["lsusb", "-d", "27c6:581a"], capture_output=True, text=True)
            usb_ok = "27c6:581a" in lsusb.stdout
            psk_ok = os.path.exists(PSK_SYSTEM_PATH) or os.path.exists(os.path.join(find_psk_dir(), "psk.bin"))

            user = os.environ.get("USER", "shohag-rana")
            enrolled = []
            tmpl_dirs = [
                TEMPLATES_BASE_DIR,
                f"/var/lib/goodix-gm168/templates/{user}",
                "/var/lib/goodix-gm168/templates"
            ]
            for td in tmpl_dirs:
                if os.path.isdir(td):
                    for entry in os.listdir(td):
                        ep = os.path.join(td, entry)
                        if os.path.isdir(ep) and (glob.glob(os.path.join(ep, "*.pgm")) or glob.glob(os.path.join(ep, "*", "*.pgm"))):
                            if entry not in ("root", user) and entry not in enrolled:
                                enrolled.append(entry)
            self.enrolled_set = set(enrolled)

            def _update():
                self.badge_usb.set_text("USB: Connected ✓" if usb_ok else "USB: Missing ✗")
                self.badge_usb.add_css_class("badge-ok") if usb_ok else self.badge_usb.remove_css_class("badge-ok")
                self.badge_psk.set_text("PSK: Ready ✓" if psk_ok else "PSK: Missing ✗")
                self.badge_psk.add_css_class("badge-ok") if psk_ok else self.badge_psk.remove_css_class("badge-ok")

                while True:
                    c = self.enrolled_box.get_first_child()
                    if not c:
                        break
                    self.enrolled_box.remove(c)

                hdr = Gtk.Label(label="Enrolled:")
                hdr.add_css_class("header-subtitle")
                self.enrolled_box.append(hdr)
                for fid in enrolled:
                    b = Gtk.Label(label=f"✓ {fid.replace('-', ' ').title()}")
                    b.add_css_class("badge-pill")
                    b.add_css_class("badge-ok")
                    self.enrolled_box.append(b)

                cur_id = self.finger_combo.get_active_id() or "right-index-finger"
                self.finger_combo.remove_all()
                for fid, fname in FINGERS:
                    suffix = "  (✓ Enrolled)" if fid in self.enrolled_set else ""
                    self.finger_combo.append(fid, fname + suffix)
                self.finger_combo.set_active_id(cur_id)
                return False

            GLib.idle_add(_update)

        threading.Thread(target=_worker, daemon=True).start()

    def on_threshold_changed(self, scale):
        val = int(round(scale.get_value()))
        self.threshold_value = val
        self.dense_matcher.min_inliers = val
        txt = f"Threshold: {val} Inliers"
        self.thresh_badge.set_text(txt)
        self.append_log(f"⚙️ DRFM threshold calibrated to {val} inliers.")

    def on_stop(self, _btn):
        self.busy = False
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.send_signal(signal.SIGINT)
            except Exception:
                pass
            try:
                self.proc.terminate()
            except Exception:
                pass
        self.set_banner("banner-idle", "Action Cancelled", "Standby.")
        self.set_busy(False)

    def on_install_driver(self, _btn):
        """Deploys DRFM system daemon and registers DRFM PAM module via pkexec."""
        self.set_busy(True)
        self.set_banner(
            "banner-waiting",
            "Deploying DRFM System-Wide...",
            "Authenticate in prompt to activate DRFM for sudo & lockscreen."
        )

        def _worker():
            candidates = [
                os.path.expanduser("~/goodix-gm168-linux-driver/install.sh"),
                os.path.abspath(os.path.join(CURRENT_DIR, "../install.sh")),
                os.path.join(CURRENT_DIR, "install.sh"),
                "/usr/local/lib/goodix-gm168/install.sh"
            ]
            script_path = next((p for p in candidates if os.path.exists(p)), candidates[0])
            cmd = f"pkexec bash '{script_path}'"
            proc = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            for line in iter(proc.stdout.readline, ""):
                self.append_log(line.strip())
            proc.stdout.close()
            proc.wait()

            if proc.returncode == 0:
                self.append_log("✓ DRFM Biometric Engine deployed to system PAM.")
                self.set_banner(
                    "banner-success",
                    "DRFM Driver Active",
                    "PAM lockscreen, sudo, and GDM switched to DRFM."
                )
            else:
                self.append_log(f"✗ Deployment failed (Exit code {proc.returncode}).")
                self.set_banner(
                    "banner-error",
                    "Deploy Cancelled",
                    "Authentication rejected or error occurred."
                )
            self.set_busy(False)
            self.refresh_status()

        threading.Thread(target=_worker, daemon=True).start()

    def on_test_touch(self, _btn):
        if self.busy:
            return
        self.set_busy(True)
        self.set_banner("banner-ready", "TOUCH SENSOR NOW", "Testing capacitive frame capture (20s)...")

        def _worker():
            ensure_fprintd_stopped()
            lock_fd = acquire_sensor_lock()
            if not lock_fd:
                self.append_log("⚠️ Sensor busy: another biometric worker holds the hardware lock.")
                self.set_banner("banner-error", "Sensor Busy", "Another auth process is running.")
                self.set_busy(False)
                return

            try:
                psk_dir = find_psk_dir()
                ld_dir = find_lib_dir()
                ld_paths = [p for p in [ld_dir, "/usr/local/lib/goodix-gm168"] if os.path.isdir(p)]
                env = os.environ.copy()
                env.update({
                    "GOODIX_GM168_DIR": psk_dir,
                    "LD_LIBRARY_PATH": ":".join(ld_paths),
                    "GM168_DUMP_FRAMES": "0"
                })
                exe = find_capture_bin()
                out_pgm = "/tmp/finger_live_capture.pgm"
                if os.path.exists(out_pgm):
                    try:
                        os.remove(out_pgm)
                    except OSError:
                        pass

                self.proc = subprocess.Popen(
                    ["timeout", "20s", exe, out_pgm],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env
                )
                for line in iter(self.proc.stdout.readline, ""):
                    line_str = line.strip()
                    if line_str:
                        self.append_log(f"[PREVIEW] {line_str}")
                self.proc.stdout.close()
                self.proc.wait()

                if os.path.exists(out_pgm) and os.path.getsize(out_pgm) > 1000:
                    raw = cv2.imread(out_pgm, cv2.IMREAD_GRAYSCALE)
                    usable, contrast, grad = self.dense_matcher.get_quality(raw)
                    self.append_log(f"Capture Quality: usable={usable}, contrast={contrast:.1f}, grad_var={grad:.1f}")
                    self.load_preview_image(out_pgm)
                    self.set_banner(
                        "banner-success" if usable else "banner-idle",
                        "Sensor Frame Captured",
                        f"Quality: {'Usable ✓' if usable else 'Low Contrast ✗'} (Grad: {grad:.1f})"
                    )
                else:
                    self.set_banner("banner-idle", "Test Ended", "No contact registered.")
            except Exception as e:
                self.append_log(f"Preview error: {e}")
                self.set_banner("banner-error", "Preview Error", str(e))
            finally:
                self.proc = None
                release_sensor_lock(lock_fd)
                self.set_busy(False)

        threading.Thread(target=_worker, daemon=True).start()

    def on_enroll(self, _btn):
        if self.busy:
            return
        self.suppress_preview_poll = True
        finger = self.finger_combo.get_active_id() or "right-index-finger"

        self.set_busy(True)
        self.set_progress(0, 16, "Initializing...")
        self.set_active_zone(1)
        self.set_banner(
            "banner-waiting",
            f"Preparing 16-Stage Enrollment ({finger})",
            "Keep finger OFF sensor during initial baseline calibration..."
        )
        self.append_log(f"=== Starting Direct DRFM 16-Stage Enrollment for '{finger}' ===")

        def _worker():
            ensure_fprintd_stopped()
            try:
                persistent_dir = os.path.join(TEMPLATES_BASE_DIR, finger)
                os.makedirs(persistent_dir, exist_ok=True)

                exe = find_capture_bin()
                if not exe or not os.path.exists(exe):
                    self.append_log(f"❌ img-capture binary not found: {exe}")
                    self.set_banner("banner-error", "Capture Engine Missing", "Deploy driver first.")
                    return

                psk_dir = find_psk_dir()
                ld_dir = find_lib_dir()
                ld_paths = [p for p in [ld_dir, "/usr/local/lib/goodix-gm168"] if os.path.isdir(p)]

                env = os.environ.copy()
                env.update({
                    "GOODIX_GM168_DIR": psk_dir,
                    "LD_LIBRARY_PATH": ":".join(ld_paths),
                    "GM168_DUMP_FRAMES": "0"
                })

                TOTAL_STAGES = 16
                stage = 0

                while stage < TOTAL_STAGES and self.busy:
                    current_stage = stage + 1
                    zone_id = get_zone_for_stage(current_stage)
                    zone_info = ZONES.get(zone_id, ZONES[1])
                    self.set_active_zone(zone_id)
                    self.set_progress(stage, TOTAL_STAGES, f"Stage {current_stage}/{TOTAL_STAGES}: {zone_info['name']}")
                    self.set_banner(
                        "banner-ready",
                        f"STAGE {current_stage}/{TOTAL_STAGES}: {zone_info['icon']} {zone_info['name']}",
                        f"{zone_info['guide']} (Touch sensor firmly)"
                    )
                    self.append_log(f"👉 Stage {current_stage}/{TOTAL_STAGES}: Touch sensor ({zone_info['name']})...")

                    stage_tmp = f"/tmp/stage_{current_stage:02d}.pgm"
                    if os.path.exists(stage_tmp):
                        try:
                            os.remove(stage_tmp)
                        except OSError:
                            pass

                    lock_fd = acquire_sensor_lock(timeout_sec=3.0)
                    if not lock_fd:
                        self.append_log("⚠️ Sensor busy: waiting for hardware lock...")
                        self.set_banner("banner-waiting", "Sensor Busy", "Waiting for hardware lock release...")
                        time.sleep(1.0)
                        continue

                    rc = -1
                    try:
                        self.proc = subprocess.Popen(
                            ["timeout", "20s", exe, stage_tmp],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1,
                            env=env
                        )

                        for line in iter(self.proc.stdout.readline, ""):
                            line_str = line.strip()
                            if not line_str:
                                continue
                            self.append_log(f"[STAGE {current_stage:02d}] {line_str}")
                            if "Touch Detected" in line_str or "touch detected" in line_str.lower():
                                self.set_banner(
                                    "banner-scanning",
                                    f"STAGE {current_stage}/{TOTAL_STAGES}: TOUCH DETECTED",
                                    "Hold finger steady while capturing ridge frame..."
                                )
                            elif "Place" in line_str or "Waiting" in line_str:
                                self.set_banner(
                                    "banner-ready",
                                    f"STAGE {current_stage}/{TOTAL_STAGES}: {zone_info['icon']} {zone_info['name']}",
                                    f"{zone_info['guide']} (Touch sensor now)"
                                )

                        self.proc.stdout.close()
                        rc = self.proc.wait()
                    except Exception as e:
                        self.append_log(f"⚠️ Stage {current_stage} subprocess error: {e}")
                    finally:
                        self.proc = None
                        release_sensor_lock(lock_fd)

                    if not self.busy:
                        self.append_log("🛑 Enrollment stopped by user.")
                        break

                    captured_file = stage_tmp if (os.path.exists(stage_tmp) and os.path.getsize(stage_tmp) > 1000) else None

                    if captured_file:
                        raw = cv2.imread(captured_file, cv2.IMREAD_GRAYSCALE)
                        if raw is not None:
                            usable, contrast, grad = self.dense_matcher.get_quality(raw)
                            try:
                                self.load_preview_image(captured_file)
                            except Exception:
                                pass

                            if not usable:
                                self.append_log(
                                    f"⚠️ Stage {current_stage} low quality (contrast={contrast:.1f}, grad={grad:.1f}). Lift finger and retry."
                                )
                                self.set_banner(
                                    "banner-error",
                                    f"RETRY STAGE {current_stage}/{TOTAL_STAGES} — LIFT FINGER NOW",
                                    "Weak scan. Lift finger completely off sensor for 1.8s baseline reset..."
                                )
                                time.sleep(1.8)
                                continue

                            dest_file = os.path.join(persistent_dir, f"stage_{current_stage:02d}.pgm")
                            if os.path.abspath(captured_file) != os.path.abspath(dest_file):
                                shutil.copyfile(captured_file, dest_file)
                            try:
                                shutil.copyfile(captured_file, PREVIEW_TMP_PATH)
                            except Exception:
                                pass

                            user = os.environ.get("USER", "shohag-rana")
                            for sys_target in (user, "root"):
                                try:
                                    sys_tmpl = f"/var/lib/goodix-gm168/templates/{sys_target}/{finger}/stage_{current_stage:02d}.pgm"
                                    os.makedirs(os.path.dirname(sys_tmpl), exist_ok=True)
                                    if os.path.abspath(captured_file) != os.path.abspath(sys_tmpl):
                                        shutil.copyfile(captured_file, sys_tmpl)
                                except Exception:
                                    pass

                            stage += 1
                            self.set_progress(stage, TOTAL_STAGES, f"Stage {stage}/{TOTAL_STAGES} Passed ✓")
                            self.append_log(
                                f"✓ Stage {stage:02d}/{TOTAL_STAGES} enrolled (contrast={contrast:.1f}, grad={grad:.1f})."
                            )

                            if stage < TOTAL_STAGES and self.busy:
                                self.set_banner(
                                    "banner-lift",
                                    f"✋ LIFT FINGER OFF SENSOR (Stage {stage}/{TOTAL_STAGES} Passed ✓)",
                                    "Keep finger lifted for 1.8s so USB & FDT baseline can recalibrate..."
                                )
                                time.sleep(1.8)
                        else:
                            self.append_log(f"⚠️ Stage {current_stage}: Could not decode PGM frame. Lift finger and retry...")
                            self.set_banner(
                                "banner-error",
                                "Frame Decode Error — Lift Finger",
                                "Lift finger off sensor for 1.8s before next attempt..."
                            )
                            time.sleep(1.8)
                    else:
                        self.append_log(
                            f"⏱️ Stage {current_stage} capture incomplete (rc={rc}). Lift finger completely and retry..."
                        )
                        self.set_banner(
                            "banner-waiting",
                            f"Stage {current_stage}/{TOTAL_STAGES}: Lift Finger to Recalibrate",
                            "Keep finger OFF sensor for 1.8s so FDT baseline resets cleanly..."
                        )
                        time.sleep(1.8)

                if stage >= TOTAL_STAGES and self.busy:
                    self.set_progress(TOTAL_STAGES, TOTAL_STAGES, "Enrolled 16/16 ✓")
                    self.set_banner(
                        "banner-success",
                        "🎉 Enrollment Complete!",
                        f"All 16 stages enrolled for '{finger}'. DRFM biometric gallery ready."
                    )
                    self.append_log(f"🎉 Successfully enrolled all 16 stages for '{finger}'!")
            except Exception as e:
                self.append_log(f"❌ Enrollment exception: {e}")
                self.set_banner("banner-error", "Enrollment Error", str(e))
            finally:
                self.proc = None
                self.suppress_preview_poll = False
                self.set_busy(False)
                self.refresh_status()

        threading.Thread(target=_worker, daemon=True).start()

    def on_match_dense(self, _btn):
        if self.busy:
            return
        finger = self.finger_combo.get_active_id() or "right-index-finger"
        template_dir = os.path.join(TEMPLATES_BASE_DIR, finger)
        templates = sorted(glob.glob(os.path.join(template_dir, "*.pgm")))

        if not templates:
            self.append_log(f"⚠️ Finger '{finger}' is not enrolled. Run '2. 16-Stage Enroll' first.")
            self.set_banner("banner-error", f"Profile '{finger}' Not Enrolled", "Please enroll before verifying.")
            return

        self.set_busy(True)
        self.set_banner("banner-ready", "DRFM Verify: Touch Sensor (360°)", "Acquiring live probe frame...")

        def _worker():
            ensure_fprintd_stopped()
            lock_fd = acquire_sensor_lock()
            if not lock_fd:
                self.append_log("⚠️ Sensor busy: another biometric worker holds the hardware lock.")
                self.set_banner("banner-error", "Sensor Busy", "Another auth process is running.")
                self.set_busy(False)
                return

            try:
                psk_dir = find_psk_dir()
                ld_dir = find_lib_dir()
                ld_paths = [p for p in [ld_dir, "/usr/local/lib/goodix-gm168"] if os.path.isdir(p)]
                env = os.environ.copy()
                env.update({
                    "GOODIX_GM168_DIR": psk_dir,
                    "LD_LIBRARY_PATH": ":".join(ld_paths),
                    "GM168_DUMP_FRAMES": "0"
                })
                exe = find_capture_bin()
                probe_path = "/tmp/dense_probe.pgm"
                if os.path.exists(probe_path):
                    try:
                        os.remove(probe_path)
                    except OSError:
                        pass

                self.proc = subprocess.Popen(
                    ["timeout", "15s", exe, probe_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env
                )
                for line in iter(self.proc.stdout.readline, ""):
                    line_str = line.strip()
                    if line_str:
                        self.append_log(f"[VERIFY] {line_str}")
                        if "Touch Detected" in line_str or "touch detected" in line_str.lower():
                            self.set_banner("banner-scanning", "TOUCH DETECTED", "Matching against 16-stage gallery...")
                self.proc.stdout.close()
                self.proc.wait()

                if not os.path.exists(probe_path) or os.path.getsize(probe_path) <= 1000:
                    self.set_banner("banner-idle", "Timed Out", "No finger contact detected.")
                    return

                probe_img = cv2.imread(probe_path, cv2.IMREAD_GRAYSCALE)
                is_usable, contrast, grad = self.dense_matcher.get_quality(probe_img)
                self.append_log(f"Probe Quality: usable={is_usable}, std={contrast:.1f}, grad_var={grad:.1f}")

                matched, max_inl, rot, vis, qp, gt, stage, hits, best_path = self.dense_matcher.match_gallery(
                    probe_img, templates
                )
                self.append_log(f"Backend match result: {matched} (Score: {qp}, Inliers: {max_inl})")

                if vis is not None:
                    self.display_numpy_image(vis)

                thresh = self.dense_matcher.min_inliers
                GLib.idle_add(lambda: self.metric_kps_lbl.set_text(f"Telemetry: Score {qp}/100 • NCC {gt}% (Hits: {hits})") or False)
                GLib.idle_add(lambda: self.metric_inliers_lbl.set_text(f"Inliers: {max_inl}/{thresh} (Stage {stage})") or False)
                GLib.idle_add(lambda: self.metric_rot_lbl.set_text(f"Rotation Delta: {rot:+.1f}°") or False)

                if matched:
                    study_applied = self.dense_matcher.adaptive_study(probe_img, best_path, max_inl)
                    study_str = f" [gx_templateStudy: Updated Stage {stage}]" if study_applied else ""
                    self.append_log(
                        f"✅ DRFM MATCH SUCCESS: Score={qp}/100, Inliers={max_inl} >= {thresh}, Stage={stage}, Hits={hits}, Rot={rot:+.1f}°{study_str}"
                    )
                    self.set_banner(
                        "banner-success",
                        f"🔓 ACCESS GRANTED: Score {qp}/100",
                        f"Verified on Stage {stage} ({max_inl} Inliers, {hits} Hits).{study_str}"
                    )
                    self.set_progress(1, 1, f"VERIFIED ✓ (Score {qp}/100)")
                else:
                    self.append_log(
                        f"❌ DRFM REJECT: Score={qp}/100, Inliers={max_inl}/{thresh}, Hits={hits} (Stage {stage})."
                    )
                    self.set_banner(
                        "banner-error",
                        f"❌ ACCESS DENIED: Score {qp}/100",
                        f"Impostor rejected (Inliers: {max_inl}/{thresh}, Hits: {hits})."
                    )
                    self.set_progress(0, 1, f"ACCESS DENIED ✗ (Score {qp}/100)")
            except Exception as e:
                self.append_log(f"Verify error: {e}")
                self.set_banner("banner-error", "Verify Error", str(e))
            finally:
                self.proc = None
                release_sensor_lock(lock_fd)
                self.set_busy(False)

        threading.Thread(target=_worker, daemon=True).start()

    def on_verify_fprintd(self, _btn):
        """Tests system PAM authentication using the DRFM PAM verification helper."""
        if self.busy:
            return
        self.set_busy(True)
        self.set_progress(0, 1, "Arming sensor...")
        self.set_banner(
            "banner-waiting",
            "Arming Sensor for PAM Auth...",
            "Initializing hardware (15s window)..."
        )
        self.append_log("=== Running System PAM Auth Test (pam_goodix_verify.py) ===")

        def _worker():
            user = os.environ.get("USER") or os.environ.get("LOGNAME") or "root"
            local_helper = os.path.join(CURRENT_DIR, "pam_goodix_verify.py")
            helper = (
                local_helper
                if os.path.exists(local_helper)
                else "/usr/local/bin/pam_goodix_verify.py"
            )

            try:
                self.proc = subprocess.Popen(
                    [sys.executable, helper, user],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1
                )
                for line in iter(self.proc.stdout.readline, ""):
                    line_str = line.strip()
                    if not line_str:
                        continue
                    self.append_log(line_str)
                    if "Place finger" in line_str or "ready" in line_str.lower():
                        self.set_banner(
                            "banner-ready",
                            "SENSOR READY: Touch Finger Now",
                            "Place enrolled finger firmly on the sensor."
                        )
                    elif "Verifying" in line_str or "Touch detected" in line_str:
                        self.set_banner(
                            "banner-scanning",
                            "TOUCH DETECTED: Analyzing DRFM...",
                            "Matching against enrolled gallery..."
                        )
                    elif "recognized" in line_str or "Authenticated" in line_str or "SUCCESS" in line_str:
                        self.set_banner(
                            "banner-success",
                            "🔓 SYSTEM UNLOCKED!",
                            "PAM DRFM verification succeeded! Lockscreen/sudo authorized."
                        )
                        self.set_progress(1, 1, "PAM VERIFIED ✓")
                    elif "timed out" in line_str:
                        self.set_banner(
                            "banner-idle",
                            "Sensor Timed Out",
                            "No finger touch registered within acquisition window."
                        )
                    elif "not recognized" in line_str or "REJECTED" in line_str or "unclear" in line_str:
                        self.set_banner(
                            "banner-error",
                            "❌ AUTHENTICATION FAILED",
                            "Finger rejected or not recognized. Check finger placement."
                        )
                        self.set_progress(0, 1, "ACCESS DENIED ✗")

                self.proc.stdout.close()
                self.proc.wait()

                if self.proc.returncode == 0:
                    self.append_log("✅ DRFM SYSTEM AUTH SUCCESS: Access GRANTED (Exit 0).")
                else:
                    self.append_log(f"❌ DRFM SYSTEM AUTH DENIED (Exit {self.proc.returncode}).")
            except Exception as e:
                self.append_log(f"PAM test error: {e}")
            finally:
                self.proc = None
                self.set_busy(False)

        threading.Thread(target=_worker, daemon=True).start()

    def on_delete_single(self, _btn):
        finger = self.finger_combo.get_active_id()
        if not finger:
            return
        shutil.rmtree(os.path.join(TEMPLATES_BASE_DIR, finger), ignore_errors=True)
        user = os.environ.get("USER", "shohag-rana")
        shutil.rmtree(f"/var/lib/goodix-gm168/templates/{user}/{finger}", ignore_errors=True)
        shutil.rmtree(f"/var/lib/goodix-gm168/templates/root/{finger}", ignore_errors=True)
        user_config_dir = os.path.expanduser(f"~/.config/goodix-gm168/templates/{finger}")
        shutil.rmtree(user_config_dir, ignore_errors=True)
        self.set_banner("banner-idle", f"Deleted {finger}", "Profile cleared.")
        self.refresh_status()

    def on_delete_all(self, _btn):
        shutil.rmtree(TEMPLATES_BASE_DIR, ignore_errors=True)
        os.makedirs(TEMPLATES_BASE_DIR, exist_ok=True)
        user = os.environ.get("USER", "shohag-rana")
        shutil.rmtree(f"/var/lib/goodix-gm168/templates/{user}", ignore_errors=True)
        shutil.rmtree("/var/lib/goodix-gm168/templates/root", ignore_errors=True)
        user_config_dir = os.path.expanduser("~/.config/goodix-gm168/templates")
        shutil.rmtree(user_config_dir, ignore_errors=True)
        self.set_banner("banner-idle", "Profiles Cleared", "All enrolled prints deleted.")
        self.refresh_status()


if __name__ == "__main__":
    app = ModernBiometricApp()
    sys.exit(app.run(sys.argv))
