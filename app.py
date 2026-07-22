"""lib_copilot 데모 — 사서 청구기호(852 ▼h) 부여 보조.

실제 사서 업무 3단계를 화면으로 그대로 옮긴다 (docs/design.md §2.2):
  1. 원제 검색     → 기존 서지 있으면 청구기호 승계 (결정론적, LLM 불필요)
  2. 082 청구기호  → 본교에서 그 번호가 뭘 의미하나 확인
  3. 제목 검색     → 청구기호 분포 → 상위 N의 의미 정합성을 LLM이 판단 → HITL

설계상 지켜야 할 것:
  - 저장소 접근은 `db.py`만 (pandas가 여기로 새지 않는다).
  - LLM 호출은 `classify.py` 하나뿐 → 3단계에서 그 함수를 **그대로 재사용**한다.
    (분포 버킷을 RetrieveResult로 옮겨 담아 넘긴다. classify는 손대지 않음)

실행: streamlit run app.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import db  # noqa: E402
from classify import classify  # noqa: E402
from config import RUNS_DIR, SCENARIO_DIR  # noqa: E402
from schema import BookInput, CandidateNumber, DistBucket, RetrieveResult  # noqa: E402

st.set_page_config(page_title="lib_copilot — 청구기호 보조", page_icon="📚", layout="wide")


# ── 캐시: Streamlit은 위젯 조작마다 스크립트를 처음부터 다시 실행한다.
#    @st.cache_data가 없으면 매번 JSON 1682건을 다시 읽는다.
@st.cache_data
def _stats() -> dict:
    return db.stats()


@st.cache_data
def _callno(ddc: str):
    return db.find_by_callno(ddc)


@st.cache_data
def _original(q: str):
    return db.find_by_original_title(q)


@st.cache_data
def _dist(q: str, top: int, extra: tuple[str, ...] = (), ddc_082: str = "",
          rejected: bool = False, min_overlap: int = 2):
    # 부제·키워드를 반드시 함께 넘긴다. 제목 토큰만으로는 겹침 2개를 못 채워
    # 매칭 0건이 되기 쉽다(「가장 인간적인 도시」가 실제로 그렇다).
    # 082도 넘긴다 → db가 retrieve_token과 똑같이 강제 포함·정렬한다.
    return db.title_distribution(q, top=top, keywords=list(extra),
                                 ddc_082=ddc_082 or None, prior_rejected=rejected,
                                 min_overlap=min_overlap)


def _log_decision(row: dict) -> None:
    """사서의 최종 결정을 runs/*.jsonl에 한 줄 append (AGENTS.md 로깅 규약).

    쌓이면 'LLM 1순위와 사서 최종이 얼마나 갈리나'를 실측할 수 있다.
    ⚠️ collection.json에는 절대 되쓰지 않는다 — 결정 결과가 DB에 들어가면
       평가 때 정답이 코퍼스에 섞여 데이터 누출이 된다(docs/design.md §8).
    """
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"decisions_{datetime.now():%Y%m%d}.jsonl"
    row = {"ts": datetime.now().isoformat(timespec="seconds"), **row}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def _buckets_to_retrieve(book: BookInput, buckets: list[DistBucket],
                         total_hits: int) -> RetrieveResult:
    """3단계 분포 → classify가 먹는 계약(RetrieveResult)으로 변환.

    이 어댑터 덕분에 classify.py를 한 줄도 고치지 않고 재사용한다.
    """
    prior = book.ddc_082
    cands = [
        CandidateNumber(
            ddc_h=b.ddc_h,
            is_082_prior=(b.ddc_h == prior),
            title_hit_count=b.count,
            shelf_count=b.shelf_count,
            title_hits=[x.title for x in b.books],
            shelf_sample=b.shelf_sample,
        )
        for b in buckets
    ]
    # 082는 분포에 없어도 후보에 강제로 넣는다(prior는 항상 판단 대상).
    if prior and not any(c.ddc_h == prior for c in cands):
        cands.insert(0, CandidateNumber(
            ddc_h=prior, is_082_prior=True, title_hit_count=0,
            shelf_count=db.shelf_count(prior), shelf_sample=db.shelf_titles(prior),
        ))
    return RetrieveResult(
        retriever="db_distribution",
        query_terms=[],
        messages=[f"제목 매칭 {total_hits}건 → 청구기호 {len(cands)}종."],
        total_title_hits=total_hits,
        candidates=cands,
    )


# ══ 사이드바: DB 상태 ══════════════════════════════════════
s = _stats()
st.sidebar.title("📚 lib_copilot")
st.sidebar.caption("사서 청구기호(852 ▼h) 부여 보조")
st.sidebar.metric("본교 장서 DB", f"{s['총 장서']:,}건")
st.sidebar.metric("청구기호 종류", f"{s['청구기호 종류']}종")
if s["상세정보 보유"] == 0:
    st.sidebar.warning("상세정보 0건 — 상세페이지 크롤 전입니다.\n원제 검색(1단계)이 반쪽으로 동작합니다.")
else:
    st.sidebar.metric("상세정보 보유", f"{s['상세정보 보유']:,}건")

# collection.json이 갱신돼도(크롤 중) 앱은 시작 시점 데이터를 캐시로 붙들고 있다.
# db._df()의 lru_cache와 st.cache_data를 둘 다 비워야 새로 읽는다.
if st.sidebar.button("🔄 DB 새로고침", help="크롤이 진행 중이면 눌러서 최신 데이터를 다시 읽습니다"):
    db._df.cache_clear()
    st.cache_data.clear()
    st.rerun()

st.title("청구기호 채택 보조")
st.caption("타도서관 DB 검색으로 청구기호 후보가 주어졌다고 가정하고, 본교 정합성을 확인합니다.")

# ══ 단계 선택 ═════════════════════════════════════════════
# st.tabs가 아니라 st.radio를 쓰는 이유: 탭은 순수 클라이언트 위젯이라 서버가
# 프로그램으로 전환시킬 수 없다. 2단계의 "정합 안 함 → 3단계로"가 실제로 화면을
# 넘기려면 상태로 제어해야 한다. (key를 주지 않아야 index로 제어된다)
STEPS = [
    "1️⃣ 원제·기존 서지 확인",
    "2️⃣ 082 의미 정합성",
    "3️⃣ 분포 + 의미 정합성 (LLM)",
]
st.session_state.setdefault("step_idx", 0)
step = st.radio("단계", STEPS, index=st.session_state.step_idx,
                horizontal=True, label_visibility="collapsed")
st.session_state.step_idx = STEPS.index(step)


def _goto(i: int) -> None:
    """다음 단계로 화면을 넘긴다."""
    st.session_state.step_idx = i
    st.rerun()


st.divider()

# ── 1단계 ────────────────────────────────────────────────
if step == STEPS[0]:
    st.subheader("원서나 기존 서지 여부를 확인합니다")
    st.caption("기존 서지·판본이 본교에 있으면 그 청구기호를 **그대로 승계**합니다. LLM 판단이 필요 없는 결정론적 단계입니다.")
    q1 = st.text_input("원제 검색", placeholder="예: Le Capitalisme est-il moral?", key="q1")
    if q1:
        found = _original(q1)
        if found:
            st.success(f"기존 서지 {len(found)}건 — 청구기호 승계 가능")
            st.dataframe(db.books_to_table(found), use_container_width=True, hide_index=True)
            st.info(f"→ 승계 후보: **{found[0].ddc_h}** (번역서면 ▼m에 `K`)")
        else:
            st.warning("본교에 기존 서지 없음 → 2단계로 진행합니다.")
    if s["상세정보 보유"] == 0:
        st.caption("⚠️ 지금은 DB에 `원서명` 필드가 없어 **제목 부분일치로만** 찾습니다. 상세페이지 크롤 후 정상 동작합니다.")
    if st.button("2단계로 →", key="go2"):
        _goto(1)

# ── 2단계 ────────────────────────────────────────────────
elif step == STEPS[1]:
    st.subheader("본교 도서관과의 청구기호 의미 정합성을 확인합니다")
    st.caption("업체가 준 082가 본교에서 실제로 **무슨 주제**인지, 그 번호대에 꽂힌 책들로 확인합니다.")
    q2 = st.text_input("업체 082 청구기호 입력하세요", placeholder="예: 720.105", key="q2")
    if q2:
        books = _callno(q2)
        if books:
            st.success(f"본교 {q2} 번호대: {len(books)}건")
            st.dataframe(db.books_to_table(books), use_container_width=True, hide_index=True)
            st.caption("이 책들이 곧 이 번호의 '의미'입니다. 신규 도서 주제와 align 하는지 보세요.")
        else:
            st.error(f"본교에 {q2} 번호대 책이 **없습니다**.")
            st.caption("본교에 없는 번호는 후보로 낼 수 없습니다(design.md §2.3의 본교-only 한계). 3단계로 진행하세요.")

        # ── 판정(HITL). 이 결과가 3단계에서 082의 신분을 바꾼다.
        st.divider()
        st.markdown(f"**082({q2})가 이 책의 주제와 정합합니까?**")
        v1, v2 = st.columns(2)
        if v1.button("정합함 — 082 채택하고 종료", key="keep082"):
            st.session_state["v082"] = ("kept", q2)
        if v2.button("정합 안 함 — 3단계로", key="rej082"):
            st.session_state["v082"] = ("rejected", q2)
            st.session_state["q3_082"] = q2   # 3단계 082 칸에 미리 채워둔다
            _goto(2)          # 판정과 동시에 3단계 화면으로 넘어간다
        st.caption("082는 업체가 준 prior일 뿐입니다. 주제와 맞으면 유지, 아니면 기각하고 분포로 재선택합니다.")

    verdict, v_num = st.session_state.get("v082", (None, None))
    if verdict == "kept":
        st.success(f"✅ 082({v_num}) 채택 — 3단계로 갈 필요 없습니다.")
        if st.button("결정 기록", key="log_kept"):
            p = _log_decision({"step": 2, "ddc_082": v_num, "verdict": "082_kept",
                               "final": v_num})
            st.caption(f"💾 {p.name}에 기록")
    elif verdict == "rejected":
        st.warning(f"⊗ 082({v_num}) 기각됨 — 3단계에서 분포로 재선택합니다.\n\n"
                   f"기각된 번호는 후보 목록에는 **남지만** 1순위 자리는 내려놓습니다. "
                   f"(근거를 남기고, LLM이 기각을 반박할 여지도 남기기 위해)")
        if st.button("3단계로 →", key="go3"):
            _goto(2)

# ── 3단계 ────────────────────────────────────────────────
else:
    st.subheader("청구기호 분포 및 의미 정합성을 확인합니다")
    st.caption("※ 원칙은 타대학 종합서지 분포이나, 현재는 **본교 DB로 대체**합니다(업무 절차와 다름).")

    # ── 골든셋 예제 불러오기 (시연용)
    # 실전에서는 사서가 키워드를 직접 넣어야 하지만(design.md §7 미해결),
    # 시연에서는 골든셋 입력을 그대로 재현해 타이핑 없이 보여준다.
    _cases = sorted(SCENARIO_DIR.glob("*.json"))
    e1, e2 = st.columns([3, 1])
    pick = e1.selectbox("골든셋 예제 불러오기", ["(직접 입력)"] + [p.stem for p in _cases],
                        help="시연용. 케이스의 제목·부제·키워드·082를 아래 칸에 채웁니다.")
    if pick != "(직접 입력)" and e2.button("불러오기", key="loadcase"):
        d = json.loads((SCENARIO_DIR / f"{pick}.json").read_text(encoding="utf-8"))["input"]
        st.session_state["q3"] = d.get("title", "")
        st.session_state["q3sub"] = d.get("subtitle") or ""
        st.session_state["q3kw"] = ", ".join(d.get("keywords", []))
        st.session_state["q3_082"] = d.get("ddc_082") or ""
        st.session_state.pop("result", None)      # 이전 판정 결과는 지운다
        st.rerun()

    c1, c2, c3 = st.columns([3, 1, 1])
    title = c1.text_input("확인하려는 책의 제목 입력하세요", placeholder="예: 가장 인간적인 도시", key="q3")
    subtitle = c1.text_input("부제 (선택)", placeholder="예: 실리콘밸리의 젊은 건축가가 안내하는 AI시대 건축", key="q3sub")
    kw_raw = c1.text_input("키워드 (선택, 쉼표로 구분)", placeholder="도시, 건축, 공간", key="q3kw")
    # 082는 2단계 판정을 이어받는다. 여기 칸이 따로 있는 이유:
    #  (a) 082가 아예 없는 책은 2단계를 건너뛰고 여기서 시작한다(082는 선택 입력)
    #  (b) 2단계를 안 거치고 바로 분포부터 보고 싶을 때 덮어쓸 수 있어야 한다
    v2_verdict, v2_num = st.session_state.get("v082", (None, None))
    # value= 대신 session_state로만 채운다. 둘을 같이 쓰면 Streamlit이 경고를 낸다.
    st.session_state.setdefault("q3_082", "")
    prior082 = c2.text_input("082 (2단계에서 자동)",
                             placeholder="없으면 비워두세요", key="q3_082")
    topn = c3.number_input("상위 N", 1, 10, 3, key="topn")
    # 검색 강도: 기본 2는 평가 경로와 동일. 0건이면 1로 낮춰 넓게 훑는다(recall 우선).
    minov = c3.select_slider("검색 강도", options=[1, 2, 3], value=2, key="minov",
                             help="제목 토큰이 몇 개 겹쳐야 건질까. 낮을수록 넓게.")

    rejected = (v2_verdict == "rejected" and v2_num == prior082)
    if rejected:
        st.info(f"⊗ **082({prior082})는 2단계에서 사서가 기각**했습니다. "
                f"후보에는 남기되 1순위 자리는 빼고, LLM에게도 그 사실을 알립니다.")
    elif prior082:
        st.caption("2단계 판정이 없습니다 → 082를 prior로 대접해 후보 맨 앞에 둡니다.")

    keywords = [k.strip() for k in kw_raw.split(",") if k.strip()]

    if title:
        extra = tuple(([subtitle] if subtitle else []) + keywords)
        buckets, total = _dist(title, int(topn), extra, prior082, rejected, int(minov))
        if not buckets:
            st.error(f"제목 매칭 0건 — 후보를 못 건졌습니다. (검색 강도 {minov})")
            wider, _ = _dist(title, int(topn), extra, prior082, rejected, 1)
            if int(minov) > 1 and wider:
                st.info(f"💡 **검색 강도를 1로 낮추면 {len(wider)}종**이 잡힙니다: "
                        + ", ".join(f"{b.ddc_h}({b.count}권)" for b in wider))
            else:
                st.caption("조어·신조어 제목은 토큰 검색으로 안 잡힙니다(케이스11 부린왕자). "
                           "부제·키워드를 넣거나, 국중 API로 목차·책소개를 받아 키워드를 채워야 합니다.")
        else:
            dist_txt = " / ".join(f"**{b.ddc_h}** {b.count}권" for b in buckets)
            st.markdown(f"청구기호 분포: {dist_txt}  ·  (제목 매칭 총 {total}건)")

            for b in buckets:
                mark = " ⊗ 사서가 2단계에서 기각" if (rejected and b.ddc_h == prior082) else ""
                with st.expander(f"{b.ddc_h}{mark} — {b.count}권 매칭 · 서가 {b.shelf_count}건(샘플)"):
                    st.dataframe(db.books_to_table(b.books),
                                 use_container_width=True, hide_index=True)

            st.divider()
            st.markdown(f"**상위 {len(buckets)}가지의 의미 정합성을 확인할까요?**")
            st.caption(f"→ 확인 대상 도서 + 각 번호대 책들(총 {sum(len(b.shelf_sample) for b in buckets)}권)을 LLM에 넘깁니다.")

            if st.button("YES — 의미 정합성 확인", type="primary"):
                book = BookInput(title=title, subtitle=subtitle or None,
                                 ddc_082=prior082 or None, keywords=keywords)
                r = _buckets_to_retrieve(book, buckets, total)
                # 사서가 앞 단계에서 내린 판정을 LLM에 함께 넘긴다(C안의 핵심).
                notes = None
                if rejected:
                    notes = (f"082({prior082})를 사서가 2단계에서 확인하고 "
                             f"'이 책 주제와 align 하지 않는다'며 기각했습니다. "
                             f"이 번호는 후보 목록에 남아 있으나 이미 한 번 탈락한 것입니다. "
                             f"분포와 서가 의미로 재선택하되, 기각이 부당해 보이면 "
                             f"근거와 함께 반박해도 됩니다.")
                with st.spinner("LLM이 주제·급을 판단하는 중…"):
                    st.session_state["result"] = classify(book, r, notes)
                st.session_state["ctx"] = {"title": title, "ddc_082": prior082,
                                           "rejected": rejected,
                                           "dist": {b.ddc_h: b.count for b in buckets}}

    if res := st.session_state.get("result"):
        st.divider()
        st.markdown("### 의미 정합성 확인 결과")
        st.caption("책의 주제 · 종류 · 목적성 관점에서의 순위입니다.")

        for i, c in enumerate(res.candidates, 1):
            st.markdown(f"**[{i}] {c.h}** — {c.label}  ·  적합도 `{c.confidence:.2f}`  ·  `{c.source.value}`")
            st.write(c.reasoning)

        if res.notes:
            st.info(f"**종합 판단 근거:** {res.notes}")

        if res.escalate:
            st.warning(f"⚠️ **사서 판단 필요(HITL)** — 신호: {', '.join(x.value for x in res.signals)}\n\n"
                       f"모호도 {res.ambiguity:.2f}. 단일 정답을 강제하지 않습니다.")

        top = res.candidates[0].h if res.candidates else "-"
        ctx = st.session_state.get("ctx", {})

        def _finish(final: str, action: str) -> None:
            """사서 최종 결정을 runs/에 남긴다. collection.json은 건드리지 않는다."""
            p = _log_decision({
                "step": 3, **ctx,
                "llm_top": top, "llm_ambiguity": res.ambiguity,
                "escalated": res.escalate,
                "signals": [x.value for x in res.signals],
                "action": action, "final": final,
                "agreed": final == top,
            })
            st.success(f"✅ ▼h = {final} 채택  ·  💾 runs/{p.name}에 기록")

        st.markdown(f"**최종 {top}(으)로 진행할까요?**")
        d1, d2, d3 = st.columns([1, 1, 2])
        if d1.button("YES"):
            _finish(top, "accept")
        if d2.button("NO"):
            st.info("후보를 다시 검토하세요. 키워드를 바꿔 3단계를 재실행할 수 있습니다.")
            _log_decision({"step": 3, **ctx, "llm_top": top, "action": "reject",
                           "final": None, "agreed": False})
        manual = d3.text_input("직접 입력", placeholder="예: 720.2", key="manual")
        if manual:
            _finish(manual, "manual")
