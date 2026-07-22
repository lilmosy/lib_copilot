# lib_copilot

도서관 청구기호(DDC 852 ▼h) 부여를 돕는 **사서-AI 협업 에이전트** (Ver 1 개발 중).

신규 도서의 서지 정보를 입력하면 → 실제 사서 업무 순서대로 검색하고 → **▼h 후보 여러 개 + 판단 근거(XAI)**를 제시한다.
정답이 갈리는 케이스는 단일 답을 강제하지 않고 **사람 사서에게 넘긴다(HITL)**.

> 기반: 교수님의 [`sogang_renAIssance`](../../sogang_renAIssance) 설계를 참조하되, **실제 업무 프로세스**에 맞춰 다시 설계.
> 전체 설계 배경은 [`docs/design.md`](docs/design.md) 참고.

---

## 왜 이 문제인가

현재 청구기호(특히 852 ▼h)를 정하는 과정은 **repetitive하고 비일관적**이다.
- repetitive → **자동화**한다.
- 비일관적(사서 암묵지) → **일관된 로직**으로 대체하고, 사람은 판단이 갈리는 지점에만 개입한다.

→ 시간 단축 + 암묵지(tacit)를 분명한 규칙으로.

## 핵심 개념

### 1. 업무 프로세스 = 파이프라인
하/중 난이도 케이스의 공통 패턴은 파이프라인화된다:

```
082 확인 → 본교서지 청구기호 검색(의미 확인) → 종합서지 제목 검색(분포 확인) → 필요시 키워드 검색 → 확정
```

082(업체 DDC)는 **선택 입력(prior)**. 있으면 청구기호 검색으로, 없으면 제목 검색으로 진입한다.
에이전트는 이 경로를 **말로 설명하며** 진행한다(그 자체가 XAI).

### 2. 난이도 = 라벨이 아니라 "에스컬레이션 신호의 강도"
하/중/상은 딱 나뉘는 정답 라벨이 아니다. 시스템은 난이도를 *입력으로 받지 않고*, **검색 결과의 모양을 보고 스스로 판정**한다.
아래 신호가 강하면 단일 정답을 강제하지 않고 후보+근거를 제시하며 HITL로 올린다:
- 후보가 성격 다른 **대분류를 넘나듦** (문학 800 vs 부동산 330 vs 성찰 170)
- 제목·메타만으론 부족 → **목차/책소개 필요**
- 제목 검색 **분포가 흩어짐**
- **기관 간 분류 상이** (교보·알라딘 등)

### 3. 시뮬레이션 전제
실제 도서관 시스템에 붙지 않는다. 고은님 근무 기록(케이스 분석 문서)을 **정답셋**으로,
"업무 중 마주치는 책들"을 모은 DB(`data/collection.json`)를 검색 대상으로 두고 시뮬레이션한다.

## 아키텍처 — funnel 2단계

```
collection.json (본교 장서 1682건)
   ① 1차 retrieve (recall 넓게)  → 후보 번호 + 각 번호대 샘플 책
   ② 2차 classify (LLM, precision) → ▼h 후보 + 근거 + 에스컬레이션 ★
```

1차는 **두 구현, 같은 계약** (내부만 다름 / 2차·배선 불변). `config.RETRIEVER`로 스위치하고 평가로 비교:

| 모듈 | 역할 | LLM? |
|------|------|------|
| [src/tokens.py](src/tokens.py) | 토큰 규칙 (retrieve_token ↔ db 공유) | ❌ |
| [src/retrieve_token.py](src/retrieve_token.py) | 1차 V1: 제목 토큰 겹침 (recall) | ❌ |
| [src/retrieve_embed.py](src/retrieve_embed.py) | 1차 V2: 임베딩 의미 유사 (백엔드 결정 후) | ❌ |
| [src/db.py](src/db.py) | 저장소 창구 (내부 pandas, 밖으론 스키마 타입만) | ❌ |
| [src/classify.py](src/classify.py) | 2차: ▼h 판단 + 에스컬레이션 (**주제 우선·개수 보조**) | ✅ (유일) |
| [src/pipeline.py](src/pipeline.py) | 배선 + 검색기 스위치 | — |
| [eval/evaluate.py](eval/evaluate.py) | 골든셋 평가 (검색기별 비교) | — |

> 제목 유사도는 "후보를 넓게 건지는 그물"일 뿐, 정답 결정자가 아니다. 결정은 2차 LLM이 **주제**로. (자세히: [docs/design.md](docs/design.md))

### 입구가 둘, 판단부는 하나

```
【평가 경로】 run.py → pipeline.py → retrieve_token ─┐
                                                    ├─→ classify.py → Claude
【데모 경로】 app.py → db.py → (어댑터) ─────────────┘
```

차이는 **누가 후보를 만드느냐**뿐이다. 평가는 자동으로(골든셋 채점), 데모는 사서가 단계별로 좁힌다(HITL).
`classify.py`부터는 완전히 같다. (design.md §2.1.1)

## 데모 (Streamlit)

실제 사서 업무 3단계를 화면으로 옮긴 것:

```
1️⃣ 원제 검색      → 기존 서지 있으면 청구기호 승계 (결정론적, LLM 불필요)
2️⃣ 082 확인       → 본교에서 그 번호가 무슨 주제인지 확인
                     [정합함 → 채택·종료]  /  [정합 안 함 → 3단계로]
3️⃣ 제목 검색      → 청구기호 분포 (A/B/C 각 x/y/z권)
                     → 상위 N의 의미 정합성을 LLM이 판단 → 순위 + 근거
                     → [YES / NO / 직접 입력]  → runs/*.jsonl에 기록
```

- 기각된 082는 후보에 **남되 1순위 자리는 빼고**, LLM에게 "사서가 기각함"을 알린다 (design.md §5.1).
- 시연용 **골든셋 예제 불러오기** 드롭다운 — 케이스 3/5/11을 클릭 한 번으로 재현.
- 앱은 **읽기 전용**이다. `collection.json`에 되쓰지 않는다(데이터 누출 방지).

## 실행

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # ANTHROPIC_API_KEY 입력

streamlit run app.py                               # 데모 (브라우저)
python src/run.py                                  # 케이스 5 분류 (output/에 덤프)
LIBCOPILOT_RETRIEVER=embed python src/run.py       # V2(임베딩)로 실행 (구현 후)
python eval/evaluate.py                            # 골든셋 평가 리포트

python crawler/callno_crawler.py                   # 청구기호 목록 크롤 (넓게)
python crawler/detail_crawler.py --resume          # 상세페이지 크롤 (깊게, ~3h)
```

## 개발 상태 (마일스톤)

- **M0~M4 완료** — 스키마 · 크롤 DB(1682건) · 골든셋 3케이스 · funnel 배선 · classify(Claude) · **Streamlit 데모 + 상세페이지 크롤**.
- 평가: Exact 33 / Prefix 67 / Top-k 67.
- **다음:** ① 국중 ISBN 서지정보 API(목차·책소개로 입력 보강) ② 홀드아웃 ③ V2(임베딩).

> ⚠️ **V2가 1번이 아닌 이유:** 케이스11에 키워드 2개만 넣으면 정답이 바로 뜬다.
> 병목은 검색 방식이 아니라 **입력 메타데이터 부재**였다. (design.md §7.1)

시행착오·의사결정은 [`docs/devlog.md`](docs/devlog.md), 실행 산출물은 `output/`에 기록한다.

## 구조

```
lib_copilot/
├── app.py         Streamlit 데모 (데모 경로 입구)
├── docs/          design.md(설계 정본) · devlog.md(시행착오 로그)
├── data/          collection.json(DB + detail) · *_coverage.json · scenarios/(골든셋)
├── crawler/       callno_crawler(목록·넓게) · detail_crawler(상세·깊게)
├── src/           schema · config · tokens · retrieve_* · db · classify · pipeline · run
├── eval/          evaluate.py
├── output/        케이스별 실행 산출물 + 분석 md
└── runs/          decisions_*.jsonl (데모 결정 로그)
```
