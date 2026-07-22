# CLAUDE.md — lib_copilot

> 이 파일은 세션 시작 시 자동 로드된다. **상세 내용을 여기 복붙하지 말고**, 아래 문서로 안내만 한다.

## 한 줄 소개
도서관 청구기호(852 ▼h) 부여를 돕는 **사서-AI 협업 에이전트**. 실제 사서 업무를 funnel 파이프라인으로 이식하고, 판단이 갈리는 지점만 사람에게 넘긴다(HITL). 시나리오 골든셋으로 평가하며 고도화.

## 먼저 읽을 문서 (여기에 진짜 내용이 있다)
- **[docs/design.md](docs/design.md)** — 설계 정본. 두 경로(§2.1.1)·데이터 계약(§3)·모듈(§4)·082 3상태(§5.1)·**키워드 문제(§7.1)**·평가/누출(§8)·**외부 데이터 소스(§11)**.
- **[docs/devlog.md](docs/devlog.md)** — 시행착오·의사결정 시간순 로그. "무슨 일이 있었나"는 여기.
- **[AGENTS.md](AGENTS.md)** — 코딩/작업 규약(불변식). 코드 손대기 전 필독.
- **[output/](output/)** — 케이스별 실행 분석(`*_analysis.md`).

## 아키텍처 (한 줄)
`BookInput → ① retrieve(recall 넓게) → ② classify(Claude, 주제·급 판단 + 에스컬레이션) → ▼h 후보`. LLM은 classify 한 곳만.

**입구가 둘, 판단부는 하나** (design.md §2.1.1):
```
【평가】 run.py → pipeline.py → retrieve_token ─┐
                                               ├─→ classify.py → Claude
【데모】 app.py → db.py → (어댑터) ─────────────┘
```
차이는 "누가 후보를 만드느냐"뿐. 평가는 자동, 데모는 사서가 단계별로 좁힌다.

## 실행
```bash
cp .env.example .env      # ANTHROPIC_API_KEY (루트 .env도 자동 탐색)
streamlit run app.py                 # 데모 (브라우저, 업무 3단계)
python src/run.py                    # 평가 경로 케이스 실행 → output/ 덤프
python eval/evaluate.py              # 골든셋 평가 (Exact/Prefix/Top-k)
python crawler/callno_crawler.py     # 청구기호 목록 크롤 (넓게)
python crawler/detail_crawler.py --resume   # 상세페이지 크롤 (깊게, ~3h, 중간저장)
```

## 현재 상태 (2026-07-22)
- funnel 관통 + **Streamlit 데모** 완료. DB = 본교 장서 **1682건**. 검색기는 **token**(V1)만, embed(V2)는 자리만.
- 골든셋 3케이스 평가 Exact 33 / Prefix 67 / Top-k 67.
- 상세페이지 크롤 진행 중 — 완료되면 `일반주제명`(58%)·`ISBN`(75%)·`원서명`(6%) 확보.

## 다음 할 일 & 주의
- **다음 (우선순위 재조정됨, design.md §10):**
  1. **국중 ISBN 서지정보 API**(§11.2) — 목차·책소개로 입력 보강. 키워드 문제의 근본 해법
  2. 홀드아웃(누출 제거)
  3. V2(임베딩) — ⚠️ 1번보다 **뒤로 밀렸다**(아래 참조)
- ⚠️ **키워드 문제(§7.1) — 최대 난제.** 골든셋 `input.keywords`는 **수기로 넣은 것**인데 실전엔 없다. 제목만 입력하면 대부분 매칭 0건. 케이스11에 키워드 2개만 넣으면 정답이 바로 뜬다 → **병목은 검색 방식이 아니라 입력 메타데이터.** 그래서 V2보다 국중 API가 먼저다.
- ⚠️ **평가 경로는 동결.** `retrieve_token`·`classify` 프롬프트를 바꾸면 점수 변화의 원인을 알 수 없다. 데모 기능은 기본 꺼진 선택 인자로. 손댔으면 `output/*_retrieve.json`과 대조.
- ⚠️ **데이터 누출:** DB를 같은 도서관에서 크롤해, 시나리오 입력 도서가 DB에 있으면 정답째로 누출(케이스3). 공정 평가엔 홀드아웃 필요. **결정 결과를 collection.json에 되쓰지 말 것.** (§8)
- ⚠️ 애매 케이스는 top pick이 **비결정적** → Exact 말고 Top-k+에스컬레이션으로 평가.
- ⚠️ **서강 상세페이지에 목차는 없다(0%).** 국중 API가 유일 경로. (§11.1)

## 작업 후
- 시행착오는 `docs/devlog.md`에, 실행 분석은 `output/*_analysis.md`에 남긴다.
