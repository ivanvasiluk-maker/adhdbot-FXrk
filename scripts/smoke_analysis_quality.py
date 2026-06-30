#!/usr/bin/env python3
"""Smoke check that first diagnosis produces a useful mechanism + Подробнее path."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from flows import (  # noqa: E402
    ai_analyze,
    ai_analyze_comprehensive,
    build_analysis_result,
    format_comprehensive_analysis,
    normalize_analysis,
    render_analysis_details_by_trainer,
    safe_analysis_memory,
)
from texts import preliminary_diagnosis_conclusion_text  # noqa: E402

SAMPLE = """Я журналист. Мне нужно сдать статью сегодня, а я завис. Уже два часа открываю документ и закрываю. Внутри паника: “сейчас все поймут, что я слабый автор”.

Я не пишу не потому, что не знаю тему, а потому что боюсь осуждения. Каждое предложение кажется тупым. Я представляю, как редактор читает и думает: “Зачем мы вообще с ним работаем?”"""


async def main() -> None:
    quick = await ai_analyze(SAMPLE, None, "")
    comp = await ai_analyze_comprehensive(SAMPLE, "beck", None, "")
    comp = normalize_analysis(comp, SAMPLE, quick)
    comp.update(safe_analysis_memory(SAMPLE, comp))
    analysis_result = build_analysis_result(comp, SAMPLE)
    comp["analysis_result"] = analysis_result

    assert analysis_result["pattern"] == "perfectionism_visibility_fear", analysis_result
    evidence = analysis_result.get("evidence_signals") or []
    assert len(evidence) >= 4, evidence
    assert any("стать" in item for item in evidence), evidence
    assert any("страх оценки" in item for item in evidence), evidence

    analysis_text = format_comprehensive_analysis(comp, quick, "beck")
    details_text = render_analysis_details_by_trainer(comp, "beck")
    for marker in ("Коротко", "Моя рабочая гипотеза", "проверяем"):
        assert marker in analysis_text, analysis_text
    for marker in ("1. Что произошло", "7. Что даёт избегание прямо сейчас", "13. Почему выбран текущий навык", "14. Какие навыки могут быть следующими"):
        assert marker in details_text, details_text
    for marker in ("статью нужно сдать сегодня", "паника", "Плохой черновик"):
        assert marker in details_text, details_text

    conclusion_text = preliminary_diagnosis_conclusion_text(
        comp.get("specific_pattern") or "",
        comp.get("useful_signal") or "",
        comp.get("skills_focus") if isinstance(comp.get("skills_focus"), list) else [],
        analysis_result.get("first_check") or "",
        analysis_result.get("recommended_skill_reason") or "",
    )
    for marker in ("Короткое заключение", "Главный узел сейчас", "Ресурс, который уже есть", "Что проверим первым", "Почему выбран этот навык"):
        assert marker in conclusion_text, conclusion_text
    assert "1. Что произошло" not in conclusion_text, conclusion_text

    vague = "мне страшно ошибиться, когда думаю обо всём сразу тревожно, не могу выбрать с чего начать"
    vague_comp = normalize_analysis({}, vague, {})
    vague_comp.update(safe_analysis_memory(vague, vague_comp))
    vague_result = build_analysis_result(vague_comp, vague)
    vague_comp["analysis_result"] = vague_result
    vague_text = format_comprehensive_analysis(vague_comp, {}, "marsha")
    forbidden = ("письмо", "ноутбук", "дедлайн", "почта", "новости", "Telegram", "YouTube", "черновик")
    assert not any(word.lower() in vague_text.lower() for word in forbidden), vague_text
    assert "Пока" in vague_text or "гипотеза" in vague_text, vague_text

    print("[SMOKE] analysis quality OK")


if __name__ == "__main__":
    asyncio.run(main())
