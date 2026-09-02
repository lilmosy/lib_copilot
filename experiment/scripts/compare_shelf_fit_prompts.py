"""다섯 shelf_fit 프롬프트를 동일한 책×서가 입력으로 비교한다.

후보 검색까지 매 버전 다시 돌리면 키워드 LLM의 흔들림과 검색 결과 차이가 섞인다.
각 케이스는 기준선으로 파이프라인을 한 번만 통과시켜 후보 서가를 고정하고, 그 동일한
후보들에 다섯 시스템 프롬프트를 반복 적용한다. 모든 LLM 호출은 기존 `fit.py`와
`keywords.py`를 통해서만 일어난다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
REAL_ROOT = EXPERIMENT_ROOT.parent / "real"
sys.path.insert(0, str(REAL_ROOT / "src"))

import aladin  # noqa: E402
import decide  # noqa: E402

# 비교 실험은 케이스마다 후보를 전량 고정해야 하므로 2단계 게이트를 끈다(evaluate.py와 동일).
decide.STAGE2_GATE = False
import fit  # noqa: E402
import llm  # noqa: E402
from pipeline import classify_book  # noqa: E402
from schema import BookInput, CandidateNumber, SHELF_FIT_RUBRIC  # noqa: E402


VARIANTS = {
    "baseline": EXPERIMENT_ROOT / "prompts" / "exp_shelf_fit_baseline.txt",
    "semantic": EXPERIMENT_ROOT / "prompts" / "exp_shelf_fit_semantic.txt",
    "two_axis": EXPERIMENT_ROOT / "prompts" / "exp_shelf_fit_two_axis.txt",
    "semantic_focus": EXPERIMENT_ROOT / "prompts" / "exp_shelf_fit_semantic_focus.txt",
    "two_axis_focus": EXPERIMENT_ROOT / "prompts" / "exp_shelf_fit_two_axis_focus.txt",
}


def _system_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace(
        "{SHELF_FIT_RUBRIC}", SHELF_FIT_RUBRIC
    )


def _scenario_paths() -> list[Path]:
    paths = list((EXPERIMENT_ROOT / "data" / "scenarios").glob("*.json"))
    return sorted(
        paths,
        key=lambda p: json.loads(p.read_text(encoding="utf-8"))["case_id"],
    )


def _book(raw: dict) -> BookInput:
    data = dict(raw)
    data.pop("keywords", None)
    return BookInput(**data)


def _candidates(out) -> list[CandidateNumber]:
    raw = (
        ([out.retrieve.prior_candidate] if out.retrieve.prior_candidate else [])
        + out.retrieve.union_candidates
        + out.retrieve.keyword_candidates
    )
    seen: set[str] = set()
    unique: list[CandidateNumber] = []
    for cand in raw:
        if cand.ddc_h not in seen and cand.shelf_count > 0 and cand.shelf_books:
            unique.append(cand)
            seen.add(cand.ddc_h)
    return unique


def _assessments(items) -> list[dict]:
    return [a.model_dump(mode="json") for a in decide.rank(list(items))]


def _checkpoint(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs는 1 이상이어야 합니다.")
    return args


def main() -> None:
    args = _parse_args()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = args.output or EXPERIMENT_ROOT / "output" / f"{stamp}_shelf_prompt_comparison.json"
    prompts = {name: _system_prompt(path) for name, path in VARIANTS.items()}
    result = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "runs": args.runs,
        "method": "후보·키워드를 케이스별 1회 고정한 뒤 동일 책×서가에 프롬프트만 교체",
        "variants": {name: str(path) for name, path in VARIANTS.items()},
        "cases": [],
    }
    started = time.monotonic()

    for index, path in enumerate(_scenario_paths(), 1):
        data = json.loads(path.read_text(encoding="utf-8"))
        book = aladin.enrich(_book(data["input"]))
        exp = data.get("expected", {})
        print(f"\n[{index}/12] #{data['case_id']} {book.title}", flush=True)

        # 기준선 1회차가 후보 검색과 첫 fit을 함께 수행한다.
        # 2단계 게이트는 위에서 껐다 — 프롬프트를 비교하려면 케이스마다 후보 전량이 필요하다.
        fit.SYSTEM_FIT = prompts["baseline"]
        out = classify_book(book, holdout=frozenset(data.get("holdout_ids") or ()))
        cands = _candidates(out)
        case = {
            "case_id": data["case_id"],
            "title": book.title,
            "writer_final": exp.get("writer_final"),
            "review_final": exp.get("review_final"),
            "split": exp.get("writer_final") != exp.get("review_final"),
            "inherited": out.inherited,
            "keywords": list(out.keywords.keywords) if out.keywords else [],
            "keyword_min_hit": out.retrieve.keyword_min_hit,
            "candidates": [
                {
                    "h": c.ddc_h,
                    "sources": sorted(c.sources),
                    "shelf_count": c.shelf_count,
                    "sample_count": len(c.shelf_books),
                }
                for c in cands
            ],
            "versions": {name: [] for name in VARIANTS},
        }
        result["cases"].append(case)

        first_trace = out.trace
        first_assessments = _assessments(out.decision.assessments)
        case["versions"]["baseline"].append(
            {"run": 1, "assessments": first_assessments, "trace": first_trace}
        )
        _checkpoint(output, result)
        print(
            f"  후보 {len(cands)}개 · baseline r1 완료"
            + (" (결정론적 승계)" if out.inherited else ""),
            flush=True,
        )

        # 승계 케이스는 프롬프트를 호출하지 않으므로 모든 버전·회차가 동일하다.
        if out.inherited:
            for name in VARIANTS:
                start_run = 2 if name == "baseline" else 1
                for run in range(start_run, args.runs + 1):
                    case["versions"][name].append(
                        {"run": run, "assessments": first_assessments, "trace": []}
                    )
            _checkpoint(output, result)
            continue

        # 회차별로 다섯 버전을 번갈아 실행해 시간대·서비스 상태 편향을 줄인다.
        for run in range(1, args.runs + 1):
            for name, system in prompts.items():
                if name == "baseline" and run == 1:
                    continue
                fit.SYSTEM_FIT = system
                t0 = time.monotonic()
                assessed = fit.assess_many(book, cands)
                trace = llm.take_trace()
                case["versions"][name].append(
                    {"run": run, "assessments": _assessments(assessed), "trace": trace}
                )
                _checkpoint(output, result)
                print(
                    f"  {name:20} r{run} · {len(assessed)}개 · "
                    f"{time.monotonic() - t0:.0f}초 · 누적 {(time.monotonic() - started) / 60:.1f}분",
                    flush=True,
                )

    result["finished_at"] = datetime.now().isoformat(timespec="seconds")
    result["elapsed_seconds"] = round(time.monotonic() - started, 1)
    _checkpoint(output, result)
    print(f"\n완료: {output}", flush=True)


if __name__ == "__main__":
    main()
