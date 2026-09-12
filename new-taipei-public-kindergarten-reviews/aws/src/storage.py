"""附件存取（S3 presigned URL）。

為什麼用 presigned URL 而不是讓瀏覽器把檔案 POST 給我們的 API：
API Gateway 的 payload 上限是 10MB，而規格允許 5 檔 × 5MB = 25MB，
一定會爆。所以瀏覽器直接把檔案傳到 S3，我們只負責簽名。

好處是簽名是純本機的雜湊運算，**不需要對外網路**，
所以 VPC 內的 Lambda 不用額外開 S3 的 VPC endpoint。
（之後若要用 HeadObject 驗證檔案真的存在，就得補一個 S3 Gateway Endpoint，免費。）

bucket 全私有：家長與公務人員看附件都是靠這裡簽出來的短效 GET 連結。
"""

import os
import uuid

BUCKET = os.environ.get("ATTACH_BUCKET", "").strip()
REGION = os.environ.get("AWS_REGION", "us-east-1")

MAX_FILES = 5
MAX_BYTES = 5 * 1024 * 1024  # 單檔 5MB
ALLOWED_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/heic": "heic",
}
UPLOAD_TTL = 900  # presigned POST 有效 15 分鐘
DOWNLOAD_TTL = 900  # presigned GET 有效 15 分鐘

_client = None


def enabled():
    return bool(BUCKET)


def _s3():
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config

        _client = boto3.client(
            "s3",
            region_name=REGION,
            # 一定要 s3v4，否則簽出來的網址在新 region 會被拒
            config=Config(signature_version="s3v4"),
        )
    return _client


def build_key(draft_id, content_type):
    ext = ALLOWED_TYPES.get(content_type, "bin")
    # 不使用使用者提供的檔名，避免路徑穿越與奇怪字元
    return f"reports/{draft_id}/{uuid.uuid4().hex}.{ext}"


def presign_upload(key, content_type):
    """回傳 presigned POST（url + fields）。

    用 POST 而不是 PUT，因為 POST policy 可以帶 content-length-range，
    在 S3 端就把 5MB 上限擋掉，不必信任前端回報的 size。
    """
    res = _s3().generate_presigned_post(
        Bucket=BUCKET,
        Key=key,
        Fields={"Content-Type": content_type},
        Conditions=[
            {"Content-Type": content_type},
            ["content-length-range", 1, MAX_BYTES],
        ],
        ExpiresIn=UPLOAD_TTL,
    )
    return {"url": res["url"], "fields": res["fields"]}


def presign_download(key, filename=None):
    """短效的下載/預覽連結。"""
    params = {"Bucket": BUCKET, "Key": key}
    if filename:
        # 讓瀏覽器直接內嵌顯示（附件都是圖片），檔名保留原始名稱
        safe = filename.replace('"', "").replace("\\", "")
        params["ResponseContentDisposition"] = f'inline; filename="{safe}"'
    return _s3().generate_presigned_url(
        "get_object", Params=params, ExpiresIn=DOWNLOAD_TTL
    )
