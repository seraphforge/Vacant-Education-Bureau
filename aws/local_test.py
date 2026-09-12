"""本機測試 Lambda handler：透過 bastion SSH tunnel 連 RDS，不需要部署到 AWS。

用途：改完 src/app.py 之後，先在本機確認 SQL 與回應都正確，再跑 deploy.ps1。

用法：
    cd aws
    python local_test.py

帳密從 deploy.config.ps1 讀（該檔已 gitignore），所以這支程式裡沒有任何密碼。
"""

import io
import json
import os
import re
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(ROOT, "deploy.config.ps1")

# bastion EC2（用來打通到私有 RDS 的 SSH tunnel）
BASTION = "ec2-user@54.234.197.134"
KEY = os.path.join(os.path.dirname(ROOT), "bastion-key.pem")
RDS = "my-mysql-db.cxxv46x5y2qp.us-east-1.rds.amazonaws.com"
PORT = 13399

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", write_through=True)


def read_config():
    """從 deploy.config.ps1 撈出 DbUser / DbPassword / DbName。"""
    if not os.path.exists(CONFIG):
        sys.exit("找不到 deploy.config.ps1，請先從 deploy.config.example.ps1 複製一份。")
    text = open(CONFIG, encoding="utf-8-sig").read()
    conf = {}
    for key in ("DbUser", "DbPassword", "DbName"):
        m = re.search(rf'{key}\s*=\s*"([^"]*)"', text)
        if not m:
            sys.exit(f"deploy.config.ps1 裡找不到 {key}")
        conf[key] = m.group(1)
    return conf


def open_tunnel():
    proc = subprocess.Popen(
        ["ssh", "-i", KEY, "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=NUL",
         "-o", "ExitOnForwardFailure=yes", "-N", "-L", f"{PORT}:{RDS}:3306", BASTION],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    for i in range(40):
        time.sleep(1)
        if proc.poll() is not None:
            sys.exit("SSH tunnel 失敗：\n" + proc.stdout.read())
        sock = socket.socket()
        sock.settimeout(1)
        try:
            sock.connect(("127.0.0.1", PORT))
            sock.close()
            print(f"tunnel ready ({i + 1}s)")
            return proc
        except OSError:
            continue
    proc.terminate()
    sys.exit("SSH tunnel 逾時")


def main():
    conf = read_config()
    tunnel = open_tunnel()
    try:
        os.environ.update({
            "DB_HOST": "127.0.0.1",
            "DB_PORT": str(PORT),
            "DB_USER": conf["DbUser"],
            "DB_PASSWORD": conf["DbPassword"],
            "DB_NAME": conf["DbName"],
        })

        sys.path.insert(0, os.path.join(ROOT, "src"))
        import app  # noqa: E402  （要等環境變數設好才能 import）

        def call(path, qs=None):
            event = {
                "rawPath": path,
                "requestContext": {"http": {"method": "GET"}},
                "queryStringParameters": qs or {},
            }
            res = app.handler(event, None)
            return res["statusCode"], json.loads(res["body"])

        print("\n=== /api/health ===")
        print(call("/api/health"))

        print("\n=== /api/counties ===")
        code, body = call("/api/counties")
        print("status", code, "| 縣市組數 =", len(body["items"]))
        for item in body["items"][:5]:
            print("  ", item)
        dirty = [i["county"] for i in body["items"] if "[" in (i["county"] or "")]
        print("   仍含 '[' 前綴的縣市:", dirty if dirty else "無")

        print("\n=== /api/kindergartens?county=新北市&name=非營利 ===")
        code, body = call("/api/kindergartens",
                          {"county": "新北市", "name": "非營利", "pageSize": "3"})
        print("status", code, "| total =", body["total"], "| year =", body["academicYear"])
        for item in body["items"]:
            print("  ", item["school_name"], "|", item["county"], "|", item["district"])

        print("\n=== /api/kindergartens?county=臺北市&ownership=公立（第2頁 + 名稱倒序）===")
        code, body = call("/api/kindergartens", {
            "county": "臺北市", "ownership": "公立", "page": "2", "pageSize": "3",
            "sortBy": "school_name", "sortDir": "desc",
        })
        print("status", code, "| total =", body["total"])
        for item in body["items"]:
            print("  ", item["school_name"])

        print("\n=== EXPLAIN：確認 idx_county 索引有被用到 ===")
        with app.get_conn().cursor() as cur:
            cur.execute("EXPLAIN SELECT COUNT(*) FROM kindergarten "
                        "WHERE academic_year=%s AND county=%s", ("114", "新北市"))
            for row in cur.fetchall():
                print("   key =", row.get("key"), "| type =", row.get("type"),
                      "| rows =", row.get("rows"))

        print("\n=== /api/academic-years ===")
        code, body = call("/api/academic-years")
        print("status", code, "|", [i["academic_year"] for i in body["items"]])
    finally:
        tunnel.terminate()


if __name__ == "__main__":
    main()
