import asyncio
import json

import aiohttp


def load_env(path: str) -> dict:
    env = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


async def main() -> None:
    env = load_env(r"D:/projects/opd/.env")
    token = env["TELEGRAM_BOT_TOKEN"]
    chat_id = env["TELEGRAM_CHAT_ID"]

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": "✅ PR Sentinel: тестовое сообщение — bot работает"}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
            print(json.dumps(data, ensure_ascii=False, indent=2))
            if data.get("ok"):
                print("OK: message sent")
            else:
                print("ERROR:", data.get("description"))


if __name__ == "__main__":
    asyncio.run(main())
