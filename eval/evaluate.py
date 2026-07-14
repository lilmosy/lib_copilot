"""골든셋 평가 하니스.

시나리오 골든셋(data/scenarios/*.json)을 현재 검색기(config.RETRIEVER)로 태우고,
예측 ▼h를 정답(교열 최종 / 작성자 확정)과 비교해 리포트한다.

지표(design.md):
  - exact_match : 예측 최상위 ▼h == 교열 최종
  - prefix_match: 앞 3자리(대분류/강목) 일치
  - topk_hit    : 후보 안에 교열 최종 포함 (상 케이스 핵심)
  - escalate    : 에스컬레이션 판정이 난이도와 맞았나

두 검색기 비교: LIBCOPILOT_RETRIEVER=token / embed 로 각각 돌려 비교한다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from config import RETRIEVER, SCENARIO_DIR  # noqa: E402
from pipeline import classify_book  # noqa: E402
from schema import BookInput  # noqa: E402


def _prefix(h: str, n: int = 3) -> str:
    return h.replace(".", "")[:n]


def evaluate_one(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    book = BookInput(**data["input"])
    expected = data.get("expected", {})
    gold = expected.get("review_final") or expected.get("writer_final")

    out = classify_book(book)
    cands = out.result.candidates
    pred = cands[0].h if cands else None
    cand_hs = [c.h for c in cands]

    return {
        "case_id": data.get("case_id"),
        "title": book.title,
        "gold": gold,
        "pred": pred,
        "candidates": cand_hs,
        "exact_match": pred == gold,
        "prefix_match": bool(pred and gold and _prefix(pred) == _prefix(gold)),
        "topk_hit": gold in cand_hs,
        "escalate": out.result.escalate,
        "difficulty": expected.get("difficulty"),
    }


def main() -> None:
    paths = (
        [Path(p) for p in sys.argv[1:]]
        if len(sys.argv) > 1
        else sorted(SCENARIO_DIR.glob("*.json"))
    )
    rows = [evaluate_one(p) for p in paths]

    n = len(rows)
    print(f"\n=== 평가 리포트 (검색기={RETRIEVER}, {n} 케이스) ===")
    for r in rows:
        mark = "✓" if r["exact_match"] else ("~" if r["prefix_match"] else "✗")
        esc = " [HITL]" if r["escalate"] else ""
        print(f" {mark} #{r['case_id']} {r['title']}: pred={r['pred']} "
              f"gold={r['gold']} 후보={r['candidates']}{esc}")

    if n:
        ex = sum(r["exact_match"] for r in rows) / n
        pf = sum(r["prefix_match"] for r in rows) / n
        tk = sum(r["topk_hit"] for r in rows) / n
        print(f"\n Exact={ex:.0%}  Prefix={pf:.0%}  Top-k={tk:.0%}")


if __name__ == "__main__":
    main()
