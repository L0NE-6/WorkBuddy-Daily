# WorkBuddy Daily

**WorkBuddy 國內版與國際版每日活躍的單檔案自動化套件。**

[简体中文](../README.md) | [English](README.en.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Português (BR)](README.pt-BR.md)

> 本頁是繁體中文快速指南；完整技術說明以[簡體中文 README](../README.md)為準。

## 概覽

| 項目 | 說明 |
| :--- | :--- |
| 用途 | 每日簽到、成長任務、小程式任務鏈、互動玩法與獎勵回讀 |
| 憑證 | 國內版 `WORKBUDDY_REFRESH_TOKEN`；國際版 `WORKBUDDY_INTL_TOKENS` |
| 執行環境 | Python 3.8+、青龍、GitHub Actions、Windows / Linux |
| 主要依賴 | `requests` |
| 成功判據 | 回讀簽到狀態、任務進度與領獎結果，不以單次 HTTP 200 判定成功 |

## 內含工具

- `workbuddy_daily.py`：國內版每日任務、小程式任務、互動玩法與報告。
- `workbuddy_login.py`：簡訊登入並輸出 `手機號:AT:RT`。
- `workbuddy_intl_daily.py`：國際版每日活躍與保活。
- `workbuddy_intl_login.py`：國際版裝置授權登入。
- `credits_report.py`：可選的積分與到期報告。

## 快速開始

```bash
pip install requests
python workbuddy_login.py
```

把輸出填入：

```text
WORKBUDDY_REFRESH_TOKEN=手機號:AT:RT
```

青龍定時任務請使用面板執行器：

```bash
task workbuddy_daily.py
```

推薦排程：

```cron
0 7,12 * * *
30 23 * * *
```

第二個排程用於夜貓子活動。

## GitHub Actions

Fork 或 Import 倉庫後，在 **Settings > Secrets and variables > Actions** 新增 Secrets。先設定 `WORKBUDDY_REFRESH_TOKEN`，國際版可再設定 `WORKBUDDY_INTL_TOKENS`。首次請手動 Run workflow，確認成功後再啟用排程。

## 國際版

```bash
python workbuddy_intl_login.py
python workbuddy_intl_daily.py
```

國內版與國際版登入態不通用，請勿混用 Token。

## 常用變數

| 變數 | 用途 |
| :--- | :--- |
| `WORKBUDDY_REFRESH_TOKEN` | 國內版帳號，一行一個 `手機號:AT:RT` |
| `WORKBUDDY_INTL_TOKENS` | 國際版帳號憑證 |
| `WORKBUDDY_TASKS` | 可選任務白名單 |
| `WORKBUDDY_SKIP_TASKS` | 可選任務黑名單 |
| `WORKBUDDY_STATE_DIR` | 持久化 Token 輪換狀態的目錄 |
| `PUSHPLUS_TOKEN`、`BARK_URL`、`WXPUSHER_APP_TOKEN`、`WECOM_WEBHOOK`、`DINGTALK_WEBHOOK` | 可選推送渠道 |

## 安全提醒

不要提交 AT、RT、登入結果或執行期狀態檔。所有憑證只放在環境變數或加密 Secrets 中；Token 輪換後產生的本機狀態檔必須排除在 Git 之外。

## 使用邊界與轉載聲明

官方倉庫、腳本、文件與 Releases 均免費公開。禁止包裝成付費資源、刪除署名、冒充作者、使用作者身份或二維碼背書，亦禁止搬運到無關帳號或群組引流。完整規則見[使用邊界與轉載聲明](../NOTICE.md)。如有違規，作者可要求下架，並可暫停更新、封存或刪除倉庫。

## 連結

- [Releases](https://github.com/L0NE-6/WorkBuddy-Daily/releases)
- [Issues](https://github.com/L0NE-6/WorkBuddy-Daily/issues)
- [更新日誌](../CHANGELOG.md)
- [完整簡體中文文件](../README.md)
