# WorkBuddy Daily

**A single-file automation suite for WorkBuddy CN and International daily activity.**

[简体中文](../README.md) | [English](README.en.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Português (BR)](README.pt-BR.md)

> This is a localized quick start. The [Simplified Chinese README](../README.md) remains the complete technical reference.

## Overview

| Item | Details |
| :--- | :--- |
| Purpose | Daily check-in, growth tasks, mini-program task chains, interactive rewards, and verified reward collection |
| Credentials | CN: `WORKBUDDY_REFRESH_TOKEN`; International: `WORKBUDDY_INTL_TOKENS` |
| Runtime | Python 3.8+, Qinglong, GitHub Actions, Windows, or Linux |
| Main dependency | `requests` |
| Verification | Reads back check-in status, task progress, and reward state instead of trusting a single HTTP 200 |

## Included scripts

- `workbuddy_daily.py`: WorkBuddy CN daily tasks, mini-program tasks, games, and reporting.
- `workbuddy_login.py`: SMS login helper that outputs a ready-to-use `phone:AT:RT` credential.
- `workbuddy_intl_daily.py`: WorkBuddy International daily activity and keepalive flow.
- `workbuddy_intl_login.py`: device authorization helper for WorkBuddy International accounts.
- `credits_report.py`: optional credit balance and expiry report.

## Quick start

```bash
pip install requests
python workbuddy_login.py
```

Add the returned value to the credential variable:

```text
WORKBUDDY_REFRESH_TOKEN=phone:AT:RT
```

For Qinglong, create a task and run it through the panel task runner:

```bash
task workbuddy_daily.py
```

Recommended schedules:

```cron
0 7,12 * * *
30 23 * * *
```

The second window is required for the late-night activity.

## GitHub Actions

Fork or import the repository, then add the required Secrets under **Settings > Secrets and variables > Actions**. Start with `WORKBUDDY_REFRESH_TOKEN`; international users may also set `WORKBUDDY_INTL_TOKENS`. Run the `WorkBuddy Daily` workflow manually once before enabling a schedule.

## International activity

```bash
python workbuddy_intl_login.py
python workbuddy_intl_daily.py
```

Use `WORKBUDDY_INTL_TOKENS` for multiple accounts. Tokens are not interchangeable between CN and International domains.

## Common variables

| Variable | Purpose |
| :--- | :--- |
| `WORKBUDDY_REFRESH_TOKEN` | CN account credentials, one `phone:AT:RT` per line |
| `WORKBUDDY_INTL_TOKENS` | International account credentials |
| `WORKBUDDY_TASKS` | Optional task allowlist |
| `WORKBUDDY_SKIP_TASKS` | Optional task denylist |
| `WORKBUDDY_STATE_DIR` | Persistent state directory for rotated tokens |
| `PUSHPLUS_TOKEN`, `BARK_URL`, `WXPUSHER_APP_TOKEN`, `WECOM_WEBHOOK`, `DINGTALK_WEBHOOK` | Optional notification channels |

## Security

Never commit access tokens, refresh tokens, login results, or runtime state files. Use environment variables or encrypted GitHub Secrets. Rotated refresh tokens may be written to local state and must stay outside Git.

## Usage and redistribution

The official repository, scripts, documentation, and Releases are free. Do not repackage them as paid resources, remove attribution, impersonate the author, use the author's identity or QR codes as endorsement, or repost them through unrelated accounts or groups for traffic. See the [Usage and Redistribution Notice](../NOTICE.md). Violations may result in takedown requests; the author may pause updates, archive, or delete the repository.

## Links

- [Releases](https://github.com/L0NE-6/WorkBuddy-Daily/releases)
- [Issues](https://github.com/L0NE-6/WorkBuddy-Daily/issues)
- [Changelog](../CHANGELOG.md)
- [Complete Chinese documentation](../README.md)
