# Variable Layout Inspector

這是可變版型電子檔檢測原型的去識別化作品集副本。內含合成版型、建版工作台、準星定位、模位候選偵測、影像對位與逐像素差分流程。

作品集案例初稿見 [`CASE_STUDY.md`](CASE_STUDY.md)。

本作品集副本僅供公開展示，未附專案重用授權；公開檢視不代表可以複製、散布或製作衍生作品。第三方套件仍依各自授權，摘要見 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

作品集圖片是可重建的合成示意素材，見 `assets/portfolio/`；重建命令為 `python scripts/create_portfolio_visuals.py`。

## 範圍

- 版型可記錄不同模數、模位座標與尺寸群組。
- 建版提供自動候選偵測，並保留人工檢視與修正流程。
- 檢測使用已保存的 Golden、準星範本及模位座標進行對位與差分。
- 差分結果標示位置，不辨識文字語意或瑕疵種類。

這是原型，不代表已通過各種客戶版型或產線驗收。自動建版品質依輸入圖像而異，保存前需人工確認。

## 隨附測試資料

版型圖由 `tests/variable/synthetic_generator.py` 產生，包含大標籤、混合尺寸、網格空位、高密度、低信心準星及不規則排列等情境。這些資料用於可重現的軟體測試，不代表真實客戶檔案。

本公開候選副本不包含真實樣本圖、客戶上傳檔或執行時暫存檔。建立 Golden 和檢測時，請使用你有權處理的影像。

## 比對前提與限制

零容差差分比較解碼後的像素。若 Golden 與待測檔經過不同的重存、壓縮或影像處理流程，即使肉眼相似，解碼像素仍可能不同並造成誤報。跨來源 JPG 容差方法目前只屬離線實驗，未整合為正式檢測模式，也未證明可泛化。

合成測試通過只代表對應測試案例符合斷言，不等同於任意版型或產線實測通過。客戶檔案與真實瑕疵資料仍需另行驗證。

## 啟動與測試

測試環境為 Python 3.11.9、NumPy 2.4.3、OpenCV 4.13.0.92 與 SciPy 1.17.1；版本記錄於 `requirements.txt`。

Windows PowerShell 安裝方式：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

執行 `run.bat` 啟動，或以 Python 執行 `app_variable.py`。服務預設使用本機 8100 埠。

合成版型初始化：

```powershell
python scripts/init_demo_layouts.py
```

測試命令：

```powershell
python -m unittest discover tests/variable -p "test_*.py"
```

已在全新 `.venv` 依 `requirements.txt` 安裝，並以 Python 3.11.9 執行完整測試：18 tests OK（147.511 秒，2026-10-27）。這些只驗證合成案例，不代表真實客戶檔或產線驗收。
