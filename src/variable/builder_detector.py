"""Automatic, reviewable layout construction for variable label sheets."""

from collections import Counter
from itertools import product
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np


CORNER_NAMES = ("top_left", "top_right", "bottom_left", "bottom_right")


class LayoutBuilderDetector:
    """Detect corner fiducials and die candidates without a target die count."""

    @staticmethod
    def _gray(img: np.ndarray) -> np.ndarray:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img

    @staticmethod
    def _corner_distance(x: float, y: float, width: int, height: int, corner: str) -> float:
        dx = x if corner.endswith("left") else width - x
        dy = y if corner.startswith("top") else height - y
        return float(np.hypot(dx, dy))

    @classmethod
    def _fiducial_candidates(cls, img: np.ndarray, corner: str, roi_ratio: float = 0.18) -> List[Dict[str, Any]]:
        gray = cls._gray(img)
        h, w = gray.shape[:2]
        rw, rh = max(1, int(w * roi_ratio)), max(1, int(h * roi_ratio))
        x0 = 0 if corner.endswith("left") else w - rw
        y0 = 0 if corner.startswith("top") else h - rh
        roi = gray[y0:y0 + rh, x0:x0 + rw]
        border = np.concatenate((roi[0], roi[-1], roi[:, 0], roi[:, -1])).astype(np.float32)
        contrast = np.abs(roi.astype(np.float32) - float(np.median(border)))
        # A high percentile is dominated by dark label artwork on mixed sheets and can
        # erase thin low-contrast fiducial strokes. Cap the contrast threshold so the
        # corner marker remains a candidate even when the ROI contains large labels.
        threshold = max(18, min(75, int(np.percentile(contrast, 65) * 0.5)))
        binary = np.where(contrast >= threshold, 255, 0).astype(np.uint8)
        # A scale-relative close joins the arms of a cross without assuming a fixed image DPI.
        k = max(3, int(round(min(w, h) * 0.0015)))
        if k % 2 == 0:
            k += 1
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        min_side = max(8, int(round(min(w, h) * 0.003)))
        max_side = max(min_side + 1, int(round(min(w, h) * 0.06)))
        candidates = []
        for contour in contours:
            bx, by, bw, bh = cv2.boundingRect(contour)
            if not (min_side <= bw <= max_side and min_side <= bh <= max_side):
                continue
            aspect = bw / float(max(1, bh))
            if not 0.35 <= aspect <= 2.85 or cv2.contourArea(contour) < min_side * min_side * 0.08:
                continue
            cx, cy = x0 + bx + bw / 2.0, y0 + by + bh / 2.0
            radius = max(12, int(round(min(w, h) * 0.006)))
            x1, x2 = max(0, int(cx) - radius), min(w, int(cx) + radius)
            y1, y2 = max(0, int(cy) - radius), min(h, int(cy) + radius)
            patch = gray[y1:y2, x1:x2]
            if patch.size == 0:
                continue
            normalized = cv2.resize(patch, (40, 40), interpolation=cv2.INTER_AREA).astype(np.float32)
            normalized -= normalized.mean()
            norm = float(np.linalg.norm(normalized))
            if norm:
                normalized /= norm
            score = cls._corner_distance(cx, cy, w, h, corner)
            shape_quality = float(np.exp(-1.5 * abs(np.log(max(0.1, aspect)))))
            # Local quality is contrast amplitude normalized to [0, 1]. It is
            # deliberately independent of the global four-corner assignment score.
            p05, p95 = np.percentile(patch.astype(np.float32), [5, 95])
            local_contrast = float(max(0.0, p95 - p05))
            local_quality = float(np.clip(local_contrast / 180.0, 0.0, 1.0))
            candidates.append({"x": float(cx), "y": float(cy), "confidence": local_quality,
                               "local_contrast": local_contrast, "local_quality": local_quality,
                               "corner_dist": score, "shape_quality": shape_quality,
                               "appearance": normalized, "box": [bx, by, bw, bh]})
        # Keep nearby square/circular marks ahead of text blocks when distances are similar.
        candidates.sort(key=lambda c: (c["corner_dist"] / max(1.0, min(w, h)) - 0.18 * c["shape_quality"]))
        return candidates[:3]

    @classmethod
    def detect_fiducials(cls, img: np.ndarray, roi_ratio: float = 0.18) -> Dict[str, Any]:
        """Jointly select corner candidates by appearance consistency and quadrilateral geometry."""
        h, w = img.shape[:2]
        candidates = {name: cls._fiducial_candidates(img, name, roi_ratio) for name in CORNER_NAMES}
        if any(not candidates[name] for name in CORNER_NAMES):
            points = {}
            for name in CORNER_NAMES:
                if candidates[name]:
                    c = candidates[name][0]
                    points[name] = {"x": c["x"], "y": c["y"], "confidence": c["local_quality"],
                                    "local_quality": c["local_quality"], "local_contrast": c["local_contrast"]}
                else:
                    points[name] = {"x": w * (0.08 if name.endswith("left") else 0.92),
                                    "y": h * (0.08 if name.startswith("top") else 0.92), "confidence": 0.0,
                                    "local_quality": 0.0, "local_contrast": 0.0}
            return {"type": "joint_corner_fiducials", "points": points, "joint_confidence": 0.0,
                    "detection_valid": False}

        best, best_score = None, -float("inf")
        for combo in product(*(candidates[n] for n in CORNER_NAMES)):
            tl, tr, bl, br = combo
            # Reject crossed / degenerate quads and marks outside their respective corners.
            if not (tl["x"] < tr["x"] and bl["x"] < br["x"] and tl["y"] < bl["y"] and tr["y"] < br["y"]):
                continue
            horizontal_error = (abs(tl["y"] - tr["y"]) + abs(bl["y"] - br["y"])) / max(1.0, 2.0 * h)
            vertical_error = (abs(tl["x"] - bl["x"]) + abs(tr["x"] - br["x"])) / max(1.0, 2.0 * w)
            width_top, width_bottom = tr["x"] - tl["x"], br["x"] - bl["x"]
            height_left, height_right = bl["y"] - tl["y"], br["y"] - tr["y"]
            scale_error = abs(width_top - width_bottom) / max(1.0, w) + abs(height_left - height_right) / max(1.0, h)
            # NCC across normalized corner patches favors one consistent mark family.
            vectors = [c["appearance"].ravel() for c in combo]
            ncc = float(np.mean([np.dot(vectors[i], vectors[j]) for i in range(4) for j in range(i + 1, 4)]))
            corner_penalty = sum(c["corner_dist"] for c in combo) / max(1.0, 2.0 * (w + h))
            shape_score = float(np.mean([c["shape_quality"] for c in combo]))
            score = ncc + 0.65 * shape_score - 5.0 * (horizontal_error + vertical_error + scale_error) - 0.2 * corner_penalty
            if score > best_score:
                best, best_score = combo, score

        if best is None:
            best = tuple(candidates[n][0] for n in CORNER_NAMES)
            best_score = 0.0
        points = {name: {"x": float(c["x"]), "y": float(c["y"]),
                         # Preserve per-corner evidence; global geometry only ranks combinations.
                         "confidence": float(c["local_quality"]),
                         "local_quality": float(c["local_quality"]),
                         "local_contrast": float(c["local_contrast"])}
                  for name, c in zip(CORNER_NAMES, best)}
        return {"type": "joint_corner_fiducials", "roi_size": int(min(w, h) * roi_ratio),
                "points": points, "joint_confidence": float(np.clip(best_score, 0.0, 1.0)),
                "detection_valid": bool(best_score > 0.0),
                "confidence_method": "local_p95_p05_contrast_over_180",
                "joint_score": float(best_score)}

    @staticmethod
    def _active_polygon(fiducials: Optional[Dict[str, Any]], width: int, height: int) -> Optional[np.ndarray]:
        if not isinstance(fiducials, dict):
            return None
        points = fiducials.get("points", {})
        if fiducials.get("detection_valid") is False:
            return None
        if not isinstance(points, dict) or not all(name in points for name in CORNER_NAMES):
            return None
        try:
            quad = np.float32([[float(points[n]["x"]), float(points[n]["y"])] for n in
                               ("top_left", "top_right", "bottom_right", "bottom_left")])
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        if not np.isfinite(quad).all() or np.any(quad[:, 0] < 0) or np.any(quad[:, 0] > width) or \
                np.any(quad[:, 1] < 0) or np.any(quad[:, 1] > height):
            return None
        if not cv2.isContourConvex(quad.astype(np.int32)) or abs(cv2.contourArea(quad)) < width * height * 0.05:
            return None
        return quad

    @staticmethod
    def _adaptive_kernels(width: int, height: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        scale = max(0.2, min(width, height) / 5905.0)
        base = max(3, int(round(15 * scale)))
        if base % 2 == 0:
            base += 1
        # Directional close kernels join artwork along each axis; a small square pass seals corners.
        return (cv2.getStructuringElement(cv2.MORPH_RECT, (base * 2 + 1, base)),
                cv2.getStructuringElement(cv2.MORPH_RECT, (base, base * 2 + 1)),
                cv2.getStructuringElement(cv2.MORPH_RECT, (base, base)))

    @staticmethod
    def cluster_size_groups(boxes: List[Dict[str, Any]], tolerance: Optional[int] = None) -> List[Dict[str, Any]]:
        if not boxes:
            return boxes
        median_side = float(np.median([min(b["w"], b["h"]) for b in boxes]))
        tol = max(3, int(round(tolerance if tolerance is not None else median_side * 0.12)))
        def bucket(b):
            return (int(round(b["w"] / tol)), int(round(b["h"] / tol)))
        counts = Counter(bucket(b) for b in boxes)
        ordered = sorted(counts, key=lambda key: (-counts[key], key))
        labels = {key: f"size_{chr(65 + i)}" if i < 26 else f"size_{i}" for i, key in enumerate(ordered)}
        for b in boxes:
            b["size_group"] = labels[bucket(b)]
        return boxes

    @classmethod
    def detect_cells(cls, img: np.ndarray, fiducials: Optional[Dict[str, Any]] = None,
                     min_dim: Optional[int] = None, max_ratio: float = 0.90) -> List[Dict[str, Any]]:
        h, w = img.shape[:2]
        gray = cls._gray(img)
        # Otsu foreground plus local adaptive foreground handles both flat and mildly uneven backgrounds.
        _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        block = max(31, int(round(min(w, h) * 0.035)))
        if block % 2 == 0:
            block += 1
        adaptive = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                         cv2.THRESH_BINARY_INV, block, 5)
        # Keep the unclosed Otsu mask as split evidence. Adaptive thresholding can
        # classify a narrow pale gap between dark labels as foreground because it
        # sees the neighboring artwork; it must not erase the valley evidence.
        raw_binary = cv2.morphologyEx(otsu, cv2.MORPH_OPEN,
                                      cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
        binary = cv2.bitwise_or(otsu, adaptive)
        kh, kv, ks = cls._adaptive_kernels(w, h)
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kh)
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kv)
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, ks)
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        polygon = cls._active_polygon(fiducials, w, h)
        # Never fall back to whole-sheet detection when fiducials were supplied but
        # cannot define a trustworthy active area.
        if fiducials is not None and polygon is None:
            return []
        threshold = max(8, int(round(min(w, h) * 0.004))) if min_dim is None else int(min_dim)
        fid_points = (fiducials or {}).get("points", {})
        fid_radius = max(20.0, min(w, h) * 0.012)
        boxes = []
        def split_candidate(x: int, y: int, bw: int, bh: int) -> List[Tuple[int, int, int, int]]:
            """Split only on a full-span, wide raw-background valley with solid sides."""
            crop = raw_binary[y:y + bh, x:x + bw]
            if crop.size == 0:
                return [(x, y, bw, bh)]
            # OpenCV rectangles include both endpoints, so a declared 8 px box gap
            # yields 7 all-background pixel columns in the raster.
            min_gap = max(6, int(round(min(w, h) * 0.0010)))
            options = []
            # A valley must cross at least 88% of the orthogonal dimension.
            for axis, profile in (("x", np.count_nonzero(crop, axis=0)),
                                  ("y", np.count_nonzero(crop, axis=1))):
                orth = bh if axis == "x" else bw
                supported = profile > 0
                runs, start = [], None
                for i, value in enumerate(supported.tolist() + [True]):
                    if not value and start is None:
                        start = i
                    elif value and start is not None:
                        if i - start >= min_gap:
                            left = int(np.count_nonzero(supported[:start]))
                            right = int(np.count_nonzero(supported[i:]))
                            if min(left, right) >= max(2, int((len(profile) - (i-start)) * 0.22)) and orth > 0:
                                options.append((i-start, axis, start, i))
                        start = None
            if not options:
                return [(x, y, bw, bh)]
            # Use the widest full-span valley; recursively process each side.
            _, axis, a, b = max(options)
            # A blank stripe alone is ambiguous: it can be a deliberate white
            # divider inside one die. Require matching near-edge artwork on both
            # sides (the synthetic separated dies have their own inset borders).
            profile = (np.count_nonzero(crop, axis=0) if axis == "x"
                       else np.count_nonzero(crop, axis=1))
            orth = bh if axis == "x" else bw
            span = bw if axis == "x" else bh
            edge_band = max(18, int(round(span * 0.04)))

            gray_crop = gray[y:y + bh, x:x + bw].astype(np.float32)

            def has_inset_edge(start: int, stop: int, direction: int) -> bool:
                # Look for a sustained intensity edge inside the candidate, comparing
                # each pixel with neighbors farther inward. This distinguishes the
                # synthetic dies' own inset outlines from a plain white divider.
                for pos in range(max(0, start), min(span, stop)):
                    lo, hi = ((max(0, pos - 8), max(0, pos - 4)) if direction < 0
                              else (min(span, pos + 4), min(span, pos + 8)))
                    if hi - lo < 3:
                        continue
                    if axis == "x":
                        values = gray_crop[:, pos]
                        neighbors = gray_crop[:, lo:hi].mean(axis=1)
                    else:
                        values = gray_crop[pos, :]
                        neighbors = gray_crop[lo:hi, :].mean(axis=0)
                    if np.count_nonzero(np.abs(values - neighbors) >= 8.0) >= orth * 0.68:
                        return True
                return False

            left_edge = has_inset_edge(max(0, a - edge_band), a - 2, -1)
            right_edge = has_inset_edge(b + 2, min(span, b + edge_band), 1)
            if not left_edge or not right_edge:
                return [(x, y, bw, bh)]
            pieces = []
            if axis == "x":
                spans = [(x, y, a, bh), (x+b, y, bw-b, bh)]
            else:
                spans = [(x, y, bw, a), (x, y+b, bw, bh-b)]
            for sx, sy, sw, sh in spans:
                if sw <= 0 or sh <= 0:
                    return [(x, y, bw, bh)]
                # Tighten each piece to its own raw foreground, retaining the close only
                # for candidate formation. This avoids inheriting the artificial bridge.
                part = raw_binary[sy:sy+sh, sx:sx+sw]
                py, px = np.where(part > 0)
                if len(px) == 0:
                    return [(x, y, bw, bh)]
                bx0, bx1, by0, by1 = int(px.min()), int(px.max())+1, int(py.min()), int(py.max())+1
                pieces.append((sx+bx0, sy+by0, bx1-bx0, by1-by0))
            # Avoid splitting a single label at a narrow internal divider: each child
            # must retain at least 30% of the parent area and 25% of the split dimension.
            if any(pw * ph < bw * bh * 0.30 for _, _, pw, ph in pieces):
                return [(x, y, bw, bh)]
            if axis == "x" and any(pw < bw * 0.25 for _, _, pw, _ in pieces):
                return [(x, y, bw, bh)]
            if axis == "y" and any(ph < bh * 0.25 for _, _, _, ph in pieces):
                return [(x, y, bw, bh)]
            return pieces

        for contour in contours:
            x, y, bw, bh = cv2.boundingRect(contour)
            if bw < threshold or bh < threshold or bw >= w * max_ratio or bh >= h * max_ratio:
                continue
            if polygon is not None:
                tolerance = 2.0
                corners = ((x, y), (x + bw, y), (x, y + bh), (x + bw, y + bh))
                if any(cv2.pointPolygonTest(polygon, (float(px), float(py)), True) < -tolerance
                       for px, py in corners):
                    continue
            cx, cy = x + bw / 2.0, y + bh / 2.0
            if any(np.hypot(cx - pt["x"], cy - pt["y"]) < fid_radius for pt in fid_points.values()):
                continue
            for sx, sy, sw, sh in split_candidate(x, y, bw, bh):
                if sw < threshold or sh < threshold:
                    continue
                boxes.append({"x": int(sx), "y": int(sy), "w": int(sw), "h": int(sh),
                              "confidence": float(min(1.0, sw * sh / max(1.0, bw * bh)))})
        boxes.sort(key=lambda c: (c["y"], c["x"]))
        for idx, box in enumerate(boxes):
            box["id"] = idx
        return cls.cluster_size_groups(boxes)

    @staticmethod
    def _box_iou(a: Dict[str, Any], b: Dict[str, Any]) -> float:
        ix = max(0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
        iy = max(0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
        inter = ix * iy
        return float(inter / max(1, a["w"] * a["h"] + b["w"] * b["h"] - inter))

    @classmethod
    def find_same_size_candidates(cls, img: np.ndarray, cells: List[Dict[str, Any]],
                                  fiducials: Dict[str, Any], ref_cell: Dict[str, Any],
                                  score_threshold: float = 0.75) -> List[Dict[str, Any]]:
        """Find missing same-size dies using image structure and conservative grid inference."""
        height, width = img.shape[:2]
        polygon = cls._active_polygon(fiducials, width, height)
        if polygon is None:
            raise ValueError("ACTIVE_AREA_UNAVAILABLE")
        try:
            ref = {key: int(round(float(ref_cell[key]))) for key in ("x", "y", "w", "h")}
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ValueError("INVALID_REFERENCE_CELL")
        if ref["w"] < 10 or ref["h"] < 10:
            raise ValueError("INVALID_REFERENCE_CELL")

        gray = cls._gray(img)
        ref_crop = gray[ref["y"]:ref["y"] + ref["h"], ref["x"]:ref["x"] + ref["w"]]
        if ref_crop.size == 0:
            raise ValueError("INVALID_REFERENCE_CELL")
        pad = max(4, int(round(min(ref["w"], ref["h"]) * 0.04)))
        x1, x2 = max(0, ref["x"] - pad), min(width, ref["x"] + ref["w"] + pad)
        y1, y2 = max(0, ref["y"] - pad), min(height, ref["y"] + ref["h"] + pad)
        outer = gray[y1:y2, x1:x2].copy()
        outer[ref["y"] - y1:ref["y"] - y1 + ref["h"], ref["x"] - x1:ref["x"] - x1 + ref["w"]] = 0
        ring_values = outer[outer > 0]
        bg_value = float(np.median(ring_values)) if ring_values.size else float(np.median(gray))
        fg_value = float(np.median(ref_crop))
        threshold = (bg_value + fg_value) / 2.0
        dark_foreground = fg_value < bg_value
        raw_mask = (gray < threshold if dark_foreground else gray > threshold).astype(np.uint8) * 255
        # Remove fine text noise while preserving the filled label component.
        raw_mask = cv2.morphologyEx(raw_mask, cv2.MORPH_CLOSE,
                                    cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
        count, labels, stats, _ = cv2.connectedComponentsWithStats(raw_mask, 8)

        def inside_active(box: Dict[str, Any]) -> bool:
            corners = ((box["x"], box["y"]), (box["x"] + box["w"], box["y"]),
                       (box["x"], box["y"] + box["h"]),
                       (box["x"] + box["w"], box["y"] + box["h"]))
            return all(cv2.pointPolygonTest(polygon, (float(px), float(py)), True) >= -2.0
                       for px, py in corners)

        def feature_score(box: Dict[str, Any]) -> Tuple[float, Dict[str, float]]:
            crop = gray[box["y"]:box["y"] + box["h"], box["x"]:box["x"] + box["w"]]
            if crop.size == 0:
                return 0.0, {"size_sim": 0.0, "border_edge": 0.0, "fill_contrast": 0.0}
            size_sim = float(np.exp(-0.5 * (abs(box["w"] - ref["w"]) / ref["w"] +
                                             abs(box["h"] - ref["h"]) / ref["h"])))
            edge = cv2.Canny(crop, 40, 120)
            band = max(2, min(box["w"], box["h"]) // 30)
            ring = np.zeros(crop.shape, dtype=bool)
            ring[:band, :] = True; ring[-band:, :] = True
            ring[:, :band] = True; ring[:, -band:] = True
            edge_density = float(np.count_nonzero(edge[ring]) / max(1, np.count_nonzero(ring)))
            ref_edge = cv2.Canny(ref_crop, 40, 120)
            ref_ring = np.zeros(ref_crop.shape, dtype=bool)
            ref_band = max(2, min(ref["w"], ref["h"]) // 30)
            ref_ring[:ref_band, :] = True; ref_ring[-ref_band:, :] = True
            ref_ring[:, :ref_band] = True; ref_ring[:, -ref_band:] = True
            ref_density = float(np.count_nonzero(ref_edge[ref_ring]) / max(1, np.count_nonzero(ref_ring)))
            border_edge = float(np.clip(1.0 - abs(edge_density - ref_density) /
                                         max(0.08, ref_density, edge_density), 0.0, 1.0))
            selected = crop < threshold if dark_foreground else crop > threshold
            fill = float(np.count_nonzero(selected) / selected.size)
            ref_fill_mask = ref_crop < threshold if dark_foreground else ref_crop > threshold
            ref_fill = float(np.count_nonzero(ref_fill_mask) / ref_fill_mask.size)
            fill_contrast = float(np.clip(1.0 - abs(fill - ref_fill) / max(0.15, ref_fill), 0.0, 1.0))
            score = 0.4 * size_sim + 0.3 * border_edge + 0.3 * fill_contrast
            return float(np.clip(score, 0.0, 1.0)), {
                "size_sim": size_sim, "border_edge": border_edge, "fill_contrast": fill_contrast}

        image_candidates = []
        min_w, max_w = ref["w"] * 0.9, ref["w"] * 1.1
        min_h, max_h = ref["h"] * 0.9, ref["h"] * 1.1
        for index in range(1, count):
            x, y, bw, bh, area = (int(value) for value in stats[index])
            if not (min_w <= bw <= max_w and min_h <= bh <= max_h):
                continue
            # Ignore near-solid full-sheet regions and tiny connected artwork fragments.
            if area < ref["w"] * ref["h"] * 0.35:
                continue
            box = {"x": x, "y": y, "w": ref["w"], "h": ref["h"]}
            score, breakdown = feature_score({"x": x, "y": y, "w": bw, "h": bh})
            if score < score_threshold or not inside_active(box):
                continue
            if any(cls._box_iou(box, cell) >= 0.05 for cell in cells):
                continue
            image_candidates.append({**box, "source": "image_search", "sources": ["image_search"],
                                     "score": score, "score_breakdown": breakdown})

        # Infer a grid only where multiple rows/columns independently support a stable pitch.
        same_size = [cell for cell in cells
                     if abs(int(cell["w"]) - ref["w"]) <= 4 and abs(int(cell["h"]) - ref["h"]) <= 4]
        def clustered(values: List[int], tolerance: int = 5) -> List[int]:
            groups: List[List[int]] = []
            for value in sorted(values):
                if not groups or value - int(round(np.median(groups[-1]))) > tolerance:
                    groups.append([value])
                else:
                    groups[-1].append(value)
            return [int(round(float(np.median(group)))) for group in groups]

        rows = clustered([int(c["y"]) for c in same_size])
        cols = clustered([int(c["x"]) for c in same_size])
        row_groups = {row: [c for c in same_size if abs(int(c["y"]) - row) <= 5] for row in rows}
        col_groups = {col: [c for c in same_size if abs(int(c["x"]) - col) <= 5] for col in cols}
        x_intervals = [b - a for row in row_groups.values() if len(row) >= 2
                       for a, b in zip(sorted(int(c["x"]) for c in row), sorted(int(c["x"]) for c in row)[1:])]
        y_intervals = [b - a for col in col_groups.values() if len(col) >= 2
                       for a, b in zip(sorted(int(c["y"]) for c in col), sorted(int(c["y"]) for c in col)[1:])]

        def stable_pitch(intervals: List[int], supported_groups: int) -> Optional[int]:
            if len(same_size) < 4 or supported_groups < 2 or len(intervals) < 2:
                return None
            # Repeated intervals dominate; reject skipped-slot intervals as multiples.
            pitch = int(round(float(np.median(intervals))))
            normalized = [value / max(1, round(value / pitch)) for value in intervals]
            pitch = int(round(float(np.median(normalized))))
            deviations = [abs(value / max(1, round(value / pitch)) - pitch) for value in intervals]
            return pitch if max(deviations, default=float("inf")) <= 5 else None

        pitch_x = stable_pitch(x_intervals, sum(len(group) >= 2 for group in row_groups.values()))
        pitch_y = stable_pitch(y_intervals, sum(len(group) >= 2 for group in col_groups.values()))
        grid_candidates = []
        if pitch_x and pitch_y:
            grid_points = [(gx, gy) for gy in range(min(rows), max(rows) + 1, pitch_y)
                           for gx in range(min(cols), max(cols) + 1, pitch_x)]
        elif pitch_x:
            grid_points = [(gx, gy) for gy in rows
                           for gx in range(min(cols), max(cols) + 1, pitch_x)]
        elif pitch_y:
            grid_points = [(gx, gy) for gx in cols
                           for gy in range(min(rows), max(rows) + 1, pitch_y)]
        else:
            grid_points = []
        for gx, gy in grid_points:
            box = {"x": int(gx), "y": int(gy), "w": ref["w"], "h": ref["h"]}
            if not inside_active(box) or any(cls._box_iou(box, cell) >= 0.05 for cell in cells):
                continue
            score, breakdown = feature_score(box)
            # Grid predictions need visible foreground and a real border, not merely a slot.
            if score < score_threshold:
                continue
            grid_candidates.append({**box, "source": "grid_inferred", "sources": ["grid_inferred"],
                                    "score": score, "score_breakdown": breakdown})

        # Merge source duplicates (normally found by grid and image independently).
        merged = list(image_candidates)
        for candidate in grid_candidates:
            duplicate = next((item for item in merged if cls._box_iou(item, candidate) >= 0.50), None)
            if duplicate:
                duplicate["sources"] = sorted(set(duplicate["sources"] + candidate["sources"]))
                if candidate["score"] > duplicate["score"]:
                    duplicate["score"] = candidate["score"]
                    duplicate["source"] = candidate["source"]
            else:
                merged.append(candidate)
        merged.sort(key=lambda item: (item["y"], item["x"], item["source"]))
        for index, candidate in enumerate(merged):
            candidate["candidate_id"] = f"candidate_{index}"
        return merged

    @classmethod
    def auto_build_layout(cls, img: np.ndarray, layout_id: str = "new_layout",
                          layout_name: str = "新版型") -> Dict[str, Any]:
        h, w = img.shape[:2]
        fiducials = cls.detect_fiducials(img)
        cells = cls.detect_cells(img, fiducials=fiducials)
        confidence = float(fiducials.get("joint_confidence", 0.0))
        return {"schema_version": "3.0.0", "layout_id": layout_id, "name": layout_name,
                "is_demo": False, "review_required": True, "review_status": "pending",
                "image_spec": {"width": int(w), "height": int(h),
                               "channels": int(img.shape[2] if img.ndim == 3 else 1)},
                "fiducials": fiducials, "cells": cells,
                "builder_diagnostics": {"fiducial_joint_confidence": confidence,
                                         "fiducial_local_qualities": {name: float(pt.get("local_quality", pt.get("confidence", 0.0)))
                                                                       for name, pt in fiducials.get("points", {}).items()},
                                         "active_area_applied": cls._active_polygon(fiducials, w, h) is not None,
                                         "candidate_count": len(cells)}}

    @staticmethod
    def crop_fiducial_templates(img: np.ndarray, fiducials: Dict[str, Any], roi_radius: int = 40) -> Dict[str, np.ndarray]:
        h, w = img.shape[:2]
        templates = {}
        for name, pt in fiducials.get("points", {}).items():
            if name not in CORNER_NAMES:
                continue
            gx, gy = int(round(pt["x"])), int(round(pt["y"]))
            x1, x2 = max(0, gx - roi_radius), min(w, gx + roi_radius)
            y1, y2 = max(0, gy - roi_radius), min(h, gy + roi_radius)
            templates[name] = img[y1:y2, x1:x2].copy()
        return templates

    @classmethod
    def measure_fiducial_quality(cls, img: np.ndarray, x: float, y: float) -> Tuple[float, float]:
        """Measure local contrast around a manually positioned fiducial center."""
        gray = cls._gray(img)
        h, w = gray.shape[:2]
        radius = max(12, int(round(min(w, h) * 0.006)))
        cx, cy = int(round(x)), int(round(y))
        patch = gray[max(0, cy-radius):min(h, cy+radius), max(0, cx-radius):min(w, cx+radius)]
        if patch.size == 0:
            return 0.0, 0.0
        p05, p95 = np.percentile(patch.astype(np.float32), [5, 95])
        contrast = float(max(0.0, p95 - p05))
        return float(np.clip(contrast / 180.0, 0.0, 1.0)), contrast
