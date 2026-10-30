"""Opt-in practice invitations and one daily task; state survives restarts in profile_json."""
import datetime as dt
import re
import uuid
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from reactivation_service import parse_dt

KEY = 'reminder_flow'


def keyboard(token, choices):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f'remind:{token}:{value}')]
        for label, value in choices])


def invite_keyboard(token):
    return keyboard(token, [('Хочу тренироваться', 'train'), ('Напомнить через час', '60'),
                            ('Через 1,5 часа', '90'), ('Дело дня', 'task'), ('Отключить напоминания', 'off')])


def draft_active(b, u, state):
    return bool(state.get('draft') and state.get('date') == b.local_date_for_user(u) and state.get('draft_stage') == u.get('stage'))


def task_pending(state, now):
    task = state.get('task') or {}
    target = parse_dt(task.get('due_at'))
    return bool(task.get('status') == 'sent' and target and target <= now < target + dt.timedelta(hours=4))


def due_at(user, clock, local_now):
    match = re.fullmatch(r'(\d{1,2}):(\d{2})', clock.strip())
    if not match:
        return None
    hour, minute = map(int, match.groups())
    # No unsolicited night messages. Same-day task times are never silently moved to tomorrow.
    if not (8 <= hour < 21 and minute < 60):
        return None
    now = local_now(user)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return target if target > now else None


async def persist(b, u, profile, state):
    profile[KEY] = state
    u['profile_json'] = profile
    await b.save_user(u, b.DB_PATH)


async def menu(b, m, u, profile, state):
    state.update(token=uuid.uuid4().hex[:10], draft=None, draft_stage=None)
    await persist(b, u, profile, state)
    await m.answer('Напоминания — только по вашему выбору. Можно включить одно приглашение на тренировку в день '
                   'или выбрать дело и время одной проверки. Всего не больше двух сообщений в день; '
                   'после закрытия дня не пишу. Отключить — /stop.\n\n'
                   f'Время: {u.get("timezone") or "Europe/Vilnius"}.', parse_mode=None,
                   reply_markup=keyboard(state['token'], [('Приглашать в 19:00', 'daily'), ('Дело дня', 'task'),
                                                          ('Отключить напоминания', 'off'), ('Вернуться к тренировке', 'train')]))


async def handle_text(b, m, u, text):
    # The message router already loaded this snapshot; ordinary input must not
    # trigger another DB read before onboarding or navigation owns it.
    profile = b._json_dict(u.get('profile_json'))
    state = profile.get(KEY) or {}
    command = text.split('@')[0].lower() if text.startswith('/') else text.lower()
    if text == '⏰ Напоминания и дело дня':
        command = '/reminders'
    task_match = re.fullmatch(r'дело дня\s*:\s*(.{3,180})', text.strip(), re.I)
    if command not in {'/reminders', '/day_task', 'дело дня'} and not task_match and not state.get('draft'):
        return False
    if b.proactive_protected(u):
        if command in {'/reminders', '/day_task', 'дело дня'} or task_match:
            await m.answer('Сначала завершим текущий вопрос. Напоминания можно отключить командой /stop; '
                           'после этого вернуться к /reminders.')
            return True
        return False
    if command == '/reminders':
        await menu(b, m, u, profile, state)
        return True
    if command in {'/day_task', 'дело дня'} or task_match:
        state.update(token=uuid.uuid4().hex[:10], draft='time' if task_match else 'task',
                     draft_stage=u['stage'], date=b.local_date_for_user(u),
                     draft_task=task_match.group(1) if task_match else None)
        await persist(b, u, profile, state)
        await m.answer('В какое время сегодня проверить результат? Напишите ЧЧ:ММ, например 19:30. '
                       'Проверки с 08:00 до 20:59. Часовой пояс: ' + (u.get('timezone') or 'Europe/Vilnius') + '. Отмена — /cancel.'
                       if task_match else 'Какое одно дело проверим сегодня? Напишите его одним сообщением. Отмена — /cancel.', parse_mode=None)
        return True
    if not state.get('draft'):
        return False
    if state.get('date') != b.local_date_for_user(u) or state.get('draft_stage') != u['stage']:
        state['draft'] = None
        await persist(b, u, profile, state)
        return False
    if command == '/cancel':
        state['draft'] = None
        await persist(b, u, profile, state)
        await m.answer('Настройка отменена. Тренировка сохранена.')
        return True
    # Global commands, known buttons and stop requests retain their original ownership.
    if text.startswith('/') or b.is_known_reply_button(text) or text.lower().strip(' .!?') in {'стоп', 'хватит', 'остановиться', 'не надо'}:
        state['draft'] = None
        await persist(b, u, profile, state)
        return False
    if state['draft'] == 'task':
        if not 3 <= len(text) <= 180:
            await m.answer('Напишите одно дело, от 3 до 180 символов, или /cancel.')
            return True
        state.update(draft_task=text, draft='time')
        await persist(b, u, profile, state)
        await m.answer('В какое время сегодня проверить результат? Напишите ЧЧ:ММ, например 19:30. '
                       'С 08:00 до 20:59, часовой пояс: ' + (u.get('timezone') or 'Europe/Vilnius') + '. Отмена — /cancel.', parse_mode=None)
        return True
    target = due_at(u, text, b.local_now_for_user)
    if target is None:
        await m.answer('Нужно будущее время сегодня с 08:00 до 20:59, в формате ЧЧ:ММ. Или /cancel.')
        return True
    state.update(draft='confirm', task_due=target.isoformat())
    await persist(b, u, profile, state)
    await m.answer(f'Проверить дело «{state["draft_task"]}» сегодня в {target:%H:%M}? '
                   'Бот спросит о результате, но не сможет проверить его самостоятельно. '
                   'Если закрыть день, проверка не придёт. При действующем лимите доставка может быть пропущена.', parse_mode=None,
                   reply_markup=keyboard(state['token'], [('Да, проверить один раз', 'confirm'), ('Отмена', 'cancel')]))
    return True


async def callback(b, c):
    u = await b.get_user(c.from_user.id, b.DB_PATH)
    profile = await b.get_user_profile(u['user_id'], b.DB_PATH)
    state = profile.get(KEY) or {}
    try:
        _, token, value = c.data.split(':', 2)
    except ValueError:
        await c.answer('Этот экран уже не актуален.'); return
    if token != state.get('token'):
        await c.answer('Этот экран уже не актуален. Откройте /reminders.'); return
    if value == 'off':
        u.update(notifications_enabled=0, reminder_mode='paused')
        state.update(invites=False, snooze=None, task=None, draft=None, token=uuid.uuid4().hex[:10])
        plan = profile.get('planned_intention') or {}
        if plan: profile['planned_intention'] = dict(plan, status='cancelled')
        await persist(b, u, profile, state)
        await c.message.answer('Все напоминания отключены. Бесплатная тренировка доступна в любое время.')
        await c.answer(); return
    if await b.handle_safety_callback(c, u, c.data or ''):
        return
    if b.proactive_protected(u):
        await c.answer('Сначала ответьте на текущий вопрос.'); return
    if value in {'task', 'daily'}:
        if value == 'task':
            state.update(draft='task', draft_stage=u['stage'], date=b.local_date_for_user(u))
            await persist(b, u, profile, state)
            await c.message.answer('Какое одно дело проверим сегодня? Напишите его одним сообщением. Отмена — /cancel.')
        else:
            u.update(notifications_enabled=1, reminder_mode='custom')
            profile['reminder_hour'] = 19
            state.update(invites=True, draft=None, token=uuid.uuid4().hex[:10])
            await persist(b, u, profile, state)
            await c.message.answer('Включено одно приглашение в день в 19:00 (' + (u.get('timezone') or 'Europe/Vilnius') + '). '
                                   'После закрытия дня не пишу. Отключить — /stop.', parse_mode=None)
        await c.answer(); return
    if value in {'confirm', 'cancel'}:
        if not state.get('draft') or state.get('draft_stage') != u['stage'] or state.get('date') != b.local_date_for_user(u):
            await c.answer('Настройка устарела. Откройте /day_task.'); return
        if value == 'confirm':
            target = parse_dt(state.get('task_due'))
            if state.get('draft') != 'confirm' or not target or target <= dt.datetime.now(dt.timezone.utc):
                await c.answer('Это время уже прошло. Укажите другое время.'); return
            state['task'] = dict(title=state['draft_task'], due_at=target.isoformat(), status='confirmed')
            if not u.get('notifications_enabled') or u.get('reminder_mode') == 'paused':
                u.update(notifications_enabled=1, reminder_mode='intent_only')
            state['snooze'] = None
        state.update(draft=None, draft_task=None, task_due=None, token=uuid.uuid4().hex[:10])
        await persist(b, u, profile, state)
        await c.message.answer('Одна проверка назначена. Отменить все напоминания — /stop.' if value == 'confirm' else 'Без новой проверки. Текущий шаг сохранён.')
        await c.answer(); return
    task = state.get('task') or {}
    if value in {'done', 'notyet'}:
        if not task_pending(state, dt.datetime.now(dt.timezone.utc)):
            await c.answer('Этот результат уже записан.'); return
        task.update(status='answered', result=value)
        state['task'] = task
        u['unanswered_proactive_count'] = 0
        state['token'] = uuid.uuid4().hex[:10]
        await persist(b, u, profile, state)
        await c.message.answer('Отметил: дело выполнено — по вашему ответу. Можно остановиться здесь.' if value == 'done'
                               else 'Отметил: пока не выполнено. Можно вернуться к практике без оценки и давления.',
                               reply_markup=keyboard(state['token'], [('Вернуться к тренировке', 'train'), ('Узнать о группе', 'group')]))
        await c.answer(); return
    if value == 'group':
        state['token'] = uuid.uuid4().hex[:10]
        await persist(b, u, profile, state)
        await b.show_day3_offer(c.message, u, 'task_check_support', mode='manual')
        await c.answer(); return
    if value in {'60', '90'}:
        if not u.get('notifications_enabled') or u.get('reminder_mode') == 'paused':
            await c.answer('Напоминания отключены. Настройки — /reminders.'); return
        now = b.local_now_for_user(u)
        target = now + dt.timedelta(minutes=int(value))
        if not (8 <= target.hour < 21) or b.day_closed_today(u) or b._user_no_reminders_today(u, now.date().isoformat()) or b._proactive_limit_reached(u, now.date().isoformat()):
            await c.answer('Сегодня напоминать больше не буду. Вернуться можно в любое время.'); return
        # A task check snoozes that check; an invitation snoozes only the invitation.
        if task.get('status') == 'sent':
            task.update(status='confirmed', due_at=target.isoformat())
            state['task'] = task
        else:
            state['snooze'] = dict(due_at=target.isoformat(), status='confirmed')
        state.update(token=uuid.uuid4().hex[:10], draft=None)
        u['unanswered_proactive_count'] = 0
        await persist(b, u, profile, state)
        await c.message.answer(f'Напомню около {target:%H:%M}. Если закрыть день или отключить напоминания, сообщение не придёт. '
                               'Планировщик проверяет расписание каждые 15 минут.', parse_mode=None,
                               reply_markup=keyboard(state['token'], [('Отключить напоминания', 'off')]))
        await c.answer(); return
    if value == 'train':
        state.update(token=uuid.uuid4().hex[:10], snooze=None, draft=None)
        u['unanswered_proactive_count'] = 0
        await persist(b, u, profile, state)
        if not await b.restore_from_notification_context(c.message, u, source='practice_invitation'):
            await b.render_current_screen(c.message, u)
        await c.answer(); return
    await c.answer('Этот ответ уже не актуален.')


async def send_invitation(b, telegram, u, text=None):
    profile = await b.get_user_profile(u['user_id'], b.DB_PATH)
    state = profile.get(KEY) or {}
    # Keep the token of an outstanding task form/result; don't overwrite its buttons.
    if draft_active(b, u, state) or task_pending(state, dt.datetime.now(dt.timezone.utc)):
        return False
    state.update(token=uuid.uuid4().hex[:10], snooze=None)
    b.remember_notification_context(u, 'practice_invitation')
    u['unanswered_proactive_count'] = int(u.get('unanswered_proactive_count') or 0) + 1
    await persist(b, u, profile, state)
    await telegram.send_message(u['chat_id'], text or 'Есть желание потренироваться? Можно вернуться к своему делу, '
                                'перенести напоминание или отключить его.', parse_mode=None, reply_markup=invite_keyboard(state['token']))
    return True


async def dispatch_due(b, telegram, u, now):
    """Caller enforces consent, safety, closed-day, quiet hours and shared daily cap."""
    profile = await b.get_user_profile(u['user_id'], b.DB_PATH)
    state = profile.get(KEY) or {}
    for kind in ('task', 'snooze'):
        plan = state.get(kind) or {}
        target = parse_dt(plan.get('due_at'))
        if plan.get('status') != 'confirmed' or not target or target > now:
            continue
        if now >= target + dt.timedelta(hours=4) or target.astimezone(b.local_now_for_user(u).tzinfo).date() != b.local_now_for_user(u).date():
            plan['status'] = 'expired'
            state[kind] = plan
            await persist(b, u, profile, state)
            continue
        if draft_active(b, u, state) or (kind == 'snooze' and task_pending(state, now)):
            return False
        # Claim before Telegram; a failed delivery is never spam-retried each tick.
        plan['status'] = 'dispatching'
        state[kind] = plan
        state['token'] = uuid.uuid4().hex[:10]
        b._increment_proactive_count(u, b.local_date_for_user(u))
        b.mark_bot_auto_message(u)
        u['unanswered_proactive_count'] = int(u.get('unanswered_proactive_count') or 0) + 1
        await persist(b, u, profile, state)
        token = state['token']
        if kind == 'task':
            choices = [('Сделано', 'done'), ('Пока нет', 'notyet'), ('Через час', '60'), ('Через 1,5 часа', '90'), ('Отключить напоминания', 'off')]
            await telegram.send_message(u['chat_id'], f'Вы выбрали дело «{plan["title"]}». Получилось его завершить?',
                                        parse_mode=None, reply_markup=keyboard(token, choices))
        else:
            b.remember_notification_context(u, 'practice_invitation')
            await telegram.send_message(u['chat_id'], 'Возвращаюсь по вашему запросу. Хотите потренироваться?',
                                        parse_mode=None, reply_markup=invite_keyboard(token))
        plan['status'] = 'sent'
        state[kind] = plan
        await persist(b, u, profile, state)
        return True
    return False
