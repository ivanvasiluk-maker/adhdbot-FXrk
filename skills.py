TRACKS = {
    "procrastination": {"title": "Запуск и прокрастинация", "description": "Старт, внимание, возврат после срыва", "enabled": True},
    "anxiety": {"title": "Тревога и действие", "description": "Действовать при тревоге и неопределённости", "enabled": False},
    "burnout": {"title": "Энергия и восстановление", "description": "Ресурс, усталость, минимальный день", "enabled": False},
    "shame": {"title": "Стыд и страх оценки", "description": "Ошибки, видимость, самокритика", "enabled": False},
    "career": {"title": "Профориентация и жизненный вектор", "description": "Смысл, выбор, работа, направление", "enabled": False},
    "migration": {"title": "Жизнь в миграции", "description": "Адаптация, язык, быт, опора", "enabled": False},
}

# Предложить изменение плана на определённый день
def propose_plan_override(u: dict, day_number: int, new_skill_id: str):
    if new_skill_id not in SKILLS_DB:
        return
    overrides = json.loads(u.get("plan_overrides_json") or "{}") if u.get("plan_overrides_json") else {}
    overrides[str(day_number)] = new_skill_id
    u["plan_overrides_json"] = json.dumps(overrides, ensure_ascii=False)
# Visible day-level core skill groups. The user sees this stable title all day;
# concrete SKILLS_DB ids below are variants/adaptations inside that core.
CORE_SKILL_GROUPS = {
    "entry_small_step": {
        "title": "Вход через маленький шаг",
        "variants": ["open_only", "task_naming", "visible_next_step", "ninety_sec_start"],
    },
    "attention_container": {
        "title": "Контейнер внимания",
        "variants": ["one_tab_focus", "phone_far_3min", "visible_next_step", "urge_surf_60"],
    },
    "bad_draft_entry": {
        "title": "Плохой черновик",
        "variants": ["bad_first_step"],
    },
    "shame_to_action": {
        "title": "От самокритики к действию",
        "variants": ["check_the_facts_light", "self_criticism_to_instruction"],
    },
    "energy_first": {
        "title": "Сначала ресурс, потом задача",
        "variants": ["body_before_task", "minimum_viable_day", "open_only"],
    },
    "return_after_slip": {
        "title": "Возврат после срыва",
        "variants": ["restart_after_slip", "task_naming", "open_only"],
    },
}

VARIANT_TO_CORE_SKILL_ID = {}
for _core_id, _core_data in CORE_SKILL_GROUPS.items():
    for _variant_id in _core_data.get("variants", []):
        VARIANT_TO_CORE_SKILL_ID.setdefault(_variant_id, _core_id)


def core_skill_id_for_variant(skill_id: str) -> str:
    return VARIANT_TO_CORE_SKILL_ID.get(skill_id or "", "entry_small_step")


def core_skill_title(core_skill_id: str) -> str:
    return CORE_SKILL_GROUPS.get(core_skill_id or "", CORE_SKILL_GROUPS["entry_small_step"])["title"]


def core_skill_title_for_variant(skill_id: str) -> str:
    return core_skill_title(core_skill_id_for_variant(skill_id))


def variants_for_core_skill(core_skill_id: str) -> list:
    return list(CORE_SKILL_GROUPS.get(core_skill_id or "", CORE_SKILL_GROUPS["entry_small_step"]).get("variants", []))

# 4-недельные шаблоны по bucket (28 дней)
PROGRAM_TEMPLATES = {
    "anxiety": {
        1: ["check_the_facts_light", "bad_first_step", "urge_surf_60", "open_only", "self_criticism_to_instruction", "if_then_plan", "restart_after_slip"],
        2: ["body_before_task", "phone_far_3min", "one_tab_focus", "visible_next_step", "ninety_sec_start", "task_naming", "body_doubling_plan"],
        3: ["minimum_viable_day", "check_the_facts_light", "bad_first_step", "if_then_plan", "urge_surf_60", "restart_after_slip", "one_tab_focus"],
        4: ["visible_next_step", "phone_far_3min", "body_before_task", "body_doubling_plan", "ninety_sec_start", "self_criticism_to_instruction", "minimum_viable_day"],
    },
    "low_energy": {
        1: ["minimum_viable_day", "body_before_task", "open_only", "task_naming", "ninety_sec_start", "visible_next_step", "restart_after_slip"],
        2: ["body_doubling_plan", "if_then_plan", "phone_far_3min", "one_tab_focus", "bad_first_step", "self_criticism_to_instruction", "check_the_facts_light"],
        3: ["urge_surf_60", "minimum_viable_day", "body_before_task", "open_only", "visible_next_step", "body_doubling_plan", "restart_after_slip"],
        4: ["task_naming", "if_then_plan", "ninety_sec_start", "phone_far_3min", "bad_first_step", "one_tab_focus", "self_criticism_to_instruction"],
    },
    "distractibility": {
        1: ["one_tab_focus", "phone_far_3min", "visible_next_step", "task_naming", "ninety_sec_start", "urge_surf_60", "restart_after_slip"],
        2: ["open_only", "if_then_plan", "body_before_task", "body_doubling_plan", "bad_first_step", "check_the_facts_light", "self_criticism_to_instruction"],
        3: ["minimum_viable_day", "one_tab_focus", "phone_far_3min", "visible_next_step", "urge_surf_60", "task_naming", "restart_after_slip"],
        4: ["ninety_sec_start", "if_then_plan", "body_before_task", "open_only", "body_doubling_plan", "bad_first_step", "check_the_facts_light"],
    },
    "mixed": {
        1: ["open_only", "task_naming", "bad_first_step", "ninety_sec_start", "one_tab_focus", "visible_next_step", "restart_after_slip"],
        2: ["phone_far_3min", "self_criticism_to_instruction", "check_the_facts_light", "urge_surf_60", "body_before_task", "minimum_viable_day", "if_then_plan"],
        3: ["body_doubling_plan", "open_only", "bad_first_step", "one_tab_focus", "visible_next_step", "restart_after_slip", "task_naming"],
        4: ["ninety_sec_start", "phone_far_3min", "urge_surf_60", "body_before_task", "if_then_plan", "check_the_facts_light", "minimum_viable_day"],
    }
}

def build_28_day_plan(bucket: str) -> list:
    b = bucket if bucket in PROGRAM_TEMPLATES else "mixed"
    days = []
    for wk in (1, 2, 3, 4):
        for sid in PROGRAM_TEMPLATES[b][wk]:
            if sid in SKILLS_DB:
                days.append(sid)
            else:
                # fallback если навыка нет
                fallback = list(SKILLS_DB.keys())[0]
                days.append(fallback)
    return days

# MVP fallback plans
PLANS = {
    "anxiety": ["check_the_facts_light", "bad_first_step", "urge_surf_60"],
    "low_energy": ["minimum_viable_day", "body_before_task", "open_only"],
    "distractibility": ["one_tab_focus", "phone_far_3min", "visible_next_step"],
    "mixed": ["open_only", "task_naming", "bad_first_step"],
}

def build_plan(bucket: str) -> list:
    return PLANS.get(bucket, PLANS["mixed"])
# Возвращает текущий план пользователя с учётом overrides
def get_current_plan(u: dict) -> list:
    bucket = u.get("bucket") or "mixed"
    base = json.loads(u.get("plan_json") or "[]")

    if not base:
        base = build_28_day_plan(bucket)

    # Фильтруем несуществующие навыки
    safe_base = []
    for sid in base:
        if sid in SKILLS_DB:
            safe_base.append(sid)
        else:
            safe_base.append(list(SKILLS_DB.keys())[0])

    overrides = json.loads(u.get("plan_overrides_json") or "{}") if u.get("plan_overrides_json") else {}
    for k, sid in overrides.items():
        try:
            day_idx = int(k) - 1
            if 0 <= day_idx < len(safe_base) and sid in SKILLS_DB:
                safe_base[day_idx] = sid
        except Exception:
            continue

    return adapt_plan_to_profile(safe_base, u)
# ============================================================
# SKILLS.PY — Все навыки, планы и функции
# ============================================================

import json
from typing import List, Dict, Any, Optional

# ============================================================
# SKILLS_DB (4 недели) — DBT / CBT / ACT / самокритика / тревога
# ============================================================

SKILLS_DB = {
        # ========================================================
        # TRACK: ANXIETY (тревожная прокрастинация)
        # ========================================================
        # WEEK 1
        "a_w1_anchor_60_base": {
            "track": "anxiety", "week": 1, "variant": "base",
            "name": "Якорь на 60 секунд",
            "goal": "Снизить тревогу и вернуть управление",
            "how": "1) 3 длинных выдоха. 2) Назови 3 предмета вокруг. 3) Сделай 1 микро-шаг 2 минуты.",
            "minimum": "Сделай только 3 выдоха.",
            "how_more": "Таймер 60 сек. Вдох 4 — выдох 6 (3 раза). Потом «что я вижу/слышу/чувствую» (1–3 пункта). Затем выбери шаг ≤2 минут (открыть файл/написать заголовок/составить 3 пункта)."
        },
        "a_w1_task_clarity_alt": {
            "track": "anxiety", "week": 1, "variant": "alt",
            "name": "Ясность задачи (3 вопроса)",
            "goal": "Убрать туман «что делать?»",
            "how": "Ответь: (1) Результат одной строкой. (2) Первый шаг одним действием. (3) Что мешает и что убрать.",
            "minimum": "Напиши только «первый шаг».",
            "how_more": "Шаблон: Результат: _. Первый шаг: _. Помеха: _ → убираю так: _. Потом 2 минуты делаю первый шаг."
        },
        # WEEK 2
        "a_w2_notice_thought_base": {
            "track": "anxiety", "week": 2, "variant": "base",
            "name": "Замечать мысль (дефузия)",
            "goal": "Снизить власть тревожных мыслей без борьбы",
            "how": "Поймал мысль → скажи: «Это мысль о …» → вернись к делу на 60–120 сек.",
            "minimum": "Один раз назвать «это мысль».",
            "how_more": "Не спорь с мыслью. Просто маркируй её: «мысль о провале/о стыде/о риске». Затем делай действие 1–2 минуты."
        },
        "a_w2_experiment_alt": {
            "track": "anxiety", "week": 2, "variant": "alt",
            "name": "Проверка тревоги действием",
            "goal": "Выйти из анализа в опыт",
            "how": "Сделай 1 микро-действие 2–3 минуты вопреки тревоге → отметь факт, что случилось реально.",
            "minimum": "30–60 сек попытки.",
            "how_more": "Пример: отправить черновик/открыть форму/набросать 3 пункта. После — одно предложение: «Реально произошло: …»"
        },
        # WEEK 3
        "a_w3_separate_critic_base": {
            "track": "anxiety", "week": 3, "variant": "base",
            "name": "Отделить критика",
            "goal": "Не принимать самокритику за истину",
            "how": "Услышал «я…» → скажи «это голос критика» → ответь как тренер другу 1 фразой.",
            "minimum": "Только метка: «критик».",
            "how_more": "Короткий ответ тренера: «Я вижу, что тебе тяжело. Давай один шаг на 2 минуты — этого достаточно»."
        },
        "a_w3_support_alt": {
            "track": "anxiety", "week": 3, "variant": "alt",
            "name": "Поддержка вместо наказания",
            "goal": "Вернуть энергию после ошибки",
            "how": "Спроси: «Как бы я поддержал близкого?» → скажи себе 1 тёплую фразу → 1 микро-шаг.",
            "minimum": "Одна фраза поддержки.",
            "how_more": "Фразы: «Мне трудно — и я всё равно пробую». «Я не обязан быть идеальным». Потом микро-шаг ≤2 минут."
        },
        # WEEK 4
        "a_w4_env_shield_base": {
            "track": "anxiety", "week": 4, "variant": "base",
            "name": "Щит окружения",
            "goal": "Снизить тревогу за счёт контроля среды",
            "how": "На 20 минут убери 1 стимул (уведомления/чат/вкладки) → сделай 10 минут одним окном.",
            "minimum": "Убери стимул на 5 минут.",
            "how_more": "Идея: меньше входов = меньше тревоги. 1 стимул убрал → уже легче стартовать."
        },
        "a_w4_weekly_review_alt": {
            "track": "anxiety", "week": 4, "variant": "alt",
            "name": "Еженедельный разбор прогресса",
            "goal": "Видеть прогресс и не сдаваться",
            "how": "3 вопроса: что сработало? где срыв? что оставляем на следующую неделю?",
            "minimum": "Ответить на 1 вопрос.",
            "how_more": "Важно не оценивать себя, а настраивать систему. Выбери 1 навык, который оставляем как базовый."
        },
        # ========================================================
        # TRACK: LOW_ENERGY (тяжело начать / «депрессивный» тип)
        # ========================================================
        # WEEK 1
        "e_w1_resistance_timer_base": {
            "track": "low_energy", "week": 1, "variant": "base",
            "name": "Таймер на сопротивление (2 минуты)",
            "goal": "Запустить действие без мотивации",
            "how": "Поставь таймер 2 минуты → делай самый тупой первый шаг → стоп по таймеру.",
            "minimum": "Открыть файл/встать со стула.",
            "how_more": "Первый шаг: открыть документ, написать заголовок, создать список из 3 пунктов, найти нужный файл."
        },
        "e_w1_restore_one_alt": {
            "track": "low_energy", "week": 1, "variant": "alt",
            "name": "Одна вещь на восстановление",
            "goal": "Вернуть базовый ресурс",
            "how": "Выбери одно: вода/еда/душ/воздух 3 мин/10 мин без экрана → потом микро-шаг.",
            "minimum": "1 глоток воды.",
            "how_more": "Сначала тело. Потом мозг. Если «пусто» — это сигнал про ресурс, а не про характер."
        },
        # WEEK 2
        "e_w2_micro_start_base": {
            "track": "low_energy", "week": 2, "variant": "base",
            "name": "Микро-старт",
            "goal": "Начать без давления результата",
            "how": "Выбери действие ≤2 минут. Критерий успеха: начал(а), а не закончил(а).",
            "minimum": "Открыть файл/взять предмет/сесть за стол.",
            "how_more": "Ставь себе задачу «начать», не «сделать». Это ломает сопротивление."
        },
        "e_w2_not_to_do_alt": {
            "track": "low_energy", "week": 2, "variant": "alt",
            "name": "Not-To-Do лист",
            "goal": "Снять перегруз и стыд",
            "how": "Запиши 3 вещи «сегодня НЕ делаю» → выбери 1 «делаю минимум» → 2 минуты минимума.",
            "minimum": "Записать 1 пункт «не делаю».",
            "how_more": "Это управление нагрузкой. Снял лишнее → появилось место для действия."
        },
        # WEEK 3
        "e_w3_return_no_punish_base": {
            "track": "low_energy", "week": 3, "variant": "base",
            "name": "Возврат без наказания",
            "goal": "Не бросать после срыва",
            "how": "Заметил(а) срыв → фраза «Я возвращаюсь — это и есть навык» → шаг ≤2 минут.",
            "minimum": "Произнести фразу.",
            "how_more": "Возврат — главный навык месяца. Он важнее идеальных дней."
        },
        "e_w3_support_alt": {
            "track": "low_energy", "week": 3, "variant": "alt",
            "name": "Поддержка вместо наказания",
            "goal": "Сохранить энергию после ошибки",
            "how": "1 фраза поддержки → 1 микро-шаг → всё.",
            "minimum": "Только фраза поддержки.",
            "how_more": "Фраза: «Сейчас трудно. Я делаю маленький шаг — этого достаточно»."
        },
        # WEEK 4
        "e_w4_env_shield_base": {
            "track": "low_energy", "week": 4, "variant": "base",
            "name": "Щит окружения",
            "goal": "Снизить нагрузку на волю",
            "how": "Убери 1 стимул на 20 минут → 10 минут одним окном.",
            "minimum": "Убери 1 стимул на 5 минут.",
            "how_more": "Когда энергии мало, среда должна помогать. Убираем один вход — становится легче."
        },
        "e_w4_weekly_review_alt": {
            "track": "low_energy", "week": 4, "variant": "alt",
            "name": "Еженедельный разбор",
            "goal": "Видеть прогресс, а не провалы",
            "how": "Что сработало? Где ломается? Что упрощаем на следующую неделю?",
            "minimum": "Ответить на 1 вопрос.",
            "how_more": "Смысл: не «я плохой», а «система требует настройки»."
        },
        # ========================================================
        # TRACK: DISTRACTIBILITY (высокая отвлекаемость)
        # ========================================================
        # WEEK 1
        "d_w1_single_window_base": {
            "track": "distractibility", "week": 1, "variant": "base",
            "name": "Одно окно 10 минут",
            "goal": "Вернуть фокус через узкий канал",
            "how": "Оставь 1 документ/вкладку → таймер 10 минут → при тяге отметь «тянет» и вернись.",
            "minimum": "1 минуту одним окном.",
            "how_more": "Навык = скорость возврата. Каждый возврат — очко. Не борись, возвращай."
        },
        "d_w1_phone_out_alt": {
            "track": "distractibility", "week": 1, "variant": "alt",
            "name": "Телефон вне доступа",
            "goal": "Срезать срывы на соцсети",
            "how": "Убери телефон/уведомления → 20 минут работы → вернуть можно потом.",
            "minimum": "Отключить звук на 10 минут.",
            "how_more": "Если нельзя убрать — экран вниз, без звука, подальше от руки."
        },
        # WEEK 2
        "d_w2_soft_return_base": {
            "track": "distractibility", "week": 2, "variant": "base",
            "name": "Мягкий возврат внимания",
            "goal": "Отвлекаемость без войны",
            "how": "Заметил(а) отвлечение → метка «ушёл» → вернулся(лась) на 2 минуты.",
            "minimum": "Один возврат на 30 сек.",
            "how_more": "Цель — не «не отвлекаться», а «быстрее возвращаться»."
        },
        "d_w2_capture_then_do_alt": {
            "track": "distractibility", "week": 2, "variant": "alt",
            "name": "Поймал импульс → записал → вернулся",
            "goal": "Убрать «надо проверить прямо сейчас»",
            "how": "Импульс → 1 строка в заметку → обратно в задачу на 2 минуты.",
            "minimum": "Записать 1 строку.",
            "how_more": "Это парковка мыслей. Ты ничего не теряешь, просто не уходишь сейчас."
        },
        # WEEK 3
        "d_w3_env_shield_base": {
            "track": "distractibility", "week": 3, "variant": "base",
            "name": "Щит окружения",
            "goal": "Снять лишние входы",
            "how": "На 20 минут убрать 1 стимул: уведомления/телефон/вкладку/шум.",
            "minimum": "Убрать стимул на 5 минут.",
            "how_more": "Не надо силы воли. Надо меньше триггеров."
        },
        "d_w3_return_no_punish_alt": {
            "track": "distractibility", "week": 3, "variant": "alt",
            "name": "Возврат без наказания",
            "goal": "Не ломаться из-за отвлечений",
            "how": "Отвлёкся(лась) → «Я возвращаюсь — это и есть навык» → 2 минуты дела.",
            "minimum": "Сказать фразу и вернуться на 30 сек.",
            "how_more": "Даже 10 отвлечений = нормально. Главное — 10 возвратов."
        },
        # WEEK 4
        "d_w4_focus_block_20_base": {
            "track": "distractibility", "week": 4, "variant": "base",
            "name": "Фокус-блок 20 минут",
            "goal": "Собрать устойчивый отрезок работы",
            "how": "Таймер 20 минут → одна задача → если тянет — метка «тянет» и обратно.",
            "minimum": "5 минут фокуса.",
            "how_more": "Если 20 тяжело — делай 2×10. Важна регулярность."
        },
        "d_w4_weekly_review_alt": {
            "track": "distractibility", "week": 4, "variant": "alt",
            "name": "Еженедельный разбор фокуса",
            "goal": "Увидеть, где теряется внимание",
            "how": "Где чаще срывало? какой стимул главный? что убираем на следующей неделе?",
            "minimum": "Ответить на 1 вопрос.",
            "how_more": "Смысл — настроить среду. Это быстрее, чем «качать волю»."
        },
        # ========================================================
        # TRACK: MIXED (смешанный)
        # ========================================================
        # WEEK 1
        "m_w1_not_to_do_base": {
            "track": "mixed", "week": 1, "variant": "base",
            "name": "Not-To-Do лист",
            "goal": "Снять перегруз и начать",
            "how": "3 «не делаю» → 1 «делаю минимум» → 2 минуты минимума.",
            "minimum": "1 пункт «не делаю».",
            "how_more": "Пример: «Не делаю идеально. Не отвечаю всем. Не открываю соцсети до шага». Потом шаг 2 минуты."
        },
        "m_w1_clarity_1line_alt": {
            "track": "mixed", "week": 1, "variant": "alt",
            "name": "Результат в 1 строку",
            "goal": "Убрать хаос задач",
            "how": "Запиши: «Сегодня результат = …» → «Первый шаг = …» → 2 минуты делаю.",
            "minimum": "Напиши только «первый шаг».",
            "how_more": "Если нет ясности: «первый шаг = открыть файл и назвать задачу»."
        },
        # WEEK 2
        "m_w2_notice_thought_base": {
            "track": "mixed", "week": 2, "variant": "base",
            "name": "Замечать мысль",
            "goal": "Снизить зависание в голове",
            "how": "«Это мысль о…» → 60–120 сек действия.",
            "minimum": "Один раз назвать мысль.",
            "how_more": "Работаем не верой, а фактом действия. Мысль есть — действие тоже есть."
        },
        "m_w2_micro_start_alt": {
            "track": "mixed", "week": 2, "variant": "alt",
            "name": "Микро-старт",
            "goal": "Запуск без мотивации",
            "how": "Выбери шаг ≤2 минут → начни → стоп.",
            "minimum": "Открыть документ/встать.",
            "how_more": "Смысл: сдвиг = старт. Не «сделал всё», а «запустил»."
        },
        # WEEK 3
        "m_w3_separate_critic_base": {
            "track": "mixed", "week": 3, "variant": "base",
            "name": "Отделить критика",
            "goal": "Не сливать энергию в самоунижение",
            "how": "Метка «критик» → 1 фраза как тренер → микро-шаг.",
            "minimum": "Сказать «критик».",
            "how_more": "Ответ тренера: «Ок, тяжело. Делаем один шаг. Я рядом»."
        },
        "m_w3_support_alt": {
            "track": "mixed", "week": 3, "variant": "alt",
            "name": "Поддержка вместо наказания",
            "goal": "Не бросать после ошибки",
            "how": "1 тёплая фраза → 1 микро-шаг.",
            "minimum": "Одна тёплая фраза.",
            "how_more": "Фраза: «Даже маленький шаг — это тренировка. Я не обязан быть идеальным»."
        },
        # WEEK 4
        "m_w4_env_shield_base": {
            "track": "mixed", "week": 4, "variant": "base",
            "name": "Щит окружения",
            "goal": "Стабилизировать день",
            "how": "Убери 1 стимул на 20 минут → 10 минут одним окном → зафиксируй факт.",
            "minimum": "Убрать стимул на 5 минут.",
            "how_more": "Фиксация факта нужна: мозг учится «я могу»."
        },
        "m_w4_weekly_review_alt": {
            "track": "mixed", "week": 4, "variant": "alt",
            "name": "Еженедельный разбор",
            "goal": "Настроить систему под реальную жизнь",
            "how": "Что сработало? где ломается? какую 1 настройку делаем на следующую неделю?",
            "minimum": "Ответить на 1 вопрос.",
            "how_more": "Это инженерия привычки: не обвиняем, а настраиваем."
        }
    }

# === FALLBACK SKILLS (базовые короткие версии) ===
SKILLS_DB.update({
    "return_no_punish": {
        "name": "Возврат без наказания",
        "goal": "Вернуться к задаче без самокритики",
        "simple": [
            "Заметь срыв",
            "Скажи себе: 'Я возвращаюсь'",
            "Сделай 60 секунд"
        ],
        "explain": "Снижает избегание через нейтрализацию стыда",
        "track": "mixed",
        "minimum": "1 минута"
    },
    "micro_start": {
        "name": "Микро-старт",
        "goal": "Запустить действие без сопротивления",
        "simple": [
            "Сделай самый маленький шаг",
            "Не думай о результате",
            "Просто начни"
        ],
        "explain": "Снижает порог входа",
        "track": "mixed",
        "minimum": "30 секунд"
    },
    "notice_thought": {
        "name": "Заметь мысль",
        "goal": "Отделить мысль от факта",
        "simple": [
            "Поймай автоматическую мысль",
            "Назови её: 'Это мысль'",
            "Вернись к действию"
        ],
        "explain": "Снижает когнитивное слияние",
        "track": "anxiety",
        "minimum": "1 повтор"
    }
})

# === Legacy short skills (для быстрых сценариев) ===
SKILLS_DB.update({
    # =============================
    # ТРЕК 1 — ТРЕВОЖНЫЙ
    # =============================
    "anx_1_anchor": {
        "track": "anxiety",
        "week": 1,
        "name": "Контакт с поверхностью",
        "simple": [
            "Поставь стопы на пол",
            "Заметь давление",
            "Назови 3 ощущения"
        ],
        "explain": "Тревога — это скачок нервной системы. Контакт с телом снижает возбуждение через сенсорную стабилизацию.",
    },
    "anx_2_worry_time": {
        "track": "anxiety",
        "week": 1,
        "name": "Отложенное беспокойство",
        "simple": [
            "Запиши тревожную мысль",
            "Назначь время для размышлений",
            "Вернись к задаче"
        ],
        "explain": "Мы не подавляем тревогу, а структурируем её. Это снижает навязчивость.",
    },
    "anx_3_exposure_micro": {
        "track": "anxiety",
        "week": 2,
        "name": "Микро-экспозиция",
        "simple": [
            "Выбери пугающее действие",
            "Сделай 30–60 секунд",
            "Остановись"
        ],
        "explain": "Избегание усиливает тревогу. Контакт с дискомфортом её снижает.",
    },
    "anx_4_cognitive_check": {
        "track": "anxiety",
        "week": 3,
        "name": "Проверка мысли",
        "simple": [
            "Запиши мысль",
            "Спроси: факт или предположение?",
            "Сделай микро-проверку"
        ],
        "explain": "КПТ: мысли — гипотезы, а не реальность.",
    },
    # =============================
    # ТРЕК 2 — ИЗБЕГАНИЕ / ДЕПРЕССИВНЫЙ
    # =============================
    "dep_1_timer": {
        "track": "depressive",
        "week": 1,
        "name": "Таймер сопротивления",
        "simple": [
            "Поставь таймер 2 минуты",
            "Начни действие",
            "Остановись по сигналу"
        ],
        "explain": "Старт важнее мотивации. Поведенческий запуск создаёт инерцию.",
    },
    "dep_2_micro_task": {
        "track": "depressive",
        "week": 1,
        "name": "Мини-задача ≤ 2 минут",
        "simple": [
            "Разбей задачу",
            "Выбери часть ≤ 2 мин",
            "Сделай только её"
        ],
        "explain": "Малый шаг снижает сопротивление.",
    },
    "dep_3_behavior_activation": {
        "track": "depressive",
        "week": 2,
        "name": "Поведенческая активация",
        "simple": [
            "Выбери нейтральное действие",
            "Сделай 5 минут",
            "Оцени самочувствие"
        ],
        "explain": "Действие → энергия. Не наоборот.",
    },
    # =============================
    # ТРЕК 3 — ОТВЛЕКАЕМОСТЬ
    # =============================
    "adhd_1_not_todo": {
        "track": "distraction",
        "week": 1,
        "name": "Not-To-Do список",
        "simple": [
            "Запиши 3 отвлекающих действия",
            "Запрети их на 30 минут",
            "Работай"
        ],
        "explain": "Удаляем триггеры → увеличиваем фокус.",
    },
    "adhd_2_focus_sprint": {
        "track": "distraction",
        "week": 1,
        "name": "Фокус-спринт 15 минут",
        "simple": [
            "Выбери 1 задачу",
            "Таймер 15 минут",
            "Без переключений"
        ],
        "explain": "Контролируемое ограничение повышает удержание внимания.",
    },
    # =============================
    # ТРЕК 4 — СМЕШАННЫЙ
    # =============================
    "mix_1_anchor_start": {
        "track": "mixed",
        "week": 1,
        "name": "Якорь + старт",
        "simple": [
            "Сделай 1 якорное дыхание",
            "Выбери микро-шаг",
            "Начни 60 секунд"
        ],
        "explain": "Стабилизация + запуск.",
    },
    "mix_2_no_self_attack": {
        "track": "mixed",
        "week": 2,
        "name": "Возврат без наказания",
        "simple": [
            "Заметь остановку",
            "Не ругай себя",
            "Вернись к шагу"
        ],
        "explain": "Самокритика усиливает избегание."
    }
})

CORE_LAUNCH_WEEK_SKILL_IDS = [
    "open_only",
    "ninety_sec_start",
    "bad_first_step",
    "task_naming",
    "one_tab_focus",
    "visible_next_step",
    "phone_far_3min",
    "restart_after_slip",
    "self_criticism_to_instruction",
    "check_the_facts_light",
    "urge_surf_60",
    "body_before_task",
    "minimum_viable_day",
    "body_doubling_plan",
    "if_then_plan",
]

CORE_LAUNCH_WEEK_SKILLS = {
    "ninety_sec_start": {
        "skill_id": "ninety_sec_start",
        "track": "low_energy",
        "week": 1,
        "variant": "core",
        "basis": "ADHD / behavioral activation",
        "name": "Старт 90 секунд",
        "goal": "Запустить действие без требования продолжать",
        "when_to_use": "Когда задача понятна, но вход вызывает сопротивление или хочется отложить.",
        "real_life_example": "Открыть письмо и 90 секунд писать черновик ответа, не отправляя его.",
        "steps": [
            "Выбери один видимый первый шаг",
            "Поставь таймер на 90 секунд",
            "Делай только до сигнала и остановись без оценки",
        ],
        "minimum_action": "Открыть место задачи и сделать 10 секунд первого движения",
        "why_short": "Мозгу легче согласиться на короткий вход, чем на всю задачу.",
        "why_long": "ADHD-мозг часто блокируется не задачей, а масштабом входа. Короткий таймер убирает обещание «работать долго» и тренирует запуск как отдельный навык.",
        "if_boring_response": "Да, это маленько и скучно. Именно поэтому сопротивление ниже.",
        "if_hard_response": "Сократи до 10 секунд или перейди на «Открыть без таймера».",
        "if_skeptic_response": "Цель не продуктивность за 90 секунд. Цель — доказать мозгу, что вход безопасен.",
        "if_failed_response": "Это данные: 90 секунд много. Следующий подход — только открыть место задачи.",
        "if_dont_understand_response": "Ты не обязан делать задачу целиком. Нужно только начать на 90 секунд и остановиться.",
        "coach_feedback": "Фиксируем не объём, а факт старта. Один вход уже тренировка.",
        "trainer_variants": {
            "beck": "Логика простая: ограниченный вход снижает угрозу и запускает поведенческую инерцию.",
            "skinny": "90 секунд. Не героизм. Стартанул — отметил.",
            "marsha": "Давай мягко: всего 90 секунд, без обязанности продолжать.",
        },
        "simple": ["Выбери первый шаг", "Таймер 90 секунд", "Остановись по сигналу"],
        "how": "Выбери один видимый первый шаг → поставь таймер на 90 секунд → остановись по сигналу.",
        "minimum": "Открыть место задачи на 10 секунд.",
        "explain": "Короткий вход снижает угрозу старта.",
        "how_more": "Открыть письмо, написать черновик 90 секунд и остановиться.",
    },
    "two_min_start": {
        "skill_id": "two_min_start",
        "track": "low_energy",
        "week": 1,
        "variant": "core",
        "name": "Старт 2 минуты",
        "goal": "Сделать первый кусок задачи без давления результата",
        "when_to_use": "Когда задача уже выбрана, но мозг спорит с началом.",
        "real_life_example": "Две минуты разбирать одну папку или писать первый абзац документа.",
        "steps": [
            "Назови действие, которое можно делать прямо сейчас",
            "Поставь таймер на 2 минуты",
            "После сигнала остановись или добровольно продолжи",
        ],
        "minimum_action": "Сделать 20 секунд выбранного действия",
        "why_short": "Начало важнее завершения: оно создаёт инерцию.",
        "why_long": "Когда результат кажется большим, старт отделяется от результата. Две минуты дают безопасный контейнер: можно начать, не подписываясь на марафон.",
        "if_boring_response": "Скучно — значит достаточно просто. Нам сейчас нужен не драйв, а вход.",
        "if_hard_response": "Уменьшаем: 20 секунд или только открыть место задачи.",
        "if_skeptic_response": "Две минуты не решают всю задачу. Они решают блокировку входа.",
        "if_failed_response": "Значит, две минуты пока много. Следующий шаг — 20 секунд или одно слово о задаче.",
        "if_dont_understand_response": "Выбери один физический шаг и делай его только до таймера.",
        "coach_feedback": "Если старт был — подход засчитан, даже без продолжения.",
        "trainer_variants": {
            "beck": "Две минуты — это эксперимент на снижение порога входа, не план выполнения.",
            "skinny": "Две минуты. Сигнал прозвенел — можешь остановиться.",
            "marsha": "Тебе не нужно тащить весь день. Только две минуты контакта.",
        },
        "simple": ["Назови действие", "Таймер 2 минуты", "Стоп или продолжить добровольно"],
        "how": "Назови действие → поставь таймер 2 минуты → остановись или продолжи добровольно.",
        "minimum": "20 секунд действия.",
        "explain": "Старт отделяется от требования закончить.",
        "how_more": "Две минуты писать первый абзац, не редактируя.",
    },
    "open_only": {
        "skill_id": "open_only",
        "track": "downscale",
        "week": 1,
        "variant": "core",
        "basis": "ADHD / behavioral activation",
        "name": "Открыть без таймера",
        "goal": "Снизить вход до почти нуля",
        "when_to_use": "Когда сложно даже поставить таймер или начать 2 минуты",
        "real_life_example": "Открыть документ, папку, чат или страницу задачи и не делать работу.",
        "steps": [
            "Открой место, где лежит задача",
            "Не работай",
            "Назови следующий физический шаг",
        ],
        "minimum_action": "Написать одно слово о задаче",
        "why_short": "Сейчас тренируем вход, а не результат",
        "why_long": "Если даже таймер ощущается как задача, значит вход слишком большой. Мы уменьшаем шаг до действия, которое мозг не воспринимает как угрозу.",
        "if_boring_response": "Да, это скучно. И это хорошо: навык должен быть достаточно маленьким, чтобы не вызывать сопротивление.",
        "if_hard_response": "Тогда не открывай задачу. Просто напиши её название одним словом.",
        "if_skeptic_response": "Это не продуктивность. Это тренировка входа. Без входа не будет результата.",
        "if_failed_response": "Значит, уменьшаем ещё. Одно слово — уже подход.",
        "if_dont_understand_response": "Нужно не работать, а только открыть место задачи и назвать следующий физический шаг.",
        "coach_feedback": "Контакт с задачей уже засчитывается: мы тренируем вход до работы.",
        "trainer_variants": {
            "beck": "Логика простая: таймер сейчас слишком большой стимул. Мы тренируем вход до таймера.",
            "skinny": "Таймер не ставим. Открыл. Назвал шаг. Вернулся.",
            "marsha": "Хорошо, тогда совсем мягко. Не нужно работать — только прикоснуться к задаче.",
        },
        "simple": ["Открой место задачи", "Не работай", "Назови следующий физический шаг"],
        "how": "Открой место, где лежит задача → не работай → назови следующий физический шаг.",
        "minimum": "Написать одно слово о задаче.",
        "explain": "Сейчас тренируем вход, а не результат.",
        "how_more": "Открой документ или чат задачи, ничего не делай, назови следующий физический шаг.",
    },
    "task_naming": {
        "skill_id": "task_naming",
        "track": "downscale",
        "week": 1,
        "variant": "core",
        "basis": "ADHD externalization",
        "name": "Назвать задачу одним словом",
        "goal": "Вернуть контакт с задачей, когда даже открыть её тяжело",
        "when_to_use": "Когда любое действие кажется слишком большим, а задача вызывает избегание.",
        "real_life_example": "Написать «налоги», «письмо», «кухня» или «презентация» в чат бота.",
        "steps": [
            "Не открывай задачу",
            "Напиши одно слово, которое её обозначает",
            "Остановись и отметь подход",
        ],
        "minimum_action": "Одно слово",
        "why_short": "Даже называние задачи уменьшает избегание.",
        "why_long": "Когда задача перегружает, мозг избегает даже контакта. Одно слово создаёт безопасную точку входа без требования делать работу.",
        "if_boring_response": "Да, это почти ничего. Именно такой размер сейчас и нужен.",
        "if_hard_response": "Напиши первую букву или эмодзи задачи — это тоже контакт.",
        "if_skeptic_response": "Это не замена работы. Это первый миллиметр входа, когда всё остальное заблокировано.",
        "if_failed_response": "Ок. Тогда ещё меньше: первая буква задачи уже считается.",
        "if_dont_understand_response": "Просто отправь одно слово, которое обозначает задачу. Больше ничего не нужно.",
        "coach_feedback": "Сопротивление стало видимым, а значит мы уже работаем с ним.",
        "trainer_variants": {
            "beck": "Мы фиксируем минимальный контакт с задачей. Это снижает избегание без перегруза.",
            "skinny": "Одно слово. Без объяснений. Отправил — подход есть.",
            "marsha": "Можно совсем маленько: только одно слово, и на этом достаточно.",
        },
        "simple": ["Не открывай задачу", "Напиши одно слово", "Остановись"],
        "how": "Не открывай задачу → напиши одно слово → остановись.",
        "minimum": "Одно слово.",
        "explain": "Минимальный контакт снижает избегание.",
        "how_more": "Написать «письмо» или «кухня» и остановиться.",
    },
    "restart_after_break": {
        "skill_id": "restart_after_break",
        "track": "mixed",
        "week": 1,
        "variant": "core",
        "name": "Возврат после перерыва",
        "goal": "Вернуться к действию без самонаказания после паузы или срыва",
        "when_to_use": "Когда отвлёкся, завис в телефоне, сделал перерыв дольше планируемого или выпал из задачи.",
        "real_life_example": "После 40 минут в телефоне сказать «я возвращаюсь» и открыть рабочий файл.",
        "steps": [
            "Назови факт без обвинения: «я отвлёкся»",
            "Скажи фразу возврата: «я возвращаюсь»",
            "Сделай самый маленький следующий шаг",
        ],
        "minimum_action": "Произнести фразу «я возвращаюсь»",
        "why_short": "Возврат важнее идеального непрерывного фокуса.",
        "why_long": "Срыв становится проблемой, когда после него включается стыд и избегание. Нейтральный возврат разрывает цикл «срыв → самокритика → ещё больше срыв».",
        "if_boring_response": "Да, фраза простая. Она нужна, чтобы не включать спор с собой.",
        "if_hard_response": "Убери шаг действия. Только скажи: «я возвращаюсь».",
        "if_skeptic_response": "Навык не оправдывает срыв. Он сокращает время между срывом и возвратом.",
        "if_failed_response": "Ничего не чиним задним числом. Следующий подход — одна фраза возврата.",
        "if_dont_understand_response": "Ты замечаешь паузу без ругани и делаешь один маленький шаг обратно.",
        "coach_feedback": "Каждый возврат укрепляет маршрут обратно к задаче.",
        "trainer_variants": {
            "beck": "Метрика — не отсутствие срывов, а скорость возврата после них.",
            "skinny": "Сорвался — заметил — вернулся. Без суда.",
            "marsha": "Ты не обязан быть идеальным. Давай просто мягко вернёмся.",
        },
        "simple": ["Назови факт", "Скажи «я возвращаюсь»", "Сделай микро-шаг"],
        "how": "Назови факт без обвинения → скажи «я возвращаюсь» → сделай микро-шаг.",
        "minimum": "Фраза «я возвращаюсь».",
        "explain": "Возврат снижает стыд и восстанавливает действие.",
        "how_more": "После залипания открыть файл и сделать один маленький шаг.",
    },
    "one_tab_focus": {
        "skill_id": "one_tab_focus",
        "track": "distractibility",
        "week": 1,
        "variant": "core",
        "basis": "ADHD attention control",
        "name": "Фокус в одной вкладке",
        "goal": "Снизить переключения и удержать одно действие",
        "when_to_use": "Когда внимание скачет между вкладками, чатами и приложениями.",
        "real_life_example": "Оставить открытым только документ и закрыть/свернуть остальные вкладки на 10 минут.",
        "steps": [
            "Выбери одну вкладку или одно окно для задачи",
            "Закрой или сверни остальные отвлекающие окна",
            "Работай только в выбранном месте один короткий подход",
        ],
        "minimum_action": "Оставить открытой одну вкладку на 60 секунд",
        "why_short": "Меньше входов — меньше поводов переключиться.",
        "why_long": "Переключение внимания часто запускается средой. Один видимый канал снижает количество стимулов и делает возврат проще.",
        "if_boring_response": "Да, среда станет скучнее. Это помогает мозгу меньше прыгать.",
        "if_hard_response": "Не закрывай всё. Просто разверни нужное окно поверх остальных на 60 секунд.",
        "if_skeptic_response": "Мы не лечим внимание силой воли. Мы уменьшаем количество триггеров вокруг задачи.",
        "if_failed_response": "Ок, значит закрывать всё слишком резко. В следующий раз только одно окно поверх.",
        "if_dont_understand_response": "Нужно оставить перед глазами только место задачи и убрать остальные стимулы хотя бы на минуту.",
        "coach_feedback": "Ты не обязан держать фокус идеально: важен быстрый возврат в одно окно.",
        "trainer_variants": {
            "beck": "Среда конкурирует за внимание. Один канал снижает нагрузку на контроль.",
            "skinny": "Одна вкладка. Остальное в сторону. Работаем коротко.",
            "marsha": "Давай уберём лишний шум и оставим только одно безопасное место для задачи.",
        },
        "simple": ["Выбери одну вкладку", "Убери остальные", "Один короткий подход"],
        "how": "Выбери одну вкладку → убери остальные → сделай короткий подход.",
        "minimum": "Одна вкладка на 60 секунд.",
        "explain": "Снижение стимулов облегчает удержание внимания.",
        "how_more": "Оставить документ поверх всех окон на 10 минут.",
    },
    "self_criticism_to_instruction": {
        "skill_id": "self_criticism_to_instruction",
        "track": "anxiety",
        "week": 1,
        "variant": "core",
        "basis": "CBT / CFT",
        "name": "Самокритику — в инструкцию",
        "goal": "Перевести внутреннюю атаку в следующий конкретный шаг",
        "when_to_use": "Когда звучит «я ленивый», «я опять всё испортил» или «со мной что-то не так».",
        "real_life_example": "Заменить «я туплю» на «открой документ и напиши заголовок».",
        "steps": [
            "Поймай фразу самокритики",
            "Убери оценку личности",
            "Сформулируй команду к действию в одном предложении",
        ],
        "minimum_action": "Заменить одну самокритичную фразу на одну инструкцию",
        "why_short": "Критика забирает энергию, инструкция возвращает действие.",
        "why_long": "Самокритика может казаться мотивацией, но часто усиливает угрозу и избегание. Инструкция оставляет только следующий шаг и уменьшает эмоциональный шум.",
        "if_boring_response": "Да, это звучит сухо. Инструкция и должна быть простой, без драматургии.",
        "if_hard_response": "Не ищи идеальную формулировку. Напиши: «следующий шаг — открыть». Этого достаточно.",
        "if_skeptic_response": "Мы не спорим с мыслью и не делаем позитивные аффирмации. Мы превращаем шум в действие.",
        "if_failed_response": "Если критика победила, уменьшаем: просто подпиши её «это критика».",
        "if_dont_understand_response": "Возьми ругательную мысль и перепиши её как инструкцию: что сделать руками дальше?",
        "coach_feedback": "Хорошо: меньше атаки, больше управляемого следующего шага.",
        "trainer_variants": {
            "beck": "Оценка личности не даёт инструкции. Нам нужна операционная команда.",
            "skinny": "Не «я ужасный». Пиши: что сделать дальше.",
            "marsha": "Давай уберём нападение на себя и оставим только маленькую подсказку к действию.",
        },
        "simple": ["Поймай самокритику", "Убери оценку", "Напиши инструкцию"],
        "how": "Поймай фразу самокритики → убери оценку личности → сформулируй инструкцию.",
        "minimum": "Одна фраза-инструкция.",
        "explain": "Инструкция снижает стыд и возвращает действие.",
        "how_more": "«Я туплю» → «открыть документ и написать заголовок».",
    },
    "crisis_grounding": {
        "skill_id": "crisis_grounding",
        "track": "anxiety",
        "week": 1,
        "variant": "core",
        "name": "Заземление в кризисе",
        "goal": "Помочь пережить пик перегруза через контакт с телом и средой",
        "when_to_use": "Когда тревога, паника, перегруз или импульс бросить всё слишком сильные.",
        "real_life_example": "Поставить стопы на пол, назвать 5 предметов вокруг и сделать один медленный выдох.",
        "steps": [
            "Поставь стопы на пол и почувствуй опору",
            "Назови 5 предметов, которые видишь",
            "Сделай длинный выдох и выбери один безопасный следующий шаг",
        ],
        "minimum_action": "Поставить стопы на пол и назвать один предмет",
        "why_short": "Контакт с телом и средой может дать нервной системе сигнал относительной безопасности.",
        "why_long": "В остром перегрузе мышление может сужаться, а тело остаётся доступным каналом стабилизации. Сенсорная опора иногда снижает возбуждение настолько, чтобы выбрать следующий безопасный маленький шаг.",
        "if_boring_response": "Да, это базово. В кризисе базовые действия работают лучше сложных решений.",
        "if_hard_response": "Сделай только стопы на пол. Больше ничего не требуется.",
        "if_skeptic_response": "Это не решает всю проблему. Это снижает пик, чтобы ты мог сделать следующий безопасный шаг.",
        "if_failed_response": "Ок. Тогда только один контакт: стопы на пол или ладонь на стол.",
        "if_dont_understand_response": "Нужно вернуть внимание в тело и комнату: стопы, предметы вокруг, длинный выдох.",
        "coach_feedback": "Сначала стабилизация, потом решение. Один контакт с опорой уже считается.",
        "trainer_variants": {
            "beck": "При перегрузе начинаем с сенсорной стабилизации: это снижает физиологическое возбуждение.",
            "skinny": "Стопы на пол. Пять предметов. Выдох. Потом один шаг.",
            "marsha": "Сейчас не нужно решать всё. Давай сначала почувствуем опору и сделаем один выдох.",
        },
        "simple": ["Стопы на пол", "Назови 5 предметов", "Длинный выдох"],
        "how": "Поставь стопы на пол → назови 5 предметов → сделай длинный выдох.",
        "minimum": "Стопы на пол и один предмет.",
        "explain": "Сенсорная опора может снизить пик возбуждения.",
        "how_more": "Стопы на полу, пять предметов вокруг, длинный выдох и один безопасный шаг.",
    },
}


CORE_BASE_SKILLS = {
    "bad_first_step": {
        "skill_id": "bad_first_step",
        "track": "shame",
        "week": 1,
        "variant": "core",
        "basis": "CBT / perfectionism",
        "name": "Плохой черновик",
        "goal": "Обойти перфекционизм через намеренно черновой вход",
        "when_to_use": "Когда стопорит риск сделать плохо, неидеально или не так, как надо.",
        "real_life_example": "Написать плохой первый абзац, черновой заголовок или кривой первый вариант решения.",
        "steps": [
            "Открой место письма или задачи",
            "Напиши одну плохую первую фразу",
            "Не отправляй и не исправляй",
        ],
        "minimum_action": "Одно плохое предложение",
        "why_short": "Черновик снижает угрозу оценки и запускает действие.",
        "why_long": "Перфекционизм часто блокирует не работу, а первый видимый след. Плохой первый шаг отделяет запуск от качества и даёт материал для правки.",
        "if_boring_response": "Да. Черновик не должен вдохновлять. Он должен существовать.",
        "if_hard_response": "Сделай ещё хуже и меньше: одно плохое слово уже вход.",
        "if_skeptic_response": "Мы не выбираем плохой результат. Мы выбираем плохой вход, чтобы появился материал.",
        "if_failed_response": "Значит, плохой шаг всё ещё слишком большой. Напиши только название черновика.",
        "if_dont_understand_response": "Сделай версию, которую не надо показывать никому. Только чтобы начать.",
        "coach_feedback": "Черновик появился — перфекционизм уже не держит вход полностью.",
        "trainer_variants": {
            "beck": "Качество проверяем позже. Сейчас задача — создать первый редактируемый след.",
            "skinny": "Сделай плохо. Потом поправишь. Сейчас нужен вход.",
            "marsha": "Можно начать с неровного черновика. Он не обязан быть хорошим.",
        },
        "simple": ["Выбери кусок", "Разреши черновик", "Сделай плохую версию"],
        "how": "Выбери маленький кусок → разреши плохой черновик → сделай 60–90 секунд плохой версии.",
        "minimum": "Одна плохая строка.",
        "explain": "Плохой черновик снижает страх оценки.",
        "how_more": "Не писать идеальное письмо — написать плохой первый вариант приветствия.",
    },
    "visible_next_step": {
        "skill_id": "visible_next_step",
        "track": "procrastination",
        "week": 1,
        "variant": "core",
        "basis": "ADHD external cue",
        "name": "Видимый следующий шаг",
        "goal": "Сделать вход в задачу заметным во внешней среде",
        "when_to_use": "Когда задача пропадает из внимания или возвращение требует слишком много вспоминания.",
        "real_life_example": "Оставить документ открытым, положить блокнот на стол или закрепить вкладку с задачей.",
        "steps": [
            "Выбери один следующий физический шаг",
            "Сделай его видимым в комнате или на экране",
            "Оставь подсказку там, где ты её точно увидишь",
        ],
        "minimum_action": "Открыть документ или положить предмет задачи на видное место",
        "why_short": "Внешняя подсказка снижает стоимость возвращения.",
        "why_long": "ADHD-внимание хуже держит намерение в голове. Видимый шаг переносит намерение во внешнюю среду и облегчает повторный вход.",
        "if_boring_response": "Да, это не работа. Это подготовка входа, чтобы работа началась легче.",
        "if_hard_response": "Сделай одну подсказку: открыть вкладку или положить лист на стол.",
        "if_skeptic_response": "Это не мотивация. Это снижение трения между тобой и первым действием.",
        "if_failed_response": "Ок. Тогда только назови, что должно быть видно.",
        "if_dont_understand_response": "Нужно оставить внешний след: что именно делать дальше, чтобы не держать это в голове.",
        "coach_feedback": "Вход стал видимым. Мозгу не надо заново искать задачу.",
        "trainer_variants": {
            "beck": "Внешняя подсказка уменьшает нагрузку на рабочую память.",
            "skinny": "Не держи в голове. Оставь на виду.",
            "marsha": "Сделай так, чтобы задача мягко напомнила о себе сама.",
        },
        "simple": ["Выбери следующий шаг", "Сделай его видимым", "Оставь подсказку"],
        "how": "Выбери шаг → вынеси его на экран или стол → оставь до следующего входа.",
        "minimum": "Одна видимая подсказка.",
        "explain": "Внешняя подсказка заменяет удержание намерения в голове.",
        "how_more": "Оставить файл открытым на нужном месте, чтобы завтра не искать вход.",
    },
    "phone_far_3min": {
        "skill_id": "phone_far_3min",
        "track": "distractibility",
        "week": 1,
        "variant": "core",
        "basis": "ADHD stimulus control",
        "name": "Телефон далеко на 3 минуты",
        "goal": "Снизить самый быстрый источник отвлечения на короткий вход",
        "when_to_use": "Когда рука сама тянется к телефону перед стартом или во время задачи.",
        "real_life_example": "Положить телефон в другую комнату и 3 минуты держать открытой только задачу.",
        "steps": [
            "Положи телефон вне досягаемости руки",
            "Открой только место задачи",
            "Держись 3 минуты без проверки телефона",
        ],
        "minimum_action": "Положить телефон экраном вниз на расстояние вытянутой руки",
        "why_short": "Дистанция добавляет паузу между импульсом и действием.",
        "why_long": "Телефон даёт быстрый стимул и перехватывает старт. Физическая дистанция снижает автоматизм и даёт окну входа шанс начаться.",
        "if_boring_response": "Да. Три минуты без телефона — это тренировка среды, не характера.",
        "if_hard_response": "Не убирай далеко. Просто переверни экраном вниз на 60 секунд.",
        "if_skeptic_response": "Мы не запрещаем телефон на всю жизнь. Мы создаём короткий коридор для входа.",
        "if_failed_response": "Ок. Тогда только переверни телефон экраном вниз.",
        "if_dont_understand_response": "Нужно убрать самый быстрый отвлекающий стимул на 3 минуты.",
        "coach_feedback": "Среда стала тише. Теперь входу легче конкурировать.",
        "trainer_variants": {
            "beck": "Стимул-контроль работает через задержку доступа к отвлечению.",
            "skinny": "Телефон дальше. Три минуты. Всё.",
            "marsha": "Давай дадим себе маленькое тихое окно без телефона.",
        },
        "simple": ["Убери телефон", "Открой задачу", "3 минуты без проверки"],
        "how": "Положи телефон дальше → открой задачу → 3 минуты без проверки.",
        "minimum": "Телефон экраном вниз на 60 секунд.",
        "explain": "Дистанция снижает автоматическое отвлечение.",
        "how_more": "Положить телефон в коридор и открыть документ на 3 минуты.",
    },
    "restart_after_slip": {
        "skill_id": "restart_after_slip",
        "track": "procrastination",
        "week": 1,
        "variant": "core",
        "basis": "DBT / relapse repair",
        "name": "Возврат после выпадения",
        "goal": "Вернуться без наказания после срыва, паузы или залипания",
        "when_to_use": "Когда выпал из задачи и хочется добить себя или бросить день.",
        "real_life_example": "После залипания сказать «возврат» и открыть задачу на 60 секунд.",
        "steps": [
            "Назови факт: я выпал",
            "Убери наказание и объяснения",
            "Вернись к самому маленькому шагу на 60 секунд",
        ],
        "minimum_action": "Сказать «возврат» и открыть место задачи",
        "why_short": "Возврат важнее идеальной непрерывности.",
        "why_long": "Пауза часто становится длиннее, когда включается самонаказание. Нейтральный возврат помогает разорвать цепочку выпадение → стыд → новое избегание.",
        "if_boring_response": "Да. Возврат выглядит скучно. Зато он чинит день.",
        "if_hard_response": "Не возвращайся к работе. Только открой место задачи.",
        "if_skeptic_response": "Цель не стереть срыв, а не дать ему забрать весь день.",
        "if_failed_response": "Ок. Тогда просто напиши сюда: «я выпал». Это уже возврат к фактам.",
        "if_dont_understand_response": "Не анализируй срыв. Назови его и сделай маленький вход обратно.",
        "coach_feedback": "Возврат засчитан. Это ключевой навык устойчивости.",
        "trainer_variants": {
            "beck": "Нейтральный возврат уменьшает цену ошибки и сохраняет поведенческую цепочку.",
            "skinny": "Выпал — вернулся. Без суда.",
            "marsha": "Можно вернуться мягко. Срыв не обязан забирать весь день.",
        },
        "simple": ["Назови выпадение", "Без наказания", "Вернись к микро-шагу"],
        "how": "Назови факт → не ругай себя → вернись к микро-шагу на 60 секунд.",
        "minimum": "Сказать «возврат» и открыть задачу.",
        "explain": "Нейтральный возврат сокращает срыв.",
        "how_more": "После телефона открыть документ и сделать один маленький шаг без разбирательства.",
    },
    "check_the_facts_light": {
        "skill_id": "check_the_facts_light",
        "track": "anxiety",
        "week": 1,
        "variant": "core",
        "basis": "DBT / CBT",
        "name": "Факт против приговора",
        "goal": "Отделить проверяемый факт от жёсткого вывода",
        "when_to_use": "Когда мысль звучит как приговор: провалю, поздно, я туплю, всё бессмысленно.",
        "real_life_example": "Заменить «я всё провалил» на «я не начал письмо; могу открыть черновик». ",
        "steps": [
            "Запиши жёсткую мысль одним предложением",
            "Выдели один проверяемый факт",
            "Сформулируй следующий маленький шаг из факта",
        ],
        "minimum_action": "Написать один факт без вывода о себе",
        "why_short": "Факт даёт действие, приговор только усиливает ступор.",
        "why_long": "DBT/CBT-проверка фактов снижает силу автоматического вывода. Когда остаётся факт, легче выбрать маленькое действие.",
        "if_boring_response": "Да, это сухо. Факт и должен быть без драматургии.",
        "if_hard_response": "Напиши только факт: что реально произошло, без вывода о себе.",
        "if_skeptic_response": "Мы не спорим с эмоцией. Мы отделяем факт, из которого можно действовать.",
        "if_failed_response": "Ок. Тогда просто зачеркни слово «всегда» или «никогда» в мысли.",
        "if_dont_understand_response": "Факт — это то, что можно снять на камеру. Приговор — вывод о тебе или будущем.",
        "coach_feedback": "Факт найден. Из него уже можно выбрать следующий шаг.",
        "trainer_variants": {
            "beck": "Отделяем наблюдение от вывода. Действие строится на наблюдении.",
            "skinny": "Факт отдельно. Приговор отдельно. Действуем из факта.",
            "marsha": "Давай мягко отделим то, что случилось, от жёсткого вывода о себе.",
        },
        "simple": ["Запиши мысль", "Найди факт", "Выбери шаг"],
        "how": "Запиши жёсткую мысль → выдели факт → выбери маленький шаг из факта.",
        "minimum": "Один факт без самоприговора.",
        "explain": "Факт снижает ступор и возвращает действие.",
        "how_more": "«Поздно» → факт: письмо не открыто → шаг: открыть черновик.",
    },
    "urge_surf_60": {
        "skill_id": "urge_surf_60",
        "track": "distractibility",
        "week": 1,
        "variant": "core",
        "basis": "DBT distress tolerance",
        "name": "Пережить импульс 60 секунд",
        "goal": "Не уходить в отвлечение на первом импульсе",
        "when_to_use": "Когда появляется сильный импульс открыть телефон, вкладку, еду или другое быстрое отвлечение.",
        "real_life_example": "Поймать желание открыть соцсеть, поставить 60 секунд и просто наблюдать волну импульса.",
        "steps": [
            "Назови импульс: меня тянет отвлечься",
            "Поставь таймер на 60 секунд",
            "Замечай волну в теле и не действуй до сигнала",
        ],
        "minimum_action": "10 секунд назвать импульс и не трогать телефон",
        "why_short": "Импульс поднимается и падает, если не кормить его сразу.",
        "why_long": "В distress tolerance важно пережить пик без автоматического действия. Даже 60 секунд создают зазор между импульсом и выбором.",
        "if_boring_response": "Да. Это тренировка паузы, не драматичный инсайт.",
        "if_hard_response": "Сократи до 10 секунд. Просто назови импульс.",
        "if_skeptic_response": "Мы не запрещаем отвлечение на всю жизнь. Мы тренируем одну паузу перед ним.",
        "if_failed_response": "Если ушёл — вернись и назови: «это был импульс». Уже данные.",
        "if_dont_understand_response": "Не борись с желанием. Наблюдай 60 секунд, не выполняя его.",
        "coach_feedback": "Пауза появилась. Это уже контроль над автоматизмом.",
        "trainer_variants": {
            "beck": "Пауза снижает автоматическое подкрепление отвлечения.",
            "skinny": "Импульс — не команда. 60 секунд держишь.",
            "marsha": "Давай просто переждём волну чуть-чуть, без борьбы с собой.",
        },
        "simple": ["Назови импульс", "Таймер 60 секунд", "Не действуй до сигнала"],
        "how": "Назови импульс → поставь 60 секунд → не действуй до сигнала.",
        "minimum": "10 секунд паузы.",
        "explain": "Пауза отделяет импульс от действия.",
        "how_more": "Перед соцсетью сказать «меня тянет» и подождать 60 секунд.",
    },
    "body_before_task": {
        "skill_id": "body_before_task",
        "track": "low_energy",
        "week": 1,
        "variant": "core",
        "basis": "ADHD / regulation",
        "name": "Сначала тело, потом задача",
        "goal": "Снизить физиологический перегруз перед входом",
        "when_to_use": "Когда тело зажато, пусто, сонно или слишком возбуждённо для старта.",
        "real_life_example": "Встать, выпить воды, сделать один длинный выдох и открыть документ.",
        "steps": [
            "Сделай один телесный сброс: вода, выдох или встать",
            "Назови задачу одним словом",
            "Открой место задачи на 30 секунд",
        ],
        "minimum_action": "Один длинный выдох перед задачей",
        "why_short": "Тело меняет уровень готовности быстрее, чем уговоры.",
        "why_long": "При ADHD и перегрузе старт часто блокируется состоянием тела. Мини-регуляция снижает шум и делает первый шаг доступнее.",
        "if_boring_response": "Да. Вода и выдох — не магия. Это вход в рабочее состояние.",
        "if_hard_response": "Только один выдох. Потом решим про задачу.",
        "if_skeptic_response": "Мы не лечим тело. Мы чуть снижаем шум перед входом.",
        "if_failed_response": "Ок. Тогда просто поставь стопы на пол.",
        "if_dont_understand_response": "Сначала маленький телесный сброс, потом один микро-вход в задачу.",
        "coach_feedback": "Состояние чуть сдвинуто. Теперь вход ближе.",
        "trainer_variants": {
            "beck": "Регуляция тела снижает стартовую цену действия.",
            "skinny": "Вода. Выдох. Открыл задачу.",
            "marsha": "Давай сначала немного поможем телу, потом мягко зайдём в задачу.",
        },
        "simple": ["Вода/выдох/встать", "Назови задачу", "Открой на 30 секунд"],
        "how": "Сделай телесный сброс → назови задачу → открой место задачи.",
        "minimum": "Один длинный выдох.",
        "explain": "Регуляция тела снижает цену старта.",
        "how_more": "Выпить воды, поставить стопы на пол, открыть файл.",
    },
    "minimum_viable_day": {
        "skill_id": "minimum_viable_day",
        "track": "burnout",
        "week": 1,
        "variant": "core",
        "basis": "DBT / burnout aware",
        "name": "Минимально жизнеспособный день",
        "goal": "Сохранить день без добивания себя при низком ресурсе",
        "when_to_use": "Когда ресурс низкий, а план дня уже выглядит невозможным.",
        "real_life_example": "Выбрать один обязательный минимум: отправить одно сообщение, оплатить один счёт, открыть один документ.",
        "steps": [
            "Назови один минимум, который удержит день",
            "Отрежь всё, что не критично сегодня",
            "Сделай минимум или его первую минуту",
        ],
        "minimum_action": "Назвать один минимум дня",
        "why_short": "Минимум снижает перегруз и защищает от полного срыва.",
        "why_long": "DBT-подход в низком ресурсе: не требовать максимум, а выбрать действие, которое сохраняет направление и не усиливает истощение.",
        "if_boring_response": "Да. Минимум не вдохновляет. Он сохраняет день.",
        "if_hard_response": "Сократи минимум до одного сообщения или одного открытия.",
        "if_skeptic_response": "Это не капитуляция. Это стратегия, чтобы не сжечь систему.",
        "if_failed_response": "Если минимум не пошёл, назови ещё меньший: что удержит день на 1%?",
        "if_dont_understand_response": "Выбери не идеальный день, а минимальный день, который не разрушит завтра.",
        "coach_feedback": "Минимум выбран. Это уже меньше хаоса и больше управления.",
        "trainer_variants": {
            "beck": "При низком ресурсе оптимизируем не продуктивность, а сохранение траектории.",
            "skinny": "Не геройствуй. Один минимум. Сделал — день удержан.",
            "marsha": "Сегодня можно не тащить всё. Давай выберем один бережный минимум.",
        },
        "simple": ["Назови минимум", "Отрежь лишнее", "Сделай первую минуту"],
        "how": "Назови минимум → отрежь некритичное → сделай минимум или первую минуту.",
        "minimum": "Один минимум дня.",
        "explain": "Минимум защищает от полного срыва.",
        "how_more": "Если весь отчёт невозможен — открыть файл и написать один пункт.",
    },
    "body_doubling_plan": {
        "skill_id": "body_doubling_plan",
        "track": "procrastination",
        "week": 1,
        "variant": "core",
        "basis": "ADHD skills training",
        "name": "Запуск рядом с человеком",
        "goal": "Использовать присутствие другого человека для старта",
        "when_to_use": "Когда одному начинать тяжело, а рядом с человеком или на созвоне вход становится легче.",
        "real_life_example": "Написать другу: «посиди со мной 15 минут в созвоне, я открою задачу». ",
        "steps": [
            "Выбери человека или формат: созвон, коворкинг, чат",
            "Попроси короткое присутствие без контроля",
            "На старте сделай только первый маленький шаг",
        ],
        "minimum_action": "Написать одному человеку просьбу о 10–15 минутах рядом",
        "why_short": "Внешнее присутствие снижает стартовое трение.",
        "why_long": "Body doubling — базовый ADHD-навык: другой человек создаёт внешний контур внимания и облегчает вход без давления дисциплины.",
        "if_boring_response": "Да. Это практично, не красиво. Работает через внешний старт.",
        "if_hard_response": "Не проси созвон. Просто напиши: «можно я рядом поработаю 10 минут?»",
        "if_skeptic_response": "Это не зависимость. Это внешний костыль для запуска, как календарь или заметка.",
        "if_failed_response": "Если никого нет, включи тихий коворкинг/стрим и открой задачу.",
        "if_dont_understand_response": "Нужен человек рядом не для контроля, а чтобы легче начать.",
        "coach_feedback": "Ты строишь внешний запуск. Это нормальный ADHD-инструмент.",
        "trainer_variants": {
            "beck": "Body doubling создаёт внешний контекст удержания внимания.",
            "skinny": "Один созвон. Один старт. Не усложняй.",
            "marsha": "Можно начинать не в одиночку. Иногда рядом с кем-то правда легче.",
        },
        "simple": ["Выбери человека", "Попроси присутствие", "Сделай первый шаг"],
        "how": "Выбери человека или формат → попроси 10–15 минут рядом → начни первый шаг.",
        "minimum": "Одно сообщение с просьбой.",
        "explain": "Присутствие другого облегчает запуск.",
        "how_more": "Созвон без разговоров: оба работают 15 минут, ты открываешь задачу.",
    },
    "if_then_plan": {
        "skill_id": "if_then_plan",
        "track": "procrastination",
        "week": 1,
        "variant": "core",
        "basis": "CBT / implementation intention",
        "name": "Если–то план",
        "goal": "Заранее связать триггер с маленьким действием",
        "when_to_use": "Когда хороший план исчезает в моменте и нужно готовое правило входа.",
        "real_life_example": "Если я закрываю завтрак, то открываю документ на 60 секунд.",
        "steps": [
            "Выбери конкретный триггер: время, место или событие",
            "Привяжи к нему маленькое действие",
            "Запиши фразу: если X, то Y",
        ],
        "minimum_action": "Написать одну фразу если–то",
        "why_short": "Готовое правило снижает количество решений в момент старта.",
        "why_long": "Implementation intention заранее связывает ситуацию и действие. Это помогает не заново решать, когда мозг уже перегружен.",
        "if_boring_response": "Да. Правило должно быть простым, иначе оно не сработает.",
        "if_hard_response": "Сделай только фразу: если сяду за стол, открою файл.",
        "if_skeptic_response": "Это не обещание идеального выполнения. Это готовый рельс для первого шага.",
        "if_failed_response": "Если правило не сработало, сделай триггер заметнее или действие меньше.",
        "if_dont_understand_response": "Формула простая: если случается X, я делаю маленький Y.",
        "coach_feedback": "Правило готово. Теперь старт меньше зависит от настроения.",
        "trainer_variants": {
            "beck": "Если–то правило снижает нагрузку выбора в момент действия.",
            "skinny": "Если X — делаешь Y. Без переговоров.",
            "marsha": "Давай сделаем маленький рельс, чтобы старт не приходилось каждый раз придумывать заново.",
        },
        "simple": ["Выбери триггер", "Выбери маленькое действие", "Запиши если–то"],
        "how": "Выбери триггер → выбери маленькое действие → запиши: если X, то Y.",
        "minimum": "Одна фраза если–то.",
        "explain": "Правило снижает количество решений перед стартом.",
        "how_more": "Если включаю ноутбук утром, то открываю тикет на 60 секунд.",
    },
}

CORE_LAUNCH_WEEK_SKILLS.update(CORE_BASE_SKILLS)

SKILLS_DB.update(CORE_LAUNCH_WEEK_SKILLS)


SKILLS = {
    "open_without_timer": {
        "title": "Открыть без таймера",
        "text": """
🧩 Навык дня: Открыть без таймера

Сейчас не нужно работать.
Нужно только снизить стоимость входа.

Сделай:
1. Открой место задачи.
2. Не работай.
3. Назови следующий физический шаг.

Минимум:
открыть место задачи на 10 секунд.
""".strip(),
    },
    "name_task_one_word": {
        "title": "Назвать задачу одним словом",
        "text": """
🧩 Навык дня: Назвать задачу одним словом

Когда задача слишком большая, мозг избегает даже контакта с ней.
Сейчас делаем минимальный контакт.

Сделай:
1. Не открывай задачу.
2. Напиши одно слово, которое её обозначает.
3. Остановись.

Минимум:
одно слово.
""".strip(),
    },
    "phone_away_3_min": {
        "title": "Телефон вне руки на 3 минуты",
        "text": """
🧩 Навык дня: Телефон вне руки на 3 минуты

Если есть залипание, сначала работаем не с задачей, а со средой.

Сделай:
1. Положи телефон вне досягаемости руки.
2. Открой место задачи.
3. Побудь рядом с задачей 3 минуты.

Минимум:
положить телефон экраном вниз на расстояние вытянутой руки.
""".strip(),
    },
    "bad_draft": {
        "title": "Плохой черновик",
        "text": """
🧩 Навык дня: Плохой черновик

Цель не качество.
Цель — разрушить заморозку.

Сделай:
1. Открой место задачи.
2. Напиши заголовок: “Плохой черновик”.
3. Напиши 3 плохих тезиса.
4. Не редактируй.

Минимум:
одно плохое предложение.
""".strip(),
    },
    "body_first": {
        "title": "Сначала тело, потом задача",
        "text": """
🧩 Навык дня: Сначала тело, потом задача

Если нет сил, вход через задачу может быть слишком тяжёлым.
Сначала даём телу сигнал безопасности.

Сделай:
1. Сделай длинный выдох.
2. Выпей воды или встань.
3. Назови задачу одним словом.

Минимум:
один длинный выдох.
""".strip(),
    },
    "one_visible_step": {
        "title": "Один видимый шаг",
        "text": """
🧩 Навык дня: Один видимый шаг

Когда вариантов много, старт ломается на выборе.
Сейчас оставляем только один следующий шаг.

Сделай:
1. Выпиши 3 возможных шага.
2. Зачеркни два.
3. Оставь один.
4. Сделай только его начало.

Минимум:
выбрать один шаг.
""".strip(),
    },
    "external_start": {
        "title": "Внешний старт",
        "text": """
🧩 Навык дня: Внешний старт

Иногда задача не запускается в одиночку.
Тогда используем опору, а не стыд.

Сделай:
1. Выбери одного человека.
2. Напиши ему: “Я застрял. Сейчас сделаю 3 минуты задачи и отпишусь”.
3. Сделай маленький вход.

Минимум:
написать сообщение, но не отправлять.
""".strip(),
    },
}


def _product_skill_to_db_entry(skill_id: str, data: dict) -> dict:
    text = (data.get("text") or "").strip()
    minimum = ""
    if "Минимум:" in text:
        minimum = text.split("Минимум:", 1)[1].strip()
    return {
        "skill_id": skill_id,
        "track": "procrastination",
        "week": 1,
        "variant": "product",
        "name": data.get("title") or skill_id,
        "title": data.get("title") or skill_id,
        "daily_text": text,
        "how": text,
        "minimum": minimum,
        "minimum_action": minimum,
        "simple": [],
        "explain": "Навык снижает стоимость входа и помогает не застревать в одном и том же сценарии.",
        "why_short": "Даём другой вход, чтобы не крутить один и тот же навык.",
    }


SKILLS_DB.update({sid: _product_skill_to_db_entry(sid, data) for sid, data in SKILLS.items()})

# PATCH 2 — Функция построения плана на 4 недели
def build_4_week_plan(track: str) -> list:
    """
    Возвращает список ID навыков на 4 недели
    """
    skills = [k for k, v in SKILLS_DB.items() if v["track"] == track]
    skills_sorted = sorted(skills, key=lambda x: SKILLS_DB[x]["week"])
    return skills_sorted

# PATCH 3 — Генерация карты месяца
def generate_month_map(track: str) -> str:
    weeks = {}
    for sid, data in SKILLS_DB.items():
        if data["track"] == track:
            weeks.setdefault(data["week"], []).append(data["name"])
    text = "🗺 План на 4 недели:\n\n"
    for w in sorted(weeks.keys()):
        text += f"Неделя {w}:\n"
        for name in weeks[w]:
            text += f"• {name}\n"
        text += "\n"
    return text

PROFILE_PROMOTED_SMALL_STEP_SKILLS = ["open_only", "task_naming", "visible_next_step", "ninety_sec_start"]
PROFILE_ATTENTION_SKILLS = ["one_tab_focus", "phone_far_3min", "visible_next_step", "urge_surf_60"]
PROFILE_BODY_DOUBLING_SKILLS = ["body_doubling_plan"]
PROFILE_LONG_OR_TIMER_SKILLS = ["ninety_sec_start", "urge_surf_60"]


def _profile_from_user_state(u: dict) -> dict:
    raw = (u or {}).get("profile_json") or (u or {}).get("profile") or {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _move_skills_to_front(plan: list, skill_ids: list) -> list:
    front = [sid for sid in skill_ids if sid in plan and sid in SKILLS_DB]
    rest = [sid for sid in plan if sid not in front]
    return front + rest


def _move_skills_to_back(plan: list, skill_ids: list) -> list:
    back_set = {sid for sid in skill_ids if sid in SKILLS_DB}
    front = [sid for sid in plan if sid not in back_set]
    back = [sid for sid in plan if sid in back_set]
    return front + back


def adapt_plan_to_profile(plan: list, u_or_profile: dict) -> list:
    """Personalize plan order from the hidden profile prompt signals.

    The user never sees `profile_prompt`; it quietly biases skill choice away from
    failed formats and toward strategies that already worked.
    """
    safe_plan = [sid for sid in (plan or []) if sid in SKILLS_DB]
    if not safe_plan:
        return safe_plan

    profile = _profile_from_user_state(u_or_profile)
    if not profile and isinstance(u_or_profile, dict) and ("profile_prompt" in u_or_profile or "successful_skills" in u_or_profile):
        profile = u_or_profile
    if not profile:
        return safe_plan

    successful = [str(x) for x in profile.get("successful_skills") or [] if x in SKILLS_DB]
    failed = [str(x) for x in profile.get("failed_skills") or [] if x in SKILLS_DB]
    best = [str(x) for x in (profile.get("best_skill"), profile.get("last_successful_skill"), profile.get("recommended_variant")) if x in SKILLS_DB]
    worst = [str(x) for x in (profile.get("worst_skill"), profile.get("failed_skill")) if x in SKILLS_DB]

    adapted = list(safe_plan)
    # If smaller steps worked or failures show the entry is too big, bias toward low-friction starts.
    if int(profile.get("downscale_count") or 0) > 0 or profile.get("needs_downscale") or profile.get("downscale_pattern"):
        adapted = _move_skills_to_front(adapted, PROFILE_PROMOTED_SMALL_STEP_SKILLS)
        adapted = _move_skills_to_back(adapted, PROFILE_LONG_OR_TIMER_SKILLS)

    # If body doubling worked, keep it visible earlier in the route.
    if profile.get("preferred_activation") == "body_doubling" or "body_doubling_plan" in successful:
        adapted = _move_skills_to_front(adapted, PROFILE_BODY_DOUBLING_SKILLS)

    # Attention escape signals bias toward attention container skills.
    if profile.get("attention_pattern") == "scroll_autopilot" or int(profile.get("attention_escape_count") or 0) > 0:
        adapted = _move_skills_to_front(adapted, PROFILE_ATTENTION_SKILLS)

    adapted = _move_skills_to_front(adapted, [*best, *successful])
    # Do not remove failed skills forever; just postpone them unless they also succeeded later.
    adapted = _move_skills_to_back(adapted, [sid for sid in [*worst, *failed] if sid not in successful and sid not in best])
    return adapted


# PATCH 4 — Override после кризиса (замена навыка)
def suggest_alternative_skill(track: str, current_skill: str, profile: dict | None = None):
    alternatives = [k for k, v in SKILLS_DB.items() if v["track"] == track and k != current_skill]
    if not alternatives:
        return None
    if profile:
        alternatives = adapt_plan_to_profile(alternatives, profile)
        alternatives = [sid for sid in alternatives if sid != current_skill]
    return alternatives[0] if alternatives else None

# PATCH 5 — Улучшенная подача навыков по стилю
def format_skill(skill_id: str, trainer_key: str):
    skill = SKILLS_DB[skill_id]
    raw_steps = skill.get("simple") or skill.get("steps")
    if not raw_steps:
        # Fallback to single-step description if no structured steps provided
        raw_steps = [skill.get("how") or ""]
    steps = "\n".join([f"{i+1}. {s}" for i, s in enumerate(raw_steps) if s])
    explain = skill.get("explain", "")
    if trainer_key == "skinny":
        return f"🧩 {skill['name']}\n\n{steps}\n\nЗачем: {explain}"
    if trainer_key == "marsha":
        return (
            f"🧩 {skill['name']}\n\n{steps}\n\n"
            f"Зачем: {explain}\n\n"
            "Ты справишься. Маленький шаг — уже шаг."
        )
    if trainer_key == "beck":
        logic = explain or skill.get("goal", "")
        return (
            f"🧩 {skill['name']}\n"
            f"Почему работает: {logic}\n\n"
            f"Шаги:\n{steps}\n\n"
            f"Минимум: {skill.get('minimum', '')}"
        )
    return f"🧩 {skill['name']}\n\n{steps}\n\nЗачем: {explain}"
