/**
 * 環境設定。
 *
 * apiBaseUrl        來自 aws/deploy.ps1 印出的 ApiUrl
 * cognitoUserPoolId 來自 aws/deploy-auth.ps1 印出的 UserPoolId
 * cognitoClientId   來自 aws/deploy-auth.ps1 印出的 UserPoolClientId
 *
 * 這三個都不是機密：Client Id 本來就會出現在瀏覽器端，
 * Cognito 的 app client 也刻意不帶 secret（SPA 無法保管密鑰）。
 *
 * 註：mock 切換不放在這裡的全域旗標，而是由各 service 內部各自控制
 * （ReportService §2/§3）。SecureReportService 已全部改打真實 API，
 * 包含風險評估 §4.8 與財報 §4.9。
 *
 * `ng serve` 也是打這個 apiBaseUrl（不是 localhost），所以後端改完要先
 * `cd aws; .\deploy.ps1`，否則新端點會回 404。
 */
export const environment = {
  apiBaseUrl: 'https://e86tz73y7h.execute-api.us-east-1.amazonaws.com',
  cognitoUserPoolId: 'us-east-1_ZCNHHYp51',
  cognitoClientId: '2bpd6sftqdpuqietvclmm2olm5',
};
