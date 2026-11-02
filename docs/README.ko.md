# WorkBuddy Daily

**WorkBuddy 중국판과 국제판 일일 활동을 자동화하는 단일 파일 도구입니다.**

[简体中文](../README.md) | [English](README.en.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Português (BR)](README.pt-BR.md)

> 이 문서는 한국어 빠른 시작 안내입니다. 전체 기술 문서는 [중국어 간체 README](../README.md)를 기준으로 합니다.

## 개요

| 항목 | 설명 |
| :--- | :--- |
| 목적 | 일일 체크인, 성장 작업, 미니프로그램 작업 체인, 보상 수령 자동화 |
| 자격 증명 | 중국판 `WORKBUDDY_REFRESH_TOKEN`, 국제판 `WORKBUDDY_INTL_TOKENS` |
| 실행 환경 | Python 3.8+, Qinglong, GitHub Actions, Windows / Linux |
| 주요 의존성 | `requests` |
| 성공 기준 | 체크인 상태, 작업 진행률, 보상 결과를 다시 조회해 확인 |

## 포함 도구

- `workbuddy_daily.py`: 중국판 일일 작업, 미니프로그램, 게임, 리포트.
- `workbuddy_login.py`: SMS 로그인 후 `phone:AT:RT` 출력.
- `workbuddy_intl_daily.py`: 국제판 일일 활동과 연결 유지.
- `workbuddy_intl_login.py`: 국제판 기기 인증 로그인.
- `credits_report.py`: 선택형 크레딧 잔액 및 만료 리포트.

## 빠른 시작

```bash
pip install requests
python workbuddy_login.py
```

출력값을 환경 변수에 설정합니다.

```text
WORKBUDDY_REFRESH_TOKEN=phone:AT:RT
```

Qinglong에서는 패널 작업 실행기로 실행합니다.

```bash
task workbuddy_daily.py
```

권장 스케줄:

```cron
0 7,12 * * *
30 23 * * *
```

두 번째 스케줄은 야간 활동에 필요합니다.

## GitHub Actions

Fork 또는 Import 후 **Settings > Secrets and variables > Actions** 에 Secrets를 추가합니다. 먼저 `WORKBUDDY_REFRESH_TOKEN`을 설정하고, 국제판은 필요할 때 `WORKBUDDY_INTL_TOKENS`를 추가합니다. 처음에는 수동 실행으로 확인한 뒤 스케줄을 활성화하세요.

## 국제판

```bash
python workbuddy_intl_login.py
python workbuddy_intl_daily.py
```

중국판과 국제판 로그인 상태는 서로 호환되지 않습니다.

## 주요 환경 변수

| 변수 | 용도 |
| :--- | :--- |
| `WORKBUDDY_REFRESH_TOKEN` | 중국판 계정, 한 줄에 하나의 `phone:AT:RT` |
| `WORKBUDDY_INTL_TOKENS` | 국제판 계정 자격 증명 |
| `WORKBUDDY_TASKS` | 선택 실행 작업 허용 목록 |
| `WORKBUDDY_SKIP_TASKS` | 건너뛸 작업 차단 목록 |
| `WORKBUDDY_STATE_DIR` | 회전된 Token의 영구 저장 경로 |
| `PUSHPLUS_TOKEN`, `BARK_URL`, `WXPUSHER_APP_TOKEN`, `WECOM_WEBHOOK`, `DINGTALK_WEBHOOK` | 선택형 알림 채널 |

## 보안

AT, RT, 로그인 결과, 실행 상태 파일을 Git에 커밋하지 마세요. 자격 증명은 환경 변수 또는 암호화된 GitHub Secrets에만 두고, 회전 후 생성되는 상태 파일도 저장소 밖에 보관해야 합니다.

## 사용 범위 및 재배포 안내

공식 저장소, 스크립트, 문서 및 Releases는 무료로 공개됩니다. 유료 리소스로 재포장하거나 저작자 표시를 제거하고, 작성자를 사칭하거나 작성자 이름 또는 QR 코드를 보증 수단으로 사용하는 행위, 무관한 계정이나 그룹으로 옮겨 트래픽을 유도하는 행위를 금지합니다. 자세한 내용은 [사용 범위 및 재배포 안내](../NOTICE.md)를 참고하세요. 위반 시 삭제 요청 대상이 될 수 있으며 작성자는 업데이트 중단, 보관 또는 저장소 삭제를 진행할 수 있습니다.

## 링크

- [Releases](https://github.com/L0NE-6/WorkBuddy-Daily/releases)
- [Issues](https://github.com/L0NE-6/WorkBuddy-Daily/issues)
- [변경 로그](../CHANGELOG.md)
- [전체 중국어 문서](../README.md)
