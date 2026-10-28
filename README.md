# gpt-claude-pipeline

GPT와 Claude가 서로의 코드를 검증하는 4단계 코드 생성 파이프라인입니다.
API 키 없이 **ChatGPT 구독(Plus/Pro)** 과 **Claude 구독(Pro)** 으로 동작합니다.

| 단계 | 담당 | 역할 |
|---|---|---|
| 1 | GPT (Codex CLI) | 요구사항으로 코드 초안 작성 |
| 2 | Claude (Claude Code CLI) | 가독성·효율·예외 처리 리팩토링 |
| 3 | GPT (새 일회성 세션) | 버그·누락 요구사항 검증, `PASS` / `FIX_NEEDED` 판정 |
| 4 | Claude | 지적을 하나씩 검증해 반영/기각하고 최종 코드 완성 |

## 준비

```bash
npm install -g @openai/codex @anthropic-ai/claude-code
codex login     # "Sign in with ChatGPT"
claude          # /login → 구독 계정으로 로그인 후 /exit
```

`OPENAI_API_KEY`, `ANTHROPIC_API_KEY` 등 API 키 환경변수는 CLI에 넘기지 않으므로 구독으로만 과금됩니다.

## 사용법

```bash
python pipeline.py "요구사항 문장"
python pipeline.py -f requirement.txt
```

기존 대화를 이어 쓰려면 대화 이름 또는 세션 ID와, 그 대화를 사용한 프로젝트 폴더를 함께 지정합니다.

```bash
python pipeline.py -f requirement.txt \
  --claude-session "<이름 또는 ID>" --claude-project-dir "<폴더>" \
  --codex-session "<이름 또는 ID>" --codex-project-dir "<폴더>"
```

- 지정한 기존 대화에는 새 턴이 기록됩니다.
- Codex 대화는 1단계 초안에만 쓰고, 3단계 검증은 독립성을 위해 항상 새 세션에서 실행합니다.
- 각 단계 결과는 `runs/<타임스탬프>/`에 저장됩니다.

모델은 `GPT_MODEL`, `CLAUDE_MODEL` 환경변수로 지정할 수 있습니다(비우면 각 CLI 기본 모델).

## 파일

- `pipeline.py` — 파이프라인 본체
- `test_pipeline.py` — 모델을 호출하지 않는 로컬 테스트 (`python -m unittest test_pipeline`)
- `SECURITY_REVIEW.md` — 보안 검토 내용과 남은 한계
- `domain_count.py` — 파이프라인으로 생성한 예제 결과물(CSV 이메일 도메인별 회원 수 집계, 멀티프로세싱)
