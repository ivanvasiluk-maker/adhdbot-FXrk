# Launch QA — SKILLER BOT

Перед закрытым платным тестом продукт **не расширяем**. Проходим этот список руками, запускаем на живых людях и дальше улучшаем только по retention, ошибкам, кликам на offer и реальным ответам.

## 17. Launch QA checklist

1. Run `/start` and choose each trainer. Check tone differs:
   - Скинни — коротко, жёстко, через действие.
   - Бек — логика, структура, проверка по действиям.
   - Марша — мягко, безопасно, без самонаказания.
2. Run text diagnosis with IT/perfectionism/body-doubling case. Check analysis is specific, not generic GPT text.
3. Click `📚 Подробнее`. It should expand the current explanation, not duplicate the same block.
4. Click `😑 Ты меня не понял`. It should ask what is wrong and rebuild analysis/skill/map, not defend the old answer.
5. Click `✅ Сделал`. Done-flow should be short: one approach counted, no questionnaire dump.
6. Click `🔁 Ещё круг` several times. After 3–4 rounds the bot should stop or show short progression.
7. Click `❌ Не сделал` → each reason. Responses must differ:
   - `😣 Слишком сложно` → smaller entry.
   - `😵 Нет сил` → body/energy.
   - `📱 Залип` → anti-scroll / one-tab / phone-away.
   - `🤔 Не понял` → simple explanation + example + physical step.
8. Test `📱 Залип` specifically: response must be anti-scroll, phone-away, or one-tab focused.
9. Test `😵 Нет сил` specifically: response must be body/energy focused.
10. Test crisis: flow must be calm → skill → solution, short and bodily.
11. Check one day = one core skill. New core skill appears only after 00:00 user timezone or in admin/test mode.
12. Check side skill appears max 1 per day and not after crisis/overload/downscale.
13. Check body-doubling signal is saved to `profile_json` as `preferred_activation = body_doubling` when user mentions coworking / call / another person nearby.
14. Enter `/test_access <TEST_CHEAT_CODE>` or the plain cheat code. Check `/set_day 3` / `/force_next_day` and `/show_offer` are available for that user without enabling global test mode.
15. Use `/set_day 3` and `/show_offer`. Check offer shows `€14.98` and sells personal system / action map, not “bot for a month”.
16. Check Sheets logs events without full personal texts, voice transcripts, crisis content, confessions, or medical details.

## 18. Final decision

После этого патча больше не расширять продукт до запуска.

Запустить на живых людях и собрать:

- retention;
- ошибки;
- клики на offer;
- реальные ответы пользователей;
- где люди бросают flow;
- какие навыки реально выполняют;
- какие причины fail нажимают чаще всего.

Дальше улучшать по данным, а не по фантазии.

## Definition of Done перед тестом

- Бот не ведёт свободный GPT-чат.
- Бот ведёт пользователя к действию.
- Sheets не тормозит ответ пользователю.
- Полный текст проблемы, транскрипты, кризисные сообщения, личные истории и медицинские детали не уходят в Sheets.
- Оплата появляется на day3 или вручную через `/show_offer` для админа.
- Есть test mode для прокликивания за один день.
- Тренеры говорят разным языком.
- Если сложно — бот уменьшает шаг, а не запускает новый разбор.
- Day3 offer продаёт персональную систему / рабочую карту действия за `€14.98`, а не “бота на месяц”.
