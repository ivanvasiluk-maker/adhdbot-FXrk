"""One presentation boundary for direct replies, callbacks and scheduled sends."""
from __future__ import annotations
import aiosqlite
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup
from core.addressing import render_address
from core.dialogue_ux import BUTTON_LABELS, plain_text


class OutgoingDialogueMiddleware(BaseRequestMiddleware):
    def __init__(self, db_path):
        self.db_path = db_path

    async def __call__(self, make_request, bot, method):
        chat_id = getattr(method, 'chat_id', None)
        # Do not reinterpret team/group messages or callback acknowledgements.
        if not isinstance(chat_id, int) or chat_id <= 0:
            return await make_request(bot, method)
        text_key = 'text' if isinstance(getattr(method, 'text', None), str) else 'caption'
        value = getattr(method, text_key, None)
        if not isinstance(value, str):
            return await make_request(bot, method)
        path = self.db_path() if callable(self.db_path) else self.db_path
        async with aiosqlite.connect(path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                'SELECT address_mode, address_form FROM users WHERE user_id=?', (chat_id,)
            )).fetchone()
        if row is None:
            return await make_request(bot, method)
        # Explicit Telegram entity offsets must not be invalidated by rewriting.
        entities = getattr(method, 'entities' if text_key == 'text' else 'caption_entities', None)
        changes = {} if entities else {text_key: render_address(plain_text(value), dict(row))}
        markup = getattr(method, 'reply_markup', None)
        if isinstance(markup, (InlineKeyboardMarkup, ReplyKeyboardMarkup)):
            field = 'inline_keyboard' if isinstance(markup, InlineKeyboardMarkup) else 'keyboard'
            rows = [[b.model_copy(update={'text': BUTTON_LABELS.get(b.text, b.text)}) for b in row] for row in getattr(markup, field)]
            changes['reply_markup'] = markup.model_copy(update={field: rows})
        return await make_request(bot, method.model_copy(update=changes))
