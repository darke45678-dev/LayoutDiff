"""
Alignment Engine for Variable Layouts.
Uses per-layout saved fiducial reference templates (TL, TR, BL, BR) to align target sheets.
Supports fast bypass on identical coordinates, sub-pixel/integer translation, and reports REVIEW on low confidence.
"""

from typing import Dict, Any, Tuple, Optional
import cv2
import numpy as np


class VariableSheetAligner:
    """Aligns target sheet against Golden using per-layout fiducial templates."""

    def __init__(self, fiducial_cfg: Dict[str, Any], fiducial_templates: Dict[str, np.ndarray]):
        self.cfg = fiducial_cfg or {}
        self.templates = fiducial_templates or {}
        self.points = self.cfg.get("points", {})
        self.search_window = self.cfg.get("search_window_px", 120)
        self.min_confidence = self.cfg.get("min_confidence", 0.60)

    def check_need_alignment(
        self,
        target_img: np.ndarray,
        golden_img: np.ndarray
    ) -> Tuple[bool, float, str]:
        """
        Quick check if target_img is already registered with golden_img at (0, 0).
        """
        if target_img is None or golden_img is None:
            return True, 0.0, "影像為空"
        if target_img.shape != golden_img.shape:
            return True, 0.0, f"尺寸不一致 ({target_img.shape} vs {golden_img.shape})"

        t_gray = cv2.cvtColor(target_img, cv2.COLOR_BGR2GRAY) if target_img.ndim == 3 else target_img
        g_gray = cv2.cvtColor(golden_img, cv2.COLOR_BGR2GRAY) if golden_img.ndim == 3 else golden_img

        scores = []
        pad = 40
        h, w = g_gray.shape[:2]

        for name, pt in self.points.items():
            gx, gy = int(round(pt["x"])), int(round(pt["y"]))
            x1, x2 = max(0, gx - pad), min(w, gx + pad)
            y1, y2 = max(0, gy - pad), min(h, gy + pad)

            patch_g = g_gray[y1:y2, x1:x2]
            patch_t = t_gray[y1:y2, x1:x2]

            if patch_g.shape == patch_t.shape and patch_g.size > 0:
                res = cv2.matchTemplate(patch_t, patch_g, cv2.TM_CCOEFF_NORMED)
                scores.append(float(res[0, 0]))

        if not scores:
            return True, 0.0, "無法比對基準準星"

        min_score = min(scores)
        mean_score = sum(scores) / len(scores)

        if min_score >= 0.985:
            return False, mean_score, f"座標一致 (相關度均分={mean_score:.4f} >= 0.985，直接比對)"

        return True, mean_score, f"存在幾何偏差 (最低準星相關度={min_score:.4f} < 0.985，啟動對位)"

    def align_sheet(
        self,
        target_img: np.ndarray,
        golden_img: np.ndarray
    ) -> Tuple[np.ndarray, Optional[np.ndarray], bool, str, float]:
        """
        Aligns target_img to golden_img using the 4 fiducial templates.
        Returns: (aligned_img, M, is_valid, msg, avg_score)
        """
        gh, gw = golden_img.shape[:2]
        t_gray = cv2.cvtColor(target_img, cv2.COLOR_BGR2GRAY) if target_img.ndim == 3 else target_img
        th, tw = t_gray.shape[:2]

        src_pts = []
        dst_pts = []
        scores = []

        search_pad = self.search_window

        for name, pt in self.points.items():
            tmpl = self.templates.get(name)
            if tmpl is None or tmpl.size == 0:
                continue

            gx, gy = int(round(pt["x"])), int(round(pt["y"]))
            tmpl_gray = cv2.cvtColor(tmpl, cv2.COLOR_BGR2GRAY) if tmpl.ndim == 3 else tmpl
            t_h, t_w = tmpl_gray.shape[:2]

            # Search ROI around golden point in target image
            sx1 = max(0, gx - search_pad)
            sx2 = min(tw, gx + search_pad)
            sy1 = max(0, gy - search_pad)
            sy2 = min(th, gy + search_pad)

            search_roi = t_gray[sy1:sy2, sx1:sx2]
            if search_roi.shape[0] < t_h or search_roi.shape[1] < t_w:
                continue

            match_res = cv2.matchTemplate(search_roi, tmpl_gray, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(match_res)

            scores.append(max_val)
            matched_x = sx1 + max_loc[0] + t_w / 2.0
            matched_y = sy1 + max_loc[1] + t_h / 2.0

            src_pts.append([matched_x, matched_y])
            dst_pts.append([gx, gy])

        if len(src_pts) < 3:
            return target_img, None, False, f"有效準星數量不足 ({len(src_pts)} < 3)", 0.0

        mean_conf = sum(scores) / len(scores)
        if mean_conf < self.min_confidence:
            return target_img, None, False, f"準星比對信心度過低 ({mean_conf:.3f} < {self.min_confidence:.2f})", mean_conf

        src_arr = np.float32(src_pts)
        dst_arr = np.float32(dst_pts)

        # Estimate partial affine (translation, rotation, uniform scale)
        M, inliers = cv2.estimateAffinePartial2D(src_arr, dst_arr)
        if M is None:
            return target_img, None, False, "仿射矩陣估計失敗", mean_conf

        # Check if transformation is pure integer translation (no rotation, scale=1.0, integer shift)
        scale = np.hypot(M[0, 0], M[1, 0])
        rot = np.degrees(np.arctan2(M[1, 0], M[0, 0]))
        tx, ty = M[0, 2], M[1, 2]

        is_pure_int_translation = (
            abs(scale - 1.0) < 1e-5 and
            abs(rot) < 1e-4 and
            abs(tx - round(tx)) < 1e-4 and
            abs(ty - round(ty)) < 1e-4
        )

        flags = cv2.INTER_NEAREST if is_pure_int_translation else cv2.INTER_LINEAR

        aligned_img = cv2.warpAffine(
            target_img,
            M,
            (gw, gh),
            flags=flags,
            borderMode=cv2.BORDER_REPLICATE
        )

        return aligned_img, M, True, f"對位成功 (平均相關度 {mean_conf:.3f})", mean_conf
