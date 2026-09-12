<#
一鍵部署後端。做的事情：
  1. 讀 deploy.config.ps1
  2. 用 AWS CLI 查出 RDS 的 VPC / 子網 / 安全群組（不用手動填）
  3. 把 src/ + pymysql 打包成 lambda.zip
  4. 上傳 zip 到 S3（artifact bucket，沒有就自動建立）
  5. 跑 CloudFormation 建立/更新整套資源
  6. 印出 API 網址

用法： cd aws ; .\deploy.ps1
#>

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# aws CLI 有時會把訊息寫到 stderr，PowerShell 在 Stop 模式下會誤判為致命錯誤，
# 所以底下改用「檢查 $LASTEXITCODE」的方式自己判斷成功與否。
function Use-NativeErrorMode { $script:ErrorActionPreference = "Continue" }
function Use-StrictErrorMode { $script:ErrorActionPreference = "Stop" }

# ---------- 1. 讀設定 ----------
if (-not (Test-Path ".\deploy.config.ps1")) {
    throw "找不到 deploy.config.ps1，請先複製 deploy.config.example.ps1 並填入資料庫密碼。"
}
. .\deploy.config.ps1

$Region      = $Config.Region
$ProjectName = $Config.ProjectName
$StackName   = $Config.StackName

function Invoke-Aws {
    param([string[]]$CliArgs)
    Use-NativeErrorMode
    $out = & aws @CliArgs 2>&1
    $code = $LASTEXITCODE
    Use-StrictErrorMode
    if ($code -ne 0) {
        $joined = $CliArgs -join ' '
        throw ("aws " + $joined + [Environment]::NewLine + ($out | Out-String))
    }
    return $out
}

function Test-AwsSuccess {
    param([string[]]$CliArgs)
    Use-NativeErrorMode
    & aws @CliArgs 2>&1 | Out-Null
    $code = $LASTEXITCODE
    Use-StrictErrorMode
    return ($code -eq 0)
}

Write-Host "==> 讀取帳號資訊" -ForegroundColor Cyan
$AccountId = (Invoke-Aws @("sts", "get-caller-identity", "--query", "Account", "--output", "text")).Trim()
Write-Host "    Account: $AccountId / Region: $Region"

# ---------- 2. 查 RDS 網路設定 ----------
Write-Host "==> 查詢 RDS ($($Config.DbInstanceId)) 的網路設定" -ForegroundColor Cyan
$rdsJson = Invoke-Aws @(
    "rds", "describe-db-instances",
    "--db-instance-identifier", $Config.DbInstanceId,
    "--region", $Region, "--output", "json"
)
$rds = ($rdsJson | ConvertFrom-Json).DBInstances[0]

$DbHost    = $rds.Endpoint.Address
$DbPort    = [string]$rds.Endpoint.Port
$VpcId     = $rds.DBSubnetGroup.VpcId
$SubnetIds = @($rds.DBSubnetGroup.Subnets | ForEach-Object { $_.SubnetIdentifier })
$RdsSgId   = $rds.VpcSecurityGroups[0].VpcSecurityGroupId

Write-Host "    Host   : $DbHost`:$DbPort"
Write-Host "    VPC    : $VpcId"
Write-Host "    Subnets: $($SubnetIds -join ',')"
Write-Host "    RDS SG : $RdsSgId"

# ---------- 3. 打包 Lambda ----------
Write-Host "==> 打包 Lambda" -ForegroundColor Cyan
$BuildDir = Join-Path $PSScriptRoot "build"
$ZipPath  = Join-Path $PSScriptRoot "lambda.zip"
Remove-Item -Recurse -Force $BuildDir -ErrorAction SilentlyContinue
Remove-Item -Force $ZipPath -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $BuildDir | Out-Null

# pymysql 是純 Python，直接安裝到 build 目錄即可（不需要編譯）
Use-NativeErrorMode
& python -m pip install -r .\src\requirements.txt --target $BuildDir --quiet --disable-pip-version-check
$pipCode = $LASTEXITCODE
Use-StrictErrorMode
if ($pipCode -ne 0) { throw "pip install 失敗" }
Copy-Item .\src\*.py $BuildDir

# 用 python 打包（PowerShell 的 Compress-Archive 在 Windows 會寫出反斜線路徑，Lambda 會壞）
Use-NativeErrorMode
& python .\build_zip.py $BuildDir $ZipPath
$zipCode = $LASTEXITCODE
Use-StrictErrorMode
if ($zipCode -ne 0) { throw "打包失敗" }
$zipSize = [math]::Round((Get-Item $ZipPath).Length / 1KB, 1)
Write-Host "    lambda.zip = $zipSize KB"

# ---------- 4. 上傳到 S3 ----------
$Bucket = "$ProjectName-artifacts-$AccountId"
Write-Host "==> 準備 S3 bucket: $Bucket" -ForegroundColor Cyan
$exists = Test-AwsSuccess @("s3api", "head-bucket", "--bucket", $Bucket, "--region", $Region)
if (-not $exists) {
    Write-Host "    建立 bucket"
    # us-east-1 建 bucket 不能帶 LocationConstraint
    Invoke-Aws @("s3api", "create-bucket", "--bucket", $Bucket, "--region", $Region) | Out-Null
    Invoke-Aws @("s3api", "put-public-access-block", "--bucket", $Bucket, "--region", $Region,
        "--public-access-block-configuration",
        "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true") | Out-Null
} else {
    Write-Host "    bucket 已存在"
}

# key 帶時間戳，CloudFormation 才知道程式碼有變、需要更新 Lambda
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Key   = "lambda/$Stamp/lambda.zip"
Write-Host "==> 上傳 s3://$Bucket/$Key" -ForegroundColor Cyan
Invoke-Aws @("s3", "cp", $ZipPath, "s3://$Bucket/$Key", "--region", $Region) | Out-Null

# ---------- 5. 部署 CloudFormation ----------
Write-Host "==> 部署 CloudFormation stack: $StackName" -ForegroundColor Cyan
$paramOverrides = @(
    "ProjectName=$ProjectName",
    "CodeS3Bucket=$Bucket",
    "CodeS3Key=$Key",
    "VpcId=$VpcId",
    "SubnetIds=$($SubnetIds -join ',')",
    "RdsSecurityGroupId=$RdsSgId",
    "DbHost=$DbHost",
    "DbPort=$DbPort",
    "DbName=$($Config.DbName)",
    "DbUser=$($Config.DbUser)",
    "DbPassword=$($Config.DbPassword)"
)

Use-NativeErrorMode
& aws cloudformation deploy `
    --template-file .\template.yaml `
    --stack-name $StackName `
    --region $Region `
    --capabilities CAPABILITY_IAM `
    --no-fail-on-empty-changeset `
    --parameter-overrides @paramOverrides
$cfnCode = $LASTEXITCODE
if ($cfnCode -ne 0) {
    Write-Host "部署失敗，最近的錯誤事件：" -ForegroundColor Red
    $q = "StackEvents[?contains(ResourceStatus, 'FAILED')].[LogicalResourceId,ResourceStatusReason]"
    & aws cloudformation describe-stack-events --stack-name $StackName --region $Region `
        --query $q --output table
    Use-StrictErrorMode
    throw "CloudFormation 部署失敗"
}
Use-StrictErrorMode

# ---------- 6. 輸出 ----------
Write-Host "==> 完成" -ForegroundColor Green
$outJson = Invoke-Aws @("cloudformation", "describe-stacks", "--stack-name", $StackName,
    "--region", $Region, "--query", "Stacks[0].Outputs", "--output", "json")
$outputs = $outJson | ConvertFrom-Json
foreach ($o in $outputs) { Write-Host ("    {0,-13}= {1}" -f $o.OutputKey, $o.OutputValue) }

$apiUrl = ($outputs | Where-Object { $_.OutputKey -eq "ApiUrl" }).OutputValue
Write-Host ""
Write-Host "測試指令：" -ForegroundColor Yellow
Write-Host "  curl `"$apiUrl/api/health`""
Write-Host "  curl `"$apiUrl/api/counties`""
Write-Host "  curl `"$apiUrl/api/kindergartens?county=新北市&name=橘&pageSize=5`""
