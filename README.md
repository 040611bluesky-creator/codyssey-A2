# 국내 여행 추천 CLI

날짜를 넣으면 LLM(Gemini)과 지도/장소 API(카카오 로컬)를 연동해 국내 여행지를 추천하고, 맛집과 1일 일정이 담긴 마크다운 리포트를 만드는 프로그램입니다.

## 실행 방법

의존성을 설치한 뒤, 여행 날짜를 `YYYY-MM-DD` 형식으로 넘겨 실행합니다.

```bash
pip install -r requirements.txt
python main.py --date "YYYY-MM-DD"
```

예시:

```bash
python main.py --date "2026-09-01"
```

## API 키 설정

1. 프로젝트 루트의 `.env.example`을 복사해 `.env`를 만듭니다.
2. `.env`에 아래 두 값을 **본인이 발급한 키**로 채웁니다.

```
GEMINI_API_KEY=
KAKAO_REST_API_KEY=
```

- `GEMINI_API_KEY`: Google AI Studio에서 발급
- `KAKAO_REST_API_KEY`: Kakao Developers 앱의 REST API 키

실제 키 값은 README나 소스 코드에 적지 마세요.

## 결과물 확인

실행이 끝나면 `results/` 폴더에 파일이 생깁니다.

- `results/{date}_travel_plan.md` — 최종 여행 리포트
- `results/{date}_travel_plan.json` — 1차 추천, 맛집 목록, 오류 목록

예: `--date "2026-09-01"`이면 `results/2026-09-01_travel_plan.md`와 `results/2026-09-01_travel_plan.json`을 확인하면 됩니다.

## 캐싱

같은 날짜로 다시 실행하면 `results/{date}_travel_plan.json`이 이미 있을 때 캐시로 동작합니다.

- 1차 추천(LLM)과 맛집 검색(지도 API)은 호출하지 않습니다.
- 저장된 데이터로 리포트만 다시 만들어 `.md`를 갱신합니다.

처음부터 API를 다시 호출하려면 해당 날짜의 JSON 파일을 지운 뒤 실행하세요.

## 보안 주의사항

- API 키를 코드, 커밋, README에 직접 작성하지 마세요.
- `.env`는 `.gitignore`에 포함되어 있어 git에 올라가지 않습니다.
- `.env.example`에는 자리 표시만 두고, 실제 키는 로컬 `.env`에만 보관하세요.
