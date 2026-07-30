# X Trend Ad Impact Checker

Xトレンドを取得し、リスティング広告の検索需要に影響しそうな話題をAIで一次判定して、Google Sheetsへ保存し、必要に応じてWindows通知を出すPythonツールです。

目的は「Xで話題になっているものを集めること」ではなく、広告運用者が早めに確認すべき市場変化を見つけることです。

## 解決したい課題

広告運用では、ニュース、制度変更、価格変動、SNS上の話題化によって検索需要が短時間で変わることがあります。

毎回Xトレンドを目視で確認し、担当ジャンルへの影響を判断するのは手間がかかります。このツールでは、トレンド取得からAI判定、履歴保存、重要トレンドの通知までを最小構成で自動化します。

## 処理フロー

1. X APIでトレンドを取得
2. トレンド名と順位をOpenAI APIへ渡す
3. AIが対象広告ジャンルへの影響可能性を判定
4. 判定結果をJSONで表示
5. Google Sheetsへ履歴保存
6. `impact_score` が閾値以上ならWindows通知

Google Sheetsの保存列は、取得日時、実行ID、WOEID、順位、トレンド名、impact_score、ad_genres、reason、X検索URLの9列です。

## AI判定

AIには、トレンドが対象ジャンルの検索・比較・申込み行動に影響しそうかを判定させています。

単純なキーワード一致では拾いにくい関連トレンドも、自然な検索行動として説明できる場合は評価します。一方で、複数段階の連想や根拠の弱い推測は除外する方針です。

`impact_score` は0から10です。関連がほぼない場合は0、検索需要や申込み行動の変化を具体的に説明できる場合に高く評価します。

AI出力はStructured OutputsとPydanticで構造化し、`ad_genres` には外部設定ファイルで読み込んだジャンルだけを許可します。

## 対象広告ジャンル

対象広告ジャンルは、公開コードには直接書かず、外部設定ファイルで管理します。

実運用では `genres.txt` を作成し、1行につき1ジャンルを書いてください。このファイルは `.gitignore` によりGit管理対象外です。

```text
実運用ジャンル1
実運用ジャンル2
実運用ジャンル3
```

公開用の例として `genres.example.txt` を用意しています。実案件を特定できるジャンル名は、このファイルやREADMEには書かない方針です。

## 実装済みの機能

- X API v2 `Trends by WOEID` によるトレンド取得
- OpenAI APIによる広告影響判定
- 外部ファイルによる対象広告ジャンル管理
- Pydanticを使ったAI出力の構造化
- Google Sheets APIによる履歴保存
- シートが空の場合の見出し行作成
- `impact_score` が閾値以上のトレンドをWindowsポップアップ通知
- X APIを使わずAI判定だけを検証する `--test-ai` モード
- X APIを使わずAI判定と通知を確認する `--test-notification` モード
- `.env` によるAPIキー・設定値の管理

Chatwork通知、Discord通知、定期実行は現時点では未実装です。

## 使用技術

- Python
- X API v2
- OpenAI API
- Google Sheets API
- Pydantic
- python-dotenv
- requests

## セットアップ

PowerShellでこのフォルダを開き、以下を実行します。

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
Copy-Item .env.example .env
Copy-Item genres.example.txt genres.txt
```

`.env` に必要な値を設定します。

```text
X_BEARER_TOKEN=ここにX APIのBearer Tokenを入力
X_WOEID=取得したい地域のWOEIDを入力
OPENAI_API_KEY=ここにOpenAI APIキーを入力
OPENAI_MODEL=gpt-4o-mini
AD_GENRES_FILE=genres.txt
GOOGLE_SERVICE_ACCOUNT_FILE=google-service-account.json
GOOGLE_SPREADSHEET_ID=保存先スプレッドシートIDを入力
GOOGLE_SHEET_NAME=トレンド履歴
NOTIFICATION_IMPACT_SCORE_THRESHOLD=5
```

`genres.txt` には、実際に判定したい広告ジャンルを1行ずつ設定します。

Google Sheetsへ保存する場合は、Google Cloudで発行したサービスアカウントの認証JSONを `google-service-account.json` としてプロジェクト直下に置きます。保存先スプレッドシートには、サービスアカウントのメールアドレスを共有ユーザーとして追加してください。

既存シートに旧仕様の「投稿数」列が残っている場合は、見出し行を現在の9列構成に合わせてください。

## 実行方法

通常実行では、Xトレンド取得、AI判定、Google Sheets保存、Windows通知まで行います。

```powershell
py main.py
```

AI判定だけを確認したい場合は、`--test-ai` を使います。このモードではX APIとGoogle Sheets APIは使いません。

```powershell
py main.py --test-ai
```

AI判定から通知まで確認したい場合は、`--test-notification` を使います。このモードでもX APIとGoogle Sheets APIは使いません。

```powershell
py main.py --test-notification
```

## セキュリティ上の配慮

- APIキーやトークンはコードに直接書かず、`.env` から読み込みます。
- 実運用ジャンルを含む `genres.txt` はGit管理対象外です。
- `.env` とGoogleサービスアカウントの認証JSONはGit管理対象外です。
- Xトレンド取得にはスクレイピングではなく公式APIを使います。
- AIの出力はStructured Outputsで制限し、後続処理に渡す形式を固定しています。

## 制約と今後の改善余地

- X APIの利用にはプランやレート制限の影響があります。
- 判定はトレンド名と順位をもとにしており、個別投稿本文やニュース本文までは参照していません。
- AI判定は一次スクリーニングであり、最終判断には人の確認が必要です。
- 通知機能、定期実行、判定閾値、ジャンル設定の運用ルールなどは今後改善できます。

## 参考

- [X API - Trends by WOEID](https://docs.x.com/x-api/trends/get-trends-by-woeid)
- [X Developer Console](https://console.x.com/)
- [OpenAI API Documentation](https://platform.openai.com/docs)
- [Google Sheets API](https://developers.google.com/sheets/api)
