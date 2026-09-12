<#
部署 Cognito 使用者池（登入用）。

這個 stack 跟 API / 前端是分開的，因為它存的是「使用者帳號」，
不希望被別的部署動作波及。

用法： cd aws ; .\deploy-auth.ps1
部署完會印出 UserPoolId / UserPoolClientId，
Client Id 要填到 webapp/src/environments/environment.ts。
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
    throw "找不到 deploy.config.ps1，請先從 deploy.config.example.ps1 複製一份。"
}
. .\deploy.config.ps1

$Region      = $Config.Region
$ProjectName = $Config.ProjectName
$AuthStack   = "$($Config.StackName)-auth"

Write-Host "==> 部署 CloudFormation stack: $AuthStack" -ForegroundColor Cyan
Use-NativeErrorMode
& aws cloudformation deploy `
    --template-file .\auth-template.yaml `
    --stack-name $AuthStack `
    --region $Region `
    --no-fail-on-empty-changeset `
    --parameter-overrides "ProjectName=$ProjectName"
$cfnCode = $LASTEXITCODE
if ($cfnCode -ne 0) {
    Write-Host "部署失敗，最近的錯誤事件：" -ForegroundColor Red
    $q = "StackEvents[?contains(ResourceStatus, 'FAILED')].[LogicalResourceId,ResourceStatusReason]"
    & aws cloudformation describe-stack-events --stack-name $AuthStack --region $Region --query $q --output table
    Use-StrictErrorMode
    throw "CloudFormation 部署失敗"
}
Use-StrictErrorMode

$outputs = (Invoke-Aws @("cloudformation", "describe-stacks", "--stack-name", $AuthStack,
    "--region", $Region, "--query", "Stacks[0].Outputs", "--output", "json")) | ConvertFrom-Json

Write-Host "==> 完成" -ForegroundColor Green
foreach ($o in $outputs) { Write-Host ("    {0,-17}= {1}" -f $o.OutputKey, $o.OutputValue) }

$clientId = ($outputs | Where-Object { $_.OutputKey -eq "UserPoolClientId" }).OutputValue
Write-Host ""
Write-Host "接下來：" -ForegroundColor Yellow
Write-Host "  1) 把這個 Client Id 填進 webapp/src/environments/environment.ts："
Write-Host "     cognitoClientId: '$clientId'" -ForegroundColor Gray
Write-Host "  2) 建一個 demo 帳號："
Write-Host "     .\create-user.ps1 -Username ntpc_staff -County 新北市" -ForegroundColor Gray
Write-Host "  3) 重跑 .\deploy.ps1，API 才會掛上 JWT 驗證"
