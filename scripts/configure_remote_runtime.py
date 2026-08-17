"""Securely store the AGCA PostgreSQL connection in macOS Keychain."""

from __future__ import annotations

import getpass
import subprocess
from urllib.parse import quote

from database.runtime_store import KEYCHAIN_SERVICE


PLACEHOLDER = "[YOUR-PASSWORD]"


def build_connection_url(template: str, password: str) -> str:
    template = template.strip()
    if PLACEHOLDER not in template:
        raise ValueError(f"Connection URI must contain {PLACEHOLDER}")
    url = template.replace(PLACEHOLDER, quote(password, safe=""), 1)
    if "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def main() -> int:
    print("Paste the Supabase Session pooler URI containing [YOUR-PASSWORD].")
    template = input("Connection URI template: ").strip()
    password = getpass.getpass("Database password (hidden): ")
    try:
        url = build_connection_url(template, password)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    account = getpass.getuser()
    result = subprocess.run(
        [
            "security",
            "add-generic-password",
            "-U",
            "-a",
            account,
            "-s",
            KEYCHAIN_SERVICE,
            "-w",
            url,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("ERROR: Could not save the connection in macOS Keychain.")
        return result.returncode
    print("AGCA remote database connection saved securely in macOS Keychain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
