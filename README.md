# lib_copilot workspace

프로젝트를 실제 데모 구현과 비교실험 자료로 나눠 둔 상위 폴더입니다.

- [`real/`](real/): 단독으로 실행·배포할 수 있는 현재 구현. 프로젝트 설명과 작업 문서도 여기가 정본입니다.
- [`experiment/`](experiment/): 다섯 프롬프트 비교를 재현하기 위한 시나리오·스크립트·결과 보관소입니다.

데모 개발은 `real/`에서 시작합니다.

```bash
cd real
pip install -r requirements.txt
streamlit run app.py
```
