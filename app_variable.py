"""
Variable Layout Inspection Backend Service.
Port: 8100.
Independent prototype server for synthetic variable layouts, manual-review
layout building, fiducial alignment, and zero-tolerance pixel inspection.
"""

import os
import sys
import json
import time
import shutil
import re
import uuid
import threading
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Dict, Any, Optional
import cv2
import numpy as np

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.core.image_io import imread_unicode, imwrite_unicode, imdecode_bytes
from src.variable.variable_aligner import VariableSheetAligner
from src.variable.variable_differ import VariableDiffer
from src.variable.builder_detector import LayoutBuilderDetector
from src.variable.builder_validator import LayoutBuilderValidator
from src.l5_device.device_controller import SimulatedDeviceController

PORT = 8100
STATIC_DIR = os.path.join(ROOT_DIR, "static_variable")
LAYOUTS_DIR = os.path.join(ROOT_DIR, "configs", "variable_layouts")
UPLOADS_DIR = os.path.join(ROOT_DIR, "runtime", "variable_uploads")
BUILDER_SESSIONS_DIR = os.path.join(UPLOADS_DIR, "builder_sessions")
BUILDER_SESSION_TTL_SECONDS = 24 * 60 * 60
BUILDER_LOCK = threading.RLock()
UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")
LAYOUT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(LAYOUTS_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(BUILDER_SESSIONS_DIR, exist_ok=True)

# Device controller instance
device = SimulatedDeviceController()

# Cache latest inspection result for 1:1 raw pixel cell cropping
LATEST_INSPECTION_CACHE: Dict[str, Any] = {}


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class VariableLayoutHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler for Variable Layout system."""

    def _send_json(self, data: Any, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _send_image(self, img_bytes: bytes, mime: str = "image/png"):
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(img_bytes)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(img_bytes)

    def _send_file(self, file_path: str, mime: str = "text/html; charset=utf-8"):
        if not os.path.exists(file_path):
            self.send_error(404, "File not found")
            return
        with open(file_path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self._send_file(os.path.join(STATIC_DIR, "index.html"), "text/html; charset=utf-8")
            return

        if path.startswith("/static/"):
            rel_path = path[len("/static/"):]
            full_path = os.path.join(STATIC_DIR, rel_path)
            mime = "text/plain"
            if rel_path.endswith(".css"):
                mime = "text/css; charset=utf-8"
            elif rel_path.endswith(".js"):
                mime = "application/javascript; charset=utf-8"
            elif rel_path.endswith(".png"):
                mime = "image/png"
            elif rel_path.endswith(".jpg"):
                mime = "image/jpeg"
            self._send_file(full_path, mime)
            return

        if path == "/api/layouts":
            self._handle_list_layouts()
            return

        if path.startswith("/api/layouts/") and path.endswith("/golden"):
            parts = path.split("/")
            lid = parts[3]
            self._handle_get_golden(lid, query)
            return

        if path.startswith("/api/layouts/") and "/fiducial/" in path:
            parts = path.split("/")
            lid = parts[3]
            corner = parts[5]
            self._handle_get_fiducial(lid, corner)
            return

        if path.startswith("/api/layouts/"):
            lid = path.split("/")[3]
            self._handle_get_layout_detail(lid)
            return

        if path == "/api/quick_samples":
            self._handle_list_quick_samples()
            return

        if path == "/api/builder/preview":
            upload_id = query.get("upload_id", [""])[0]
            if not UPLOAD_ID_RE.fullmatch(upload_id):
                self.send_error(400, "Invalid upload_id")
                return
            preview_path = os.path.join(BUILDER_SESSIONS_DIR, upload_id, "preview.jpg")
            self._send_file(preview_path, "image/jpeg")
            return

        if path == "/api/inspect/latest_preview":
            preview_path = os.path.join(UPLOADS_DIR, "latest_diff_preview.jpg")
            self._send_file(preview_path, "image/jpeg")
            return

        if path == "/api/inspect/cell_crop":
            self._handle_inspect_cell_crop(query)
            return

        self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len) if content_len > 0 else b""

        if path == "/api/builder/detect":
            self._handle_builder_detect(body)
            return

        if path == "/api/builder/validate":
            self._handle_builder_validate(body)
            return

        if path == "/api/builder/find_candidates":
            self._handle_builder_find_candidates(body)
            return

        if path == "/api/builder/save":
            self._handle_builder_save(body)
            return

        if path == "/api/inspect":
            self._handle_inspect(body, query)
            return

        self.send_error(404, "Not Found")

    # ---------------- Handlers ----------------

    def _handle_list_layouts(self):
        results = []
        if os.path.exists(LAYOUTS_DIR):
            for entry in os.listdir(LAYOUTS_DIR):
                cfg_path = os.path.join(LAYOUTS_DIR, entry, "layout.json")
                if os.path.isfile(cfg_path):
                    try:
                        with open(cfg_path, "r", encoding="utf-8") as f:
                            cfg = json.load(f)
                        cells = cfg.get("cells", [])
                        groups = sorted(list(set(c.get("size_group", "default") for c in cells)))
                        results.append({
                            "id": cfg.get("layout_id", entry),
                            "name": cfg.get("name", entry),
                            "die_count": len(cells),
                            "spec": cfg.get("image_spec", {}),
                            "is_demo": cfg.get("is_demo", False),
                            "size_groups": groups
                        })
                    except Exception as e:
                        pass
        self._send_json({"layouts": results})

    def _handle_get_layout_detail(self, lid: str):
        cfg_path = os.path.join(LAYOUTS_DIR, lid, "layout.json")
        if not os.path.exists(cfg_path):
            self._send_json({"error": "Layout not found"}, 404)
            return
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        self._send_json(cfg)

    def _handle_get_golden(self, lid: str, query: Dict[str, Any]):
        golden_path = os.path.join(LAYOUTS_DIR, lid, "golden.png")
        if not os.path.exists(golden_path):
            self.send_error(404, "Golden image not found")
            return
        
        # Check if thumbnail / downscaled preview requested
        downscale = query.get("preview", ["false"])[0].lower() == "true"
        if downscale:
            thumb_path = os.path.join(LAYOUTS_DIR, lid, "golden_thumb.jpg")
            if not os.path.exists(thumb_path):
                img = imread_unicode(golden_path)
                if img is not None:
                    h, w = img.shape[:2]
                    scale = 1800.0 / float(w)
                    small = cv2.resize(img, (1800, int(h * scale)), interpolation=cv2.INTER_AREA)
                    imwrite_unicode(thumb_path, small)
            if os.path.exists(thumb_path):
                self._send_file(thumb_path, "image/jpeg")
                return

        self._send_file(golden_path, "image/png")

    def _handle_get_fiducial(self, lid: str, corner: str):
        tmpl_path = os.path.join(LAYOUTS_DIR, lid, "fiducials", f"{corner}.png")
        if os.path.exists(tmpl_path):
            self._send_file(tmpl_path, "image/png")
        else:
            self.send_error(404, "Fiducial not found")

    def _handle_list_quick_samples(self):
        samples = []
        if os.path.exists(UPLOADS_DIR):
            for f in sorted(os.listdir(UPLOADS_DIR)):
                if f.lower().endswith((".png", ".jpg", ".jpeg")) and not f.startswith("temp_"):
                    samples.append(f)
        self._send_json({"samples": samples})

    def _handle_builder_detect(self, body: bytes):
        content_type = self.headers.get("Content-Type", "")
        img = None
        request_data = {}

        if "application/json" in content_type:
            try:
                request_data = json.loads(body.decode("utf-8"))
                upload_id = request_data.get("upload_id", "")
                if upload_id:
                    if not UPLOAD_ID_RE.fullmatch(str(upload_id)):
                        self._send_json({"success": False, "error": "upload_id 格式錯誤"}, 400)
                        return
                    source_path = os.path.join(BUILDER_SESSIONS_DIR, upload_id, "golden.png")
                    img = imread_unicode(source_path)
                else:
                    sample_name = request_data.get("sample_file")
                    if sample_name and os.path.basename(sample_name) == sample_name:
                        path = os.path.join(UPLOADS_DIR, sample_name)
                        img = imread_unicode(path)
            except Exception:
                pass
        else:
            # Direct binary image decode
            img = imdecode_bytes(body)

        if img is None:
            self._send_json({"error": "無法解碼影像檔案，請確認是否為有效 PNG/JPG"}, 400)
            return

        # Every builder source and preview belongs to its own opaque upload ID.
        upload_id = request_data.get("upload_id") or uuid.uuid4().hex
        if not UPLOAD_ID_RE.fullmatch(str(upload_id)):
            self._send_json({"success": False, "error": "upload_id 格式錯誤"}, 400)
            return
        session_dir = os.path.join(BUILDER_SESSIONS_DIR, upload_id)
        os.makedirs(session_dir, exist_ok=True)
        if not request_data.get("upload_id"):
            imwrite_unicode(os.path.join(session_dir, "golden.png"), img)
        elif not os.path.isfile(os.path.join(session_dir, "golden.png")):
            self._send_json({"success": False, "error": "建版暫存已過期，請重新上傳樣本。"}, 404)
            return
        os.utime(session_dir, None)

        # Generate preview
        h, w = img.shape[:2]
        scale = 1800.0 / float(w)
        preview_img = cv2.resize(img, (1800, int(h * scale)), interpolation=cv2.INTER_AREA)
        imwrite_unicode(os.path.join(session_dir, "preview.jpg"), preview_img)

        # Run automatic detection, or re-run it using manually corrected fiducials.
        fiducials = request_data.get("fiducials")
        if fiducials:
            if not isinstance(fiducials, dict):
                self._send_json({"success": False, "error": "準星資料格式錯誤"}, 400)
                return
            fiducials["detection_valid"] = True
            fiducials["type"] = "manual_corner_fiducials"
            if LayoutBuilderDetector._active_polygon(fiducials, img.shape[1], img.shape[0]) is None:
                self._send_json({"success": False, "error": "四顆準星必須在圖片內並構成有效凸四邊形。"}, 400)
                return
        layout_cfg = LayoutBuilderDetector.auto_build_layout(img, layout_id="new_layout", layout_name="自定義新版型")
        if fiducials:
            layout_cfg["fiducials"] = fiducials
            layout_cfg["cells"] = LayoutBuilderDetector.detect_cells(img, fiducials=fiducials)
            layout_cfg["builder_diagnostics"]["active_area_applied"] = \
                LayoutBuilderDetector._active_polygon(fiducials, img.shape[1], img.shape[0]) is not None
            layout_cfg["builder_diagnostics"]["candidate_count"] = len(layout_cfg["cells"])
        session_meta = {"layout": layout_cfg, "updated_at": time.time()}
        with open(os.path.join(session_dir, "layout.json"), "w", encoding="utf-8") as f:
            json.dump(session_meta, f, ensure_ascii=False)
        warnings = LayoutBuilderValidator.validate_layout(layout_cfg)
        self._cleanup_builder_sessions(exclude=upload_id)

        self._send_json({
            "success": True,
            "layout": layout_cfg,
            "warnings": warnings,
            "upload_id": upload_id,
            "preview_url": f"/api/builder/preview?upload_id={upload_id}"
        })

    def _load_builder_session(self, upload_id: str):
        if not UPLOAD_ID_RE.fullmatch(str(upload_id)):
            return None, None
        session_dir = os.path.join(BUILDER_SESSIONS_DIR, upload_id)
        golden_path = os.path.join(session_dir, "golden.png")
        if not os.path.isfile(golden_path):
            return None, None
        image = imread_unicode(golden_path)
        if image is not None:
            os.utime(session_dir, None)
        return session_dir, image

    @staticmethod
    def _cleanup_builder_sessions(exclude: str = ""):
        cutoff = time.time() - BUILDER_SESSION_TTL_SECONDS
        try:
            for entry in os.scandir(BUILDER_SESSIONS_DIR):
                if entry.is_dir() and entry.name != exclude and entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry.path, ignore_errors=True)
        except OSError:
            pass

    def _handle_builder_validate(self, body: bytes):
        try:
            req = json.loads(body.decode("utf-8"))
            session_dir, image = self._load_builder_session(str(req.get("upload_id", "")))
            if image is None:
                self._send_json({"error": "建版暫存已過期，請重新上傳樣本。"}, 404)
                return
            report = LayoutBuilderValidator.validate_for_save(req.get("layout", {}), image)
            report["layout"] = req.get("layout", {})
            self._send_json(report)
        except Exception as exc:
            self._send_json({"error": str(exc)}, 400)

    def _handle_builder_find_candidates(self, body: bytes):
        try:
            req = json.loads(body.decode("utf-8"))
            _, image = self._load_builder_session(str(req.get("upload_id", "")))
            if image is None:
                self._send_json({"error": "建版暫存已過期，請重新上傳樣本。"}, 404)
                return
            cells, fiducials, ref_cell = req.get("cells"), req.get("fiducials"), req.get("ref_cell")
            if not isinstance(cells, list) or not isinstance(fiducials, dict) or not isinstance(ref_cell, dict):
                self._send_json({"error": "搜尋資料格式錯誤，請確認模位、準星與參考框。"}, 400)
                return
            candidates = LayoutBuilderDetector.find_same_size_candidates(image, cells, fiducials, ref_cell)
            counts = {source: sum(source in item.get("sources", [item.get("source")])
                                  for item in candidates)
                      for source in ("image_search", "grid_inferred")}
            self._send_json({"success": True, "candidates": candidates, "counts": counts,
                             "message": f"找到 {len(candidates)} 個同尺寸候選框"})
        except ValueError as exc:
            status = 422 if str(exc) == "ACTIVE_AREA_UNAVAILABLE" else 400
            self._send_json({"success": False, "error": str(exc)}, status)
        except Exception as exc:
            self._send_json({"success": False, "error": f"候選框搜尋失敗：{exc}"}, 500)

    def _handle_builder_save(self, body: bytes):
        staging = None
        try:
            req = json.loads(body.decode("utf-8"))
            layout_id_value = req.get("layout_id", "")
            layout_id = layout_id_value.strip() if isinstance(layout_id_value, str) else ""
            name_value = req.get("name", "")
            name = (name_value.strip() if isinstance(name_value, str) else "") or layout_id
            layout_cfg = req.get("layout", {})
            upload_id = str(req.get("upload_id", ""))
            overwrite = req.get("overwrite") is True
            confirmed_warnings = req.get("confirmed_warnings") is True

            if req.get("user_confirmed") is not True:
                self._send_json({"error": "請先在建版畫布確認模位與準星，再勾選人工確認後儲存。"}, 400)
                return

            if not LAYOUT_ID_RE.fullmatch(layout_id):
                self._send_json({"error": "LAYOUT_ID_INVALID", "message": "版型代碼只允許英數、底線與連字號，長度 1–64 字元。"}, 400)
                return

            session_dir, golden_img = self._load_builder_session(upload_id)
            if golden_img is None:
                self._send_json({"error": "建版暫存已過期或不存在，請重新上傳樣本。"}, 404)
                return

            report = LayoutBuilderValidator.validate_for_save(layout_cfg, golden_img)
            if report["errors"]:
                self._send_json({"error": "BUILDER_VALIDATION_FAILED", "errors": report["errors"],
                                 "warnings": report["warnings"]}, 400)
                return
            if report["warnings"] and not confirmed_warnings:
                self._send_json({"error": "WARNINGS_REQUIRE_CONFIRMATION", "warnings": report["warnings"]}, 422)
                return

            layout_folder = os.path.join(LAYOUTS_DIR, layout_id)
            if os.path.exists(layout_folder) and not overwrite:
                self._send_json({"error": "LAYOUT_EXISTS", "layout_id": layout_id,
                                 "message": f"版型代碼 {layout_id} 已存在，確認覆寫嗎？"}, 409)
                return

            layout_cfg["review_required"] = True
            layout_cfg["review_status"] = "confirmed"

            # Construct a complete sibling directory first; only then replace the target.
            staging = os.path.join(LAYOUTS_DIR, f".{layout_id}.staging-{uuid.uuid4().hex}")
            backup = os.path.join(LAYOUTS_DIR, f".{layout_id}.backup-{uuid.uuid4().hex}")
            os.makedirs(os.path.join(staging, "fiducials"), exist_ok=False)
            if not imwrite_unicode(os.path.join(staging, "golden.png"), golden_img):
                raise OSError("無法寫入 Golden 影像")
            templates = LayoutBuilderDetector.crop_fiducial_templates(golden_img, layout_cfg["fiducials"], roi_radius=40)
            if set(templates) != {"top_left", "top_right", "bottom_left", "bottom_right"}:
                shutil.rmtree(staging, ignore_errors=True)
                self._send_json({"error": "FIDUCIAL_TEMPLATES_INCOMPLETE", "message": "四角準星範本未完整產生。"}, 400)
                return
            for cname, tmpl in templates.items():
                if tmpl.size == 0 or not imwrite_unicode(os.path.join(staging, "fiducials", f"{cname}.png"), tmpl):
                    raise OSError(f"無法寫入 {cname} 準星範本")
            layout_cfg["layout_id"] = layout_id
            layout_cfg["name"] = name
            layout_cfg["is_demo"] = False
            with open(os.path.join(staging, "layout.json"), "w", encoding="utf-8") as f:
                json.dump(layout_cfg, f, ensure_ascii=False, indent=2)
            # Windows cannot replace a non-empty directory directly: move old aside,
            # install the complete staging tree, and restore the old tree on failure.
            with BUILDER_LOCK:
                if os.path.exists(layout_folder):
                    if not overwrite:
                        shutil.rmtree(staging, ignore_errors=True)
                        self._send_json({"error": "LAYOUT_EXISTS", "layout_id": layout_id}, 409)
                        return
                    os.replace(layout_folder, backup)
                try:
                    os.replace(staging, layout_folder)
                except Exception:
                    if os.path.exists(backup) and not os.path.exists(layout_folder):
                        os.replace(backup, layout_folder)
                    raise
                if os.path.exists(backup):
                    shutil.rmtree(backup, ignore_errors=True)

            self._send_json({"success": True, "layout_id": layout_id, "name": name})

        except Exception as e:
            if staging and os.path.isdir(staging):
                shutil.rmtree(staging, ignore_errors=True)
            self._send_json({"error": str(e)}, 500)

    def _handle_inspect(self, body: bytes, query: Dict[str, Any]):
        t0 = time.time()
        layout_id = query.get("layout_id", [""])[0]
        img = None

        content_type = self.headers.get("Content-Type", "")
        if "application/json" in content_type:
            try:
                data = json.loads(body.decode("utf-8"))
                layout_id = data.get("layout_id", layout_id)
                sample_name = data.get("sample_file")
                if sample_name:
                    path = os.path.join(UPLOADS_DIR, sample_name)
                    img = imread_unicode(path)
            except Exception:
                pass
        else:
            img = imdecode_bytes(body)

        if not layout_id:
            self._send_json({"error": "必須指定 layout_id"}, 400)
            return

        layout_folder = os.path.join(LAYOUTS_DIR, layout_id)
        cfg_path = os.path.join(layout_folder, "layout.json")
        golden_path = os.path.join(layout_folder, "golden.png")

        if not os.path.exists(cfg_path) or not os.path.exists(golden_path):
            self._send_json({"error": f"版型 {layout_id} 不存在或資料不完整"}, 404)
            return

        with open(cfg_path, "r", encoding="utf-8") as f:
            layout_cfg = json.load(f)

        golden_img = imread_unicode(golden_path)
        if golden_img is None or img is None:
            self._send_json({"error": "影像讀取失敗"}, 400)
            return

        # 1. Pixel Dimension Check
        spec = layout_cfg.get("image_spec", {})
        spec_w = spec.get("width", golden_img.shape[1])
        spec_h = spec.get("height", golden_img.shape[0])

        if img.shape[1] != spec_w or img.shape[0] != spec_h:
            device.report_inspection_complete("ERROR")
            self._send_json({
                "status": "PIXEL_SPEC_MISMATCH",
                "verdict": "PIXEL_SPEC_MISMATCH",
                "message": f"像素規格不符 (待測檔: {img.shape[1]}x{img.shape[0]}, 規格: {spec_w}x{spec_h})。依零容差規範嚴禁縮放，直接判定規格不符！",
                "alignment": {"needed": False, "aligned": False, "badge": "規格不符"},
                "total_cells": len(layout_cfg.get("cells", [])),
                "pass_count": 0,
                "fail_count": 0,
                "cells": [],
                "elapsed_ms": int((time.time() - t0) * 1000)
            })
            return

        # 2. Alignment
        fid_folder = os.path.join(layout_folder, "fiducials")
        templates = {}
        for corner in ["top_left", "top_right", "bottom_left", "bottom_right"]:
            tmpl_p = os.path.join(fid_folder, f"{corner}.png")
            if os.path.exists(tmpl_p):
                templates[corner] = imread_unicode(tmpl_p)

        aligner = VariableSheetAligner(layout_cfg.get("fiducials", {}), templates)
        need_align, base_score, check_msg = aligner.check_need_alignment(img, golden_img)

        aligned_img = img
        alignment_info = {
            "needed": need_align,
            "aligned": False,
            "badge": "基準座標吻合 (直接比對)",
            "message": check_msg,
            "confidence": base_score
        }

        if need_align:
            aligned_img, M, is_valid, align_msg, conf = aligner.align_sheet(img, golden_img)
            alignment_info["confidence"] = conf
            alignment_info["message"] = align_msg

            if not is_valid:
                device.report_inspection_complete("REVIEW")
                self._send_json({
                    "status": "REVIEW",
                    "verdict": "REVIEW",
                    "message": f"準星對位信心度不足 ({align_msg})，依規定直接轉人工複核，不發送偽 PASS / FAIL 訊號。",
                    "alignment": {
                        "needed": True,
                        "aligned": False,
                        "badge": "⚠ 對位失敗轉人工複核 (REVIEW)",
                        "message": align_msg,
                        "confidence": conf
                    },
                    "total_cells": len(layout_cfg.get("cells", [])),
                    "pass_count": 0,
                    "fail_count": 0,
                    "cells": [],
                    "elapsed_ms": int((time.time() - t0) * 1000)
                })
                return

            alignment_info["aligned"] = True
            alignment_info["badge"] = "⚠ 已對位 (仿射重採樣)"
            if M is not None:
                alignment_info["shift"] = [float(M[0, 2]), float(M[1, 2])]

        # 3. Pixel Difference
        diff_mask = VariableDiffer.compare(aligned_img, golden_img)
        total_diff_px = int(np.count_nonzero(diff_mask))

        # 4. Dynamic Die Attribution
        cells = layout_cfg.get("cells", [])
        cell_results, pass_cnt, fail_cnt = VariableDiffer.attribute_cells(diff_mask, cells)

        final_verdict = "PASS" if fail_cnt == 0 else "FAIL"
        device.report_inspection_complete(final_verdict)

        # 5. Generate Visual Preview for UI
        h, w = golden_img.shape[:2]
        scale = 1800.0 / float(w)
        pw, ph = 1800, int(h * scale)

        preview_canvas = cv2.resize(aligned_img, (pw, ph), interpolation=cv2.INTER_AREA)
        # Highlight failed cells and defects
        for cr in cell_results:
            cx = int(cr["x"] * scale)
            cy = int(cr["y"] * scale)
            cw = int(cr["w"] * scale)
            ch = int(cr["h"] * scale)

            if cr["verdict"] == "FAIL":
                cv2.rectangle(preview_canvas, (cx, cy), (cx + cw, cy + ch), (0, 0, 255), 2)
                # Defect bounding boxes
                for d in cr["defects"]:
                    db = d["bbox"]
                    dx = int((cr["x"] + db[0]) * scale)
                    dy = int((cr["y"] + db[1]) * scale)
                    dw = max(3, int(db[2] * scale))
                    dh = max(3, int(db[3] * scale))
                    cv2.rectangle(preview_canvas, (dx - 2, dy - 2), (dx + dw + 2, dy + dh + 2), (0, 0, 255), 2)
            else:
                cv2.rectangle(preview_canvas, (cx, cy), (cx + cw, cy + ch), (0, 200, 0), 1)

        imwrite_unicode(os.path.join(UPLOADS_DIR, "latest_diff_preview.jpg"), preview_canvas)

        elapsed_ms = int((time.time() - t0) * 1000)

        # Cache inspection result for 1:1 raw pixel cell cropping
        global LATEST_INSPECTION_CACHE
        LATEST_INSPECTION_CACHE = {
            "layout_id": layout_id,
            "layout_cfg": layout_cfg,
            "golden_img": golden_img,
            "aligned_img": aligned_img,
            "diff_mask": diff_mask,
            "cell_results": cell_results
        }
        # Persist to runtime disk for survivability across server restarts
        try:
            imwrite_unicode(os.path.join(UPLOADS_DIR, "latest_aligned.png"), aligned_img)
            imwrite_unicode(os.path.join(UPLOADS_DIR, "latest_diff_mask.png"), diff_mask)
            with open(os.path.join(UPLOADS_DIR, "latest_cache_meta.json"), "w", encoding="utf-8") as f:
                json.dump({"layout_id": layout_id}, f)
        except Exception as err:
            print("[CACHE] Disk save notice:", err)

        self._send_json({
            "status": final_verdict,
            "verdict": final_verdict,
            "message": "檢測完成: 全數合格" if final_verdict == "PASS" else f"檢測完成: 發現 {fail_cnt} 模瑕疵",
            "alignment": alignment_info,
            "total_difference_pixels": total_diff_px,
            "total_cells": len(cells),
            "pass_count": pass_cnt,
            "fail_count": fail_cnt,
            "cells": cell_results,
            "preview_url": "/api/inspect/latest_preview",
            "elapsed_ms": elapsed_ms
        })

    def _handle_inspect_cell_crop(self, query: Dict[str, Any]):
        global LATEST_INSPECTION_CACHE
        if not LATEST_INSPECTION_CACHE or "aligned_img" not in LATEST_INSPECTION_CACHE:
            # Auto-restore from disk cache if server was restarted
            try:
                meta_p = os.path.join(UPLOADS_DIR, "latest_cache_meta.json")
                aligned_p = os.path.join(UPLOADS_DIR, "latest_aligned.png")
                diff_p = os.path.join(UPLOADS_DIR, "latest_diff_mask.png")
                if os.path.exists(meta_p) and os.path.exists(aligned_p) and os.path.exists(diff_p):
                    with open(meta_p, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    layout_id = meta.get("layout_id")
                    layout_folder = os.path.join(LAYOUTS_DIR, layout_id)
                    layout_cfg_p = os.path.join(layout_folder, "layout.json")
                    golden_p = os.path.join(layout_folder, "golden.png")
                    if os.path.exists(layout_cfg_p) and os.path.exists(golden_p):
                        with open(layout_cfg_p, "r", encoding="utf-8") as f:
                            cfg = json.load(f)
                        LATEST_INSPECTION_CACHE = {
                            "layout_id": layout_id,
                            "layout_cfg": cfg,
                            "golden_img": imread_unicode(golden_p),
                            "aligned_img": imread_unicode(aligned_p),
                            "diff_mask": cv2.imread(diff_p, cv2.IMREAD_GRAYSCALE),
                            "cell_results": []
                        }
            except Exception as err:
                print("[CACHE] Auto-restore notice:", err)

        if not LATEST_INSPECTION_CACHE or "aligned_img" not in LATEST_INSPECTION_CACHE:
            self.send_error(404, "No active inspection result cached")
            return

        try:
            cid = int(query.get("cell_id", [0])[0])
            mode = query.get("mode", ["composite"])[0].lower()

            layout_cfg = LATEST_INSPECTION_CACHE["layout_cfg"]
            cells = layout_cfg.get("cells", [])
            target_cell = None
            for c in cells:
                if c["id"] == cid:
                    target_cell = c
                    break

            if not target_cell:
                self.send_error(404, f"Cell #{cid} not found in layout")
                return

            x, y, w, h = target_cell["x"], target_cell["y"], target_cell["w"], target_cell["h"]
            golden_img = LATEST_INSPECTION_CACHE["golden_img"]
            aligned_img = LATEST_INSPECTION_CACHE["aligned_img"]
            diff_mask = LATEST_INSPECTION_CACHE["diff_mask"]

            g_crop = golden_img[y:y + h, x:x + w]
            t_crop = aligned_img[y:y + h, x:x + w]
            d_crop = diff_mask[y:y + h, x:x + w]

            # Diff overlay: target image with diff pixels in bright red (0, 0, 255)
            overlay = t_crop.copy()
            diff_count = int(np.count_nonzero(d_crop))
            if diff_count > 0:
                overlay[d_crop > 0] = [0, 0, 255]
                regions = VariableDiffer.extract_regions(d_crop)
                for r in regions:
                    rx, ry, rw, rh = r.bbox
                    cv2.rectangle(overlay, (rx, ry), (rx + rw, ry + rh), (0, 0, 255), 2)

            if mode == "golden":
                out_img = g_crop
            elif mode == "target":
                out_img = t_crop
            elif mode == "diff":
                out_img = overlay
            else:
                # Composite: 3 panels horizontally with clean English header labels
                header_h = 32
                panel_w = w
                gap = 8
                total_w = panel_w * 3 + gap * 2
                total_h = h + header_h

                composite = np.full((total_h, total_w, 3), (20, 24, 33), dtype=np.uint8)

                x_g = 0
                x_t = panel_w + gap
                x_d = (panel_w + gap) * 2

                composite[header_h:header_h + h, x_g:x_g + panel_w] = g_crop
                composite[header_h:header_h + h, x_t:x_t + panel_w] = t_crop
                composite[header_h:header_h + h, x_d:x_d + panel_w] = overlay

                font = cv2.FONT_HERSHEY_SIMPLEX
                font_scale = max(0.45, min(0.75, w / 450.0))
                cv2.putText(composite, "1. Golden (Master)", (x_g + 10, 22), font, font_scale, (220, 220, 220), 1, cv2.LINE_AA)
                cv2.putText(composite, "2. Target (Aligned)", (x_t + 10, 22), font, font_scale, (220, 220, 220), 1, cv2.LINE_AA)
                diff_title = f"3. Diff Overlay (Red: {diff_count} px)" if diff_count > 0 else "3. Diff Overlay (0 px Diff)"
                diff_color = (0, 0, 255) if diff_count > 0 else (0, 220, 0)
                cv2.putText(composite, diff_title, (x_d + 10, 22), font, font_scale, diff_color, 1, cv2.LINE_AA)

                out_img = composite

            ok, enc = cv2.imencode(".png", out_img)
            if ok:
                self._send_image(enc.tobytes(), "image/png")
            else:
                self.send_error(500, "Encoding failed")

        except Exception as e:
            self.send_error(500, str(e))


def run_server(port: int = PORT):
    VariableLayoutHandler._cleanup_builder_sessions()
    server = ThreadingHTTPServer(("0.0.0.0", port), VariableLayoutHandler)
    print(f"===========================================================")
    print(f"  可變版型電子檔檢測系統 (Variable Layout System)")
    print(f"  服務啟動於: http://localhost:{port}")
    print(f"  支援任意模數 (1~N模)、同版混款、防呆建版、零容差像素比對")
    print(f"===========================================================")

    def _open_browser():
        time.sleep(0.6)
        try:
            import webbrowser
            webbrowser.open(f"http://localhost:{port}/")
        except Exception:
            pass

    import threading
    threading.Thread(target=_open_browser, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服務停止。")
        server.server_close()


if __name__ == "__main__":
    run_server(PORT)
