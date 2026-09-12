# -*- coding: utf-8 -*-
"""Open an SSH tunnel to the private RDS via the bastion host, return a PyMySQL connection.

Mirrors aws/local_test.py's approach (uses the ssh CLI + subprocess), but standalone
so the scraper can create/load the punishment table.
"""
import atexit
import os
import socket
import subprocess
import sys
import time

import pymysql

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # ntpc_hackathon/
KEY = os.path.join(ROOT, "bastion-key.pem")
BASTION = "ec2-user@54.234.197.134"
RDS = "my-mysql-db.cxxv46x5y2qp.us-east-1.rds.amazonaws.com"
LOCAL_PORT = 13399

DB_USER = os.environ.get("DB_USER", "admin")
# 密碼不寫在程式裡。設環境變數 DB_PASSWORD，或讓呼叫端從
# aws/deploy.config.ps1（已 gitignore）讀出來後再設進環境。
#   PowerShell: $env:DB_PASSWORD = "..."
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_NAME = os.environ.get("DB_NAME", "readme")

_tunnel = None


def open_tunnel():
    global _tunnel
    if _tunnel is not None:
        return
    proc = subprocess.Popen(
        ["ssh", "-i", KEY, "-o", "StrictHostKeyChecking=no",
         "-o", "UserKnownHostsFile=NUL", "-o", "ExitOnForwardFailure=yes",
         "-N", "-L", f"{LOCAL_PORT}:{RDS}:3306", BASTION],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    for i in range(40):
        time.sleep(1)
        if proc.poll() is not None:
            sys.exit("SSH tunnel failed:\n" + proc.stdout.read())
        sock = socket.socket()
        sock.settimeout(1)
        try:
            sock.connect(("127.0.0.1", LOCAL_PORT))
            sock.close()
            _tunnel = proc
            atexit.register(lambda: proc.terminate())
            print(f"tunnel ready ({i+1}s)")
            return
        except OSError:
            continue
    proc.terminate()
    sys.exit("SSH tunnel timed out")


def connect():
    if not DB_PASSWORD:
        sys.exit(
            "缺少 DB_PASSWORD 環境變數。\n"
            "PowerShell：$env:DB_PASSWORD = (從 aws/deploy.config.ps1 取得)"
        )
    open_tunnel()
    return pymysql.connect(
        host="127.0.0.1", port=LOCAL_PORT,
        user=DB_USER, password=DB_PASSWORD, database=DB_NAME,
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10, read_timeout=30, write_timeout=30, autocommit=True,
    )
