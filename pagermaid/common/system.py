import contextlib
import io
import sys
import traceback
from typing import Optional, TYPE_CHECKING

from pagermaid.services import client as httpx_client, bot, sqlite
from pagermaid.utils import lang, logs
from pagermaid.utils.listener import from_self

if TYPE_CHECKING:
    from pagermaid.enums import Message


async def run_eval(cmd: str, message=None) -> str:
    old_stderr = sys.stderr
    old_stdout = sys.stdout
    redirected_output = sys.stdout = io.StringIO()
    redirected_error = sys.stderr = io.StringIO()
    stdout, stderr, exc = None, None, None
    try:
        await aexec(cmd, message, bot)
    except Exception:  # noqa
        exc = traceback.format_exc()
    stdout = redirected_output.getvalue()
    stderr = redirected_error.getvalue()
    sys.stdout = old_stdout
    sys.stderr = old_stderr
    if exc:
        evaluation = exc
    elif stderr:
        evaluation = stderr
    elif stdout:
        evaluation = stdout
    else:
        evaluation = "Success"
    return evaluation


async def aexec(code, event, client):
    text = (
        (
            ("async def __aexec(e, client): " + "\n msg = message = context = e")
            + "\n reply = await context.get_reply_message() if context else None"
        )
        + "\n chat = e.chat_id if e else None"
    ) + "".join(f"\n {x}" for x in code.split("\n"))
    if sys.version_info >= (3, 13):
        local = {}
        exec(text, globals(), local)
    else:
        exec(text)
        local = locals()

    return await local["__aexec"](event, client)


async def paste_pb(
    content: str, private: bool = True, sunset: int = 3600
) -> Optional[str]:
    data = {
        "c": content,
    }
    if private:
        data["p"] = "1"
    if sunset:
        data["sunset"] = sunset
    result = await httpx_client.post("https://fars.ee", data=data)
    if result.is_error:
        return None
    return result.headers.get("location")


async def process_exit(start: int, _client, message=None):
    if message:
        sqlite["exit_msg"] = {"cid": message.chat_id, "mid": message.id}
        logs.info(f"[process_exit] exit_msg updated: {sqlite['exit_msg']}")
        return

    try:
        logs.info("[process_exit] reading exit_msg from sqlite...")
        raw = sqlite.get("exit_msg")
        logs.info(f"[process_exit] raw exit_msg type: {type(raw)}, repr: {repr(raw)[:200]}")  # 截断防止日志过长
        data = dict(raw) if raw else {}
        logs.info(f"[process_exit] exit_msg data: {data}")
    except Exception as e:
        logs.error(f"[process_exit] failed to read exit_msg: {e!r}")
        data = {}

    cid, mid = data.get("cid", 0), data.get("mid", 0)
    if start and data and cid and mid:
        try:
            logs.info(f"[process_exit] fetching message cid={cid}, mid={mid} ...")
            msg: "Message" = await _client.get_messages(cid, ids=mid)
            logs.info(f"[process_exit] fetched message: {msg}")
            if msg:
                await msg.edit(
                    (msg.text if from_self(msg) and msg.text else "")
                    + f"\n\n> {lang('restart_complete')}",
                    parse_mode='md'
                )
                logs.info("[process_exit] message edited")
        except Exception as e:
            logs.error(f"[process_exit] restore message failed: {e!r}")
        finally:
            if "exit_msg" in sqlite:
                del sqlite["exit_msg"]
                logs.info("[process_exit] exit_msg deleted")
