"""
Safe Unicode Image I/O for Windows Environment.
"""

import os
import cv2
import numpy as np
from typing import Optional, List


def imread_unicode(path: str, flags: int = cv2.IMREAD_COLOR) -> Optional[np.ndarray]:
    """Reads image from path safely supporting Unicode characters (e.g. Chinese characters in Windows)."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as fp:
            data = fp.read()
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, flags)
        return img
    except Exception:
        return None


def imwrite_unicode(path: str, img: np.ndarray, params: Optional[List[int]] = None) -> bool:
    """Writes image to path safely supporting Unicode characters."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        ext = os.path.splitext(path)[1].lower() or ".jpg"
        if params is None:
            if ext in [".png"]:
                params = [int(cv2.IMWRITE_PNG_COMPRESSION), 4]
            else:
                params = [int(cv2.IMWRITE_JPEG_QUALITY), 90]
        ok, enc = cv2.imencode(ext, img, params)
        if ok:
            with open(path, "wb") as fp:
                fp.write(enc.tobytes())
            return True
        return False
    except Exception:
        return False


def imdecode_bytes(data: bytes, flags: int = cv2.IMREAD_COLOR) -> Optional[np.ndarray]:
    """Decodes image directly from raw byte buffer."""
    if not data:
        return None
    try:
        arr = np.frombuffer(data, dtype=np.uint8)
        return cv2.imdecode(arr, flags)
    except Exception:
        return None
