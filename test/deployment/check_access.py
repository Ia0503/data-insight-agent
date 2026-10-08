"""Read-only verification of the local password hash through hidden input."""

import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    from app.core.access import verify_password
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.analysis_password_required:
        raise SystemExit("Password protection is disabled.")
    password = getpass.getpass("核对调用密码（隐藏输入）：")
    if not verify_password(password, settings.analysis_password_hash.get_secret_value()):
        raise SystemExit("Password verification failed; no changes or model requests.")
    print("Local password hash matches; no changes or model requests.")


if __name__ == "__main__":
    main()
