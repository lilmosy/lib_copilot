"""판정 — **여기에 LLM 호출이 없다.**

LLM은 후보마다 `ShelfFitAssessment`(점수 + 근거)를 낼 뿐이고, 정렬과 수렴 판정은
전부 이 파일이 정한다. 그래야 저장된 회차 json의 점수만으로
문턱을 다시 쓸어볼 수 있다 — 재실행 0회, 비용 0원(2026-08-05).

**점수를 더하거나 가중합하지 않는다**(spec §7). 벡터를 정렬해 정책을 적용할 뿐이다.
"""

from __future__ import annotations

import os

from config import GAP_MIN, THRESHOLD_2
from schema import CandidateDecision, ShelfFitAssessment

# 1단계 게이트(EARLY_STOP·gate_1·THRESHOLD_1)는 2026-09-02에 제거했다.
# 값이 없어서다 — 오답 082가 정답 082보다 점수가 높은 역전이 실측됐다(config.py 주석).
# 082는 게이트가 아니라 최초 후보다.
#
# ── 2단계 게이트 ─────────────────────────────────────────
# 082+종합서지까지의 후보만으로 `decide()`가 수렴 판정을 내리면 키워드 단계를 건너뛴다.
# **T1과 다른 점: 별도 문턱이 없다.** 최종 판정과 똑같은 두 조건(THRESHOLD_2·GAP_MIN)을
# 더 적은 후보에 적용할 뿐이라, 값의 근거가 최종 판정의 근거와 같다. 8-25 관측에서
# 이 지점은 깨끗이 갈렸다 — #3·#4·#6 종결, 사람이 갈린 4건은 전원 저지(devlog 9-02(3)).
#
# "누가 확정하느냐"와는 무관하다 — 확정은 언제나 사서가 앱에서 한다(구 AUTO_CONFIRM은
# 2026-09-02 제거, config.py 주석). 게이트가 아끼는 것은 키워드 LLM 호출 서너 번과
# 사서가 볼 후보 목록의 군더더기다.
#
# ⚠️ 평가 회차는 이걸 끈다 — 닫히면 그 케이스의 키워드 후보 점수가 아예 안 생겨
#    분포에 구멍이 난다. `evaluate.py`·비교 스크립트가 `decide.STAGE2_GATE = False`로
#    직접 끄고 회차 조건에 기록한다.
STAGE2_GATE = os.environ.get("LIBCOPILOT_STAGE2_GATE", "on").lower() == "on"


def rank(assessments: list[ShelfFitAssessment]) -> list[ShelfFitAssessment]:
    """점수 내림차순. 점수가 같으면 번호 문자열로 — 회차마다 순서가 흔들리면 안 된다.

    ⚠️ 문자열 보조 기준은 **재현성**을 위한 것이지 순위의 근거가 아니다. 화면에 순위를
       찍을 때는 이 목록을 그대로 1위·2위로 세지 말고 `with_ranks()`를 쓴다."""
    return sorted(assessments, key=lambda a: (-a.shelf_fit, a.h))


def with_ranks(ranked: list[ShelfFitAssessment]
               ) -> list[tuple[int, bool, ShelfFitAssessment]]:
    """`(순위, 동순위인가, 후보)`. **점수가 같으면 같은 순위를 준다** — 1·1·3 식.

    #8 야구장에서 780.2·781.63(정답)·782.42164·796이 전부 0.78인데 "780.2"가 문자열이
    작다는 이유로 1순위처럼 보인 적이 있다. 점수가 같으면 우열이 **없는** 것이므로
    화면도 그렇게 보여야 한다 — 셋 다 1위다.

    가운데 bool은 "이 순위를 다른 후보와 나눠 갖는가"다. 화면은 이 값만 보고 강조하면
    된다(색·배지). 정렬 자체는 `rank()` 그대로라 재현성은 유지된다.
    """
    groups: list[list[ShelfFitAssessment]] = []
    for a in ranked:
        if groups and a.shelf_fit == groups[-1][0].shelf_fit:
            groups[-1].append(a)
        else:
            groups.append([a])
    out, n = [], 1
    for g in groups:
        for a in g:
            out.append((n, len(g) > 1, a))
        n += len(g)
    return out


def decide(assessments: list[ShelfFitAssessment]) -> CandidateDecision:
    """판정 — `converged` 불린 하나를 낸다. True면 ✅, False면 ⚠️ 사서가 가른다.
    확정 자체는 어느 쪽이든 사서가 앱에서 한다(수렴이면 클릭 한 번일 뿐).

    수렴 판정은 **두 조건을 다 넘어야** 한다(2026-09-02, devlog 9-02(3) 관측으로 채택):
      점수(THRESHOLD_2)  "어떤 서가도 확신 못 하는 책"을 막는다 — gap이 넉넉해도
                         절대 점수가 낮으면 확정 근거가 없다(#11 부린왕자).
      격차(GAP_MIN)      "그럴듯한 1위 뒤에 경합이 숨은 책"을 막는다 — 사람이 갈린
                         책은 1위 점수가 높아도 gap이 좁다(#9 서점, 0.90+/gap 0.10↓).
    동점(격차 0.00)은 별도 조건이 아니다 — GAP_MIN이 이미 잡는다. 다만 그때는 "1순위 대
    2순위"가 아니라 **공동 1위**라고 말한다. 화면 순위도 마찬가지로 `with_ranks()`가
    같은 점수에 같은 순위를 준다(#8 야구장의 0.78 네쌍둥이 → 넷 다 1위).
    """
    ranked = rank(assessments)
    if not ranked:
        return CandidateDecision(
            assessments=[], converged=False,
            reason="점수를 매긴 후보가 하나도 없습니다. "
                   "검색이 못 건졌거나 후보 번호대에 본교 장서가 없습니다.")
    top = ranked[0]
    if top.shelf_fit < THRESHOLD_2:
        return CandidateDecision(
            assessments=ranked, converged=False,
            reason=(f"1순위 {top.h}의 서가 적합도가 {top.shelf_fit:.2f}로 "
                    f"문턱({THRESHOLD_2:.2f})에 못 미칩니다 — "
                    f"제시된 어느 번호대에도 뚜렷하게 어울리지 않습니다."))
    # 후보가 하나뿐이면 경합 자체가 없다 — gap 조건은 따질 대상이 없어 통과로 본다.
    gap = round(top.shelf_fit - ranked[1].shelf_fit, 6) if len(ranked) > 1 else None
    if gap is not None and gap < GAP_MIN:
        if gap == 0:
            # 점수가 같으면 1위가 여럿이다 — "1순위/2순위"라고 쓰면 없는 우열을 만든다.
            same = [a.h for _, tied, a in with_ranks(ranked) if tied and a.shelf_fit == top.shelf_fit]
            reason = (f"공동 1위입니다({top.shelf_fit:.2f}): {', '.join(same)}. "
                      f"점수가 같아 우열이 없고 화면 순서는 번호 문자열 정렬일 뿐입니다 — "
                      f"사서가 직접 골라주세요.")
        else:
            reason = (f"1순위 {top.h}({top.shelf_fit:.2f})가 문턱은 넘지만, "
                      f"2순위 {ranked[1].h}({ranked[1].shelf_fit:.2f})와의 격차 "
                      f"{gap:.2f}가 기준({GAP_MIN:.2f})에 못 미칩니다 — "
                      f"상위 후보가 경합합니다. 사서가 갈라주세요.")
        return CandidateDecision(assessments=ranked, converged=False, reason=reason)
    return CandidateDecision(
        assessments=ranked, converged=True,
        reason=(f"1순위 {top.h}의 서가 적합도 {top.shelf_fit:.2f}가 문턱"
                f"({THRESHOLD_2:.2f})을 넘고 2순위와의 격차도 충분합니다."))
