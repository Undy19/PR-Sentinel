import asyncio
import json
from pathlib import Path

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
    env = load_env(str(Path(__file__).parent.parent / ".env"))
    token = env["TELEGRAM_BOT_TOKEN"]
    chat_id = env["TELEGRAM_CHAT_ID"]

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": "✅ PR Sentinel: тестовое сообщение — bot работает"}

    max_attempts = 5
    data = None
    for attempt in range(1, max_attempts + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    data = await resp.json()
            break
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if attempt == max_attempts:
                print(f"ERROR after {max_attempts} attempts: {exc}")
                raise
            delay = attempt
            print(f"attempt {attempt}/{max_attempts} failed ({exc}); retrying in {delay}s")
            await asyncio.sleep(delay)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if data.get("ok"):
        print("OK: message sent")
    else:
        print("ERROR:", data.get("description"))


if __name__ == "__main__":
    _policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if _policy is not None:
        asyncio.set_event_loop_policy(_policy())
    asyncio.run(main())
