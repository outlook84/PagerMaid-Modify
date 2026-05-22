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
from pyromod.methods.sign_in_qrcode import start_client

bot.PARENT_DIR = Path(working_dir)
path.insert(1, f"{working_dir}{sep}plugins")

INITIAL_RETRY_DELAY = 5
MAX_RETRY_DELAY = 120
STABLE_RETRY_RESET_AFTER = 300


async def sleep_before_retry(delay):
    logs.warning(f"{lang('telegram_retrying')} {delay}s")
    await asyncio.sleep(delay)
    return min(delay * 2, MAX_RETRY_DELAY)


async def idle():
    task = None
    idle_task = asyncio.current_task()
    retry_delay = INITIAL_RETRY_DELAY

    def signal_handler(_, __):
        if task and not task.done():
            task.cancel()
        elif idle_task and not idle_task.done():
            idle_task.cancel()

    for s in (SIGINT, SIGTERM, SIGABRT):
        signal_fn(s, signal_handler)

    try:
        while True:
            if not bot.is_connected():
                try:
                    logs.info(lang("telegram_connecting"))
                    await bot.connect()
                except (OSError, ConnectionError, TimeoutError, asyncio.TimeoutError) as e:
                    logs.warning(f"{lang('telegram_connection_failed')}: {type(e).__name__}: {e}")
                    retry_delay = await sleep_before_retry(retry_delay)
                    continue

            started_at = asyncio.get_running_loop().time()
            t = bot._run_until_disconnected()
            task = asyncio.create_task(t)
            disconnected_logged = False
            try:
                await task
            except asyncio.CancelledError:
                break
            except (OSError, ConnectionError, TimeoutError, asyncio.TimeoutError) as e:
                logs.warning(f"{lang('telegram_disconnected')}: {type(e).__name__}: {e}")
                disconnected_logged = True

            if getattr(bot, "_should_restart", False):
                break

            if asyncio.get_running_loop().time() - started_at >= STABLE_RETRY_RESET_AFTER:
                retry_delay = INITIAL_RETRY_DELAY

            if not disconnected_logged:
                logs.warning(lang("telegram_disconnected"))
            retry_delay = await sleep_before_retry(retry_delay)
    except asyncio.CancelledError:
        if task and not task.done():
            task.cancel()


async def console_bot():
    try:
        logs.info(lang("telegram_connecting"))
        await start_client(bot)
        me = await bot.get_me()
    except AuthKeyError:
        logs.error(lang("telegram_auth_key_invalid"))
        SessionFileManager.safe_remove_session()
        exit()
    except (OSError, ConnectionError, TimeoutError, asyncio.TimeoutError) as e:
        logs.warning(f"{lang('telegram_connection_failed')}: {type(e).__name__}: {e}")
        raise
    bot.me = me
    if me.bot:
        SessionFileManager.safe_remove_session()
        exit()
    logs.info(f"{lang('save_id')} {me.first_name}({me.id})")
    await load_all()


async def main():
    logs.info(lang("platform") + platform + lang("platform_load"))
    if not scheduler.running:
        scheduler.start()
    try:
        retry_delay = INITIAL_RETRY_DELAY
        while True:
            try:
                await console_bot()
                break
            except (OSError, ConnectionError, TimeoutError, asyncio.TimeoutError):
                retry_delay = await sleep_before_retry(retry_delay)
        logs.info(lang("start"))
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
