"""Interactively write only the salted password hash to the ignored local .env."""

import getpass
from pathlib import Path

from app.core.access import password_hash
from dotenv import set_key

ROOT = Path(__file__).resolve().parents[1]


def main():
    target = ROOT / ".env"
    if not target.is_file():
        raise SystemExit("请先运行 backend/setup_local.py 创建本地配置。")
    password = getpass.getpass("调用密码（输入不显示）：")
    repeated = getpass.getpass("再次输入调用密码：")
    if password != repeated:
        raise SystemExit("两次输入不同，未修改配置。")
    encoded = password_hash(password)
    set_key(target, "ANALYSIS_PASSWORD_HASH", encoded, encoding="utf-8")
    set_key(target, "ANALYSIS_PASSWORD_REQUIRED", "true", quote_mode="never", encoding="utf-8")
    print("调用密码已配置；仅保存带随机盐的哈希，重启后端后生效。")


if __name__ == "__main__":
    main()
