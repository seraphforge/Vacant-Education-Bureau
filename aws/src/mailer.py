"""寄信（Amazon SES v2）。

VPC 內的 Lambda 沒有對外網路，所以 template.yaml 建了一個
`com.amazonaws.<region>.email` 的 interface VPC endpoint（PrivateLink），
SESv2 的 API 呼叫走內網到那個 endpoint，不需要 NAT Gateway。

兩種模式（環境變數 MAIL_MODE）
------------------------------
dev  預設。不真的寄信，把驗證碼印到 CloudWatch log，並讓 API 回傳 devOtp。
     SES 現在還在 sandbox（只能寄給已驗證的信箱），demo 時用這個模式才不會卡住。
ses  真的寄。收件人必須是已驗證的 identity，否則 SES 會拒絕；
     這種失敗一律吞掉並回 False，不讓寄信失敗連帶讓整個回報流程失敗。
"""

import os

MAIL_MODE = os.environ.get("MAIL_MODE", "dev").strip().lower()
MAIL_FROM = os.environ.get("MAIL_FROM", "").strip()
# 前端網址，用來組出 /report/<token> 的完整連結
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")

_client = None


def _ses():
    global _client
    if _client is None:
        import boto3  # Lambda runtime 內建，本機測試才需要自己裝

        _client = boto3.client("sesv2")
    return _client


def tracking_url(token):
    """給家長點的完整追蹤連結。"""
    return f"{PUBLIC_BASE_URL}/report/{token}" if PUBLIC_BASE_URL else f"/report/{token}"


def _send(to_email, subject, body_text):
    """實際送出。回傳 True 表示 SES 收下了。"""
    if MAIL_MODE != "ses":
        print(f"[mailer:dev] to={to_email} subject={subject}")
        print(f"[mailer:dev] body=\n{body_text}")
        return False
    if not MAIL_FROM:
        print("[mailer] MAIL_MODE=ses 但沒設定 MAIL_FROM，略過寄信")
        return False
    try:
        _ses().send_email(
            FromEmailAddress=MAIL_FROM,
            Destination={"ToAddresses": [to_email]},
            Content={
                "Simple": {
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": body_text, "Charset": "UTF-8"}},
                }
            },
        )
        return True
    except Exception as exc:  # noqa: BLE001
        # 最常見的原因：SES 還在 sandbox，收件人未驗證。
        # 不能讓這件事讓整個回報流程失敗，所以只記 log。
        print(f"[mailer] send failed ({type(exc).__name__}): {exc}")
        return False


def send_otp(to_email, code, school_name, ttl_minutes):
    subject = "【教育機構風險評估整合平臺】回報驗證碼"
    body = (
        f"您好，\n\n"
        f"您正在回報「{school_name}」的相關情形。\n"
        f"驗證碼：{code}\n\n"
        f"請在 {ttl_minutes} 分鐘內於網頁上輸入此驗證碼，完成後回報才會正式送出。\n"
        f"若這不是您本人的操作，請忽略本信。\n"
    )
    return _send(to_email, subject, body)


def send_tracking_link(to_email, case_no, token, school_name):
    subject = f"【教育機構風險評估整合平臺】回報已受理（案號 {case_no}）"
    body = (
        f"您好，\n\n"
        f"您對「{school_name}」的回報已完成驗證並送交主管機關。\n"
        f"案號：{case_no}\n\n"
        f"可隨時透過下列連結查詢處理進度：\n{tracking_url(token)}\n\n"
        f"請妥善保存此連結，任何人取得連結即可查看此案件進度。\n"
    )
    return _send(to_email, subject, body)


def send_reply_notice(to_email, case_no, token):
    """政府端回覆時的通知信。刻意不夾帶回覆內容，避免寄錯信箱洩漏案情。"""
    subject = f"【教育機構風險評估整合平臺】案件 {case_no} 有新回覆"
    body = (
        f"您好，\n\n"
        f"您的回報案件（案號 {case_no}）有新的處理進度或回覆。\n"
        f"請點選下列連結查看：\n{tracking_url(token)}\n"
    )
    return _send(to_email, subject, body)
