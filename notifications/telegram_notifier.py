"""Minimal Telegram notifier for AG Crypto Analyst.

Credentials are read only from environment variables:
  AGCA_TELEGRAM_BOT_TOKEN
  AGCA_TELEGRAM_CHAT_ID

No secret is stored in the repository. Callers can safely test ``is_configured``
before sending. Network failures return False and do not crash the live cycle.
"""
from __future__ import annotations

import os

import requests

BOT_TOKEN_ENV = "AGCA_TELEGRAM_BOT_TOKEN"
CHAT_ID_ENV = "AGCA_TELEGRAM_CHAT_ID"


def is_configured() -> bool:
    return bool(os.getenv(BOT_TOKEN_ENV) and os.getenv(CHAT_ID_ENV))


def send_message(text: str) -> bool:
    token = os.getenv(BOT_TOKEN_ENV)
    chat_id = os.getenv(CHAT_ID_ENV)
    if not token or not chat_id:
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        return bool(payload.get("ok"))
    except Exception as exc:
        print(f"Telegram HATA: {exc}")
        return False


def main() -> None:
    print("AG Crypto Analyst - Telegram Notifier Self-Test")
    if not is_configured():
        print("Durum: YAPILANDIRILMAMIŞ")
        print(f"Eksik ortam değişkenleri: {BOT_TOKEN_ENV} ve/veya {CHAT_ID_ENV}")
        print("Not: hiçbir gizli değer ekrana yazdırılmadı.")
        return

    print("Durum: YAPILANDIRILMIŞ")
    print("Test mesajı gönderiliyor...")
    ok = send_message("✅ AG Crypto Analyst Telegram bağlantısı aktif. Forward Shadow alarm kanalı hazır.")
    print("Gönderim:", "BAŞARILI" if ok else "BAŞARISIZ")


if __name__ == "__main__":
    main()
