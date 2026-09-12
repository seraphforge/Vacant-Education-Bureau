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
 * useMockApi        規劃中（尚未上線）的家長回報 / 政府端回報 / 風險評估
 *                   端點會走 mock 實作（回傳與 API_SPEC 契約對齊的假資料）。
 *                   後端上線後把這個關成 false，元件不用改。
 *                   已上線的端點（/api/kindergartens、/api/counties、
 *                   /api/secure/me、/api/secure/kindergartens）不受此旗標影響。
 */
export const environment = {
  apiBaseUrl: 'https://e86tz73y7h.execute-api.us-east-1.amazonaws.com',
  cognitoUserPoolId: 'us-east-1_ZCNHHYp51',
  cognitoClientId: '2bpd6sftqdpuqietvclmm2olm5',
  useMockApi: true,
};
