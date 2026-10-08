"""Create local configuration without overwriting existing credentials."""

import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for directory in ("runtime/uploads", "runtime/models"):
    (ROOT / directory).mkdir(parents=True, exist_ok=True)
target = ROOT / ".env"
if target.exists():
    print(".env already exists; left unchanged.")
else:
    password = secrets.token_hex(24)
    # 使用完整模板，新环境也保留模型待填写项；真实凭据只写本地 .env。
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    configuration = template.replace("YOUR_DATABASE_PASSWORD_HERE", password)
    if os.name != "nt":
        configuration += f"\nAPP_UID={os.getuid()}\nAPP_GID={os.getgid()}\n"
    target.write_text(
        configuration,
        encoding="utf-8",
    )
    print("Created local .env with a generated password (not displayed).")
