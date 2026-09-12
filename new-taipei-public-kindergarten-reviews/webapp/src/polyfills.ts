/**
 * amazon-cognito-identity-js 內部用到 Node.js 的 `buffer` 套件，
 * 而 `buffer` 會存取 Node 才有的 `global`。
 * 瀏覽器裡沒有 `global`，不補這一行的話頁面會直接掛掉
 * （console 出現 "global is not defined"，整個 Angular 都不會啟動）。
 */
(window as unknown as { global: Window }).global = window;
