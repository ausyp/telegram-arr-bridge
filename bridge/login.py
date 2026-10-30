"""One-time interactive Telegram login: python -m bridge.login

Run it from the container's console. It asks for your phone number and the code
Telegram sends you, then saves the session in the data folder. The running app
notices the new session within about 15 seconds.
"""

import asyncio
import os

from pyrogram import Client, errors

from .config import Settings


async def run() -> None:
    s = Settings.from_env()
    path = os.path.join(s.data_dir, "bridge.session")
    for attempt in range(2):
        try:
            async with Client("bridge", api_id=s.api_id, api_hash=s.api_hash, workdir=s.data_dir) as client:
                me = await client.get_me()
            break
        except errors.Unauthorized:
            if attempt or not os.path.exists(path):
                raise
            print("The saved session was logged out. Starting a fresh login.")
            os.remove(path)
    # The console usually runs as root while the app runs as PUID, so hand the
    # session to whoever owns the data folder.
    if os.getuid() == 0:
        st = os.stat(s.data_dir)
        os.chown(path, st.st_uid, st.st_gid)
    print(f"Logged in as {me.first_name} (@{me.username}). You can close this console.")


if __name__ == "__main__":
    asyncio.run(run())
