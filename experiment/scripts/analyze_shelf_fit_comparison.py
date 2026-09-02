"""고정 후보 shelf_fit 프롬프트 비교 JSON의 정량 요약."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path


def _by_h(run: dict) -> dict[str, dict]:
    return {a["h"]: a for a in run["assessments"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8"))

    print("version\tconv_top\tconv_margin\tsplit_pair_top\tsplit_pair_top2\t"
          "split_pair_gap\ttop_stable\tscore_sd")
    for version in data["variants"]:
        conv_top = 0
        conv_total = 0
        conv_margins: list[float] = []
        split_pair_top = 0
        split_total = 0
        split_pair_top2 = 0
        split_pair_gaps: list[float] = []
        top_stable = 0
        sds: list[float] = []

        for case in data["cases"]:
            runs = case["versions"][version]
            tops: list[str | None] = []
            scores_by_h: dict[str, list[float]] = {}
            for run in runs:
                assessments = run["assessments"]
                tops.append(assessments[0]["h"] if assessments else None)
                by_h = _by_h(run)
                for h, assessment in by_h.items():
                    scores_by_h.setdefault(h, []).append(assessment["shelf_fit"])

                review = case["review_final"]
                writer = case["writer_final"]
                if not case["split"]:
                    conv_total += 1
                    if assessments and assessments[0]["h"] == review:
                        conv_top += 1
                    if review in by_h:
                        others = [a["shelf_fit"] for a in assessments if a["h"] != review]
                        if others:  # 결정론적 승계(#1)는 경쟁 후보가 없어 margin 계산에서 제외한다.
                            conv_margins.append(by_h[review]["shelf_fit"] - max(others))
                else:
                    split_total += 1
                    pair = {writer, review}
                    if assessments and assessments[0]["h"] in pair:
                        split_pair_top += 1
                    if len(assessments) >= 2 and {a["h"] for a in assessments[:2]} == pair:
                        split_pair_top2 += 1
                    if writer in by_h and review in by_h:
                        split_pair_gaps.append(
                            abs(by_h[writer]["shelf_fit"] - by_h[review]["shelf_fit"])
                        )

            if len(set(tops)) == 1:
                top_stable += 1
            for scores in scores_by_h.values():
                if len(scores) > 1:
                    sds.append(statistics.pstdev(scores))

        mean = lambda xs: statistics.mean(xs) if xs else float("nan")
        print(
            f"{version}\t{conv_top}/{conv_total}\t{mean(conv_margins):+.3f}\t"
            f"{split_pair_top}/{split_total}\t{split_pair_top2}/{split_total}\t"
            f"{mean(split_pair_gaps):.3f}\t{top_stable}/{len(data['cases'])}\t{mean(sds):.3f}"
        )

    print("\nCASE TOPS")
    for case in data["cases"]:
        print(f"#{case['case_id']} {case['title']}")
        for version in data["variants"]:
            runs = case["versions"][version]
            tops = [r["assessments"][0]["h"] if r["assessments"] else "-" for r in runs]
            print(f"  {version:20} {', '.join(tops)}")

    print("\nSPLIT PAIR SCORES")
    for case in data["cases"]:
        if not case["split"]:
            continue
        print(f"#{case['case_id']} writer={case['writer_final']} review={case['review_final']}")
        for version in data["variants"]:
            values = []
            for run in case["versions"][version]:
                by_h = _by_h(run)
                w = by_h.get(case["writer_final"], {}).get("shelf_fit")
                r = by_h.get(case["review_final"], {}).get("shelf_fit")
                values.append(f"{w}/{r}")
            print(f"  {version:20} {', '.join(values)}")


if __name__ == "__main__":
    main()
