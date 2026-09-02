"""책 한 권 분류해보기 (CLI). **채점하지 않는다.**

    cd real && python run.py mybook.json

골든셋 채점은 `../experiment/evaluate.py`가 한다. 여기서는 한 권의 결과와 근거만 출력한다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import decide
from config import RETRIEVER
from pipeline import classify_book
from schema import BookInput

def _load(path: Path) -> tuple[BookInput, dict, str, frozenset[str]]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    book = BookInput(**data["input"]) if "input" in data else BookInput(**data)
    expected = data.get("expected", {})
    return book, expected, path.stem, frozenset(data.get("holdout_ids") or ())


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("사용법: python run.py <book.json>")
    path = Path(sys.argv[1])
    book, expected, stem, holdout = _load(path)

    print(f"\n📖 분류 대상: 「{book.title}」  (082={book.ddc_082 or '없음'}, 검색기={RETRIEVER})")
    out = classify_book(book, holdout=holdout or None)

    dec, retr = out.decision, out.retrieve
    fits = dec.assessments          # 이미 shelf_fit 내림차순 (decide.rank)

    # ══ 결론 먼저 ══ (app.py와 같은 순서·같은 문구. 화면이 두 벌이 되면 안 된다)
    print("\n" + "═" * 64)
    if out.inherited:
        print(f"✅ 기존 서지를 승계했습니다.   ▼h = {fits[0].h}")
    elif dec.converged:
        print(f"✅ 1순위가 정해졌습니다.   ▼h = {fits[0].h}   "
              f"적합도 {fits[0].shelf_fit:.2f}")
        for i, tied, a in decide.with_ranks(fits)[1:]:
            mark = " ※공동" if tied else ""
            print(f"     [{i}{mark}] {a.h}   적합도 {a.shelf_fit:.2f}   (참고 후보)")
    else:
        print(f"⚠️  사서 판단이 필요합니다.  (후보 {len(fits)}개 · 수렴 조건 미달)")
        for i, tied, a in decide.with_ranks(fits):
            mark = " ※공동" if tied else ""
            print(f"     [{i}{mark}] {a.h}   적합도 {a.shelf_fit:.2f}   {a.shelf_label}")
    print(f"\n🧭 판정: {dec.reason}")
    print("═" * 64)

    # ══ 근거는 아래 ══
    print("\n[근거]")
    for m in retr.messages:
        print(f"  → {m}")

    def _show(c, tag):
        ex = ", ".join(b.title for b in c.shelf_books[:3]) or "(본교 미보유)"
        print(f"   {c.ddc_h}{tag}  [본교 서가 {c.shelf_count}건(샘플)]  예: {ex}")

    print("\n🔎 1단계 — 082(업체번호):")
    if retr.prior_candidate:
        _show(retr.prior_candidate, " ★082")
        if out.prior:
            print(f"      1단계 fit: {out.prior.shelf_fit:.2f} — {out.prior.fit_reasoning}")
    else:
        print("   (082 없음)")

    print("\n🔎 2단계 — 종합서지 득표 (082 제외):")
    for c in retr.union_candidates:
        _show(c, f"  [{c.union_votes}개 대학]")

    if retr.keyword_query:
        print(f"\n🔎 3단계 — 키워드 검색: {', '.join(retr.keyword_query)}")
        for c in retr.keyword_candidates:
            _show(c, f"  [{c.keyword_hits}권 매칭]")

    # 후보마다 **따로** 매긴 점수다. 서로를 안 보고 나온 값이라 나란히 놓고 비교해도 된다.
    _src = {c.ddc_h: " · ".join(sorted(c.sources)) or "082"
            for c in (([retr.prior_candidate] if retr.prior_candidate else [])
                      + retr.union_candidates + retr.keyword_candidates)}
    print("\n📚 후보별 독립 적합도:")
    for i, tied, a in decide.with_ranks(fits):
        mark = " ※공동순위" if tied else ""
        print(f"  [{i}{mark}] {a.h}  shelf_fit {a.shelf_fit:.2f}  〔{_src.get(a.h, '?')}〕")
        print(f"      서가 요약: {a.shelf_label}")
        print(f"      {a.fit_reasoning}")
        if a.cited_books:
            print(f"      근거 도서: {', '.join(a.cited_books)}")

    if expected:
        print(f"\n🎯 정답: 작성자확정={expected.get('writer_final')} "
              f"교열최종={expected.get('review_final')} 난이도={expected.get('difficulty')}")



if __name__ == "__main__":
    main()
