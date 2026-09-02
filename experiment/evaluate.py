"""골든셋 평가 — 사서가 실제로 처리한 12권으로 채점한다.

각 시나리오의 `input`만 파이프라인에 넣고(정답은 넘기지 않는다), 나온 ▼h를 `expected`와 대조한다.
정답이 샐 수 없는 이유는 약속이 아니라 구조다 — `BookInput`에 정답 필드가 존재하지 않는다.

채점은 **정답이 하나인가 갈렸는가**로 두 갈래다 (난이도 라벨은 쓰지 않는다):
  - 수렴(writer == review) → **exact**: 1순위가 교열 최종과 같은가.
  - 갈림(writer != review) → **converged=false 이면서 covers_both**: 사람도 나뉜 건이므로
      단일 정답을 요구하지 않고, 경합한 두 답(작성자·교열)을 후보에 다 담고 사서에게 넘겼는가로 본다.
  참고 지표: topk(교열최종이 후보에 있나) · prefix(오류분석용, 헤드라인 아님)

단계별 귀속(코드로 계산, 근거 톺아보기의 뼈대):
  - inherited?      : 0차 승계로 끝났나
  - union_recall?   : 교열 최종이 종합서지 voting(서강 제외)에 애초에 들어왔나
                      (안 들어왔으면 = 검색·데이터 문제지 판단 문제 아님)
  - home_shelf?     : 그 번호대가 본교에 있나 (없으면 서가 의미를 못 읽음)

산출물 → output/<회차>.json. 여러 조건을 비교한 사람용 요약은 별도 분석기가 만든다.
**회차는 덮어쓰지 않는다** — LLM 판단이라 같은 조건에서도 결과가 흔들리므로 회차를 쌓아 비교해야 한다.

실행:
    cd experiment
    python evaluate.py                                  전체 12케이스
    python evaluate.py --label=aladin                   회차에 라벨(무엇을 바꾼 실행인지)
    python evaluate.py data/scenarios/case05_*.json     한 건만
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REAL_ROOT = ROOT.parent / "real"
SCENARIO_DIR = ROOT / "data" / "scenarios"
OUTPUT_DIR = ROOT / "output"
sys.path.insert(0, str(REAL_ROOT / "src"))

import union_db  # noqa: E402
import decide as _decide  # noqa: E402
from config import GAP_MIN, THRESHOLD_2  # noqa: E402

# ⚠️ 평가 회차는 2단계 게이트를 **끈다.** 닫히면 그 케이스의 키워드 후보에 점수가 아예
#    안 생겨 분포에 구멍이 난다 — 문턱을 저장된 json만으로 다시 쓸어보려면 전량이 필요하다.
#    운영(app.py·run.py)은 기본 on이다. 회차 조건에 `stage2_gate: false`로 기록된다.
_decide.STAGE2_GATE = False
from pipeline import classify_book  # noqa: E402
from schema import BookInput  # noqa: E402

def _prefix(h: str, n: int = 3) -> str:
    return (h or "").replace(".", "")[:n]


def _realistic_input(raw: dict) -> BookInput:
    """실전 반영: 수기 keywords 제거(있어도 무시). 나머지 서지는 그대로."""
    d = dict(raw)
    d.pop("keywords", None)
    return BookInput(**d)


def evaluate_one(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    book = _realistic_input(data["input"])
    exp = data.get("expected", {})
    review = exp.get("review_final")
    writer = exp.get("writer_final")
    gold = review or writer

    holdout = frozenset(data.get("holdout_ids") or ())
    out = classify_book(book, holdout=holdout)
    fits = out.decision.assessments          # 이미 shelf_fit 내림차순 (decide.rank)
    pred = fits[0].h if fits else None
    cand_hs = [a.h for a in fits]

    # ── 단계별 귀속(코드) ──
    votes, vmeta = union_db.voting(book.title)
    union_has_gold = gold in votes
    _retr_cands = (([out.retrieve.prior_candidate] if out.retrieve.prior_candidate else [])
                   + out.retrieve.union_candidates + out.retrieve.keyword_candidates)
    home_shelf_gold = any(c.ddc_h == gold and c.shelf_count > 0 for c in _retr_cands)

    row = {
        "case_id": data.get("case_id"),
        "slug": path.stem,
        "title": book.title,
        "difficulty": exp.get("difficulty"),
        "ddc_082": book.ddc_082,
        "writer_final": writer, "review_final": review, "gold": gold,
        "pred": pred, "candidates": cand_hs,
        # ── 채점: 정답이 하나인가 갈렸는가로 갈린다 ──
        # 난이도 라벨(하/중/상)은 쓰지 않는다. 실전엔 없는 라벨이고, 무엇보다
        # writer != review("사람 둘이 갈렸다")가 그 라벨과 12/12 일치한다 —
        # 즉 '상'의 정의가 곧 뒤집힘이다(docx: "교열 후 …사서마다 판단 다름"은 전부 상).
        "split": bool(writer and review and writer != review),
        "exact": pred == gold,                    # 정답 하나일 때의 지표
        "covers_both": bool(writer and review     # 갈렸을 때의 지표: 경합 쌍을 다 담았나
                            and any(_prefix(c) == _prefix(writer) for c in cand_hs)
                            and review in cand_hs),
        "prefix": bool(pred and gold and _prefix(pred) == _prefix(gold)),  # 오류분석용(헤드라인 아님)
        "topk": gold in cand_hs,
        # 수렴 판정(코드가 두 조건으로 계산) — "사람이 봐야 했나"의 기대값은 위 `split`이
        # 그대로다("작성자와 교열이 갈렸다" = "사람이 봐야 했던 건", 12/12 일치, 2026-08-01).
        "converged": out.decision.converged,
        "reason": out.decision.reason,
        # 귀속
        "inherited": out.inherited,
        "went_keyword": getattr(out, "went_keyword", True),  # 키워드 단계까지 갔나 (B 전용)
        "union_has_gold": union_has_gold,
        # 키워드 채널 — 무엇을 검색했고, 정답이 그 결과에 있었나
        "keywords": list(out.keywords.keywords) if out.keywords else [],
        "keyword_min_hit": out.retrieve.keyword_min_hit,
        "keyword_n_found": len(out.retrieve.keyword_hits_all),
        "keyword_has_gold": any(h == gold for h, _ in out.retrieve.keyword_hits_all),
        "keyword_gold_rank": next(
            (i for i, (h, _) in enumerate(out.retrieve.keyword_hits_all, 1) if h == gold), None),
        "cand_sources": {c.ddc_h: sorted(c.sources) for c in _retr_cands},
        "union_dist": dict(list(votes.items())[:8]),
        "home_shelf_gold": home_shelf_gold,
        # 082 후보의 fit — 채점엔 안 쓴다. 관측값(T1은 9-02에 "값 없음"으로 제거됐지만,
        # 082 점수의 분포 자체는 계속 본다). fit_assessments 안에도 그대로 들어 있다.
        "prior_fit": out.prior.shelf_fit if out.prior else None,
        "prior_verdict": out.prior.fit_reasoning if out.prior else None,
        # ── 후보별 독립 점수 (구 `fits`) ──
        # 점수만 남기던 자리에 근거·인용도서를 함께 남긴다. 문턱을 재산정할 때
        # `trace`(프롬프트 전문)를 파싱하지 않고 여기만 보면 되게 하려는 것이다.
        "fit_assessments": [a.model_dump() for a in fits],
        # gap(1위−2위 격차)은 2026-09-02부터 판정 조건이다(config.GAP_MIN) —
        # 사람이 갈린 책은 1위 점수가 높아도 gap이 좁다(#9 실측, devlog 9-02(3)).
        # 여기 기록은 저장된 json만으로 문턱을 다시 쓸어보기 위한 것.
        "gap": (round(fits[0].shelf_fit - fits[1].shelf_fit, 3)
                if len(fits) >= 2 else None),
        "expected_note": exp.get("note"),
        # ── 「무엇을 넣었나」 요약 — 회차 md의 ⬜블록이 쓴다 ──
        # 프롬프트 전문(케이스당 4만~10만 자)은 접어 두고, 여기 적힌 것만 펼쳐 보여준다.
        # 서가 책 목록 자체는 JSON에서도 생략되므로 **권수만** 세어 남긴다.
        "input_book": {"저자": book.author or "-", "부제": book.subtitle or "-",
                       "책소개": (book.description or "")[:160]},
        "retrieve_messages": list(out.retrieve.messages),
        "retriever": out.retrieve.retriever,
        "input_cands": [
            {"h": c.ddc_h, "is082": c.ddc_h == book.ddc_082, "votes": c.union_votes,
             "shelf": c.shelf_count, "books": len(c.shelf_books),
             "desc": sum(1 for b in c.shelf_books if b.description)}
            for c in _retr_cands],
        "trace": out.trace,   # LLM 원물(프롬프트·출력·토큰·지연) — 케이스별 상세가 쓴다
        "_out": out,  # 톺아보기용(직렬화 시 제거)
    }
    return row


def _one_liner(row: dict) -> str:
    """한 줄 원인 — 단계별 사실(코드 계산)에서 합성한다. 유형에 따라 잣대가 다르다."""
    # (1단계 게이트 분기는 2026-09-02 게이트 제거와 함께 사라졌다 — 모든 책이 3단계까지 간다.)
    if row["split"]:                                  # 사람도 갈린 건
        if not row["converged"] and row["covers_both"]:
            return "경합한 두 답을 후보에 담고 사서에게 넘김 — 의도대로 작동."
        if row["covers_both"]:
            return "후보엔 두 답이 다 있었으나 수렴으로 판정함 — 다음엔 같은 확신으로 틀린다."
        missing = "작성자안" if row["review_final"] in row["candidates"] else "교열최종"
        return f"경합 후보 중 {missing}이 후보에 없어 사서가 비교할 수 없음."
    if row["exact"]:
        return "정답 적중."
    if row["inherited"]:
        return "0차 승계로 결정."
    if not row["union_has_gold"]:
        return "정답이 종합서지 득표에도 없어 후보에 아예 못 들어옴 — 검색/데이터 한계."
    if row["gold"] in row["candidates"]:
        return "정답이 후보엔 있으나 1순위를 다른 번호로 고름 — 판단 실패."
    return "정답이 후보에 없음 — 본교 서가 의미로 다른 번호를 선택."


def _cause_and_fix(row: dict) -> tuple[str, str, str]:
    """실패를 (무슨 일이 났나 · 왜 · 어떻게 고치나)로 쪼갠다. 실제 번호를 넣어 구체적으로.

    쪼개는 이유: 프롬프트로 고칠 것과 데이터를 채워야 할 것이 섞이면 엉뚱한 데를 손본다.
    """
    ok = (not row["converged"] and row["covers_both"]) if row["split"] else row["exact"]
    if ok:
        return ("", "", "")
    gold, pred = row["gold"], row["pred"]
    cands = ", ".join(row["candidates"])

    if not (row["union_has_gold"] or row["inherited"]):
        return (f"정답 `{gold}`이 후보에 아예 없었음<br>(후보: {cands})",
                "본교에 기존 판본이 없어 승계가 안 되고, 종합서지 득표에도 그 번호가 없음",
                "**전체 재크롤 대기** — 판단이 아니라 장서 커버리지 문제")
    if row["split"] and not row["covers_both"]:
        return (f"`{pred}`을 골랐고 후보는 {cands}",
                f"실제로 갈린 건 `{row['writer_final']}` ↔ `{row['review_final']}`인데 "
                f"`{row['writer_final']}`이 후보에 없어 **사서가 그 비교를 못 함**",
                "프롬프트 — “갈리면 경합 후보를 빠짐없이 담아라”<br>(수정 완료, 다음 회차에서 확인)")
    if row["split"]:
        return (f"`{pred}`으로 **수렴 판정**(답은 맞았지만)",
                f"경합 쌍 `{row['writer_final']}`·`{row['review_final']}`을 후보에 다 담고도 "
                "‘갈린다’고 표시하지 않음 — 다음엔 같은 확신으로 틀린다",
                "**수렴 조건(THRESHOLD_2·GAP_MIN)** — 사람이 갈린 책을 막게 재보정")
    if gold in row["candidates"]:
        return (f"`{pred}`을 1순위로 고름<br>(정답 `{gold}`은 후보에 있었음)",
                "본교 서가에서 비슷해 보이는 책 몇 권에 끌려 다른 번호를 택함",
                "프롬프트 — 서가의 한두 권보다 **분포와 책의 성격**을 보도록")
    return (f"`{pred}`을 고름 (정답 `{gold}`은 후보에도 없음)",
            "본교 서가 의미를 읽고 다른 번호대를 택함",
            "서가 샘플 구성 / 프롬프트")


def _verdict(row: dict) -> str:
    if row["split"]:
        return "✅" if (not row["converged"] and row["covers_both"]) else "❌"
    return "✅" if row["exact"] else "❌"


def _fp8(text: str) -> str:
    """프롬프트 지문 8자. 라벨을 안 바꿔도 회차를 구별하려고 찍는다."""
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def _setup_dict() -> dict:
    """이 회차가 무슨 조건이었나 — **json만 보고 재현할 수 있어야 한다.**

    `--label`은 사람이 손으로 붙이는 이름이라, 프롬프트를 고치고 라벨을 안 바꾸면
    두 회차가 구별되지 않는다. 코드가 아는 것(모델·임계값·프롬프트 파일과 그 지문)을
    함께 박아 둔다. **지문(fp)이 다르면 프롬프트가 다른 회차다** — 라벨과 무관하게.
    """
    import fit as _fit
    import keywords as _kw
    import prompt as _pr
    from config import (FIT_CONCURRENCY, KEYWORD_MIN_HIT, KEYWORD_SHELF,
                        MAX_KEYWORD_CANDIDATES, MAX_UNION_CANDIDATES,
                        MODEL_FIT, MODEL_KEYWORD, SHELF_DESC_TOP, SHELF_SAMPLE)
    return {
        "structure": "3단계 누적 · 독립 shelf_fit",
        "model_fit": MODEL_FIT, "model_keyword": MODEL_KEYWORD,
        "prompt_fit": _pr.PROMPT_USED.get("systemprompt_shelf_fit"),
        "prompt_fit_fp": _fp8(_fit.SYSTEM_FIT),
        "prompt_keyword": _pr.PROMPT_USED.get("systemprompt_keyword"),
        "prompt_keyword_fp": _fp8(_kw.SYSTEM_KEYWORD),
        "threshold_2": THRESHOLD_2, "gap_min": GAP_MIN,
        # 평가는 항상 게이트를 열어 둔다(위 주석). 이 값 없이 went_keyword를 읽으면 안 된다.
        "stage2_gate": _decide.STAGE2_GATE,
        "fit_concurrency": FIT_CONCURRENCY,
        "shelf_sample": SHELF_SAMPLE, "shelf_desc_top": SHELF_DESC_TOP,
        "keyword_min_hit": KEYWORD_MIN_HIT, "max_keyword": MAX_KEYWORD_CANDIDATES,
        "keyword_shelf": KEYWORD_SHELF,
        "max_union": MAX_UNION_CANDIDATES,
    }


def run_once(paths: list[Path], run_id: str, label: str) -> dict:
    """12건을 한 번 돌려 json 하나를 남긴다. 반환은 점수 요약(터미널 출력용)."""
    rows = []
    print(f"\n=== 평가 {run_id} ({len(paths)} 케이스) ===")
    for p in paths:
        r = evaluate_one(p)
        rows.append(r)
        # ⚠️ 두 축을 곱해서 하나의 ✓/✗로 만들지 않는다.
        #    "넘겼나"와 "비교거리를 줬나"는 실패 원인이 정반대라, 섞으면 무엇을 고칠지 알 수 없다.
        kind = "갈림" if r["split"] else "수렴"
        if r["split"]:
            note = (f"넘김 {'O' if not r['converged'] else 'X'} · "
                    f"비교거리 {'O' if r['covers_both'] else 'X'}")
        else:
            note = "수렴 판정" if r["converged"] else "미수렴 → 사서"
            if not r["exact"]:
                note += " ⚠️틀림"
        inh = " (승계)" if r["inherited"] else ""
        ur = "" if r["union_has_gold"] or r["inherited"] else " ⚠︎union에 정답없음"
        eq = "=" if r["pred"] == r["gold"] else "≠"
        print(f" #{r['case_id']:>2} [{kind}] {r['title'][:18]:18} "
              f"{str(r['pred']):>10} {eq} {str(r['gold']):<10} {note}{inh}{ur}")

    if not rows:
        return {}

    # 케이스별 상세 = json 한 개 (입력·후보·판단·단계 귀속·LLM 원물)
    cases = []
    for r in rows:
        out = r.pop("_out")
        r["_one_liner"] = _one_liner(r)
        # mode="json"이라야 set(`sources`)이 json 타입으로 바뀐다.
        # 그냥 model_dump()면 파이썬 set이 그대로 나와 json.dumps가 죽는다(2026-08-18).
        retr = out.retrieve.model_dump(mode="json")
        # 서가 책 목록(후보당 40권 × 상세정보)은 빼고 저장한다.
        # 같은 DB면 언제든 재현되는 값인데 회차당 750KB를 먹는다(빼면 34KB).
        # 판단 근거는 fit_assessments의 fit_reasoning·cited_books에 문장으로 남아 있다.
        # ⚠️ `keyword_hits_all`(검색 전량)은 **지우지 않는다** — 컷을 재실행 없이 다시
        #    쓸어보고, 꼬리에서 재분류 큐를 뽑는 재료다(design.md §11.1).
        for c in ([retr.get("prior_candidate")] + (retr.get("union_candidates") or [])
                  + (retr.get("keyword_candidates") or [])):
            if isinstance(c, dict):
                c["shelf_books"] = f"(생략 — {len(c.get('shelf_books') or [])}권)"
        cases.append({**r, "retrieve": retr, "decision": out.decision.model_dump()})

    n = len(rows)
    ids = lambda rs: [r["case_id"] for r in rs]

    # ── 두 묶음으로 나눠 센다 ──
    # 수렴: 두 조건(THRESHOLD_2·GAP_MIN)을 넘어 ✅로 뜬 것과 그중 틀린 것(가장 위험)
    # 넘김: 제대로 넘겼나 · 불필요하게 넘겼나 · 넘겼어야 했는데 놓쳤나 · 비교거리를 줬나
    # (승계는 판정이 아니라 규칙이므로 수렴 묶음에서 뺀다)
    conv    = [r for r in rows if r["converged"] and not r["inherited"]]
    conv_ng = [r for r in conv if not r["exact"]]
    sent    = [r for r in rows if not r["converged"]]
    over    = [r for r in sent if not r["split"]]       # 안 갈렸는데 넘김 = 낭비
    miss    = [r for r in conv if r["split"]]           # 갈렸는데 수렴 판정 = 누락
    good    = [r for r in sent if r["split"]]
    cover   = [r for r in good if r["covers_both"]]
    uncover = [r for r in good if not r["covers_both"]]
    cmax    = max(len(r["candidates"]) for r in rows)
    ur      = sum(not (r["union_has_gold"] or r["inherited"]) for r in rows)

    scores = {
        "수렴판정": f"{len(conv)}/{n}", "수렴_정답": len(conv) - len(conv_ng),
        "수렴_오답": ids(conv_ng),
        "넘김": f"{len(sent)}/{n}", "제대로넘김": len(good),
        "불필요하게넘김": ids(over), "넘김누락": ids(miss),
        "비교거리": f"{len(cover)}/{len(good)}" if good else "-",
        "후보최대": cmax, "union_recall실패": ur,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / f"{run_id}.json").write_text(
        json.dumps({"run": run_id, "label": label, "setup": _setup_dict(),
                    "scores": scores, "cases": cases},
                   ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(f"\n ── 수렴 판정 (점수·격차 두 조건 통과, 승계 제외) ──")
    print(f" 수렴             {len(conv)}/{n}건   그중 맞음 {len(conv)-len(conv_ng)}")
    if conv_ng:
        print(f" ⚠️ 수렴인데 틀림   {len(conv_ng)}건 {ids(conv_ng)}   ← 가장 위험")
    print(f"\n ── 사서에게 넘김 ──")
    print(f" 넘긴 것          {len(sent)}/{n}건   제대로 넘김 {len(good)}건")
    if over:
        print(f" 불필요하게 넘김   {len(over)}건 {ids(over)}   ← 자동화 가치 깎임")
    if miss:
        print(f" 넘겼어야 했는데 X {len(miss)}건 {ids(miss)}   ← 누락")
    if good:
        print(f" 비교거리 준 것    {len(cover)}/{len(good)}"
              + (f"   (못 준 것 {ids(uncover)})" if uncover else ""))
    print(f"\n 후보 개수 최대 {cmax}   ·   union recall 실패 {ur}/{n}")
    print(f" → {OUTPUT_DIR}/{run_id}.json")
    return scores


def _positive_int(value: str) -> int:
    """argparse용 양의 정수 검증 — 0회 실행이나 음수 오타를 조용히 넘기지 않는다."""
    try:
        parsed = int(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError("정수를 입력하세요.") from e
    if parsed < 1:
        raise argparse.ArgumentTypeError("1 이상의 정수를 입력하세요.")
    return parsed


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="골든셋 도서를 분류하고 회차별 평가 JSON을 저장합니다.",
    )
    parser.add_argument(
        "paths", nargs="*", type=Path, metavar="SCENARIO",
        help="평가할 시나리오 JSON. 생략하면 data/scenarios/*.json 전체를 평가합니다.",
    )
    parser.add_argument(
        "--label", default="", metavar="NAME",
        help="산출물 파일명과 setup에 남길 실험 라벨입니다.",
    )
    parser.add_argument(
        "--runs", type=_positive_int, default=1, metavar="N",
        help="같은 조건의 반복 회차 수입니다. 기본값: 1",
    )
    args = parser.parse_args(argv)
    missing = [p for p in args.paths if not p.is_file()]
    if missing:
        parser.error("시나리오 파일을 찾을 수 없습니다: " + ", ".join(map(str, missing)))
    return args


def main(argv: list[str] | None = None) -> None:
    """`python evaluate.py --label=xxx --runs=3`

    **회차를 여러 번 돌리는 게 기본이다.** LLM 판단이라 같은 조건에서도 흔들려서,
    한 회차 숫자로는 "고칠 가치가 있는 실패"와 "노이즈"를 못 가른다 —
    실제로 #3이 같은 조건 두 회차에서 정답↔오답으로 뒤집혔다.

    md는 만들지 않는다. json만 남기고, 여러 조합을 다 돌린 뒤 한 장으로 종합한다.
    """
    args = _parse_args(argv)
    label = args.label
    runs = args.runs
    paths = args.paths or sorted(SCENARIO_DIR.glob("*.json"))

    stamp = datetime.now().strftime("%Y%m%d-%H%M") + (f"_{label}" if label else "")
    allscores = []
    for i in range(1, runs + 1):
        run_id = f"{stamp}.r{i}" if runs > 1 else stamp
        allscores.append(run_once(paths, run_id, label))

    if runs > 1:
        print("\n" + "=" * 60)
        print(f"  {stamp} — {runs}회차 요약")
        print("=" * 60)
        for i, s in enumerate(allscores, 1):
            if s:
                print(f"  r{i}  실제자동 {s['자동확정']} (맞음 {s['자동확정_정답']}) · "
                      f"참고문턱통과 {s['참고문턱통과']} (맞음 {s['참고문턱통과_정답']}) · "
                      f"넘김 {s['넘김']} · 비교거리 {s['비교거리']}")
        print("\n  ⚠️ 회차마다 다르면 그게 '흔들림'이다. 고칠 가치가 있는 건 '항상 틀림'이다.")


if __name__ == "__main__":
    main()
