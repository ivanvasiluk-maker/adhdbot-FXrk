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
    for marker in ("Моя гипотеза", "Механизм", "Как справляемся", "плохой черновик"):
        assert marker in analysis_text, analysis_text
    for marker in ("Почему такая гипотеза", "Связка выглядит так", "Что проверяем"):
        assert marker in details_text, details_text

    print("[SMOKE] analysis quality OK")


if __name__ == "__main__":
    asyncio.run(main())
