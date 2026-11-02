# WorkBuddy Daily

**WorkBuddy 中国版・国際版のデイリーアクティビティをまとめて自動化する単一ファイルツール。**

[简体中文](../README.md) | [English](README.en.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Português (BR)](README.pt-BR.md)

> このページは日本語クイックガイドです。完全な技術資料は[簡体字中国語 README](../README.md)を参照してください。

## 概要

| 項目 | 内容 |
| :--- | :--- |
| 目的 | 毎日チェックイン、成長タスク、ミニプログラムタスク、報酬受取を自動化 |
| 認証情報 | 中国版 `WORKBUDDY_REFRESH_TOKEN`、国際版 `WORKBUDDY_INTL_TOKENS` |
| 実行環境 | Python 3.8+、Qinglong、GitHub Actions、Windows / Linux |
| 主な依存 | `requests` |
| 成功判定 | チェックイン状態、タスク進捗、報酬結果を再取得して確認 |

## 収録ツール

- `workbuddy_daily.py`: 中国版のデイリータスク、ミニプログラム、ゲーム、レポート。
- `workbuddy_login.py`: SMS ログインで `phone:AT:RT` を出力。
- `workbuddy_intl_daily.py`: 国際版デイリーアクティビティとキープアライブ。
- `workbuddy_intl_login.py`: 国際版のデバイス認可ログイン。
- `credits_report.py`: クレジット残高と有効期限の任意レポート。

## クイックスタート

```bash
pip install requests
python workbuddy_login.py
```

出力値を設定します。

```text
WORKBUDDY_REFRESH_TOKEN=phone:AT:RT
```

Qinglong ではパネルのタスクランナー経由で実行します。

```bash
task workbuddy_daily.py
```

推奨スケジュール:

```cron
0 7,12 * * *
30 23 * * *
```

夜間アクティビティには 2 本目のスケジュールが必要です。

## GitHub Actions

Fork または Import 後、**Settings > Secrets and variables > Actions** に Secrets を登録します。最初に `WORKBUDDY_REFRESH_TOKEN`、国際版は必要に応じて `WORKBUDDY_INTL_TOKENS` を設定してください。最初は手動実行で確認し、その後スケジュールを有効化します。

## 国際版

```bash
python workbuddy_intl_login.py
python workbuddy_intl_daily.py
```

中国版と国際版のログイン状態は互換性がありません。

## 主な環境変数

| 変数 | 用途 |
| :--- | :--- |
| `WORKBUDDY_REFRESH_TOKEN` | 中国版アカウント、1 行に 1 件の `phone:AT:RT` |
| `WORKBUDDY_INTL_TOKENS` | 国際版アカウント認証情報 |
| `WORKBUDDY_TASKS` | 実行対象タスクの許可リスト |
| `WORKBUDDY_SKIP_TASKS` | スキップするタスクの拒否リスト |
| `WORKBUDDY_STATE_DIR` | ローテーション済み Token の永続保存先 |
| `PUSHPLUS_TOKEN`、`BARK_URL`、`WXPUSHER_APP_TOKEN`、`WECOM_WEBHOOK`、`DINGTALK_WEBHOOK` | 任意の通知チャネル |

## セキュリティ

AT、RT、ログイン結果、実行時状態ファイルを Git にコミットしないでください。認証情報は環境変数または暗号化された GitHub Secrets に保存し、ローテーション後の状態ファイルもリポジトリ外に置きます。

## 利用範囲と再配布について

公式リポジトリ、スクリプト、ドキュメント、Releases は無料で公開されています。有料リソースへの再包装、著作者表示の削除、作者のなりすまし、作者名や QR コードによる推薦表現、無関係なアカウントやグループへの転載による集客は禁止です。詳細は[利用範囲と再配布に関する通知](../NOTICE.md)を参照してください。違反時は削除要請の対象となり、作者は更新停止、アーカイブ、リポジトリ削除を行う場合があります。

## リンク

- [Releases](https://github.com/L0NE-6/WorkBuddy-Daily/releases)
- [Issues](https://github.com/L0NE-6/WorkBuddy-Daily/issues)
- [変更履歴](../CHANGELOG.md)
- [完全な中国語ドキュメント](../README.md)
