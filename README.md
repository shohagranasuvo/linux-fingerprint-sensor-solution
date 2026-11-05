# Goodix GM168 Linux Biometric Driver & DRFM Engine

[![Ubuntu](https://img.shields.io/badge/Ubuntu-22.04%20%7C%2024.04-orange?logo=ubuntu)](https://ubuntu.com)
[![Architecture](https://img.shields.io/badge/Arch-x86__64-blue)]()
[![Hardware](https://img.shields.io/badge/Goodix-27c6%3A581a%20%7C%2027c6%3A589a-green)]()
[![FAR](https://img.shields.io/badge/FAR-Zero--FAR%20Verified-brightgreen)]()
[![License](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](LICENSE)

Native Linux driver, PAM authentication module, and GTK3 management suite for **Goodix GM168** capacitive fingerprint sensors. 

Provides **Windows Hello Enclave Parity** on Linux using our reverse-engineered **Dense Ridge-Flow Matcher (DRFM)** and **Zero-FAR Security Gates**, fully replacing the broken `fprintd` Bozorth3 engine.

---

## 💻 Supported Hardware

| Component | Identifier |
| :--- | :--- |
| **Vendor / Chip** | Shenzhen Goodix Technology Co., Ltd. (GM168 / GM168SEC) |
| **USB Device IDs** | `27c6:581a`, `27c6:589a` |
| **Supported Laptops** | **Xiaomi RedmiBook Pro 14S**<br>**Xiaomi RedmiBook Pro 15**<br>**Xiaomi Mi NoteBook Ultra** |
| **Tested OS** | Ubuntu 22.04 LTS, Ubuntu 24.04 LTS, Debian 12+, Linux Mint |

---

## ⚡ The Breakthrough: Why Standard Linux Failed & How We Fixed It

### The Problem in Standard Linux
Standard Linux `fprintd` / `libfprint` relies on the **NIST Bozorth3** algorithm, which requires at least 12 distinct minutiae points (ridge endings and bifurcations). Because the Goodix GM168 is a compact $80 \times 64$ sensor patch ($\sim 4 \times 3\text{ mm}$), it only captures 3 to 6 minutiae points, causing standard Linux authentication to permanently fail.

### The Reverse-Engineered (REA) Solution
By reverse-engineering the official Goodix Windows Hello driver (`GoodixEngineAdapter.dll` and `AdapterEnclave.signed.dll`), we replicated Windows Hello's proprietary architecture in open-source Python and C:

1. **Dense Ridge-Flow Matching (DRFM)**: Extracts 700+ dense ridge gradient descriptors across the entire capacitive matrix instead of counting sparse minutiae points.
2. **2D Affine Partial RANSAC**: Invariant to finger tilt and arbitrary $360^\circ$ orientation angles.
3. **Multi-Octave Physical Spatial Deduplication**: Filters pyramid octave responses with a 6x6 pixel spatial non-maximum suppression grid so multi-scale duplicates of the same point are not inflated.
4. **`valid_area` Bounding Spread**: Mandates $\ge 12\%$ active sensor surface coverage ($42\text{ px} \times 34\text{ px}$ span) to reject localized skin smudges or scratches.
5. **Dense Ridge Patch NCC**: Normalized Cross-Correlation ($\ge 0.28$) across warped overlapping ridge contours.
6. **Adaptive Template Study (`gx_templateStudy`)**: High-confidence authentications dynamically enrich the stored templates over time to accommodate pressure, seasonal changes, and skin moisture.
7. **Zero-FAR Dual-Path Gate**: Guarantees zero false acceptances while providing instantaneous recognition for enrolled fingers.

---

## 🚀 One-Line Installation (Ubuntu / Debian)

Open a terminal and run this single command:

```bash
curl -fsSL https://raw.githubusercontent.com/shohagranasuvo/goodix-gm168-linux-driver/main/install.sh | sudo bash
```

### Alternative: Install via `.deb` Package

You can also download the pre-built `.deb` from the [Releases](https://github.com/shohagranasuvo/goodix-gm168-linux-driver/releases) page and install:

```bash
sudo dpkg -i goodix-gm168-linux-driver_1.0.0_amd64.deb
sudo apt-get install -f   # Fixes any missing dependencies automatically
```

---

## 👆 How to Use

### 1. Launch the Biometric Suite
* **From Desktop**: Click the **Goodix Fingerprint Suite** icon on your Desktop or Application Menu.
* **From Terminal**:
  ```bash
  python3 /usr/local/lib/goodix-gm168/fingerprint_gui.py
  ```

### 2. Enroll Your Finger
1. In the application, select your profile (e.g., `Right Index`).
2. Click **`👆 2. 16-Stage Enroll`**.
3. Place your finger on the sensor 16 times as prompted, slightly tilting your finger to capture edges, center, and tip (identical to the Windows Hello enrollment experience).

### 3. Test & Verify
* Click **`⚡ 3. DRFM Verify (Adaptive)`** to see live match visualization with green inlier consensus vectors and telemetry metrics.
* Click **`🔑 4. System Auth`** to verify system PAM unlock readiness.

### 4. System Unlock in Everyday Use
Once enrolled, your sensor unlocks the system everywhere:
* **Terminal `sudo`**: Run `sudo ls` or any administrative command and touch the sensor.
* **Lock Screen**: Press `Super + L` to lock your screen, then touch the sensor to unlock instantly.
* **GDM / LightDM Login**: Log in to your desktop session with your fingerprint.

---

## 🛠️ Repository Architecture

```text
goodix-gm168-linux-driver/
├── src/
│   ├── dense_matcher.py        # DRFM Zero-FAR Engine (Windows Hello Enclave Parity)
│   ├── pam_goodix_verify.py    # Linux PAM Biometric Authentication Module
│   ├── goodix_daemon.py        # Optional Biometric Service Daemon
│   └── fingerprint_gui.py      # GTK3 Goodix Biometric Suite Application
├── bin/
│   ├── img-capture             # Standalone sensor frame acquisition tool
│   └── libfprint-2.so.2.0.0    # Compiled libfprint library with GM168 protocol support
├── udev/
│   └── 60-goodix-gm168.rules   # Udev device access rules for 27c6:581a / 27c6:589a
├── desktop/
│   └── goodix-fingerprint.desktop # Desktop launcher
├── packaging/
│   └── build_deb.sh            # Debian package builder
├── install.sh                  # One-line automated Ubuntu/Debian installer
├── uninstall.sh                # Clean uninstaller
├── LICENSE                     # GNU General Public License v3.0
└── README.md                   # Documentation
```

---

## 🗑️ Uninstallation

If you ever wish to remove the driver and restore default system PAM settings:

```bash
sudo /usr/local/lib/goodix-gm168/uninstall.sh
```
*(Or clone this repo and run `sudo ./uninstall.sh`)*

---

## 🤝 Contributing & Acknowledgments
* **Author**: [Shohag Rana Suvo](https://github.com/shohagranasuvo)
* **Reverse Engineering**: Goodix Windows Biometric Driver Interface (WBDI) analysis via REA.
* **Upstream Research**: Thanks to the `goodix-fp-linux-dev` community and `yakoshiq` for foundational GM168 USB descriptor research.

---

## 📄 License
This project is licensed under the [GNU General Public License v3.0](LICENSE).
