"""Generate an ADMIN_PASSWORD_HASH without exposing the password in shell history."""

import sys
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.auth import password_hash


def main() -> None:
    password = getpass("Admin password: ")
    confirmation = getpass("Repeat admin password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match.")
    if not 8 <= len(password) <= 128:
        raise SystemExit("Password must be 8–128 characters.")
    print(password_hash(password))


if __name__ == "__main__":
    main()
