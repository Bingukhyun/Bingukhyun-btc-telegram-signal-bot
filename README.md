# Food Emulsion & Lipid Processing Daily Digest

매일 아침(한국시간 07:00)에 **food emulsion / 식품 유화 / 유지가공 / lipid processing** 관련 최신 논문·기사·산업 동향을 자동 수집해서 Telegram으로 보내는 GitHub Actions 프로젝트입니다.

## 1) 이 프로젝트가 하는 일
- Crossref, PubMed, Europe PMC, Google News RSS에서 최근 소식 수집
- 중복 제거(DOI/PMID/URL/유사 제목)
- 중요 키워드 기반 우선순위 점수화
- 한국어 요약 생성(OpenAI 키 있으면 AI 요약, 없으면 규칙 기반 요약)
- Telegram으로 자동 발송(또는 dry_run 테스트 출력)

## 2) Telegram Bot 만드는 방법
1. Telegram에서 `@BotFather` 검색
2. `/newbot` 입력
3. 봇 이름/아이디 설정
4. 발급된 토큰을 복사 (예: `123456:ABC...`)

## 3) TELEGRAM_BOT_TOKEN 확인 방법
- BotFather가 준 토큰 문자열 전체를 사용합니다.
- 이 값을 GitHub Secrets의 `TELEGRAM_BOT_TOKEN`에 저장합니다.

## 4) TELEGRAM_CHAT_ID 확인 방법
쉬운 방법(개인 채팅):
1. 만든 봇에게 아무 메시지나 보냅니다.
2. 브라우저에서 아래 호출:
   - `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
3. 응답 JSON에서 `chat.id` 값을 확인
4. 값을 GitHub Secrets의 `TELEGRAM_CHAT_ID`에 저장

## 5) GitHub Secrets 설정 방법
GitHub 저장소 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

필수 Secrets:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `OPENAI_API_KEY` (선택: 없으면 규칙 기반 요약)

## 6) GitHub Variables 설정 방법
같은 메뉴에서 **Variables** 탭으로 이동 후 아래 값 등록(없으면 기본값 사용):
- `OPENAI_MODEL` (기본: `gpt-4o-mini`)
- `MAX_ITEMS` (기본: `12`)
- `LOOKBACK_HOURS` (기본: `24`)
- `NCBI_EMAIL` (권장: 본인 이메일)
- `CROSSREF_MAILTO` (권장: 본인 이메일)

## 7) GitHub Actions 수동 실행 방법
1. GitHub 저장소 → **Actions**
2. **Daily Food Emulsion Digest** 워크플로 선택
3. **Run workflow** 클릭
4. `dry_run` 값을 `true` 또는 `false` 선택해서 실행

## 8) dry_run=true 테스트 방법
- `dry_run=true`로 실행하면 Telegram 전송 없이 로그/출력만 생성됩니다.
- 처음 세팅 검증 시 반드시 `true`로 먼저 테스트하세요.

## 9) 매일 아침 자동 실행 원리
- 워크플로의 cron이 UTC 기준 `0 22 * * *`로 설정되어 있습니다.
- UTC 22:00은 한국시간(Asia/Seoul) 다음날 07:00입니다.
- 즉 매일 한국시간 아침 7시에 자동 실행됩니다.

## 10) 검색어 수정 방법
- `config/topics.json` 파일에서 `topics`, `priority_keywords`를 수정하면 됩니다.

## 11) 오류가 났을 때 확인할 위치
1. GitHub 저장소 → **Actions**
2. 실패한 실행 클릭
3. `Run daily digest` 단계 로그 확인
4. 흔한 원인:
   - Secrets 오타/누락
   - Telegram chat id 미일치
   - 외부 API 일시 오류

## 12) 보안 주의사항(중요)
- 토큰/API 키/Chat ID를 코드 파일에 직접 넣지 마세요.
- 반드시 GitHub Secrets/Variables로 관리하세요.
- 실수로 커밋했다면 즉시 키를 폐기(revoke)하고 재발급하세요.

## 로컬에서 간단 실행(선택)
```bash
pip install -r requirements.txt
python src/digest.py --dry-run true
```
