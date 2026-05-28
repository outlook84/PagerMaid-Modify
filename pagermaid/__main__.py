import asyncio
import contextlib
from os import sep
from pathlib import Path
from signal import signal as signal_fn, SIGINT, SIGTERM, SIGABRT
from sys import path, platform, exit

from telethon.errors.rpcerrorlist import AuthKeyError

from pagermaid.common.reload import load_all
from pagermaid.config import Config
from pagermaid.dependence import client as httpx_client, scheduler, sqlite
from pagermaid.hook import HookRunner
from pagermaid.services import bot
from pagermaid.static import working_dir
from pagermaid.utils import lang, logs, SessionFileManager
from pyromod.methods.sign_in_qrcode import start_client

bot.PARENT_DIR = Path(working_dir)
path.insert(1, f"{working_dir}{sep}plugins")

INITIAL_RETRY_DELAY = 5
MAX_RETRY_DELAY = 120
STABLE_RETRY_RESET_AFTER = 300
RETRYABLE_CONNECTION_ERRORS = (
    OSError,
    ConnectionError,
    TimeoutError,
    asyncio.TimeoutError,
)
shutdown_hooks_ran = False


def install_signal_handlers(shutdown_event, active_task_getter=None):
    current_task = asyncio.current_task()

    def signal_handler(_, __):
        shutdown_event.set()
        task = active_task_getter() if active_task_getter else None
        if task and not task.done():
            task.cancel()
        elif current_task and not current_task.done():
            current_task.cancel()

    for s in (SIGINT, SIGTERM, SIGABRT):
        signal_fn(s, signal_handler)


async def sleep_before_retry(delay, shutdown_event):
    logs.warning(f"{lang('telegram_retrying')} {delay}s")
    sleep_task = asyncio.create_task(asyncio.sleep(delay))
    shutdown_task = asyncio.create_task(shutdown_event.wait())
    try:
        done, _ = await asyncio.wait(
            {sleep_task, shutdown_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if shutdown_task in done:
            raise asyncio.CancelledError
        await sleep_task
    finally:
        for task in (sleep_task, shutdown_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(sleep_task, shutdown_task, return_exceptions=True)
    return min(delay * 2, MAX_RETRY_DELAY)


async def idle(shutdown_event):
    task = None
    retry_delay = INITIAL_RETRY_DELAY

    install_signal_handlers(shutdown_event, lambda: task)

    try:
        while True:
            if shutdown_event.is_set():
                break
            if not bot.is_connected():
                try:
                    logs.info(lang("telegram_connecting"))
                    await bot.connect()
                except RETRYABLE_CONNECTION_ERRORS as e:
                    logs.warning(f"{lang('telegram_connection_failed')}: {type(e).__name__}: {e}")
                    retry_delay = await sleep_before_retry(retry_delay, shutdown_event)
                    continue

            started_at = asyncio.get_running_loop().time()
            t = bot._run_until_disconnected()
            task = asyncio.create_task(t)
            disconnected_logged = False
            try:
                await task
            except asyncio.CancelledError:
                break
            except RETRYABLE_CONNECTION_ERRORS as e:
                logs.warning(f"{lang('telegram_disconnected')}: {type(e).__name__}: {e}")
                disconnected_logged = True

            if getattr(bot, "_should_restart", False):
                break

            if asyncio.get_running_loop().time() - started_at >= STABLE_RETRY_RESET_AFTER:
                retry_delay = INITIAL_RETRY_DELAY

            if not disconnected_logged:
                logs.warning(lang("telegram_disconnected"))
            retry_delay = await sleep_before_retry(retry_delay, shutdown_event)
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
    except RETRYABLE_CONNECTION_ERRORS as e:
        logs.warning(f"{lang('telegram_connection_failed')}: {type(e).__name__}: {e}")
        raise
    bot.me = me
    if me.bot:
        SessionFileManager.safe_remove_session()
        exit()
    logs.info(f"{lang('save_id')} {me.first_name}({me.id})")
    await load_all()


async def shutdown_services():
    global shutdown_hooks_ran

    if not shutdown_hooks_ran:
        shutdown_hooks_ran = True
        with contextlib.suppress(Exception):
            await HookRunner.shutdown(None)

    if scheduler.running:
        with contextlib.suppress(Exception):
            scheduler.shutdown()

    if bot.is_connected():
        with contextlib.suppress(Exception):
            await bot.disconnect()

    if not httpx_client.is_closed:
        with contextlib.suppress(Exception):
            await httpx_client.aclose()

    with contextlib.suppress(Exception):
        sqlite.close()


async def main():
    logs.info(lang("platform") + platform + lang("platform_load"))
    shutdown_event = asyncio.Event()
    install_signal_handlers(shutdown_event)
    if not scheduler.running:
        scheduler.start()
    try:
        retry_delay = INITIAL_RETRY_DELAY
        while True:
            try:
                await console_bot()
                break
            except RETRYABLE_CONNECTION_ERRORS:
                retry_delay = await sleep_before_retry(retry_delay, shutdown_event)
        logs.info(lang("start"))
        await idle(shutdown_event)
    finally:
        await shutdown_services()

        if getattr(bot, "_should_restart", False):
            exit(0)


try:
    bot.loop.run_until_complete(main())
finally:
    pending = [task for task in asyncio.all_tasks(bot.loop) if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        bot.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    bot.loop.run_until_complete(bot.loop.shutdown_asyncgens())
    bot.loop.close()
