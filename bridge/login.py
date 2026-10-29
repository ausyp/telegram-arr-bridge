"""One-time interactive Telegram login: python -m bridge.login

Run it from the container's console. It asks for your phone number and the code
Telegram sends you, then saves the session in the data folder. The running app
notices the new session within about 15 seconds.
"""

import asyncio

from pyrogram import Client

from .config import Settings


async def run() -> None:
    s = Settings.from_env()
    async with Client("bridge", api_id=s.api_id, api_hash=s.api_hash, workdir=s.data_dir) as client:
        me = await client.get_me()
        print(f"Logged in as {me.first_name} (@{me.username}). You can close this console.")


if __name__ == "__main__":
    asyncio.run(run())
