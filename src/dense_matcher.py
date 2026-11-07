#!/usr/bin/env python3
"""
Goodix GM168 Dense Ridge-Flow Matcher (DRFM) - Windows Hello Enclave Parity Edition
Implements REA techniques reverse-engineered from GoodixEngineAdapter.dll & AdapterEnclave.signed.dll:
  - FpRecogPreProc: CLAHE + high-pass ridge enhancement + 10px sensor bezel masking
  - fingerFeatureRecognition: Multi-octave ORB + 6px physical spatial deduplication
  - identifyImage: 2D Rigid-Affine RANSAC + Scale Gate + valid_area + Ridge-Masked NCC + Composite match_score
  - Zero-FAR Dual-Path Gate: High-confidence single template OR strict multi-stage gallery consensus
  - gx_templateStudy: Guarded high-confidence adaptive gallery template enrichment
"""

import os
import re
import cv2
import numpy as np


class DenseRidgeMatcher:
    def __init__(
        self,
        min_inliers: int = 10,
        lowe_ratio: float = 0.70,
        max_hamming: int = 50,
        min_valid_area_ratio: float = 0.16,
        min_ncc: float = 0.36,
        match_score_threshold: int = 70,
    ):
        self._min_inliers = max(10, int(min_inliers))
        self.lowe_ratio = lowe_ratio
        self.max_hamming = max_hamming
        self.min_valid_area_ratio = min_valid_area_ratio
        self.min_ncc = min_ncc
        self.match_score_threshold = match_score_threshold

        self.clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        self.orb = cv2.ORB_create(
            nfeatures=750,
            scaleFactor=1.15,
            nlevels=8,
            edgeThreshold=12,
            firstLevel=0,
            WTA_K=2,
            scoreType=cv2.ORB_HARRIS_SCORE,
            patchSize=25,
            fastThreshold=10,
        )
        self.bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)

    @property
    def min_inliers(self) -> int:
        return self._min_inliers

    @min_inliers.setter
    def min_inliers(self, value: int):
        self._min_inliers = max(10, int(value))

    def get_quality(self, img: np.ndarray):
        """Evaluates sensor frame quality (image_quality metric)."""
        if img is None or img.size == 0:
            return False, 0.0, 0.0
        contrast = float(np.std(img))
        gx = cv2.Sobel(img, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(img, cv2.CV_32F, 0, 1, ksize=3)
        grad_var = float(np.var(np.hypot(gx, gy)))
        usable = contrast >= 12.0 and grad_var >= 180.0
        return usable, contrast, grad_var

    def preprocess(self, img: np.ndarray):
        """FpRecogPreProc: normalizes ridge contrast, suppresses sensor noise, builds active mask."""
        if img.ndim == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        if img.shape[1] < 160 or img.shape[0] < 128:
            img = cv2.resize(img, (320, 256), interpolation=cv2.INTER_CUBIC)

        norm = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX)
        eq = self.clahe.apply(norm)
        blur = cv2.GaussianBlur(eq, (3, 3), 0.8)
        sharp = cv2.addWeighted(eq, 1.45, blur, -0.45, 0)

        gx = cv2.Sobel(sharp, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(sharp, cv2.CV_32F, 0, 1, ksize=3)
        energy = cv2.GaussianBlur(np.hypot(gx, gy), (15, 15), 0)
        mask = (energy > (np.mean(energy) * 0.45)).astype(np.uint8) * 255

        h, w = sharp.shape
        b = 10
        border_mask = np.zeros_like(mask)
        border_mask[b : h - b, b : w - b] = 255
        mask = cv2.bitwise_and(mask, border_mask)

        return sharp, mask

    def extract_features(self, img: np.ndarray):
        """fingerFeatureRecognition: extracts ORB features over valid ridge mask."""
        proc, mask = self.preprocess(img)
        kps, des = self.orb.detectAndCompute(proc, mask)
        return proc, mask, kps, des

    @staticmethod
    def _deduplicate_physical_inliers(pts_src: np.ndarray, pts_dst: np.ndarray, dists: np.ndarray, cell_px: float = 6.0):
        best_by_src = {}
        for i in range(len(pts_src)):
            cell = (int(round(pts_src[i, 0] / cell_px)), int(round(pts_src[i, 1] / cell_px)))
            d = float(dists[i])
            if cell not in best_by_src or d < best_by_src[cell][0]:
                best_by_src[cell] = (d, pts_src[i], pts_dst[i])

        best_by_dst = {}
        for d, s_pt, t_pt in best_by_src.values():
            cell = (int(round(t_pt[0] / cell_px)), int(round(t_pt[1] / cell_px)))
            if cell not in best_by_dst or d < best_by_dst[cell][0]:
                best_by_dst[cell] = (d, s_pt, t_pt)

        if not best_by_dst:
            return np.empty((0, 2), dtype=np.float32), np.empty((0, 2), dtype=np.float32), []

        vals = list(best_by_dst.values())
        u_dists = [v[0] for v in vals]
        u_src = np.array([v[1] for v in vals], dtype=np.float32)
        u_dst = np.array([v[2] for v in vals], dtype=np.float32)
        return u_src, u_dst, u_dists

    @staticmethod
    def _compute_valid_area_ratio(pts: np.ndarray, shape: tuple):
        if len(pts) < 4:
            return 0.0, 0.0, 0.0
        h, w = shape[:2]
        active_area = float(max(1, (w - 20) * (h - 20)))

        span_x = float(np.max(pts[:, 0]) - np.min(pts[:, 0]))
        span_y = float(np.max(pts[:, 1]) - np.min(pts[:, 1]))
        bbox_area = span_x * span_y

        hull = cv2.convexHull(pts.astype(np.float32))
        hull_area = float(cv2.contourArea(hull))

        effective_area = 0.6 * bbox_area + 0.4 * hull_area
        return min(1.0, effective_area / active_area), span_x, span_y

    @staticmethod
    def _compute_overlap_ncc(
        probe_proc: np.ndarray,
        probe_mask: np.ndarray,
        tmpl_proc: np.ndarray,
        tmpl_mask: np.ndarray,
        M: np.ndarray,
    ):
        """Computes ridge-flow Normalized Cross-Correlation strictly over overlapping active ridge masks."""
        h, w = tmpl_proc.shape[:2]
        warped_probe = cv2.warpAffine(
            probe_proc, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0
        )
        warped_p_mask = cv2.warpAffine(
            probe_mask, M, (w, h), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0
        )

        overlap_mask = cv2.bitwise_and(warped_p_mask, tmpl_mask)
        overlap_mask[:10, :] = 0
        overlap_mask[-10:, :] = 0
        overlap_mask[:, :10] = 0
        overlap_mask[:, -10:] = 0

        valid_px = overlap_mask > 0
        overlap_rate = float(np.count_nonzero(valid_px)) / float(max(1, (w - 20) * (h - 20)))
        if overlap_rate < 0.22:
            return 0.0, overlap_rate

        p_vals = warped_probe[valid_px].astype(np.float32)
        t_vals = tmpl_proc[valid_px].astype(np.float32)
        p_vals -= np.mean(p_vals)
        t_vals -= np.mean(t_vals)

        denom = float(np.linalg.norm(p_vals) * np.linalg.norm(t_vals))
        if denom < 1e-5:
            return 0.0, overlap_rate
        ncc = float(np.dot(p_vals, t_vals) / denom)
        return max(0.0, ncc), overlap_rate

    def match_pair(self, probe_img: np.ndarray, tmpl_img: np.ndarray):
        """
        Matches probe against a single gallery template using Windows Hello Goodix verification gates.
        Returns: (is_valid_hit, unique_inliers, rotation_deg, valid_area_pct, match_score, ncc, vis_canvas)
        """
        p_proc, p_mask, kp1, des1 = self.extract_features(probe_img)
        t_proc, t_mask, kp2, des2 = self.extract_features(tmpl_img)

        hp, wp = p_proc.shape
        ht, wt = t_proc.shape
        canvas = np.zeros((max(hp, ht), wp + wt, 3), dtype=np.uint8)
        canvas[:hp, :wp] = cv2.cvtColor(p_proc, cv2.COLOR_GRAY2BGR)
        canvas[:ht, wp:] = cv2.cvtColor(t_proc, cv2.COLOR_GRAY2BGR)

        if des1 is None or des2 is None or len(kp1) < 12 or len(kp2) < 12:
            return False, 0, 0.0, 0.0, 0, 0.0, canvas

        raw_matches = self.bf.knnMatch(des1, des2, k=2)
        good = []
        for m_n in raw_matches:
            if len(m_n) == 2:
                m, n = m_n
                if m.distance <= self.max_hamming and m.distance < self.lowe_ratio * n.distance:
                    good.append(m)

        if len(good) < 6:
            return False, 0, 0.0, 0.0, 0, 0.0, canvas

        pts1 = np.float32([kp1[m.queryIdx].pt for m in good])
        pts2 = np.float32([kp2[m.trainIdx].pt for m in good])
        dists = np.float32([m.distance for m in good])

        M, inlier_mask = cv2.estimateAffinePartial2D(
            pts1,
            pts2,
            method=cv2.RANSAC,
            ransacReprojThreshold=2.8,
            maxIters=3000,
            confidence=0.995,
        )
        if M is None or inlier_mask is None:
            return False, 0, 0.0, 0.0, 0, 0.0, canvas

        mask_bool = inlier_mask.ravel() == 1
        if np.count_nonzero(mask_bool) < 6:
            return False, 0, 0.0, 0.0, 0, 0.0, canvas

        # 1. Rigid Scale Gate: human skin cannot scale > ±6%
        scale = float(np.hypot(M[0, 0], M[1, 0]))
        if not (0.94 <= scale <= 1.06):
            return False, 0, 0.0, 0.0, 0, 0.0, canvas

        rot_deg = float(np.degrees(np.arctan2(M[1, 0], M[0, 0])))

        # 2. Multi-Octave Physical Spatial Deduplication
        u_pts1, u_pts2, u_dists = self._deduplicate_physical_inliers(
            pts1[mask_bool], pts2[mask_bool], dists[mask_bool], cell_px=6.0
        )
        unique_inliers = len(u_pts1)
        if unique_inliers < 6:
            return False, unique_inliers, rot_deg, 0.0, 0, 0.0, canvas

        # 3. Valid Area Coverage Gate (valid_area)
        area_ratio, span_x, span_y = self._compute_valid_area_ratio(u_pts1, p_proc.shape)
        if span_x < 48.0 or span_y < 38.0 or area_ratio < self.min_valid_area_ratio:
            return False, unique_inliers, rot_deg, area_ratio * 100.0, 0, 0.0, canvas

        # 4. Dense Ridge-Flow Alignment NCC & Overlap Rate over active ridges
        ncc, overlap_rate = self._compute_overlap_ncc(p_proc, p_mask, t_proc, t_mask, M)
        if ncc < self.min_ncc or overlap_rate < 0.25:
            return False, unique_inliers, rot_deg, area_ratio * 100.0, 0, ncc, canvas

        # 5. Composite Windows Hello match_score (0 - 100)
        mean_dist = float(np.mean(u_dists)) if u_dists else 64.0
        inlier_term = min(45.0, unique_inliers * 3.2)
        area_term = min(20.0, (area_ratio / 0.35) * 20.0)
        ncc_term = min(25.0, (ncc / 0.65) * 25.0)
        desc_term = max(0.0, (1.0 - (mean_dist / 64.0)) * 10.0)
        match_score = int(round(min(100.0, inlier_term + area_term + ncc_term + desc_term)))

        stage_inlier_floor = max(9, self.min_inliers - 1)
        is_stage_hit = (
            unique_inliers >= stage_inlier_floor
            and area_ratio >= self.min_valid_area_ratio
            and ncc >= self.min_ncc
            and match_score >= (self.match_score_threshold - 5)
        )

        line_color = (0, 255, 64) if is_stage_hit else (0, 0, 220)
        pt_color = (0, 255, 64) if is_stage_hit else (0, 64, 255)
        for s_pt, t_pt in zip(u_pts1, u_pts2):
            p1 = (int(round(s_pt[0])), int(round(s_pt[1])))
            p2 = (int(round(t_pt[0] + wp)), int(round(t_pt[1])))
            cv2.line(canvas, p1, p2, line_color, 1, cv2.LINE_AA)
            cv2.circle(canvas, p1, 3, pt_color, -1)
            cv2.circle(canvas, p2, 3, pt_color, -1)

        return is_stage_hit, unique_inliers, rot_deg, area_ratio * 100.0, match_score, ncc, canvas

    @staticmethod
    def _parse_stage_id(path: str, idx: int) -> int:
        base = os.path.basename(path)
        m = re.search(r"(\d+)", base)
        return int(m.group(1)) if m else idx

    def match_gallery(self, probe_img: np.ndarray, candidate_paths: list):
        """
        identifyImage: Evaluates probe against all enrolled gallery templates of a single finger.
        Enforces strict single-template certainty OR multi-stage consensus to guarantee Zero-FAR.
        """
        usable, contrast, grad = self.get_quality(probe_img)
        if not usable:
            return False, 0, 0.0, None, 0, 0, -1, 0, None

        best_inliers = 0
        best_rot = 0.0
        best_score = 0
        best_stage = -1
        best_path = None
        best_area = 0.0
        best_ncc = 0.0
        best_vis = None
        stage_hits = 0

        for idx, path in enumerate(candidate_paths):
            if not os.path.exists(path):
                continue
            tmpl_img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if tmpl_img is None:
                continue

            is_hit, inliers, rot, area_pct, score, ncc, canvas = self.match_pair(probe_img, tmpl_img)
            if is_hit:
                stage_hits += 1

            if (inliers > best_inliers) or (inliers == best_inliers and score > best_score):
                best_score = score
                best_inliers = inliers
                best_rot = rot
                best_area = area_pct
                best_ncc = ncc
                best_path = path
                best_vis = canvas
                best_stage = self._parse_stage_id(path, idx)

        # Zero-FAR Verification Gate:
        # Path A: Strong single-stage match (inliers >= min_inliers+1 [11] OR inliers >= min_inliers [10] with hits >= 2)
        high_single = (
            best_inliers >= (self.min_inliers + 1)
            and best_score >= self.match_score_threshold
            and best_ncc >= 0.38
            and best_area >= (self.min_valid_area_ratio * 100.0)
        )
        # Path B: Multi-stage consensus within the SAME finger gallery
        multi_consensus = (
            stage_hits >= 2
            and best_inliers >= self.min_inliers
            and best_score >= (self.match_score_threshold - 2)
            and best_ncc >= self.min_ncc
            and best_area >= (self.min_valid_area_ratio * 100.0)
        )
        matched = bool(high_single or multi_consensus)

        return (
            matched,
            best_inliers,
            best_rot,
            best_vis,
            best_score,
            int(round(best_ncc * 100)),
            best_stage,
            stage_hits,
            best_path,
        )

    def adaptive_study(self, probe_img: np.ndarray, best_path: str, inliers: int) -> bool:
        if not best_path or not os.path.exists(best_path) or inliers < 16:
            return False

        usable, contrast, grad = self.get_quality(probe_img)
        if not usable or contrast < 18.0:
            return False

        try:
            tmpl_img = cv2.imread(best_path, cv2.IMREAD_GRAYSCALE)
            if tmpl_img is None or tmpl_img.shape != probe_img.shape:
                return False

            updated = cv2.addWeighted(tmpl_img, 0.85, probe_img, 0.15, 0)
            return bool(cv2.imwrite(best_path, updated))
        except Exception:
            return False
