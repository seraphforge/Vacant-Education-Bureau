## 本機 OCR 模式

不使用 Gemini 或 OpenAI 時，可用 Tesseract OCR 處理所有 PDF 的第 5、6 頁：
```bash
python data/finance_pdf_cleaning/scripts/extract_financial_reports.py --ocr
```

OCR 結果會依學年度／幼兒園輸出為 `*_page5_ocr.csv` 與 `*_page6_ocr.csv`。專案已包含 `tessdata/chi_tra.traineddata`，腳本會自動使用 `chi_tra+eng` 辨識繁體中文與數字；若 Tesseract 安裝在非預設路徑，可設定 `TESSERACT_CMD`。
<h1 align="center">README!!!</h1>

## 簡介

現行教保機構風險評估方案多仰賴人力，且缺乏各項資訊的交叉比對與整合，導致評估效率與完整性較低。針對以上困境，我們欲利用 AI Agent 自動、彙整各項公開資料（其中包括Google Map 評論在內等網路輿論），並與政府內部資料如財報等，整合至單一平臺，方便審查與分析。同時於平臺串接 AI 模型，藉由交叉對比不同學校、追蹤各學校財報的年度變化等方法，綜合所有資料並自動分析教育機構的各類風險指數。最終以可視化方式清楚、簡潔的呈現結果。除此之外，我們亦將在其中加入家長回報系統，藉由這些第一手資料提升準確度與預測效率。

- 作品簡報：*pending*
- 線上 Demo：*pending*

## 技術棧

- 前端：Angular, PrimeNG
- 後端：*pending*
- 資料庫：MySQL
- AI：*pending*

## 系統架構

## 財報風險偵測項目

財報資料清理後，將針對下列項目進行異常分析：

- 人事費異常
- 業務費異常
- 修繕／採購費突然暴增
- 業務發展費異常
- 其他支出異常
- 預算與決算落差
- 年度支出突然大幅變化

PDF 抽取階段先保留人事費、業務費、修繕及採購費、業務發展費、其他支出、預算總額與決算總額等原始欄位。異常判定則需要搭配同一幼兒園的多年度資料，計算年度變化率、預算執行率與各支出項目占比，避免只依單一年度金額誤判。

## 貢獻者

<a href="https://github.com/seraphforge/Vacant-Education-Bureau/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=seraphforge/Vacant-Education-Bureau" />
</a>

Made with [contrib.rocks](https://contrib.rocks).

## 授權條款
