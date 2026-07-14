"""케이스 실행기 (CLI) + output/ 중간산출물 덤프.

기본: 케이스5 골든셋을 현재 검색기(config.RETRIEVER)로 분류.
    python src/run.py
    python src/run.py data/scenarios/case05_humane_city.json

output/ 에 사람이 눈으로 볼 3종을 남긴다:
    <case>_<retriever>_retrieve.json  — 1차가 뭘 건졌나
    <case>_<retriever>_result.json    — 2차 LLM 결과
    <case>_<retriever>_run.json       — 둘 합본 + 정답
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from config import OUTPUT_DIR, RETRIEVER, SCENARIO_DIR
from pipeline import classify_book
from schema import BookInput

_DEFAULT = SCENARIO_DIR / "case05_humane_city.json"


def _load(path: Path) -> tuple[BookInput, dict, str]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    book = BookInput(**data["input"]) if "input" in data else BookInput(**data)
    expected = data.get("expected", {})
    return book, expected, path.stem


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT
    book, expected, stem = _load(path)

    print(f"\n📖 분류 대상: 「{book.title}」  (082={book.ddc_082 or '없음'}, 검색기={RETRIEVER})\n")
    out = classify_book(book)

    for m in out.retrieve.messages:
        print(f"  → {m}")

    print("\n🔎 1차 후보 번호 (주제 파악용 샘플):")
    for c in out.retrieve.candidates:
        tag = " ★082" if c.is_082_prior else ""
        print(f"   {c.ddc_h}{tag}  [매칭 {c.title_hit_count} · 서가 {c.shelf_count}(샘플)]  "
              f"예: {', '.join(c.shelf_sample[:3])}")

    print("\n📚 2차 ▼h 후보 (LLM):")
    for i, cand in enumerate(out.result.candidates, 1):
        print(f"  [{i}] {cand.h}  {cand.label}  (적합도 {cand.confidence:.2f}, {cand.source.value})")
        print(f"      근거: {cand.reasoning}")

    if out.result.notes:
        print(f"\n🧠 종합 판단 근거: {out.result.notes}")
    print(f"   (모호도 ambiguity: {out.result.ambiguity:.2f})")

    if out.result.escalate:
        print(f"\n⚠️  HITL (신호: {', '.join(s.value for s in out.result.signals)})")
    elif out.result.candidates:
        print(f"\n✅ 추천(사서 검토): {out.result.candidates[0].h}")

    if expected:
        print(f"\n🎯 정답: 작성자확정={expected.get('writer_final')} "
              f"교열최종={expected.get('review_final')} 난이도={expected.get('difficulty')}")

    # ── output/ 덤프 ──
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    base = f"{stem}_{RETRIEVER}"
    (OUTPUT_DIR / f"{base}_retrieve.json").write_text(
        out.retrieve.model_dump_json(indent=2), encoding="utf-8")
    (OUTPUT_DIR / f"{base}_result.json").write_text(
        out.result.model_dump_json(indent=2), encoding="utf-8")
    (OUTPUT_DIR / f"{base}_run.json").write_text(
        json.dumps({
            "input": book.model_dump(),
            "expected": expected,
            "retrieve": out.retrieve.model_dump(),
            "result": out.result.model_dump(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n💾 output/{base}_*.json 저장\n")


if __name__ == "__main__":
    main()
