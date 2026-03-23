# Предложить изменение плана на определённый день
def propose_plan_override(u: dict, day_number: int, new_skill_id: str):
    if new_skill_id not in SKILLS_DB:
        return
    overrides = json.loads(u.get("plan_overrides_json") or "{}") if u.get("plan_overrides_json") else {}
    overrides[str(day_number)] = new_skill_id
    u["plan_overrides_json"] = json.dumps(overrides, ensure_ascii=False)
# 4-недельные шаблоны по bucket (28 дней)
PROGRAM_TEMPLATES = {
    "anxiety": {
        1: ["a_w1_anchor_60_base", "a_w1_task_clarity_alt", "a_w1_anchor_60_base", "a_w1_task_clarity_alt", "a_w1_anchor_60_base", "a_w1_task_clarity_alt", "a_w1_anchor_60_base"],
        2: ["a_w2_notice_thought_base", "a_w2_experiment_alt", "a_w2_notice_thought_base", "a_w2_experiment_alt", "a_w2_notice_thought_base", "a_w2_experiment_alt", "a_w2_notice_thought_base"],
        3: ["a_w3_separate_critic_base", "a_w3_support_alt", "a_w3_separate_critic_base", "a_w3_support_alt", "a_w3_separate_critic_base", "a_w3_support_alt", "a_w3_separate_critic_base"],
        4: ["a_w4_env_shield_base", "a_w4_weekly_review_alt", "a_w4_env_shield_base", "a_w4_weekly_review_alt", "a_w4_env_shield_base", "a_w4_weekly_review_alt", "a_w4_env_shield_base"],
    },
    "low_energy": {
        1: ["e_w1_resistance_timer_base", "e_w1_restore_one_alt", "e_w1_resistance_timer_base", "e_w1_restore_one_alt", "e_w1_resistance_timer_base", "e_w1_restore_one_alt", "e_w1_resistance_timer_base"],
        2: ["e_w2_micro_start_base", "e_w2_not_to_do_alt", "e_w2_micro_start_base", "e_w2_not_to_do_alt", "e_w2_micro_start_base", "e_w2_not_to_do_alt", "e_w2_micro_start_base"],
        3: ["e_w3_return_no_punish_base", "e_w3_support_alt", "e_w3_return_no_punish_base", "e_w3_support_alt", "e_w3_return_no_punish_base", "e_w3_support_alt", "e_w3_return_no_punish_base"],
        4: ["e_w4_env_shield_base", "e_w4_weekly_review_alt", "e_w4_env_shield_base", "e_w4_weekly_review_alt", "e_w4_env_shield_base", "e_w4_weekly_review_alt", "e_w4_env_shield_base"],
    },
    "distractibility": {
        1: ["d_w1_single_window_base", "d_w1_phone_out_alt", "d_w1_single_window_base", "d_w1_phone_out_alt", "d_w1_single_window_base", "d_w1_phone_out_alt", "d_w1_single_window_base"],
        2: ["d_w2_soft_return_base", "d_w2_capture_then_do_alt", "d_w2_soft_return_base", "d_w2_capture_then_do_alt", "d_w2_soft_return_base", "d_w2_capture_then_do_alt", "d_w2_soft_return_base"],
        3: ["d_w3_env_shield_base", "d_w3_return_no_punish_alt", "d_w3_env_shield_base", "d_w3_return_no_punish_alt", "d_w3_env_shield_base", "d_w3_return_no_punish_alt", "d_w3_env_shield_base"],
        4: ["d_w4_focus_block_20_base", "d_w4_weekly_review_alt", "d_w4_focus_block_20_base", "d_w4_weekly_review_alt", "d_w4_focus_block_20_base", "d_w4_weekly_review_alt", "d_w4_focus_block_20_base"],
    },
    "mixed": {
        1: ["m_w1_not_to_do_base", "m_w1_clarity_1line_alt", "m_w1_not_to_do_base", "m_w1_clarity_1line_alt", "m_w1_not_to_do_base", "m_w1_clarity_1line_alt", "m_w1_not_to_do_base"],
        2: ["m_w2_notice_thought_base", "m_w2_micro_start_alt", "m_w2_notice_thought_base", "m_w2_micro_start_alt", "m_w2_notice_thought_base", "m_w2_micro_start_alt", "m_w2_notice_thought_base"],
        3: ["m_w3_separate_critic_base", "m_w3_support_alt", "m_w3_separate_critic_base", "m_w3_support_alt", "m_w3_separate_critic_base", "m_w3_support_alt", "m_w3_separate_critic_base"],
        4: ["m_w4_env_shield_base", "m_w4_weekly_review_alt", "m_w4_env_shield_base", "m_w4_weekly_review_alt", "m_w4_env_shield_base", "m_w4_weekly_review_alt", "m_w4_env_shield_base"],
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
    "anxiety": ["notice_thought", "micro_start", "return_no_punish"],
    "low_energy": ["micro_start", "return_no_punish", "micro_start"],
    "distractibility": ["micro_start", "micro_start", "return_no_punish"],
    "mixed": ["return_no_punish", "micro_start", "notice_thought"],
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

    return safe_base
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

# PATCH 4 — Override после кризиса (замена навыка)
def suggest_alternative_skill(track: str, current_skill: str):
    alternatives = [k for k, v in SKILLS_DB.items() if v["track"] == track and k != current_skill]
    if not alternatives:
        return None
    return alternatives[0]

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
