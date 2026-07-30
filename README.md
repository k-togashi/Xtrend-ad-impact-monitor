# X Trend Ad Impact Checker

Xトレンドを取得し、リスティング広告に関連する検索が増えそうな話題をAIで一次判定して、Google Sheetsへ保存し、必要に応じてWindows通知を出すPythonツールです。

目的は「Xで話題になっているものを集めること」ではなく、広告運用者が早めに確認すべき市場変化を見つけることです。

## 解決したい課題

広告運用では、ニュース、制度変更、価格変動、SNS上の話題化によって関連する検索が短時間で増えることがあります。

毎回Xトレンドを目視で確認し、担当ジャンルへの影響を判断するのは手間がかかります。このツールでは、トレンド取得からAI判定、履歴保存、重要トレンドの通知までを最小構成で自動化します。

## 処理フロー

1. Windowsタスクスケジューラから1時間ごとに自動実行
2. X APIでトレンドを取得
3. トレンド名と順位をOpenAI APIへ渡す
4. AIが対象広告ジャンルに関連する検索が増える可能性を判定
5. 判定結果をJSONで表示
6. Google Sheetsへ履歴保存
7. `impact_score` が閾値以上ならWindows通知

Google Sheetsの保存列は、取得日時、実行ID、WOEID、順位、トレンド名、impact_score、ad_genres、reason、X検索URLの9列です。

## AI判定

AIには、トレンドが「対象広告ジャンルに属するか」ではなく、「対象広告ジャンルに関連する検索が増える可能性があるか」を判定させています。

判定では、トレンドから具体的な検索行動までを自然に説明できるかという「直接性」を重視しています。

- 高く評価する例：「ミームコイン」→ 仮想通貨に関する検索が増える可能性がある
- 高く評価する例：「新NISA」→ 株・証券口座に関する検索が増える可能性がある
- 除外する例：「男子バレー → ストレス → AGA」のように、複数段階の推測が必要な関連付け

`impact_score` は0〜10で出力します。関連がほぼない場合は0とし、対象ジャンルに関する検索が増える可能性を具体的に説明できるものほど高く評価します。

AIは検索の変化を検知するための一次判定に使用し、その後の広告への影響や対応については人が判断します。

また、判定結果はStructured OutputsとPydanticを使って決まった形式に構造化し、後続のGoogle Sheets保存や通知処理で扱いやすい形にしています。

## 対象広告ジャンル

対象広告ジャンルは、公開コードには直接書かず、外部設定ファイルで管理しています。

実運用では `genres.txt` を作成し、判定したい広告ジャンルを1行につき1ジャンル設定します。`genres.txt` は `.gitignore` によりGit管理対象外としています。

公開用には `genres.example.txt` を用意しており、実運用のジャンル情報を公開リポジトリに含めない設計にしています。

## 実装済みの機能

- Windowsタスクスケジューラによる1時間ごとの定期実行
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

※ChatworkやDiscordなど、外部サービスへの通知は現時点では未実装です。

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

Google Sheetsへ保存するには、Google Cloudで発行したサービスアカウントの認証JSONを google-service-account.json としてプロジェクト直下に配置し、保存先スプレッドシートをサービスアカウントへ共有します。

## 実行方法

通常実行では、Xトレンド取得、AI判定、Google Sheetsへの履歴保存を行い、impact_score が閾値以上のトレンドがある場合はWindows通知を表示します。

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

実運用ではWindowsタスクスケジューラに登録し、1時間ごとに通常実行しています。

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
- 今後は、トレンド名だけでは意味を特定しにくい話題への対応や、AI判定精度の検証、Chatwork等の外部サービスへの通知などを改善できます。

## 参考

- [X API - Trends by WOEID](https://docs.x.com/x-api/trends/get-trends-by-woeid)
- [X Developer Console](https://console.x.com/)
- [OpenAI API Documentation](https://platform.openai.com/docs)
- [Google Sheets API](https://developers.google.com/sheets/api)
