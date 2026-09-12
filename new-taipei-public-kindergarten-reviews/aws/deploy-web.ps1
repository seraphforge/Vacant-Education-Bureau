<#
把前端部署到 AWS（S3 + CloudFront）。做的事情：
  1. 建立/更新 CloudFormation stack（S3 bucket + CloudFront）
  2. 在 webapp/ 執行 ng build
  3. 把 dist/webapp/browser/ 同步到 S3
  4. 清 CloudFront 快取（invalidation），讓新版立刻生效
  5. 印出 live demo 網址

用法： cd aws ; .\deploy-web.ps1
#>

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Use-NativeErrorMode { $script:ErrorActionPreference = "Continue" }
function Use-StrictErrorMode { $script:ErrorActionPreference = "Stop" }

function Invoke-Aws {
    param([string[]]$CliArgs)
    Use-NativeErrorMode
    $out = & aws @CliArgs 2>&1
    $code = $LASTEXITCODE
    Use-StrictErrorMode
    if ($code -ne 0) {
        throw ("aws " + ($CliArgs -join ' ') + [Environment]::NewLine + ($out | Out-String))
    }
    return $out
}

if (-not (Test-Path ".\deploy.config.ps1")) {
    throw "找不到 deploy.config.ps1，請先複製 deploy.config.example.ps1。"
}
. .\deploy.config.ps1

$Region      = $Config.Region
$ProjectName = $Config.ProjectName
$WebStack    = "$($Config.StackName)-web"
$WebAppDir   = Join-Path (Split-Path $PSScriptRoot -Parent) "webapp"

# ---------- 1. 建立 S3 + CloudFront ----------
Write-Host "==> 部署 CloudFormation stack: $WebStack" -ForegroundColor Cyan
Write-Host "    (第一次建立 CloudFront 大約要 3~8 分鐘，請耐心等)"
Use-NativeErrorMode
& aws cloudformation deploy `
    --template-file .\web-template.yaml `
    --stack-name $WebStack `
    --region $Region `
    --no-fail-on-empty-changeset `
    --parameter-overrides "ProjectName=$ProjectName"
$cfnCode = $LASTEXITCODE
if ($cfnCode -ne 0) {
    Write-Host "部署失敗，最近的錯誤事件：" -ForegroundColor Red
    $q = "StackEvents[?contains(ResourceStatus, 'FAILED')].[LogicalResourceId,ResourceStatusReason]"
    & aws cloudformation describe-stack-events --stack-name $WebStack --region $Region --query $q --output table
    Use-StrictErrorMode
    throw "CloudFormation 部署失敗"
}
Use-StrictErrorMode

$outputs = (Invoke-Aws @("cloudformation", "describe-stacks", "--stack-name", $WebStack,
    "--region", $Region, "--query", "Stacks[0].Outputs", "--output", "json")) | ConvertFrom-Json
function Get-Out($key) { ($outputs | Where-Object { $_.OutputKey -eq $key }).OutputValue }

$Bucket    = Get-Out "WebBucketName"
$DistId    = Get-Out "DistributionId"
$SiteUrl   = Get-Out "SiteUrl"
Write-Host "    Bucket       = $Bucket"
Write-Host "    Distribution = $DistId"

# ---------- 2. 建置前端 ----------
Write-Host "==> 建置 Angular（production）" -ForegroundColor Cyan
Push-Location $WebAppDir
if (-not (Test-Path ".\node_modules")) {
    Write-Host "    node_modules 不存在，先跑 npm install"
    Use-NativeErrorMode
    & npm install
    $npmCode = $LASTEXITCODE
    Use-StrictErrorMode
    if ($npmCode -ne 0) { Pop-Location; throw "npm install 失敗" }
}
Use-NativeErrorMode
& npx ng build --configuration production
$buildCode = $LASTEXITCODE
Use-StrictErrorMode
Pop-Location
if ($buildCode -ne 0) { throw "ng build 失敗" }

$DistDir = Join-Path $WebAppDir "dist\webapp\browser"
if (-not (Test-Path (Join-Path $DistDir "index.html"))) {
    throw "找不到 $DistDir\index.html，請確認 angular.json 的 outputPath"
}

# ---------- 3. 上傳到 S3 ----------
# 帶 hash 的 js/css 可以長期快取；index.html 一定要 no-cache，
# 否則使用者會一直拿到舊版的 HTML（指向已被刪掉的舊 js）。
Write-Host "==> 上傳到 s3://$Bucket" -ForegroundColor Cyan
Invoke-Aws @("s3", "sync", $DistDir, "s3://$Bucket", "--region", $Region,
    "--delete", "--cache-control", "public,max-age=31536000,immutable",
    "--exclude", "index.html", "--exclude", "*.map") | Out-Null

Invoke-Aws @("s3", "cp", (Join-Path $DistDir "index.html"), "s3://$Bucket/index.html",
    "--region", $Region, "--cache-control", "no-cache,no-store,must-revalidate",
    "--content-type", "text/html; charset=utf-8") | Out-Null

# ---------- 4. 清 CloudFront 快取 ----------
Write-Host "==> 清除 CloudFront 快取" -ForegroundColor Cyan
$inv = (Invoke-Aws @("cloudfront", "create-invalidation", "--distribution-id", $DistId,
    "--paths", "/*", "--output", "json")) | ConvertFrom-Json
Write-Host "    Invalidation = $($inv.Invalidation.Id)"

# ---------- 5. 完成 ----------
Write-Host ""
Write-Host "==> 完成！Live demo 網址：" -ForegroundColor Green
Write-Host "    $SiteUrl" -ForegroundColor Yellow
Write-Host ""
Write-Host "剛建立的 CloudFront 可能還要 1~2 分鐘才會在全球生效。" -ForegroundColor DarkGray
