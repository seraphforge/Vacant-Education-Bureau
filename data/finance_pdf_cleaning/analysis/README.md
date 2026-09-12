# 財報特徵工程管線

把 `processed/` 下各園各年度的財報 CSV 彙整成統一長表，並計算 YoY、Z-score 與跨表衍生指標，供後續風險評分使用。

## 資料年份限制（重要）

| 資料類別 | 涵蓋年份 | 說明 |
| --- | --- | --- |
| 收支餘絀表（收支類）| 109～113 | 109 由 110 學年度 PDF 第 7 頁（前一年度欄）抽出 |
| 其他所有表（非收支類）| 110～113 | 資產負債、現金流量、附表二/三/四等皆無 109 |

## 執行順序

```bash
# 1) 彙整所有 CSV 成統一長表
python data/finance_pdf_cleaning/analysis/build_long_table.py
# 2) 計算特徵（YoY / Z-score / 衍生指標）
python data/finance_pdf_cleaning/analysis/build_features.py
```

輸出於 `analysis/outputs/`：

- `metric_long.csv`：統一長表（園代碼, 園名, 學年度, 資料表, 指標, 數值, 資料類別）
- `features_long.csv`：長表加上 YoY 變化額/率、歷史均值/標準差、近 3 年移動平均、Z-score
- `features_derived.csv`：跨表衍生指標寬表（每生人事費、每生支出、師生比等）

## 三條防呆規則（已落實於 build_features.py）

1. **YoY 基準**：收支類 110 年以 109 為基準可算；非收支類最早年（110）的 YoY 一律 `NaN`，趨勢自 111 起算。
2. **跨表衍生指標**：以 `how='outer'` 合併營運資料，缺營運資料的年度（如 109）衍生指標為 `NaN`，**絕不補 0**。
3. **歷史基準比對**：動態時間窗——收支類歷史可含 109，非收支類僅自 110 起算；`expanding`/`rolling` 皆設 `min_periods`，年份不足不報錯。

## 可擴充性（新增資料源不需重做）

下游只依賴 `metric_long.csv`。要新增資料源（學生人數、教保人員數、營運與人事事件等），
只需另外產生一份**相同欄位的長表**，下游即自動融合：

- 財務類新指標：直接讓 `build_long_table.py` 多一個 loader，append 進 `metric_long.csv`。
- 營運資料：另存為 `analysis/outputs/operating_long.csv`（欄位含 `園代碼, 學年度, 指標, 數值`，
  指標如 `學生人數`、`教保人員數`）。`build_features.py` 偵測到該檔會自動 `outer merge`，
  並計算每生支出、師生比等衍生指標；沒有該檔時這些欄位保持 `NaN`，不影響財務特徵。
