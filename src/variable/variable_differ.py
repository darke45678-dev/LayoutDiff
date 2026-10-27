"""
Pixel Difference & Dynamic Die Attribution Engine for Variable Layouts.
Strict zero-tolerance pixel subtraction and 8-connected component defect bounding boxes.
"""

from dataclasses import dataclass
from typing import List, Tuple, Dict, Any
import cv2
import numpy as np


@dataclass(frozen=True)
class DifferenceRegion:
    bbox: Tuple[int, int, int, int]  # (x, y, w, h)
    changed_pixels: int


class VariableDiffer:
    """Calculates exact pixel difference and maps diffs to layout dies."""

    @staticmethod
    def compare(target: np.ndarray, golden: np.ndarray) -> np.ndarray:
        if target.shape != golden.shape:
            raise ValueError(f"Image shapes must match exactly: {target.shape} != {golden.shape}")
        if target.ndim == 3:
            changed = np.any(target != golden, axis=2)
        else:
            changed = target != golden
        return (changed.astype(np.uint8) * 255)

    @staticmethod
    def extract_regions(diff_mask: np.ndarray) -> List[DifferenceRegion]:
        if diff_mask.ndim != 2:
            raise ValueError("Difference mask must be single-channel")
        binary = np.where(diff_mask != 0, 255, 0).astype(np.uint8)
        count, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
        regions = []
        for i in range(1, count):
            regions.append(DifferenceRegion(
                bbox=(int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP]),
                      int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT])),
                changed_pixels=int(stats[i, cv2.CC_STAT_AREA])
            ))
        return regions

    @classmethod
    def attribute_cells(
        cls,
        diff_mask: np.ndarray,
        cells: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], int, int]:
        """
        Attributes difference pixels to each die based on cell coordinates (x, y, w, h).
        Returns: (cell_results, pass_count, fail_count)
        """
        cell_results = []
        pass_count = 0
        fail_count = 0

        for cell in cells:
            cid = cell.get("id")
            x, y, w, h = cell["x"], cell["y"], cell["w"], cell["h"]
            sub_mask = diff_mask[y:y + h, x:x + w]
            changed_pixels = int(np.count_nonzero(sub_mask))
            regions = cls.extract_regions(sub_mask)
            verdict = "FAIL" if changed_pixels > 0 else "PASS"

            if verdict == "PASS":
                pass_count += 1
            else:
                fail_count += 1

            cell_results.append({
                "id": cid,
                "x": x, "y": y, "w": w, "h": h,
                "size_group": cell.get("size_group", "default"),
                "verdict": verdict,
                "defect_count": len(regions),
                "difference_pixel_count": changed_pixels,
                "defects": [{
                    "bbox": list(r.bbox),
                    "area_px": r.changed_pixels
                } for r in regions]
            })

        return cell_results, pass_count, fail_count
