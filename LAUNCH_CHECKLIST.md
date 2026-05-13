# LAUNCH CHECKLIST — SKILLER BOT

## Обязательная ручная проверка перед запуском

1. `/start` работает.
2. Имя сохраняется.
3. Выбор тренера работает.
4. Язык тренера отличается в skill cards.
5. Voice diagnosis работает.
6. Text diagnosis работает.
7. Quick test работает.
8. Wow analysis shown.
9. Details button works.
10. “Я не понимаю” works.
11. Skill card clean, no “Как: Почему работает”.
12. “Сложно даже таймер” triggers downscale, not full analysis.
13. “Ты меня не понял” does not restart onboarding.
14. Карта 4 недель не показывается автоматически слишком рано.
15. Route shown by button.
16. Action done logs event.
17. Action failed logs event.
18. Downscale logs event.
19. Evening check-in works.
20. Day3 offer appears.
21. Payment click logs event.
22. `/testmode_on` works.
23. `/set_day 3` works.
24. `/show_offer` works.
25. Google Sheets sync works.
26. Crisis flow works.
27. Background ping does not spam.
28. Railway deploy starts.
29. No `TelegramConflictError`.
30. `/stats` works for admin.
31. `/health` works.

## Быстрый paid-flow smoke test

1. `/start`
2. Ввести имя.
3. Выбрать Бека.
4. Ответить на согласие уведомлений.
5. Пройти голосовую диагностику.
6. Посмотреть разбор.
7. Нажать «Подробнее».
8. Нажать «Я не понимаю».
9. Получить первый навык.
10. Написать «сложно даже таймер».
11. Проверить downscale.
12. Нажать «Сделал».
13. Проверить вечерний check-in.
14. `/testmode_on`
15. `/set_day 3`
16. `/show_offer`
17. Кликнуть `7 дней — €20`.
18. Проверить событие в SQLite.
19. `/sync_sheets`
20. Проверить строку в Google Sheets.
21. `/stats`

## Definition of Done перед тестом

- Бот не ведёт свободный GPT-чат.
- Бот ведёт пользователя к действию.
- Sheets не тормозит ответ пользователю.
- Полный текст проблемы, транскрипты, кризисные сообщения, личные истории и медицинские детали не уходят в Sheets.
- Оплата появляется на day3 или вручную через `/show_offer` для админа.
- Есть test mode для прокликивания за один день.
- Тренеры говорят разным языком.
- Если сложно — бот уменьшает шаг, а не запускает новый разбор.
