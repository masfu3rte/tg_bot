"""Persistent Telegram notification queue with backoff and explicit completion effects."""

import asyncio
import json
import logging
import time
import hashlib
from contextvars import ContextVar
from html import escape
from html.parser import HTMLParser

from aiogram.types import InlineKeyboardMarkup, InputMediaPhoto
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

log = logging.getLogger(__name__)
delivery_deferred = ContextVar("delivery_deferred", default=False)
notification_scope = ContextVar("notification_scope", default="")


class FeedbackProxy:
    def __init__(self, event):
        self.event = event

    def __getattr__(self, key):
        value = getattr(self.event, key)
        return FeedbackProxy(value) if key == "message" and value is not None else value

    async def answer(self, *args, **kwargs):
        if delivery_deferred.get():
            text = "Действие сохранено. Часть сообщений ожидает доставки. При ошибке потребуется помощь сотрудника сервиса."
            args = (text,) + args[1:] if args else (text,)
            kwargs.pop("text", None)
        return await self.event.answer(*args, **kwargs)


async def notify(db, bot, method, **kwargs):
    effect = kwargs.pop("_effect", None)
    key = kwargs.pop("_key", None)
    if method in ("send_message", "send_photo"):
        text = (
            kwargs.pop("text", None)
            if method == "send_message"
            else kwargs.pop("caption", "")
        )
        jobs = content(text=text or "", effect=effect, **kwargs)
    else:
        jobs = [dict(method=method, kwargs=kwargs, effect=effect)]
    if key is None:
        key = (
            "notification-"
            + hashlib.sha256(
                (
                    notification_scope.get()
                    + json.dumps(jobs, default=serialise, sort_keys=True)
                ).encode()
            ).hexdigest()
        )
    delivered = await queue_batch(db, bot, key, jobs)
    if not delivered:
        delivery_deferred.set(True)
    return delivered


class HTMLChunks(HTMLParser):
    def __init__(self, limit=3500):
        super().__init__(convert_charrefs=True)
        self.limit, self.parts, self.buffer, self.tags = limit, [], "", []

    def flush(self):
        self.parts.append(
            self.buffer + "".join(f"</{tag}>" for tag, _ in reversed(self.tags))
        )
        self.buffer = "".join(raw for _, raw in self.tags)

    def handle_starttag(self, tag, attrs):
        raw = (
            "<"
            + tag
            + "".join(f' {k}="{escape(v or "", quote=True)}"' for k, v in attrs)
            + ">"
        )
        self.tags.append((tag, raw))
        self.buffer += raw

    def handle_endtag(self, tag):
        self.buffer += f"</{tag}>"
        if self.tags and self.tags[-1][0] == tag:
            self.tags.pop()

    def handle_data(self, text):
        for char in text:
            value = escape(char, quote=False)
            if len(self.buffer) + len(value) + 200 > self.limit:
                self.flush()
            self.buffer += value


def chunks(text):
    parser = HTMLChunks()
    parser.feed(text)
    parser.close()
    if parser.buffer:
        parser.parts.append(parser.buffer)
    return parser.parts or ["—"]


def content(
    chat_id,
    text,
    *,
    photo=None,
    reply_markup=None,
    message_thread_id=None,
    effect=None,
    **extra,
):
    common = dict(chat_id=chat_id, **extra)
    if message_thread_id:
        common["message_thread_id"] = message_thread_id
    jobs = []
    if photo and len(text) <= 950:
        jobs.append(
            dict(
                method="send_photo",
                kwargs=dict(
                    common, photo=photo, caption=text, reply_markup=reply_markup
                ),
                effect=effect,
            )
        )
    else:
        if photo:
            media_effect = (
                {"kind": "request_media", "id": effect["id"]}
                if effect and effect.get("kind") == "publish_request"
                else None
            )
            jobs.append(
                dict(
                    method="send_photo",
                    kwargs=dict(common, photo=photo),
                    effect=media_effect,
                )
            )
        blocks = chunks(text)
        for i, block in enumerate(blocks):
            jobs.append(
                dict(
                    method="send_message",
                    kwargs=dict(
                        common,
                        text=block,
                        reply_markup=reply_markup if i == len(blocks) - 1 else None,
                    ),
                    effect=effect if i == len(blocks) - 1 else None,
                )
            )
    return jobs


def serialise(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    raise TypeError(type(value).__name__)


def enqueue(db, key, jobs):
    for index, job in enumerate(jobs):
        db.conn.execute(
            "INSERT OR IGNORE INTO outbox(job_key,payload,effect) VALUES (?,?,?)",
            (
                f"{key}:{index}",
                json.dumps(
                    {"method": job["method"], "kwargs": job["kwargs"]},
                    default=serialise,
                ),
                json.dumps(job.get("effect")),
            ),
        )


async def queue_batch(db, bot, key, jobs):
    with db.conn:
        enqueue(db, key, jobs)
    await drain(db, bot)
    statuses = [
        r[0]
        for r in db.conn.execute(
            "SELECT status FROM outbox WHERE job_key LIKE ?", (key + ":%",)
        )
    ]
    return bool(statuses) and all(s == "sent" for s in statuses)


async def drain(db, bot):
    if not hasattr(db, "delivery_lock"):
        db.delivery_lock = asyncio.Lock()
    async with db.delivery_lock:
        # Preserve each batch's order; a failed photo blocks its associated text.
        rows = db.conn.execute(
            "SELECT * FROM outbox WHERE status='pending' ORDER BY id"
        ).fetchall()
        blocked = {
            r[0].rsplit(":", 1)[0]
            for r in db.conn.execute(
                "SELECT job_key FROM outbox WHERE status='blocked'"
            )
        }
        for row in rows:
            batch = row["job_key"].rsplit(":", 1)[0]
            if batch in blocked or row["next_attempt"] > time.time():
                blocked.add(batch)
                continue
            payload = json.loads(row["payload"])
            kw = payload["kwargs"]
            if kw.get("reply_markup"):
                kw["reply_markup"] = InlineKeyboardMarkup.model_validate(
                    kw["reply_markup"]
                )
            if "media" in kw:
                kw["media"] = [InputMediaPhoto.model_validate(m) for m in kw["media"]]
            try:
                result = await getattr(bot, payload["method"])(**kw)
            except Exception as exc:
                attempts = row["attempts"] + 1
                delay = (
                    exc.retry_after
                    if isinstance(exc, TelegramRetryAfter)
                    else min(3600, 5 * 2 ** min(attempts, 9))
                )
                status = (
                    "blocked"
                    if isinstance(exc, TelegramForbiddenError) or attempts >= 10
                    else "pending"
                )
                with db.conn:
                    db.conn.execute(
                        "UPDATE outbox SET status=?,attempts=?,next_attempt=?,last_error=? WHERE id=?",
                        (
                            status,
                            attempts,
                            time.time() + delay,
                            type(exc).__name__,
                            row["id"],
                        ),
                    )
                log.warning(
                    "Delivery job %s: %s (attempt %s)",
                    row["id"],
                    type(exc).__name__,
                    attempts,
                )
                blocked.add(batch)
                continue
            effect = json.loads(row["effect"]) if row["effect"] else None
            mid = getattr(result, "message_id", None)
            mid = mid if isinstance(mid, int) else None
            with db.conn:
                db.conn.execute(
                    "UPDATE outbox SET status='sent',result_message_id=?,last_error=NULL WHERE id=?",
                    (mid, row["id"]),
                )
                if effect and effect["kind"] == "publish_request":
                    db.conn.execute(
                        "UPDATE requests SET status='approved',channel_message_id=?,channel_is_photo=? WHERE id=?",
                        (
                            mid,
                            int(
                                payload["method"]
                                in ("send_photo", "edit_message_caption")
                            ),
                            effect["id"],
                        ),
                    )
                    owner = db.conn.execute(
                        "SELECT user_id FROM requests WHERE id=?", (effect["id"],)
                    ).fetchone()
                    enqueue(
                        db,
                        f'request-approved-notify-{effect["id"]}',
                        content(owner[0], f'Ваш запрос №{effect["id"]} опубликован.'),
                    )
                elif effect and effect["kind"] == "request_media":
                    db.conn.execute(
                        "UPDATE requests SET channel_media_message_id=? WHERE id=?",
                        (mid, effect["id"]),
                    )
                elif effect and effect["kind"] == "arrival":
                    db.conn.execute(
                        "UPDATE offers SET arrival_notified=1 WHERE id=?",
                        (effect["id"],),
                    )


async def scheduler(db, bot):
    while True:
        try:
            await drain(db, bot)
        except Exception as exc:
            log.error("Delivery worker failed: %s", type(exc).__name__)
        await asyncio.sleep(10)


async def answer_content(msg, text, *, photo=None, reply_markup=None):
    # UI responses do not claim that a business operation was delivered.
    if photo and len(text) <= 950:
        return await msg.answer_photo(
            photo=photo, caption=text, reply_markup=reply_markup
        )
    if photo:
        await msg.answer_photo(photo=photo)
    result = None
    for index, block in enumerate(chunks(text)):
        result = await msg.answer(
            block, reply_markup=reply_markup if index == len(chunks(text)) - 1 else None
        )
    return result
