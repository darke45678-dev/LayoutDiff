# LayoutDiff (Variable Layout Inspector)

可變版型電子檔檢測原型（Core Edition）。提供自動版型候選偵測、人工編修確認、自適應準星對位與逐像素零容差差分功能。

---

## 專案簡介

在印刷、包裝與標籤製造流程中，不同版型可能包含數十至上百個不同尺寸、排列不規則的模位。傳統比對方式面對版面平移或非固定網格時容易產生大量誤報。

**LayoutDiff** 針對可變版型設計，核心特色包括：
- **智慧模位候選偵測**：自動分析版面中的標籤／模位幾何邊界，依尺寸自動分群，並支援同尺寸候選智慧聚合。
- **建版工作台 (Builder Workbench)**：提供 Web 視覺化介面，供工程師人工檢視、微調或增刪模位框，並儲存為版型規範（Layout Profile）。
- **特徵準星對位 (Fiducial Alignment)**：支援十字準星與自訂角點標記，具備幾何平移與旋轉校正能力。
- **逐模位零容差像素差分**：在已鎖定的模位框內執行高速逐像素比對，精確定位差異像素位置與所屬模位。

---

## 核心目錄結構

```text
LayoutDiff/
├── app_variable.py          # 後端 HTTP 服務與 API 整合 (Port 8100)
├── requirements.txt         # 核心環境套件需求 (NumPy, OpenCV, SciPy)
├── .gitignore               # Git 忽略設定
├── README.md                # 專案說明文件
├── src/
│   ├── core/
│   │   └── image_io.py      # Unicode 路徑相容之影像讀寫與二進位解碼
│   ├── l5_device/
│   │   └── device_controller.py # 虛擬設備狀態模擬控制器
│   └── variable/
│       ├── builder_detector.py  # 準星定位、模位邊界偵測與尺寸群組
│       ├── builder_validator.py # 建版重疊、邊界溢出與品質診斷
│       ├── variable_aligner.py  # 依準星特徵執行高精度影像對位
│       └── variable_differ.py   # 模位 ROI 限制之像素差分引擎
└── static_variable/         # Web 視覺化建版與檢測操作介面
    ├── index.html           # 工作台主頁面
    ├── style.css            # 介面樣式
    └── app.js               # 前端互動與畫布繪製邏輯
```

---

## 快速開始

### 1. 環境需求
建議使用 Python 3.11+。

安裝依賴套件：
```bash
pip install -r requirements.txt
```

### 2. 啟動服務
執行主後端程式：
```bash
python app_variable.py
```
啟動後，開啟瀏覽器造訪：
```text
http://localhost:8100/
```

### 3. 操作流程
1. **建版階段 (Builder)**：
   - 上傳基準圖 (Golden Image)。
   - 點擊自動偵測模位候選，系統將框選出模位並分群。
   - 於畫布上手動微調、標註四角準星特徵。
   - 儲存版型設定檔。
2. **檢測階段 (Inspection)**：
   - 載入已儲存版型。
   - 上傳待測印刷影像。
   - 系統依準星自動校準對齊，並針對每個模位計算差異像素與檢測狀態 (PASS / FAIL / REVIEW)。

---

## 技術特點與邊界說明

1. **候選偵測本質**：自動偵測旨在大幅減少人工拉框時間，作為候選建議；在正式保存前仍需人工目視確認。
2. **像素零容差**：比對以解碼後之原始像素為基準。若來源圖檔經過不同程度的失真壓縮（如 JPEG 重存），微小解碼量化差可能觸發差異標記。
3. **無硬體相依**：本核心版本採用純軟體與模擬控制器，可獨立於實體產線機台進行離線驗證與演算法展示。
