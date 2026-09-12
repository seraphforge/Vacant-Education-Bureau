/**
 * 環境設定。
 *
 * apiBaseUrl        來自 aws/deploy.ps1 印出的 ApiUrl
 * cognitoUserPoolId 來自 aws/deploy-auth.ps1 印出的 UserPoolId
 * cognitoClientId   來自 aws/deploy-auth.ps1 印出的 UserPoolClientId
 *
 * 這三個都不是機密：Client Id 本來就會出現在瀏覽器端，
 * Cognito 的 app client 也刻意不帶 secret（SPA 無法保管密鑰）。
 */
export const environment = {
  apiBaseUrl: 'https://e86tz73y7h.execute-api.us-east-1.amazonaws.com',
  cognitoUserPoolId: 'us-east-1_ZCNHHYp51',
  cognitoClientId: '2bpd6sftqdpuqietvclmm2olm5',
};
