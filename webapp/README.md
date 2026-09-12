# 前端（Angular + PrimeNG）

## 版本

| 套件 | 版本 | 備註 |
|---|---|---|
| Angular | 18 | 本機 Node 是 18.19.0，Angular 19+ 需要 Node 18.19.1 以上，所以停在 18 |
| PrimeNG | 17.18.x | PrimeNG 18 需要 Angular 19，故搭配 17 |
| PrimeIcons / PrimeFlex | 7 / 3 | 圖示與 utility class |

## 啟動

```powershell
cd webapp
npm install        # 第一次才需要
npm start          # = ng serve，開 http://localhost:4200
```

打包正式版：

```powershell
npm run build      # 產出到 dist/webapp/browser
```

## 部署到 AWS

Live demo：**https://d17mx0mlb8rctm.cloudfront.net**（S3 + CloudFront）

```powershell
cd ..\aws
.\deploy-web.ps1
```

這個腳本會自動 `ng build --configuration production`、上傳到 S3、清 CloudFront
快取，所以**不需要**自己先 build。設定細節見 [`aws/README.md`](../aws/README.md)
的「前端託管」章節。

## 設定後端網址

只有一個地方：`src/environments/environment.ts`

```ts
export const environment = {
  apiBaseUrl: 'https://e86tz73y7h.execute-api.us-east-1.amazonaws.com',
};
```

這個網址是 `aws/deploy.ps1` 部署完印出來的 `ApiUrl`。

## 檔案結構

```
src/
├─ environments/environment.ts              後端 API 網址
├─ styles.scss                              全域樣式
└─ app/
   ├─ app.config.ts                         註冊 HttpClient / animations
   ├─ app.routes.ts                         路由（/kindergartens）
   ├─ models/kindergarten.model.ts          API 的 TypeScript 型別
   ├─ services/kindergarten.service.ts      呼叫後端的唯一入口
   └─ pages/kindergarten-list/              查詢畫面
      ├─ kindergarten-list.component.ts
      ├─ kindergarten-list.component.html
      └─ kindergarten-list.component.scss
```

## 目前畫面做了什麼

- 學年度下拉（預設最新的 114 學年度）
- 縣市下拉（可打字過濾，選項後面顯示筆數）
- 公私立下拉
- 園所名稱關鍵字（後端用 `LIKE %關鍵字%`；按 Enter 也可查詢）
- PrimeNG `p-table`，**伺服器端分頁**（`[lazy]="true"`）：
  換頁或排序時只向 API 要那一頁，不會把 7 萬筆全抓回瀏覽器
- 學年度 / 園所名稱可點欄位標題排序

## PrimeNG 樣式怎麼載入的

在 `angular.json` 的 `styles` 陣列，順序不能換：

```json
"node_modules/primeng/resources/themes/lara-light-blue/theme.css",
"node_modules/primeng/resources/primeng.min.css",
"node_modules/primeicons/primeicons.css",
"node_modules/primeflex/primeflex.css",
"src/styles.scss"
```

換主題就是換第一行的 `lara-light-blue`，可選的主題在
`node_modules/primeng/resources/themes/` 下面。

因為 PrimeNG 主題 CSS 本身就有 500 kB 左右，`angular.json` 的 bundle budget
已經放寬到 1.5MB / 3MB，否則 build 會噴 budget 警告。
