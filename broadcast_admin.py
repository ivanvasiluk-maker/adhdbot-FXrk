"""Two-step, admin-only Telegram broadcasts with a durable delivery journal."""

import asyncio
import datetime as dt
import hmac
import logging
from pathlib import Path
import sqlite3
import uuid
from typing import Any, Dict, List, Optional

import aiosqlite
from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import bot as app


log = logging.getLogger("bot.broadcast")
router = Router(name="admin_broadcast")

BROADCAST_MAX_LENGTH = 3500
BROADCAST_SEND_INTERVAL_SECONDS = 0.05
BROADCAST_PENDING: Dict[int, Dict[str, Any]] = {}
BROADCAST_ACTIVE_ADMINS: set[int] = set()
MANUAL_BROADCAST_MAX_RECIPIENTS = 500


def database_candidates(configured_path: str) -> List[Path]:
    """Find likely SQLite files without reading any user content."""
    configured = Path(configured_path).expanduser()
    roots = [configured.parent, Path.cwd(), Path.cwd() / "data", Path("/app"), Path("/app/data"), Path("/data")]
    candidates = [configured]
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for pattern in ("*.db", "*.sqlite", "*.sqlite3"):
            candidates.extend(root.glob(pattern))
    unique: List[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate.absolute()
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique[:30]


def inspect_database(path: Path) -> Dict[str, Any]:
    """Return file metadata and a user count; never select user rows."""
    result: Dict[str, Any] = {"path": str(path), "exists": path.is_file(), "users": None, "size": 0, "error": ""}
    if not result["exists"]:
        return result
    try:
        result["size"] = path.stat().st_size
        uri = f"file:{path}?mode=ro"
        with sqlite3.connect(uri, uri=True) as db:
            has_users = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
            ).fetchone()
            result["users"] = int(db.execute("SELECT COUNT(*) FROM users").fetchone()[0]) if has_users else None
    except Exception as exc:
        result["error"] = type(exc).__name__
    return result


def database_report(configured_path: str) -> str:
    configured = str(Path(configured_path).expanduser().absolute())
    rows = [inspect_database(path) for path in database_candidates(configured_path)]
    lines = [f"Активный DB_PATH: {configured}", "", "Найденные SQLite-файлы:"]
    found = False
    for row in rows:
        if not row["exists"]:
            if row["path"] == configured:
                lines.append(f"— {row['path']} — файла нет")
            continue
        found = True
        users = "нет таблицы users" if row["users"] is None else f"users: {row['users']}"
        error = f", ошибка: {row['error']}" if row["error"] else ""
        lines.append(f"— {row['path']} — {users}, {row['size']} байт{error}")
    if not found:
        lines.append("— других баз не найдено")
    lines.append("\nКоманда ничего не изменяет и не показывает данные пользователей.")
    return "\n".join(lines)


async def ensure_broadcast_tables(db_path: str) -> None:
    """Create an append-only journal without changing persistent user state."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS broadcast_runs (
                run_id TEXT PRIMARY KEY,
                admin_id INTEGER NOT NULL,
                message_text TEXT NOT NULL,
                status TEXT NOT NULL,
                total INTEGER NOT NULL DEFAULT 0,
                sent INTEGER NOT NULL DEFAULT 0,
                blocked INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                opted_out INTEGER NOT NULL DEFAULT 0,
                no_chat INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS broadcast_deliveries (
                run_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                chat_id INTEGER,
                status TEXT NOT NULL,
                error TEXT,
                delivered_at TEXT NOT NULL,
                PRIMARY KEY (run_id, user_id)
            )
            """
        )
        await db.commit()


async def broadcast_audience(db_path: str) -> tuple[List[Dict[str, int]], Dict[str, int]]:
    """Return unique opted-in private chats and transparent exclusion totals."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = [dict(row) for row in await (await db.execute(
            """
            SELECT user_id, telegram_id, chat_id, notifications_enabled, notification_consent
            FROM users
            ORDER BY user_id
            """
        )).fetchall()]

    recipients: List[Dict[str, int]] = []
    seen_chat_ids: set[int] = set()
    counts = {
        "total_users": len(rows), "eligible": 0, "opted_out": 0,
        "no_chat": 0, "duplicate_chat": 0,
    }
    for row in rows:
        if row.get("notifications_enabled") == 0 or row.get("notification_consent") == 0:
            counts["opted_out"] += 1
            continue
        raw_chat_id = row.get("chat_id") or row.get("telegram_id") or row.get("user_id")
        try:
            chat_id = int(raw_chat_id or 0)
            user_id = int(row.get("user_id") or 0)
        except (TypeError, ValueError):
            chat_id = 0
            user_id = 0
        if chat_id <= 0 or user_id <= 0:
            counts["no_chat"] += 1
            continue
        if chat_id in seen_chat_ids:
            counts["duplicate_chat"] += 1
            continue
        seen_chat_ids.add(chat_id)
        recipients.append({"user_id": user_id, "chat_id": chat_id})
    counts["eligible"] = len(recipients)
    return recipients, counts


def preview_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Отправить всем", callback_data=f"broadcast:confirm:{token}"),
        InlineKeyboardButton(text="Отмена", callback_data=f"broadcast:cancel:{token}"),
    ]])


def restart_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Попробовать снова", callback_data="broadcast:restart"),
    ]])


async def record_delivery(
    db: aiosqlite.Connection,
    run_id: str,
    recipient: Dict[str, int],
    status: str,
    error: str = "",
) -> None:
    await db.execute(
        """
        INSERT OR REPLACE INTO broadcast_deliveries
            (run_id, user_id, chat_id, status, error, delivered_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            run_id, recipient["user_id"], recipient["chat_id"], status,
            str(error or "")[:300], dt.datetime.now(dt.timezone.utc).isoformat(),
        ),
    )
    await db.commit()


async def run_broadcast(
    bot: Bot,
    admin_id: int,
    run_id: str,
    message_text: str,
    *,
    recipients_override: Optional[List[Dict[str, int]]] = None,
) -> None:
    """Deliver one confirmed broadcast in the background and report exact totals."""
    BROADCAST_ACTIVE_ADMINS.add(admin_id)
    stats = {"sent": 0, "blocked": 0, "failed": 0}
    try:
        await ensure_broadcast_tables(app.DB_PATH)
        if recipients_override is None:
            recipients, audience_counts = await broadcast_audience(app.DB_PATH)
        else:
            recipients = list(recipients_override)
            audience_counts = {
                "total_users": len(recipients), "eligible": len(recipients),
                "opted_out": 0, "no_chat": 0, "duplicate_chat": 0,
            }
        async with aiosqlite.connect(app.DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO broadcast_runs
                    (run_id, admin_id, message_text, status, total, opted_out, no_chat, created_at)
                VALUES (?, ?, ?, 'sending', ?, ?, ?, ?)
                """,
                (
                    run_id, admin_id, message_text, len(recipients),
                    audience_counts["opted_out"], audience_counts["no_chat"],
                    dt.datetime.now(dt.timezone.utc).isoformat(),
                ),
            )
            await db.commit()

            for recipient in recipients:
                status = "failed"
                error = ""
                try:
                    for attempt in range(2):
                        try:
                            await bot.send_message(
                                recipient["chat_id"], message_text,
                                reply_markup=restart_keyboard(), parse_mode=None,
                            )
                            status = "sent"
                            break
                        except TelegramRetryAfter as exc:
                            if attempt == 1:
                                raise
                            await asyncio.sleep(float(exc.retry_after) + 0.25)
                except TelegramForbiddenError as exc:
                    status = "blocked"
                    error = str(exc)
                except TelegramBadRequest as exc:
                    error = str(exc)
                    lowered = error.lower()
                    status = "blocked" if "chat not found" in lowered or "deactivated" in lowered else "failed"
                except Exception as exc:
                    status = "failed"
                    error = f"{type(exc).__name__}: {exc}"

                stats[status] += 1
                await record_delivery(db, run_id, recipient, status, error)
                await asyncio.sleep(BROADCAST_SEND_INTERVAL_SECONDS)

            await db.execute(
                """
                UPDATE broadcast_runs
                SET status='completed', sent=?, blocked=?, failed=?, completed_at=?
                WHERE run_id=?
                """,
                (
                    stats["sent"], stats["blocked"], stats["failed"],
                    dt.datetime.now(dt.timezone.utc).isoformat(), run_id,
                ),
            )
            await db.commit()

        await bot.send_message(
            admin_id,
            "Рассылка завершена.\n"
            f"Отправлено: {stats['sent']}\n"
            f"Бот заблокирован/чат недоступен: {stats['blocked']}\n"
            f"Другие ошибки: {stats['failed']}\n"
            f"Не подписаны на уведомления: {audience_counts['opted_out']}\n"
            f"Без доступного chat_id: {audience_counts['no_chat']}",
            parse_mode=None,
        )
    except Exception as exc:
        log.exception("Broadcast %s failed", run_id)
        try:
            async with aiosqlite.connect(app.DB_PATH) as db:
                await db.execute(
                    "UPDATE broadcast_runs SET status='failed', completed_at=? WHERE run_id=?",
                    (dt.datetime.now(dt.timezone.utc).isoformat(), run_id),
                )
                await db.commit()
            await bot.send_message(
                admin_id,
                f"Рассылка остановлена из-за ошибки: {type(exc).__name__}. Повторно не запускай её, пока не проверен журнал.",
                parse_mode=None,
            )
        except Exception:
            log.exception("Could not report broadcast failure to admin %s", admin_id)
    finally:
        BROADCAST_ACTIVE_ADMINS.discard(admin_id)


def is_broadcast_command(message: Message) -> bool:
    return app.normalize_slash_command((message.text or "").strip()) == "/broadcast"


def parse_manual_broadcast(text: str) -> tuple[List[Dict[str, int]], str]:
    """Parse an admin-supplied recovery audience without persisting it in users."""
    parts = text.strip().split(maxsplit=1)
    payload = parts[1].strip() if len(parts) == 2 else ""
    if "|" not in payload:
        raise ValueError("Формат: /broadcast_ids 123456789,987654321 | текст сообщения")
    raw_ids, message_text = (part.strip() for part in payload.split("|", 1))
    if not raw_ids or not message_text:
        raise ValueError("Нужны и список Telegram ID, и текст сообщения после символа |.")

    recipients: List[Dict[str, int]] = []
    seen: set[int] = set()
    for raw_id in raw_ids.replace(";", ",").split(","):
        raw_id = raw_id.strip()
        if not raw_id or not raw_id.isdecimal():
            raise ValueError("Telegram ID должны быть положительными числами через запятую.")
        chat_id = int(raw_id)
        if chat_id <= 0:
            raise ValueError("Telegram ID должны быть положительными числами.")
        if chat_id not in seen:
            seen.add(chat_id)
            recipients.append({"user_id": chat_id, "chat_id": chat_id})
    if len(recipients) > MANUAL_BROADCAST_MAX_RECIPIENTS:
        raise ValueError(f"Слишком много получателей: максимум {MANUAL_BROADCAST_MAX_RECIPIENTS}.")
    if len(message_text) > BROADCAST_MAX_LENGTH:
        raise ValueError(
            f"Сообщение слишком длинное: {len(message_text)} символов. Максимум: {BROADCAST_MAX_LENGTH}."
        )
    return recipients, message_text


@router.message(lambda message: app.normalize_slash_command((message.text or "").strip()) == "/db_files")
async def on_database_report_command(message: Message) -> None:
    if not app.is_admin(message.from_user.id):
        await message.answer("Админская команда недоступна для этого пользователя.")
        return
    await message.answer(database_report(app.DB_PATH), parse_mode=None)


@router.message(lambda message: app.normalize_slash_command((message.text or "").strip()) == "/broadcast_ids")
async def on_manual_broadcast_command(message: Message) -> None:
    uid = message.from_user.id
    if not app.is_admin(uid):
        await message.answer("Админская команда недоступна для этого пользователя.")
        return
    if uid in BROADCAST_ACTIVE_ADMINS:
        await message.answer("Предыдущая рассылка ещё выполняется. Дождись итогового отчёта.")
        return
    try:
        recipients, message_text = parse_manual_broadcast(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc), parse_mode=None)
        return

    token = uuid.uuid4().hex[:16]
    BROADCAST_PENDING[uid] = {
        "token": token,
        "text": message_text,
        "recipients": recipients,
    }
    await message.answer(
        "Предпросмотр восстановительной рассылки:\n\n"
        f"{message_text}\n\n"
        "———\n"
        f"Получателей из восстановленного списка: {len(recipients)}\n\n"
        "После подтверждения отменить уже отправленные сообщения нельзя.",
        reply_markup=preview_keyboard(token), parse_mode=None,
    )


@router.message(is_broadcast_command)
async def on_broadcast_command(message: Message) -> None:
    uid = message.from_user.id
    if not app.is_admin(uid):
        await message.answer("Админская команда недоступна для этого пользователя.")
        return
    if uid in BROADCAST_ACTIVE_ADMINS:
        await message.answer("Предыдущая рассылка ещё выполняется. Дождись итогового отчёта.")
        return

    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)
    message_text = parts[1].strip() if len(parts) == 2 else ""
    if not message_text:
        await message.answer(
            "Формат: /broadcast <текст сообщения>\n\n"
            f"Максимальная длина: {BROADCAST_MAX_LENGTH} символов. Отправка начнётся только после предпросмотра и подтверждения."
        )
        return
    if len(message_text) > BROADCAST_MAX_LENGTH:
        await message.answer(
            f"Сообщение слишком длинное: {len(message_text)} символов. Максимум: {BROADCAST_MAX_LENGTH}."
        )
        return

    recipients, counts = await broadcast_audience(app.DB_PATH)
    token = uuid.uuid4().hex[:16]
    BROADCAST_PENDING[uid] = {"token": token, "text": message_text}
    await message.answer(
        "Предпросмотр рассылки:\n\n"
        f"{message_text}\n\n"
        "———\n"
        f"Получателей: {len(recipients)}\n"
        f"Исключены из-за отключённых уведомлений: {counts['opted_out']}\n"
        f"Нет доступного chat_id: {counts['no_chat']}\n\n"
        "После подтверждения отменить уже отправленные сообщения нельзя.",
        reply_markup=preview_keyboard(token), parse_mode=None,
    )


@router.callback_query(lambda callback: (callback.data or "").startswith("broadcast:"))
async def on_broadcast_callback(callback: CallbackQuery) -> None:
    data = callback.data or ""
    uid = callback.from_user.id

    if data == "broadcast:restart":
        user = await app.get_user(uid, app.DB_PATH)
        app.prepare_calendar_routing(user)
        app.mark_user_activity(user, active=not app.day_closed_today(user))
        await app.save_user(user, app.DB_PATH)
        await callback.answer("Открываю SKILLER")
        await app.show_existing_user_start_menu(callback.message, user)
        return

    if not app.is_admin(uid):
        await callback.answer("Недоступно", show_alert=True)
        return
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[1] not in {"confirm", "cancel"}:
        await callback.answer("Неизвестное действие", show_alert=True)
        return
    action, token = parts[1], parts[2]
    pending = BROADCAST_PENDING.get(uid)
    if not pending or not hmac.compare_digest(str(pending.get("token") or ""), token):
        await callback.answer("Этот предпросмотр уже неактуален", show_alert=True)
        return

    if action == "cancel":
        BROADCAST_PENDING.pop(uid, None)
        await callback.answer("Рассылка отменена")
        await callback.message.edit_text("Рассылка отменена.", parse_mode=None)
        return
    if uid in BROADCAST_ACTIVE_ADMINS:
        await callback.answer("Другая рассылка уже выполняется", show_alert=True)
        return

    message_text = str(pending.get("text") or "")
    recipients_override = pending.get("recipients")
    BROADCAST_PENDING.pop(uid, None)
    run_id = f"broadcast_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S')}_{token[:8]}"
    if recipients_override is None:
        recipients, counts = await broadcast_audience(app.DB_PATH)
    else:
        recipients = list(recipients_override)
        counts = {"opted_out": 0, "no_chat": 0}
    BROADCAST_ACTIVE_ADMINS.add(uid)
    await callback.answer("Рассылка запущена")
    await callback.message.edit_text(
        f"Рассылка запущена для {len(recipients)} получателей. Итоговый отчёт придёт отдельным сообщением."
        f"\nИсключены из-за отключённых уведомлений: {counts['opted_out']}.",
        parse_mode=None,
    )
    asyncio.create_task(
        run_broadcast(
            callback.bot, uid, run_id, message_text,
            recipients_override=recipients if recipients_override is not None else None,
        )
    )
