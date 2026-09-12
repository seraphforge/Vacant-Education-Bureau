# 部署設定（含密碼，請勿 commit；已列入 .gitignore）
# 用法：複製成 deploy.config.ps1 後改成自己的值

$Config = @{
    Region        = "us-east-1"
    ProjectName   = "ntpc-kg"
    StackName     = "ntpc-kg-api"

    # RDS 實例名稱，deploy.ps1 會自動查出 VPC / Subnet / SecurityGroup
    DbInstanceId  = "my-mysql-db"

    DbName        = "readme"
    DbUser        = "admin"
    DbPassword    = "CHANGE_ME"

    # ---- 家長回報 ----
    # dev = 不真的寄信，驗證碼直接回在 API response（SES 還在 sandbox 時用這個）
    # ses = 真的寄信，MailFrom 必須是已在 SES 驗證過的地址
    MailMode      = "dev"
    MailFrom      = ""

    # 寄給家長的追蹤連結會用這個網址組出 <PublicBaseUrl>/report/<token>
    # 留空的話信裡只會有相對路徑。填 deploy-web.ps1 印出的 CloudFront 網址。
    PublicBaseUrl = ""

    # 驗證碼的 HMAC pepper（驗證碼本身不入庫，只存 HMAC）。請改成隨機字串。
    OtpPepper     = "CHANGE_ME_RANDOM_STRING"
}
