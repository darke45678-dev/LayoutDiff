"""
Builder Validator for Variable Layouts.
Inspects auto-detected layouts for potential issues:
- Low fiducial confidence (requires manual verification)
- Suspected missing cells in regular grids
- Overlapping cell bounding boxes
- Non-standard/irregular mixed layouts
"""

from typing import Dict, Any, List, Tuple
import numpy as np
import cv2

from src.variable.builder_detector import CORNER_NAMES, LayoutBuilderDetector


class LayoutBuilderValidator:
    """Validates layout proposals and returns diagnostic warnings/notes."""

    @staticmethod
    def check_fiducial_confidence(fiducials: Dict[str, Any], min_conf: float = 0.85) -> List[Dict[str, Any]]:
        """Checks if any fiducial confidence is below min_conf."""
        warnings = []
        points = fiducials.get("points", {})
        for name, pt in points.items():
            # Prefer the preserved per-corner detector evidence. The legacy
            # confidence field remains a fallback for older saved layouts.
            conf = float(pt.get("local_quality", pt.get("confidence", 0.0)))
            if conf < min_conf:
                warnings.append({
                    "code": "LOW_FIDUCIAL_CONFIDENCE",
                    "severity": "WARNING",
                    "corner": name,
                    "confidence": conf,
                    "local_contrast": float(pt.get("local_contrast", 0.0)),
                    "message": f"準星 [{name}] 信心度過低 ({conf:.2f} < {min_conf:.2f})，請確認該角落準星位置或手動拖曳微調。"
                })
        return warnings

    @staticmethod
    def check_overlapping_cells(cells: List[Dict[str, Any]], min_overlap_px: int = 1) -> List[Dict[str, Any]]:
        """Detects if any two cell bounding boxes overlap significantly."""
        warnings = []
        n = len(cells)
        for i in range(n):
            c1 = cells[i]
            x1_a, y1_a = c1["x"], c1["y"]
            x2_a, y2_a = x1_a + c1["w"], y1_a + c1["h"]

            for j in range(i + 1, n):
                c2 = cells[j]
                x1_b, y1_b = c2["x"], c2["y"]
                x2_b, y2_b = x1_b + c2["w"], y1_b + c2["h"]

                inter_w = max(0, min(x2_a, x2_b) - max(x1_a, x1_b))
                inter_h = max(0, min(y2_a, y2_b) - max(y1_a, y1_b))
                inter_area = inter_w * inter_h

                if inter_area >= min_overlap_px:
                    warnings.append({
                        "code": "OVERLAPPING_CELLS",
                        "severity": "WARNING",
                        "cell_ids": [c1["id"], c2["id"]],
                        "overlap_area_px": inter_area,
                        "message": f"模位 #{c1['id']} 與 #{c2['id']} 存在重疊區域 ({inter_area} px)，請確認是否為重複偵測並手動刪除。"
                    })
        return warnings

    @staticmethod
    def check_missing_cells_grid(cells: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Infers if a grid-like layout has missing cells based on row/column pitch.
        """
        if len(cells) < 6:
            return []

        # Find median width and height
        ws = [c["w"] for c in cells]
        hs = [c["h"] for c in cells]
        med_w = np.median(ws)
        med_h = np.median(hs)

        # Check if sizes are relatively uniform (if variance > 20%, it's a mixed layout, skip grid inference)
        if np.std(ws) / med_w > 0.20 or np.std(hs) / med_h > 0.20:
            return []

        # Cluster into rows by Y (within half med_h)
        rows: List[List[Dict[str, Any]]] = []
        sorted_cells = sorted(cells, key=lambda c: (c["y"], c["x"]))

        for c in sorted_cells:
            placed = False
            for r in rows:
                if abs(r[0]["y"] - c["y"]) < med_h * 0.4:
                    r.append(c)
                    placed = True
                    break
            if not placed:
                rows.append([c])

        # Analyze column spacing in each row
        missing_slots = []
        for r_idx, r in enumerate(rows):
            if len(r) < 2:
                continue
            r.sort(key=lambda c: c["x"])
            dxs = [r[i+1]["x"] - r[i]["x"] for i in range(len(r) - 1)]
            if not dxs:
                continue
            med_dx = np.median(dxs)
            if med_dx <= med_w:
                continue

            # Check gaps between consecutive cells in row
            for i in range(len(r) - 1):
                gap = r[i+1]["x"] - r[i]["x"]
                if gap > med_dx * 1.6:
                    num_missing = int(round(gap / med_dx)) - 1
                    for m in range(num_missing):
                        est_x = int(r[i]["x"] + (m + 1) * med_dx)
                        est_y = int(r[0]["y"])
                        missing_slots.append({
                            "row": r_idx,
                            "estimated_x": est_x,
                            "estimated_y": est_y,
                            "estimated_w": int(med_w),
                            "estimated_h": int(med_h)
                        })

        if missing_slots:
            return [{
                "code": "SUSPECTED_MISSING_CELLS",
                "severity": "INFO",
                "count": len(missing_slots),
                "slots": missing_slots,
                "message": f"網格分析偵測到疑似漏框 ({len(missing_slots)} 處空缺)，請確認是否為留白或需補框。"
            }]

        return []

    @staticmethod
    def check_layout_regularity(cells: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Summarizes layout regularity and size groups."""
        warnings = []
        groups = set(c.get("size_group", "default") for c in cells)
        if len(groups) > 1:
            warnings.append({
                "code": "IRREGULAR_GRID_LAYOUT",
                "severity": "INFO",
                "size_groups": sorted(list(groups)),
                "message": f"版面為多尺寸混排或非單一規格網格 (共 {len(groups)} 種尺寸分群: {sorted(list(groups))})。"
            })
        return warnings

    @classmethod
    def validate_layout(cls, layout_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Runs all validation heuristics on a layout."""
        results = []
        fiducials = layout_cfg.get("fiducials", {})
        cells = layout_cfg.get("cells", [])

        results.extend(cls.check_fiducial_confidence(fiducials))
        results.extend(cls.check_overlapping_cells(cells))
        results.extend(cls.check_missing_cells_grid(cells))
        results.extend(cls.check_layout_regularity(cells))

        diagnostics = layout_cfg.get("builder_diagnostics", {})
        joint_conf = float(diagnostics.get("fiducial_joint_confidence", 0.0))
        points = fiducials.get("points", {}) if isinstance(fiducials, dict) else {}
        manually_positioned = all(isinstance(points.get(name), dict) and points[name].get("manual_override")
                                  for name in CORNER_NAMES)
        if joint_conf < 0.25 and not manually_positioned:
            results.append({
                "code": "LOW_JOINT_FIDUCIAL_CONFIDENCE",
                "severity": "WARNING",
                "confidence": joint_conf,
                "message": f"四角準星聯合配對信心不足 ({joint_conf:.2f})，請在畫布確認或拖曳校正。"
            })
        if not diagnostics.get("active_area_applied", False):
            results.append({
                "code": "ACTIVE_AREA_UNAVAILABLE",
                "severity": "WARNING",
                "message": "無法建立四準星有效區域，候選模位尚未完成區域外過濾。請確認準星位置。"
            })
        if not cells:
            results.append({"code": "NO_CELLS_DETECTED", "severity": "WARNING",
                            "message": "沒有偵測到模位，請調整樣本或手動拉框。"})

        spec = layout_cfg.get("image_spec", {})
        width, height = int(spec.get("width", 0)), int(spec.get("height", 0))
        for cell in cells:
            x, y, w, h = (int(cell[k]) for k in ("x", "y", "w", "h"))
            if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height:
                results.append({"code": "CELL_OUT_OF_BOUNDS", "severity": "WARNING",
                                "cell_ids": [cell.get("id")],
                                "message": f"模位 #{cell.get('id')} 超出樣本影像範圍。"})

        return results

    @classmethod
    def validate_for_save(cls, layout_cfg: Dict[str, Any], image: np.ndarray) -> Dict[str, List[Dict[str, Any]]]:
        """Return hard errors and confirmable warnings using the actual source image."""
        errors: List[Dict[str, Any]] = []
        warnings: List[Dict[str, Any]] = []
        if not isinstance(layout_cfg, dict):
            return {"errors": [{"code": "INVALID_LAYOUT", "message": "版型資料格式錯誤。"}], "warnings": []}

        h, w = image.shape[:2]
        spec = layout_cfg.get("image_spec") or {}
        if spec.get("width") != w or spec.get("height") != h:
            errors.append({"code": "IMAGE_SPEC_MISMATCH", "message": "版型影像尺寸與上傳的 Golden 不一致。"})

        fids = layout_cfg.get("fiducials") or {}
        if not isinstance(fids, dict):
            fids = {}
        points = fids.get("points") or {}
        if not isinstance(points, dict):
            points = {}
            fids["points"] = points
        bad_fid = []
        for name in CORNER_NAMES:
            pt = points.get(name)
            try:
                if not isinstance(pt, dict):
                    raise ValueError
                x, y = float(pt["x"]), float(pt["y"])
                if not np.isfinite([x, y]).all() or x < 0 or y < 0 or x > w or y > h:
                    raise ValueError
            except (TypeError, KeyError, ValueError):
                bad_fid.append(name)
        polygon = LayoutBuilderDetector._active_polygon(fids, w, h)
        if bad_fid or polygon is None:
            errors.append({"code": "INVALID_FIDUCIAL_QUADRILATERAL",
                           "corners": bad_fid,
                           "message": "四角準星必須在影像範圍內，且依 TL/TR/BR/BL 順序構成有效凸四邊形。"})
        else:
            for name, pt in points.items():
                if name not in CORNER_NAMES or not isinstance(pt, dict):
                    continue
                quality, contrast = LayoutBuilderDetector.measure_fiducial_quality(image, pt["x"], pt["y"])
                pt["local_quality"] = quality
                pt["local_contrast"] = contrast
                if pt.get("manual_override"):
                    pt["confidence"] = quality

        cells = layout_cfg.get("cells")
        if not isinstance(cells, list) or not cells:
            errors.append({"code": "NO_CELLS", "message": "版型至少需要一個模位框。"})
            cells = []

        seen_ids = set()
        valid_cells = []
        for cell in cells:
            if not isinstance(cell, dict):
                errors.append({"code": "INVALID_CELL", "message": "模位框資料格式錯誤。"})
                continue
            cid = cell.get("id")
            try:
                if isinstance(cid, bool) or not isinstance(cid, (int, float)) or not np.isfinite(float(cid)) or int(cid) != cid:
                    raise ValueError
                cid = int(cid)
                if cid in seen_ids:
                    errors.append({"code": "DUPLICATE_CELL_ID", "cell_ids": [cid], "message": f"模位 ID #{cid} 重複。"})
                seen_ids.add(cid)
                coords = [cell[k] for k in ("x", "y", "w", "h")]
                if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(float(v)) for v in coords):
                    raise ValueError
                x, y, cw, ch = (int(round(float(v))) for v in coords)
            except (KeyError, TypeError, ValueError, OverflowError):
                errors.append({"code": "INVALID_CELL_GEOMETRY", "cell_ids": [cell.get("id")],
                               "message": f"模位 #{cell.get('id', '?')} 的座標必須是有效數值。"})
                continue
            cell.update({"id": cid, "x": x, "y": y, "w": cw, "h": ch})
            valid_cells.append(cell)
            if cw < 10 or ch < 10:
                errors.append({"code": "CELL_TOO_SMALL", "cell_ids": [cid], "message": f"模位 #{cid} 寬高不得小於 10 px。"})
            if x < 0 or y < 0 or cw <= 0 or ch <= 0 or x + cw > w or y + ch > h:
                errors.append({"code": "CELL_OUT_OF_BOUNDS", "cell_ids": [cid], "message": f"模位 #{cid} 超出樣本影像範圍。"})
            elif polygon is not None:
                corners = ((x, y), (x + cw, y), (x, y + ch), (x + cw, y + ch))
                if any(cv2.pointPolygonTest(polygon, (float(px), float(py)), True) < -2.0 for px, py in corners):
                    warnings.append({"code": "CELL_OUTSIDE_ACTIVE_AREA", "severity": "WARNING", "cell_ids": [cid],
                                     "message": f"模位 #{cid} 未完全落在四準星有效區域內，請確認。"})

        # Keep the existing heuristic diagnostics, but remove fatal geometry issues
        # from the warning list because they are reported above as blocking errors.
        diagnostic_cfg = dict(layout_cfg)
        diagnostic_cfg["image_spec"] = {"width": w, "height": h}
        diagnostic_cfg["cells"] = valid_cells
        diagnostic_cfg["builder_diagnostics"] = dict(layout_cfg.get("builder_diagnostics") or {})
        diagnostic_cfg["builder_diagnostics"]["active_area_applied"] = polygon is not None
        warnings.extend(cls.validate_layout(diagnostic_cfg))
        warnings = [item for item in warnings if item.get("code") not in {
            "CELL_OUT_OF_BOUNDS", "NO_CELLS_DETECTED", "ACTIVE_AREA_UNAVAILABLE"
        }]
        # Deduplicate warnings produced by both geometric and heuristic checks.
        unique = {}
        for item in warnings:
            key = (item.get("code"), tuple(item.get("cell_ids", [])), item.get("corner"))
            unique[key] = item
        return {"errors": errors, "warnings": list(unique.values())}
