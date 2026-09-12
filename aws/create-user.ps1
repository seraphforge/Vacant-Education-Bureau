<#
手動建立一個公務人員帳號（demo 用）。

用法：
    .\create-user.ps1 -Username ntpc_staff -County 新北市
    .\create-user.ps1 -Username tpe_staff  -County 臺北市 -Password 'MyPass1234'
    .\create-user.ps1 -Username moe_admin  -Admin          # 看全國

沒帶 -Password 就自動產一組並印出來。
帳號建立後密碼直接設為永久（--permanent），
避免首次登入卡在 NEW_PASSWORD_REQUIRED，demo 現場才不會出狀況。
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Username,
    [string]$County = "",
    [string]$Agency = "",
    [string]$Password = "",
    [switch]$Admin
)

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

. .\deploy.config.ps1
$Region    = $Config.Region
$AuthStack = "$($Config.StackName)-auth"

$outputs = (Invoke-Aws @("cloudformation", "describe-stacks", "--stack-name", $AuthStack,
    "--region", $Region, "--query", "Stacks[0].Outputs", "--output", "json")) | ConvertFrom-Json
$PoolId = ($outputs | Where-Object { $_.OutputKey -eq "UserPoolId" }).OutputValue
if (-not $PoolId) { throw "找不到 UserPoolId，請先執行 .\deploy-auth.ps1" }

if (-not $Password) {
    # 產一組符合密碼原則（大小寫 + 數字，至少 8 碼）的密碼
    $chars = "abcdefghijkmnopqrstuvwxyz"
    $upper = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    $rand = [System.Random]::new()
    $Password = "Ntpc" + `
        (-join (1..6 | ForEach-Object { $chars[$rand.Next($chars.Length)] })) + `
        $upper[$rand.Next($upper.Length)] + $rand.Next(1000, 9999)
}

# 組出 user attributes
$attrs = @()
if ($County) { $attrs += "Name=custom:county,Value=$County" }
if ($Agency) { $attrs += "Name=custom:agency,Value=$Agency" }

Write-Host "==> 建立使用者 $Username" -ForegroundColor Cyan
$createArgs = @("cognito-idp", "admin-create-user",
    "--user-pool-id", $PoolId, "--username", $Username,
    "--message-action", "SUPPRESS", # 不寄邀請信
    "--region", $Region)
if ($attrs.Count -gt 0) { $createArgs += @("--user-attributes") + $attrs }
Invoke-Aws $createArgs | Out-Null

Write-Host "==> 設定永久密碼" -ForegroundColor Cyan
Invoke-Aws @("cognito-idp", "admin-set-user-password",
    "--user-pool-id", $PoolId, "--username", $Username,
    "--password", $Password, "--permanent", "--region", $Region) | Out-Null

if ($Admin) {
    Write-Host "==> 加入 admin 群組（可看全國）" -ForegroundColor Cyan
    Invoke-Aws @("cognito-idp", "admin-add-user-to-group",
        "--user-pool-id", $PoolId, "--username", $Username,
        "--group-name", "admin", "--region", $Region) | Out-Null
}

Write-Host ""
Write-Host "==> 帳號建立完成" -ForegroundColor Green
Write-Host "    帳號   : $Username"
Write-Host "    密碼   : $Password" -ForegroundColor Yellow
Write-Host "    縣市   : $(if ($County) { $County } else { '(未設定)' })"
Write-Host "    admin  : $(if ($Admin) { '是（看全國）' } else { '否' })"
Write-Host ""
Write-Host "    請自行記下密碼，這裡不會留存。" -ForegroundColor DarkGray
