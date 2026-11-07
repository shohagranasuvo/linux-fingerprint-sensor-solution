# Goodix GM168 Linux Biometric Driver & DRFM Suite
### Zero-FAR Windows Hello Parity for Linux (Ubuntu / Debian / Linux Mint)

[![Ubuntu](https://img.shields.io/badge/Ubuntu-22.04%20%7C%2024.04%20%7C%2026.04-orange?logo=ubuntu)](https://ubuntu.com)
[![Architecture](https://img.shields.io/badge/Arch-x86__64-blue)]()
[![Hardware](https://img.shields.io/badge/Goodix-27c6%3A581a%20%7C%2027c6%3A589a-green)]()
[![Security](https://img.shields.io/badge/Security-Zero--FAR%20Verified-brightgreen)]()
[![Release](https://img.shields.io/badge/Release-v1.1.0-blue)](https://github.com/shohagranasuvo/linux-fingerprint-sensor-solution/releases)
[![License](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](LICENSE)

A complete, production-ready Linux biometric driver, PAM authentication stack, and modern GTK4 management application for **Goodix GM168** capacitive fingerprint sensors.

Replaces the broken default Linux `fprintd` / Bozorth3 stack with a reverse-engineered **Dense Ridge-Flow Matcher (DRFM)** and **Adaptive Template Learning (`gx_templateStudy`)**, delivering the seamless **Windows Hello** unlock experience on Linux.

---

## 💻 Supported Hardware & Systems

| Item | Details |
| :--- | :--- |
| **Supported Chips** | Shenzhen Goodix Technology Co., Ltd. (**GM168** / **GM168SEC**) |
| **USB Device IDs** | `27c6:581a`, `27c6:589a` |
| **Verified Laptops** | • **Xiaomi RedmiBook Pro 14S**<br>• **Xiaomi RedmiBook Pro 15**<br>• **Xiaomi Mi NoteBook Ultra**<br>• Other laptops utilizing Goodix 581a / 589a sensors |
| **Supported OS** | • **Ubuntu** 22.04 LTS, 24.04 LTS, 24.10, 26.04<br>• **Debian** 12 (Bookworm) & newer<br>• **Linux Mint** 21+<br>• **Pop!_OS** 22.04+ |

---

## ⚡ The Breakthrough: Why Standard Linux Failed & How We Solved It

### The Problem with Stock Linux (`fprintd` / `libfprint`)
Standard Linux biometric packages rely on the **NIST Bozorth3** minutiae matching algorithm. Bozorth3 requires at least 12 distinct minutiae points (ridge endings and bifurcations). Because the Goodix GM168 is an ultra-compact $80 \times 64$ patch sensor ($\sim 4 \times 3\text{ mm}$), it only produces 3 to 6 minutiae points per touch. Consequently:
- Enrolling fails or takes dozens of failed attempts.
- Verification fails virtually 100% of the time.

### The Reverse-Engineered (REA) Solution
By analyzing the official Windows Hello driver (`GoodixEngineAdapter.dll` and `AdapterEnclave.signed.dll`), we implemented Windows Hello's biometric pipeline for Linux:

1. **Dense Ridge-Flow Matcher (DRFM)**: Extracts 700+ dense spatial ridge descriptors across the full sensor frame instead of counting sparse minutiae points.
2. **2D Rigid-Affine RANSAC**: Invariant to arbitrary touch angles ($360^\circ$ rotation) and finger tilt.
3. **Multi-Octave Physical Spatial Deduplication**: Eliminates multi-scale pyramid duplicates using a 6x6 pixel spatial non-maximum suppression grid.
4. **Active Ridge-Masked NCC**: Normalized Cross-Correlation computed strictly over overlapping ridge contours.
5. **Adaptive Template Study (`gx_templateStudy`)**: Every high-confidence unlock dynamically enriches stored templates over time, effortlessly adapting to seasonal skin changes, moisture, and pressure differences.
6. **Zero-FAR Security Gate**: Enforces strict area spread ($\ge 16\%$) and consensus thresholds, preventing false acceptances while keeping unlock instant.

---

## 🚀 Easy Installation

### Option A: One-Line Installer (Recommended)

Open your terminal and run:

```bash
curl -fsSL https://raw.githubusercontent.com/shohagranasuvo/linux-fingerprint-sensor-solution/main/install.sh | sudo bash
```

The script automatically detects your system ABI, installs dependencies, builds rules, stops conflicting background daemons, and registers DRFM PAM authentication.

---

### Option B: Pre-Built `.deb` Package

Download and install the Debian package directly from GitHub Releases:

```bash
wget https://github.com/shohagranasuvo/linux-fingerprint-sensor-solution/releases/download/v1.1.0/goodix-gm168-linux-driver_1.1.0_amd64.deb
sudo dpkg -i goodix-gm168-linux-driver_1.1.0_amd64.deb
sudo apt-get install -f
```

---

### Option C: Install from Source

```bash
git clone https://github.com/shohagranasuvo/linux-fingerprint-sensor-solution.git
cd linux-fingerprint-sensor-solution
sudo ./install.sh
```

---

## 👆 How to Enroll and Use

### 1. Open the Biometric Application
* **From Application Menu**: Search for **Goodix Fingerprint Suite**.
* **From Terminal**:
  ```bash
  goodix-enroll
  ```
  *(or `python3 /usr/local/lib/goodix-gm168/fingerprint_gui.py`)*

---

### 2. Enroll Your Finger (16-Stage Windows Hello Parity)
1. Select your target finger profile (e.g. `Right Index (Profile 1 - Flat Center)`).
2. Click **`👆 2. 16-Stage Enroll`**.
3. Follow the on-screen coach through the 4 anatomical zones:
   - **Stages 1–4 (🎯 Center Pad)**: Place flat center of your finger pad firmly on sensor.
   - **Stages 5–8 (👆 Fingertip)**: Tilt your finger forward (~30°) using the tip.
   - **Stages 9–12 (👈 Left Edge)**: Roll your finger slightly to the left side edge.
   - **Stages 13–16 (👉 Right Edge)**: Roll your finger slightly to the right side edge.

> [!TIP]
> **Important**: When each stage succeeds, the banner turns blue (**✋ LIFT FINGER OFF SENSOR**). **Lift your finger completely off the sensor for ~1.8 seconds**. This allows the capacitive sensing channels to reset their baseline before the next stage.

---

### 3. Verify Your Fingerprint
* Click **`⚡ 3. DRFM Verify (Adaptive)`**: Touch the sensor at any angle to test matching against the enrolled 16-stage gallery with live keypoint visualization and telemetry score.
* Click **`🔑 4. System Auth`**: Runs a live PAM authentication test (`pam_goodix_verify.py`).

---

### 4. Seamless System Unlocking in Daily Use
Once enrolled, your fingerprint sensor unlocks Linux everywhere automatically:

* **Sudo in Terminal**: Run `sudo ls` or any administrative command and touch the sensor to authorize.
* **Lock Screen**: Press `Super + L` to lock your desktop, then touch the sensor to unlock instantly.
* **GDM / Desktop Login**: Touch the sensor to log into your user session.

---

## ⚙️ Calibration & Thresholds

The application includes an in-app sensitivity calibration slider:
* 🟢 **Sensitive (`4` Inliers)**: Quickest matching; great for dry skin or light taps.
* 🔵 **Balanced (`6` Inliers)**: Recommended default setting.
* 🔒 **Strict (`8+` Inliers)**: Highest security margin.

---

## 🔍 Troubleshooting & FAQ

<details>
<summary><b>1. "Touch not detected" or capture timeout during enrollment</b></summary>

- **Make sure to lift your finger completely between stages**: The Goodix sensor driver calibrates its idle capacitance baseline (`ch >= 340`) when initializing each stage. If your finger stays on the sensor, baseline calibration cannot occur.
- Press firmly and cover the active sensor area.
</details>

<details>
<summary><b>2. "Unit fprintd.service is masked" message</b></summary>

This is intentional and expected. Standard `fprintd` conflicts with Goodix bulk USB endpoints and corrupts TLS frames. The installer masks `fprintd` and delegates authentication to our standalone DRFM PAM engine.
</details>

<details>
<summary><b>3. How do I clear enrolled templates?</b></summary>

Open `goodix-enroll`, choose the profile, and click **`🗑️ Delete`** to remove a single finger profile, or **`Clear All`** to remove all enrolled profiles.
</details>

---

## 🛠️ Repository File Structure

```text
linux-fingerprint-sensor-solution/
├── src/
│   ├── dense_matcher.py        # DRFM Zero-FAR Engine (Windows Hello Enclave Parity)
│   ├── pam_goodix_verify.py    # Linux PAM Biometric Authentication Module
│   ├── goodix_daemon.py        # Biometric Service Daemon
│   └── fingerprint_gui.py      # GTK4 Modern Biometric Management Application
├── bin/
│   ├── img-capture             # Hardware sensor frame acquisition binary
│   └── libfprint-2.so.2.0.0    # Compiled libfprint library with GM168 protocol support
├── udev/
│   └── 60-goodix-gm168.rules   # Udev device access rules (27c6:581a / 27c6:589a)
├── desktop/
│   └── goodix-fingerprint.desktop # Desktop application launcher
├── packaging/
│   ├── build_deb.sh            # Automated Debian .deb package builder
│   └── goodix-gm168-linux-driver_1.1.0_amd64.deb # Pre-built package
├── install.sh                  # One-line automated Ubuntu/Debian installer
├── uninstall.sh                # Clean uninstallation script
├── LICENSE                     # GNU General Public License v3.0
└── README.md                   # Documentation
```

---

## 🗑️ Uninstallation

To remove the driver and restore default system authentication settings:

```bash
sudo /usr/local/lib/goodix-gm168/uninstall.sh
```
*(Or run `sudo ./uninstall.sh` inside the cloned repository directory).*

---

## 🤝 Credits & Acknowledgments
* **Author & Lead Developer**: [Shohag Rana Suvo](https://github.com/shohagranasuvo)
* **Reverse Engineering**: Goodix Windows Biometric Driver Interface (WBDI) analysis via REA.
* **Community**: Thanks to the `goodix-fp-linux-dev` community and `yakoshiq` for foundational GM168 USB descriptor research.

---

## 📄 License
This project is licensed under the [GNU General Public License v3.0](LICENSE).
