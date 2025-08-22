import asyncio
from os import sep
from pathlib import Path
from signal import signal as signal_fn, SIGINT, SIGTERM, SIGABRT
from sys import path, platform, exit

from telethon.errors.rpcerrorlist import AuthKeyError

from pagermaid.common.reload import load_all
from pagermaid.config import Config
from pagermaid.dependence import scheduler
from pagermaid.services import bot
from pagermaid.static import working_dir
from pagermaid.utils import lang, logs, SessionFileManager
from pagermaid.utils.listener import process_exit
from pyromod.methods.sign_in_qrcode import start_client

bot.PARENT_DIR = Path(working_dir)
path.insert(1, f"{working_dir}{sep}plugins")


async def idle():
    task = None

    def signal_handler(_, __):
        task.cancel()

    for s in (SIGINT, SIGTERM, SIGABRT):
        signal_fn(s, signal_handler)

    while True:
        t = bot._run_until_disconnected()
        task = asyncio.create_task(t)
        try:
            await task
        except asyncio.CancelledError:
            break


async def console_bot():
    try:
        await start_client(bot)
    except AuthKeyError:
        SessionFileManager.safe_remove_session()
        exit()
    me = await bot.get_me()
    bot.me = me
    if me.bot:
        SessionFileManager.safe_remove_session()
        exit()
    logs.info(f"{lang('save_id')} {me.first_name}({me.id})")
    await load_all()
    await process_exit(start=True, _client=bot)


async def main():
    logs.info(lang("platform") + platform + lang("platform_load"))
    if not scheduler.running:
        scheduler.start()
    await console_bot()
    logs.info(lang("start"))
    try:
        await idle()
    finally:
        if scheduler.running:
            scheduler.shutdown()

        if bot.is_connected():
            try:
                await bot.disconnect()
            except ConnectionError:
                pass

        if getattr(bot, "_should_restart", False):
            exit(0)


bot.loop.run_until_complete(main())
