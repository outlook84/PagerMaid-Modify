from typing import Optional

import telethon
from telethon.errors import MessageAuthorRequiredError
from telethon.tl.patched import Message

from pagermaid.dependence import add_delete_message_job, get_sudo_list
from ..methods.get_dialogs_list import get_dialogs_list as get_dialogs_list_func

from ..utils import patch, patchable
from ..utils.handler_priority import HandlerList


@patch(telethon.TelegramClient)
class TelegramClient(telethon.TelegramClient):
    @patchable
    def __init__(self, *args, **kwargs):
        self.old__init__(*args, **kwargs)
        self._event_builders = HandlerList()

    @patchable
    async def get_dialogs_list(self: "telethon.TelegramClient"):
        return await get_dialogs_list_func(self)


# pagermaid-modify


@patch(telethon.tl.patched.Message)
class Message(telethon.tl.patched.Message):
    @patchable
    async def safe_delete(self, *args, **kwargs):
        try:
            return await self.delete(*args, **kwargs)
        except Exception:  # noqa
            return False

    @patchable
    async def obtain_message(self) -> Optional[str]:
        """Obtains a message from either the reply message or command arguments."""
        if self.arguments:
            return self.arguments
        if reply := await self.get_reply_message():
            return reply.text
        return None

    @patchable
    async def delay_delete(self, delay: int = 60):
        add_delete_message_job(self, delay)

    @patchable
    async def edit(self, *args, **kwargs):
        msg = None
        text = args[0] if len(args) > 0 else kwargs.get("message", "")
        no_reply = kwargs.pop("no_reply") if "no_reply" in kwargs else False
        sudo_users = get_sudo_list()
        reply_to = self.reply_to_msg_id
        from_id = self.sender_id
        is_self = self.out

        if "link_preview" not in kwargs:
            kwargs["link_preview"] = bool(self.web_preview)

        if "buttons" not in kwargs:
            kwargs["buttons"] = self.reply_markup

        if len(text) < 4096:
            if from_id in sudo_users or self.chat_id in sudo_users:
                if reply_to and (not is_self) and (not no_reply):
                    kwargs["reply_to"] = reply_to
                    msg = await self._client.send_message(
                        await self.get_input_chat(), *args, **kwargs
                    )
                elif is_self:
                    msg = await self._client.edit_message(
                        await self.get_input_chat(), self.id, *args, **kwargs
                    )
                elif not no_reply:
                    kwargs["reply_to"] = self.id
                    msg = await self._client.send_message(
                        await self.get_input_chat(), *args, **kwargs
                    )
            else:
                try:
                    msg = await self._client.edit_message(
                        await self.get_input_chat(), self.id, *args, **kwargs
                    )
                except MessageAuthorRequiredError:
                    if not no_reply:
                        kwargs["reply_to"] = self.id
                        msg = await self._client.send_message(
                            await self.get_input_chat(), *args, **kwargs
                        )
        else:
            with open("output.log", "w+") as file:
                file.write(text)
            msg = await self._client.send_file(
                await self.get_input_chat(), "output.log", reply_to=self.id
            )
        if not msg:
            return self
        msg.parameter = self.parameter if hasattr(self, "parameter") else []
        msg.arguments = self.arguments if hasattr(self, "arguments") else ""
        return msg
