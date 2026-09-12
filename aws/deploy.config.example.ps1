# 部署設定（含密碼，請勿 commit；已列入 .gitignore）
# 用法：複製成 deploy.config.ps1 後改成自己的值

$Config = @{
    Region        = "us-east-1"
    ProjectName   = "ntpc-kg"
    StackName     = "ntpc-kg-api"

    # RDS 實例名稱，deploy.ps1 會自動查出 VPC / Subnet / SecurityGroup
    DbInstanceId  = "my-mysql-db"

    DbName        = "moe"
    DbUser        = "admin"
    DbPassword    = "CHANGE_ME"
}
