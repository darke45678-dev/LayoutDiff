# Variable Layout Inspector (LayoutDiff)

這是可變版型電子檔檢測原型的去識別化作品集副本。內含建版工作台、準星定位、模位候選偵測、影像對位與逐像素差分流程。

作品集案例深度分析見 [`CASE_STUDY.md`](CASE_STUDY.md)。

本作品集副本僅供公開展示，未附專案重用授權；公開檢視不代表可以複製、散布或製作衍生作品。第三方套件仍依各自授權，摘要見 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

作品集圖片是可重建的合成示意素材，見 `assets/portfolio/`。

---

## 範圍

- **多模位版型**：版型可記錄不同模數、模位座標與尺寸群組。
- **建版工作台**：提供自動候選偵測，並保留人工檢視與微調修正流程。
- **特徵對位與差分**：檢測使用已保存的 Golden、準星範本及模位座標進行自適應對位與逐像素差分。
- **位置標示**：差分結果標示差異位置，不辨識文字語意或瑕疵種類。

這是原型，不代表已通過各種客戶版型或產線驗收。自動建版品質依輸入圖像而異，保存前需人工確認。

---

## 隨附說明

本公開作品集副本不包含真實樣本圖、客戶上傳檔或執行時暫存檔。建立 Golden 和檢測時，請使用你有權處理的影像。

---

## 比對前提與限制

零容差差分比較解碼後的像素。若 Golden 與待測檔經過不同的重存、壓縮或影像處理流程，即使肉眼相似，解碼像素仍可能不同並造成誤報。跨來源 JPG 容差方法目前只屬離線實驗，未整合為正式檢測模式，也未證明可泛化。

---

## 核心目錄結構

```text
LayoutDiff/
├── app_variable.py          # 後端 HTTP 服務與 API 整合 (Port 8100)
├── requirements.txt         # 核心環境套件需求 (NumPy, OpenCV, SciPy)
├── CASE_STUDY.md            # 作品集案例研究與技術指標深入分析
├── THIRD_PARTY_NOTICES.md   # 第三方套件授權聲明 (NumPy, OpenCV, SciPy)
├── README.md                # 專案總覽文件
├── .gitignore               # Git 忽略設定
├── assets/
│   └── portfolio/           # 作品集展示示意圖檔
├── src/
│   ├── core/
│   │   └── image_io.py      # Unicode 路徑相容之影像讀寫與位元組解碼
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

## 啟動方式

測試環境為 Python 3.11+、NumPy 2.4.3、OpenCV 4.13.0.92 與 SciPy 1.17.1；版本記錄於 `requirements.txt`。

### 1. 安裝依賴套件

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### 2. 啟動後端服務

```powershell
python app_variable.py
```

服務預設使用本機 8100 埠：`http://localhost:8100/`。
