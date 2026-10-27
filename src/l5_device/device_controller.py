"""
src/l5_device/device_controller.py

L5 模擬設備狀態機與工控 I/O 控制器 (Simulated Device Controller)
遵循規範：《熱轉印標籤 AOI DEMO 整合架構書_規劃版.docx》表 2 (L5 設備整合) 與 表 6 (P5 系統整合)

【嚴格邊界聲明】：
本模組為工控與 PLC 訊號的模擬實作 (Simulation Mode)，用於 DEMO 展示與系統狀態流程閉環。
未連接實體 PLC (如三菱 FX5U、歐姆龍 NX1P2)、實體電磁閥或剔除氣缸。
所有對外日誌與輸出嚴格強制標記 [SIMULATION]。
"""

import threading
import time
from typing import Dict, Any, Optional

class DeviceState:
    READY = "READY"       # 系統就緒，等待檢測觸發 (D1000 = 0)
    BUSY = "BUSY"         # 檢測中，鎖定輸入 (D1000 = 10)
    PASS = "PASS"         # 整版合格放行 (D1000 = 1)
    FAIL = "FAIL"         # 確診瑕疵剔除 (D1000 = 2)
    REVIEW = "REVIEW"     # 待品管人工複核 (D1000 = 3)
    ERROR = "ERROR"       # 設備/檔案異常故障 (D1000 = 99)

PLC_REGISTER_MAP = {
    DeviceState.READY: 0,
    DeviceState.BUSY: 10,
    DeviceState.PASS: 1,
    DeviceState.FAIL: 2,
    DeviceState.REVIEW: 3,
    DeviceState.ERROR: 99
}

class SimulatedDeviceController:
    """
    執行 7 大狀態機與虛擬 PLC 暫存器輸出
    支援故障安全連鎖 (Fail-safe Interlocking)
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._current_state = DeviceState.READY
        self._last_state_change = time.time()
        self._current_sheet = None
        self._error_message = None
        self._cycle_count = 0
        self._history = []

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            plc_code = PLC_REGISTER_MAP.get(self._current_state, 99)
            return {
                "state": self._current_state,
                "is_simulation": True,
                "plc_register": {
                    "D1000_VERDICT": plc_code,
                    "description": f"[SIMULATION] PLC Register D1000 = {plc_code} ({self._current_state})"
                },
                "current_sheet": self._current_sheet,
                "error_message": self._error_message,
                "cycle_count": self._cycle_count,
                "elapsed_since_state_change_s": round(time.time() - self._last_state_change, 2)
            }

    def trigger_inspection_start(self, sheet_name: str) -> bool:
        """
        接收觸發檢測訊號：轉移為 BUSY
        """
        with self._lock:
            if self._current_state == DeviceState.BUSY:
                print(f"[SIMULATION] [WARNING] Cannot start: Device already BUSY with {self._current_sheet}")
                return False
            
            self._current_state = DeviceState.BUSY
            self._current_sheet = sheet_name
            self._error_message = None
            self._last_state_change = time.time()
            self._cycle_count += 1
            print(f"[SIMULATION] Virtual PLC Write: D1000 = {PLC_REGISTER_MAP[DeviceState.BUSY]} (BUSY) - Started sheet: {sheet_name}")
            return True

    def report_inspection_complete(self, overall_verdict: str, sheet_name: str = None) -> str:
        """
        檢測完成，依結果轉移至 PASS, FAIL, REVIEW
        """
        with self._lock:
            if overall_verdict == "PASS":
                target_state = DeviceState.PASS
            elif overall_verdict == "FAIL":
                target_state = DeviceState.FAIL
            elif overall_verdict == "REVIEW":
                target_state = DeviceState.REVIEW
            else:
                target_state = DeviceState.ERROR
                self._error_message = f"Unknown overall verdict: {overall_verdict}"

            self._current_state = target_state
            if sheet_name:
                self._current_sheet = sheet_name
            self._last_state_change = time.time()
            plc_val = PLC_REGISTER_MAP.get(target_state, 99)
            print(f"[SIMULATION] Virtual PLC Write: D1000 = {plc_val} ({target_state}) - Overall: {overall_verdict}")
            return target_state

    def report_error(self, err_msg: str) -> None:
        """
        異常故障安全連鎖：立即切入 ERROR，阻斷放行/剔除
        """
        with self._lock:
            self._current_state = DeviceState.ERROR
            self._error_message = str(err_msg)
            self._last_state_change = time.time()
            plc_val = PLC_REGISTER_MAP[DeviceState.ERROR]
            print(f"[SIMULATION] Virtual PLC Write: D1000 = {plc_val} (ERROR) - Reason: {err_msg}")

    def reset_to_ready(self) -> None:
        """
        復歸訊號 (RESET)：清除故障或等待下一個循環
        """
        with self._lock:
            self._current_state = DeviceState.READY
            self._error_message = None
            self._last_state_change = time.time()
            plc_val = PLC_REGISTER_MAP[DeviceState.READY]
            print(f"[SIMULATION] Virtual PLC Write: D1000 = {plc_val} (READY) - System reset complete")

# 單例模式提供全域存取
_global_device_controller: Optional[SimulatedDeviceController] = None
_device_lock = threading.Lock()

def get_device_controller() -> SimulatedDeviceController:
    global _global_device_controller
    with _device_lock:
        if _global_device_controller is None:
            _global_device_controller = SimulatedDeviceController()
        return _global_device_controller
