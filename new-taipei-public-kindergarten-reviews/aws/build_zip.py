"""把 build/ 目錄打包成 lambda.zip。

不用 PowerShell 的 Compress-Archive，因為它在 Windows 上會把路徑分隔符寫成
反斜線，Lambda 解壓後會找不到套件（例如 pymysql）。
"""

import os
import sys
import zipfile

build_dir = sys.argv[1]
zip_path = sys.argv[2]

with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(build_dir):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "bin")]
        for name in files:
            if name.endswith(".pyc"):
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, build_dir).replace(os.sep, "/")
            zf.write(full, rel)

print(f"{zip_path} ({os.path.getsize(zip_path)} bytes)")
