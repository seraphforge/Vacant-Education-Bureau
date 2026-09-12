<h1 align="center">README!!!</h1>

![README!!!](assets/banner.png)

## 簡介

現行教保機構風險評估方案多仰賴人力，且缺乏各項資訊的交叉比對與整合，導致評估效率與完整性較低。針對以上困境，我們欲利用 AI Agent 自動、彙整各項公開資料（其中包括Google Map 評論在內等網路輿論），並與政府內部資料如財報等，整合至單一平臺，方便審查與分析。同時於平臺串接 AI 模型，藉由交叉對比不同學校、追蹤各學校財報的年度變化等方法，綜合所有資料並自動分析教育機構的各類風險指數。最終以可視化方式清楚、簡潔的呈現結果。除此之外，我們亦將在其中加入家長回報系統，藉由這些第一手資料提升準確度與預測效率。

- 作品簡報：*pending*
- 線上 Demo：**https://d17mx0mlb8rctm.cloudfront.net**

## 技術棧

- 前端：Angular 18, PrimeNG 17（`webapp/`）
- 後端：AWS Serverless — API Gateway (HTTP API) + Lambda (Python 3.12) + CloudFormation（`aws/`）
- 前端託管：S3 + CloudFront（全 serverless，無需維護伺服器）
- 資料庫：Amazon RDS for MySQL 8.4（schema `readme`）
- AI：*pending*

## 系統架構

```
                    使用者瀏覽器
                    │            │
        靜態檔案     │            │  API 呼叫 (JSON)
                    ▼            ▼
            CloudFront        API Gateway (HTTP API)
                 │                    │
                 ▼                    ▼
        S3「ntpc-kg-web」      Lambda「ntpc-kg-api」
        (Angular 建置產物)      (Python + PyMySQL，位於 RDS 的 VPC 內)
                                      │  MySQL 3306
                                      ▼
                            RDS MySQL「my-mysql-db」
                            readme.kindergarten
                            (104～114 學年度，74,628 列)
```

全部 AWS 資源都由 CloudFormation 定義（`aws/template.yaml`、`aws/web-template.yaml`），
可重建、可交接、可一次刪除。

## 快速開始

```powershell
# 1) 部署後端 API（第一次要先複製 deploy.config.example.ps1 成 deploy.config.ps1 並填密碼）
cd aws
.\deploy.ps1          # 完成後會印出 ApiUrl

# 2) 部署前端到 AWS（S3 + CloudFront）
.\deploy-web.ps1      # 完成後會印出 live demo 網址

# 或 2') 只在本機開發前端
cd ..\webapp
npm install
npm start             # http://localhost:4200
```

細節說明：

- 後端與部署：[`aws/README.md`](aws/README.md)
- 前端：[`webapp/README.md`](webapp/README.md)

## 目前完成度

- [x] 幼兒園清單查詢 API（縣市 / 名稱 LIKE / 公私立 / 學年度 / 分頁 / 排序）
- [x] Angular + PrimeNG 查詢畫面（伺服器端分頁）
- [x] 前後端皆部署於 AWS，具備可公開存取的 live demo
- [ ] 財報、Google Map 評論等資料源整合
- [ ] AI 風險指數分析
- [ ] 家長回報系統

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

## 本機 OCR 模式

不使用 Gemini 或 OpenAI 時，可用 Tesseract OCR 處理所有 PDF 的第 5、6 頁：

```bash
python data/finance_pdf_cleaning/scripts/extract_financial_reports.py --ocr
```

OCR 結果會依學年度／幼兒園輸出為 `*_page5_ocr.csv` 與 `*_page6_ocr.csv`。腳本會使用 `chi_tra+eng` 辨識繁體中文與數字，需自行準備 `tessdata/chi_tra.traineddata`（未納入版控）；若 Tesseract 安裝在非預設路徑，可設定 `TESSERACT_CMD`。

## 貢獻者

<a href="https://github.com/seraphforge/Vacant-Education-Bureau/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=seraphforge/Vacant-Education-Bureau" />
</a>

Made with [contrib.rocks](https://contrib.rocks).

## 授權條款
