"""lib_copilot 데모 — 사서 청구기호(852 ▼h) 부여 보조 (ver_2, 한 방 자동화).

업무 자동화 관점: 사서가 단계마다 클릭하는 마법사가 아니라,
**입력 → [분석] 한 번 → 결과 + 근거 + (다른 선택 가능)** 를 바로 제시한다.
평가 경로와 **같은 pipeline**을 부른다(경로 통합). 화면엔 LLM 결과만(정답/점수는 안 보임).

화면 구성 (사서 업무 절차 그대로, 위에서 아래로):
  결론 → 종합 판단 근거 → 확정 UI → 「어떻게 이 번호가 됐나」
  근거 탭: ① 업체 청구기호(082)  ② 종합서지 청구기호  (③은 둘 다 아닌 번호가 나올 때만)
  ※ 본교 서가는 별도 단계가 아니라 ①②를 판단하는 재료다. 각 탭 안에 접어 둔다.

실행: cd real && streamlit run app.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import decide  # noqa: E402
import sogang_db  # noqa: E402
import union_db  # noqa: E402
from config import RUNS_DIR  # noqa: E402
from pipeline import classify_book  # noqa: E402
from schema import BookInput  # noqa: E402

st.set_page_config(page_title="lib_copilot — 청구기호 보조", page_icon="📚", layout="wide")


@st.cache_data
def _stats():
    return sogang_db.stats()


@st.cache_data
def _run_pipeline(title, subtitle, author, ddc_082, is_translation, original_title):
    """입력된 신간 한 권을 운영 파이프라인으로 분류한다."""
    book = BookInput(title=title, subtitle=subtitle or None, author=author or None,
                     ddc_082=ddc_082 or None, is_translation=is_translation,
                     original_title=original_title or None)
    out = classify_book(book)
    votes, vmeta = union_db.voting(title)
    return out, votes, vmeta


def _log_decision(row: dict) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"decisions_{datetime.now():%Y%m%d}.jsonl"
    row = {"ts": datetime.now().isoformat(timespec="seconds"), **row}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


# ══ 사이드바 ══════════════════════════════════════════════
s = _stats()
st.sidebar.title("📚 lib_copilot")
st.sidebar.caption("사서 청구기호(852 ▼h) 부여 보조 · ver_2")
st.sidebar.metric("본교 장서 DB", f"{s['총 장서']:,}건")
st.sidebar.metric("청구기호 종류", f"{s['청구기호 종류']}종")
st.sidebar.caption("종합서지(KERIS): 12케이스 · 306개 대학 소장")
st.sidebar.divider()
st.sidebar.caption("흐름: 0차 원제 승계 → 1차 082+종합 voting → 2차 LLM 주제판단 → 확정(HITL)")

st.title("청구기호 채택 보조")
st.caption("신규 도서 서지를 넣으면, 종합서지 분포와 본교 서가 의미로 ▼h를 판단합니다. 최종 결정은 사서가 합니다.")

# ══ 입력 ══════════════════════════════════════════════════
c1, c2 = st.columns([3, 2])
title = c1.text_input("제목", key="f_title", placeholder="예: 가장 인간적인 도시")
subtitle = c1.text_input("부제 (선택)", key="f_sub")
author = c1.text_input("저자 (선택)", key="f_author")
ddc_082 = c2.text_input("082 업체번호 (선택)", key="f_082", placeholder="없으면 비워두세요")
is_tr = c2.checkbox("번역서", key="f_tr")
orig = c2.text_input("원제 (번역서면)", key="f_orig")

if st.button("🔎 분석", type="primary", disabled=not title):
    with st.spinner("0차 승계 → 종합 voting → LLM 주제판단 중…"):
        st.session_state["out"] = _run_pipeline(
            title, subtitle, author, ddc_082, is_tr, orig,
        )
        st.session_state["ctx"] = {"title": title, "ddc_082": ddc_082}

# ══ 결과 ══════════════════════════════════════════════════
if "out" in st.session_state:
    out, votes, vmeta = st.session_state["out"]
    dec, retr = out.decision, out.retrieve
    top = dec.top          # 1순위 ShelfFitAssessment (없으면 None)

    st.divider()
    # 화면은 **수렴 판정**(converged) 하나로 갈린다 — 점수 ≥ 0.80 이고 격차 ≥ 0.15면 ✅.
    # 확정은 어느 쪽이든 사서가 한다. 진짜 차이는 문구가 아니라 **클릭 수**다:
    #   수렴 → 기본값이 채워진 [확정] 한 번. 갈림 → 고르기 전엔 확정 못 함.
    # 문구는 run.py와 한 글자도 다르지 않게 유지한다(화면이 두 벌이 되면 안 된다).
    if out.inherited:
        st.success(f"### ✅ 기존 서지를 승계했습니다.   ▼h = {top.h}")
    elif dec.converged:
        st.success(f"### ✅ 1순위가 정해졌습니다.   ▼h = {top.h}")
    else:
        st.warning(f"### ⚠️ 사서 판단이 필요합니다.  "
                   f"(후보 {len(dec.assessments)}개 · 수렴 조건 미달)")
    st.caption(f"🧭 {dec.reason}")

    # ══ 확정 UI ══ 근거보다 **위**에 둔다. 수렴 케이스를 스크롤 없이 1클릭으로 끝내기 위해서다.
    #    (그래도 근거는 아래에 항상 펼쳐져 있다 — 접으면 '틀린 채 확정'을 못 잡는다)
    opts = [a.h for a in dec.assessments]
    MANUAL = "직접 입력"
    prior = retr.prior_candidate

    # 순위 — **점수가 같으면 같은 순위다**(1·1·3). 목록에 뜨는 차례는 번호 문자열 정렬일
    # 뿐이라, 그걸 1위·2위로 세면 없는 우열을 사서에게 보여주게 된다.
    # `_rank[h] = (순위, 공동인가)` — 라벨과 기록 양쪽이 이 하나를 쓴다.
    _rank = {a.h: (r, t) for r, t, a in decide.with_ranks(dec.assessments)}

    def _opt_label(h: str) -> str:
        r, tied = _rank.get(h, (None, False))
        return f"{r}위{' 공동' if tied else ''} · {h}" if r else h

    def _confirm(final: str, rank: int | None, src: str) -> None:
        """rank: 1=1순위 / 2,3=LLM 다른 후보 / None=그 외.
        src: llm | union | 082 | manual — 관성 클릭과 판단을 구분하는 값.

        **사서가 1순위를 안 고르면 그 자체가 판례다**(docs/ledger.md).
        어느 두 번호 사이에서 갈렸는지(`경합쌍`)와 사서가 적어준 이유를 함께 남긴다.
        같은 경합쌍이 여러 번 쌓이면 "이 자리는 기준을 정해야 한다"는 신호가 된다.
        ⚠️ 이유는 **사서가 직접 쓴 문장만** 저장한다. LLM이 요약하거나 대신 쓰지 않는다 —
           틀린 근거가 그대로 규칙으로 굳는다.
        """
        overturned = bool(top and final != top.h)
        row = {**st.session_state["ctx"], "llm_top": top.h if top else None,
               "candidates": opts, "converged": dec.converged,
               "final": final, "picked_rank": rank, "picked_from": src,
               "agreed": not overturned}
        if overturned:
            row["경합쌍"] = sorted([top.h, final])
            row["뒤집은_이유"] = st.session_state.get("why", "").strip()
        p = _log_decision(row)
        st.success(f"▼h = {final} 확정 · runs/{p.name} 기록")
        if overturned and not row.get("뒤집은_이유"):
            st.info("이유를 적어두시면 다음에 같은 자리에서 시스템이 먼저 알려드립니다.")

    def _why_box() -> None:
        """1순위가 아닌 번호를 고를 때만 뜨는 이유 칸. **선택 입력**이다 —
        필수로 만들면 사서가 아무 말이나 채워 넣어 판례가 오염된다."""
        st.text_input(
            "왜 1순위가 아닌 번호로 바꾸셨나요? (선택 · 다음에 같은 자리에서 참고합니다)",
            key="why", placeholder="예: 본교에 기존 서지가 있으면 승계가 먼저입니다")

    # LLM 후보 밖의 번호들도 **클릭**으로 고를 수 있게 한다. 직접 입력보다 언제나 낫고,
    # 이 번호들은 실제로 시스템이 본 것(082 · 타대학 분류)이라 임의 입력이 아니다.
    _seen = set(opts)
    _extra: list[tuple[str, str, str]] = []          # (표시라벨, 번호, 출처)
    if prior and prior.ddc_h not in _seen:
        _extra.append((f"{prior.ddc_h} (082 · 본교 {prior.shelf_count}건)", prior.ddc_h, "082"))
        _seen.add(prior.ddc_h)
    for cn in retr.union_candidates:
        if cn.ddc_h not in _seen:
            _extra.append((f"{cn.ddc_h} ({cn.union_votes}개 대학 · 본교 {cn.shelf_count}건)",
                           cn.ddc_h, "union"))
            _seen.add(cn.ddc_h)

    def _other_ui(key: str) -> None:
        """'다른 번호로 확정하기' 접기 — LLM 참고 후보 + 종합서지/082 번호 + 직접 입력."""
        # 갈림이면 라디오가 이미 전 후보를 보여주므로 여기서 또 세우지 않는다.
        # 수렴이면 위에 1순위 버튼 하나뿐이라, 나머지 후보를 여기에 둔다.
        # (조건은 위 분기와 같은 converged여야 한다 — 다르면 수렴 케이스의
        #  다른 후보가 어디에도 안 나온다.)
        llm_alt = opts[1:] if dec.converged else []
        labels = list(llm_alt) + [lb for lb, _, _ in _extra] + [MANUAL]
        n = len(labels) - 1
        # 고를 번호가 없으면(후보가 이미 다 올라간 경우) '선택지 0개'라 쓰지 않는다.
        head = f"다른 번호로 확정하기  ·  선택지 {n}개" if n else "직접 입력해서 확정하기"
        with st.expander(head):
            # _extra 라벨과 '직접 입력'은 _rank에 없어 그대로 나온다.
            sel = st.radio("번호", labels, index=None, key=f"{key}_r",
                           label_visibility="collapsed", format_func=_opt_label)
            typed = st.text_input("번호 입력", key=f"{key}_t", placeholder="예: 720.2") \
                if sel == MANUAL else ""
            if sel == MANUAL:
                final, rank, src = typed.strip(), None, "manual"
            elif sel in llm_alt:
                final, rank, src = sel, _rank.get(sel, (None, False))[0], "llm"
            elif sel:
                final, rank, src = next((h, None, s) for lb, h, s in _extra if lb == sel)
            else:
                final, rank, src = "", None, ""
            if final and top and final != top.h:
                _why_box()
            if st.button("이 번호로 확정", disabled=not final, key=f"{key}_go"):
                _confirm(final, rank, src)

    if not dec.converged:
        # 갈림 — 기본 선택을 비운다. 그래야 그 클릭이 '관성'이 아니라 '판단'으로 기록된다.
        # 라디오에는 경합 후보만 둔다(선택 강제의 초점이 흐려지지 않게). 나머지는 접기로.
        pick = st.radio("아래에서 골라주세요", opts, index=None, horizontal=True, key="pick",
                        format_func=_opt_label)
        if pick and top and pick != top.h:
            _why_box()
        if st.button("확정", type="primary", disabled=not pick):
            _confirm(pick, _rank.get(pick, (None, False))[0], "llm")
        _other_ui("esc")
    else:
        # 수렴 — 기본값이 채워진 버튼 하나. 이견이 있을 때만 펼친다.
        # (수렴이어도 이 버튼은 사서가 누른다 — 클릭이 한 번일 뿐이다.)
        if st.button(f"✅ {top.h} 확정", type="primary"):
            _confirm(top.h, 1, "llm")
        _other_ui("conv")

    # ══ 근거 ══ 탭으로 쪼개지 않는다. 판단 과정은 순서가 있어서, 탭에 넣으면
    #    사서가 결론 칸만 누르고 끝낸다. 클릭해야 보이는 것은 '원자료(서가 책)'뿐이다.
    st.divider()
    st.markdown("#### 어떻게 이 번호가 됐나")

    fit_by_h = {a.h: a for a in dec.assessments}    # 번호 → 그 서가의 독립 fit

    def _badge(h: str, shelf: int | None = None, prior: bool = False) -> str:
        """'기각'은 082에만 쓴다. 082는 1단계에서 명시적으로 평가되지만, 종합서지의
        나머지 번호는 '판단해서 버린' 게 아니라 '후보로 안 올린' 것이다.
        여기에 기각이라 쓰면 '왜 기각했는지 근거가 없다'는 오해가 생긴다."""
        if h in fit_by_h:
            b = "✅ 채택" if (top and h == top.h) else "⬜ 후보"
        else:
            b = "❌ 기각" if prior else "– 후보 아님"
        # 본교에 그 번호대 책이 없으면 서가 의미를 읽을 수 없다 → 판정 옆에 한 줄로 붙인다.
        return f"{b} · 본교 미보유" if shelf == 0 else b

    def _shelf(cn) -> None:
        if not cn.shelf_books:
            return          # 미보유는 판정 배지에 이미 붙어 있다. 빈 칸을 또 만들지 않는다.
        with st.expander(f"▸ {cn.ddc_h} 서가 책 {cn.shelf_count}건 보기"):
            st.dataframe(
                [{"제목": b.title, "저자": b.author, "발행": b.publisher,
                  "주제명": b.detail.get("일반주제명", "")} for b in cn.shelf_books],
                width="stretch", hide_index=True)

    # 082도 종합서지도 아닌 번호를 LLM이 냈을 때만 셋째 탭이 생긴다(평소엔 2칸).
    known = ({prior.ddc_h} if prior else set()) | {cn.ddc_h for cn in retr.union_candidates}
    others = [a for a in dec.assessments if a.h not in known]

    names = ["① 업체 청구기호 (082)", "② 종합서지 청구기호"] + (["③ 그 밖의 후보"] if others else [])
    tabs = st.tabs(names)

    with tabs[0]:
        if prior is None:
            st.caption("082 없음 — 종합서지부터 판단했습니다.")
        else:
            st.markdown(f"### `{prior.ddc_h}` → {_badge(prior.ddc_h, prior.shelf_count, prior=True)}")
            st.caption(f"본교 {prior.ddc_h} 서가 {prior.shelf_count}건")
            # 같은 번호가 다른 채널에서도 발견됐으면 그 사실을 보여준다.
            # 독립 근거로 세는 게 아니라 "어디서 관찰됐나"의 기록이다 — 점수와 무관.
            _also = []
            if "종합서지" in prior.sources:
                _also.append(f"종합서지 {prior.union_votes}개 대학 중복 "
                             f"(승계 가능성이 있어 득표로는 미반영)")
            if "키워드검색" in prior.sources:
                _also.append(f"키워드검색에도 걸림({prior.keyword_hits}권)")
            if _also:
                st.caption("함께 발견: " + " · ".join(_also))
            # 082 서가의 fit은 **전 과정에서 한 번만** 매겨진다. 그 하나가 곧 근거다.
            a = fit_by_h.get(prior.ddc_h) or out.prior
            if a:
                st.caption(f"서가 요약: {a.shelf_label}")
                st.markdown(f"> {a.fit_reasoning}")
                st.caption(f"이 서가만 놓고 본 적합도 {a.shelf_fit:.2f}"
                           + (f" · 근거 도서: {', '.join(a.cited_books)}" if a.cited_books else ""))
            else:
                # 082가 없거나 구버전 회차 데이터를 열었을 때만 여기로 온다.
                st.info("**후보에서 제외했습니다.** 판단 근거는 위 '종합 판단 근거'를 보세요.")
            _shelf(prior)

    with tabs[1]:
        if not retr.union_candidates:
            st.caption("종합서지에서 이 책을 찾지 못했습니다.")
        else:
            if vmeta["matched_title"]:
                st.caption(f"매칭: 「{vmeta['matched_title']}」 · "
                           f"DDC {vmeta['ddc_libraries']}개 대학(서강 {vmeta['home_excluded']}개 제외) · "
                           f"KDC {vmeta['kdc_libraries']}개(미집계) · 082는 이 집계에서 제외")
            # 막대 = 쏠렸나 흩어졌나를 한눈에. 표 = 정확한 숫자와 판정. 역할이 다르다.
            # st.bar_chart 대신 altair를 쓰는 이유는 labelAngle=0 하나 때문이다.
            # 기본값이면 '780.9'가 7/8/0/./9 로 세로로 쪼개져 읽을 수 없다.
            _bars = pd.DataFrame([{"번호": cn.ddc_h, "대학 수": cn.union_votes}
                                  for cn in retr.union_candidates[:6]])
            st.altair_chart(
                alt.Chart(_bars).mark_bar().encode(
                    x=alt.X("번호:N", sort=None, axis=alt.Axis(labelAngle=0, title=None)),
                    y=alt.Y("대학 수:Q"),
                ).properties(height=180),
                width="stretch")
            st.dataframe(
                [{"번호": cn.ddc_h, "종합서지": f"{cn.union_votes}개 대학",
                  "본교 서가": f"{cn.shelf_count}건", "후보 채택": _badge(cn.ddc_h, cn.shelf_count)}
                 for cn in retr.union_candidates],
                width="stretch", hide_index=True)
            st.caption("※ 위 막대·표는 **타대학이 실제로 매긴 번호 전부**입니다(후보 목록이 아닙니다). "
                       "이 중 시스템이 후보로 올린 번호에만 아래에 근거가 붙습니다.")
            st.caption("※ 대학 수는 '분류 기준'이 아니라 '어느 번호부터 본교에서 확인할지'의 순서 힌트입니다.")
            st.divider()
            for cn in retr.union_candidates:
                a = fit_by_h.get(cn.ddc_h)
                if a:
                    st.markdown(f"**{a.h}** {_badge(a.h, cn.shelf_count)} · 적합도 {a.shelf_fit:.2f}")
                    if "키워드검색" in cn.sources:
                        st.caption(f"함께 발견: 키워드검색에도 걸림({cn.keyword_hits}권) — 증거 중첩")
                    st.caption(f"서가 요약: {a.shelf_label}")
                    st.markdown(f"> {a.fit_reasoning}")
                    if a.cited_books:
                        st.caption(f"근거 도서: {', '.join(a.cited_books)}")
                _shelf(cn)

    # 2단계에서 종결됐으면 그 사실을 근거 칸에도 남긴다 — 키워드 후보가 없는 게 '못 찾아서'가
    # 아니라 '안 찾아서'임을 사서가 알아야 한다.
    if not out.inherited and not getattr(out, "went_keyword", True):
        st.info("**종합서지까지로 충분했습니다.** 점수·격차 기준을 모두 넘어 "
                "키워드 검색은 하지 않았습니다.")

    if others:
        with tabs[2]:
            st.caption("082에도 종합서지에도 없던 번호입니다. 본교 서가 의미로만 판단했습니다.")
            for a in others:
                st.markdown(f"### `{a.h}` {_badge(a.h)} · 적합도 {a.shelf_fit:.2f}")
                st.caption(f"서가 요약: {a.shelf_label}")
                st.markdown(f"> {a.fit_reasoning}")
                if a.cited_books:
                    st.caption(f"근거 도서: {', '.join(a.cited_books)}")
