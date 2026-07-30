"""Xトレンドを取得し、広告検索への影響度をAIで判定するプログラム。"""

import argparse
import ctypes
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from google.auth.exceptions import GoogleAuthError
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ConfigDict, Field, create_model


SHEET_HEADERS = [
    "取得日時（JST）",
    "実行ID",
    "WOEID",
    "順位",
    "トレンド名",
    "impact_score",
    "ad_genres",
    "reason",
    "X検索URL",
]
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
DEFAULT_NOTIFICATION_IMPACT_SCORE_THRESHOLD = 5
DEFAULT_AD_GENRES_FILE = "genres.txt"

# AI判定だけを確認するための一時的なテストデータ
AI_TEST_TRENDS = [
    {"trend_name": "大型セール"},
    {"trend_name": "新制度発表"},
    {"trend_name": "価格改定"},
    {"trend_name": "比較ランキング"},
    {"trend_name": "スポーツ大会"},
    {"trend_name": "記念イベント"},
    {"trend_name": "$SAMPLE"},
]


def get_settings():
    """.envから必要な設定を読み込む。"""
    load_dotenv()
    bearer_token = os.getenv("X_BEARER_TOKEN")
    woeid = os.getenv("X_WOEID")
    openai_api_key = os.getenv("OPENAI_API_KEY")
    openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    if not bearer_token or not woeid or not openai_api_key:
        raise ValueError(
            ".env に X_BEARER_TOKEN、X_WOEID、OPENAI_API_KEY を設定してください。"
        )

    if not woeid.isdigit():
        raise ValueError("X_WOEID には数字だけを設定してください。")

    return bearer_token, woeid, openai_api_key, openai_model


def get_openai_settings():
    """AI判定テストに必要なOpenAI設定だけを読み込む。"""
    load_dotenv()
    openai_api_key = os.getenv("OPENAI_API_KEY")
    openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    if not openai_api_key:
        raise ValueError(".env に OPENAI_API_KEY を設定してください。")

    return openai_api_key, openai_model


def get_ad_genres_file():
    """広告ジャンル設定ファイルの場所を読み込む。"""
    load_dotenv()
    return Path(os.getenv("AD_GENRES_FILE", DEFAULT_AD_GENRES_FILE))


def load_ad_genres():
    """広告ジャンルを外部ファイルから1行ずつ読み込む。"""
    genres_file = get_ad_genres_file()
    if not genres_file.is_file():
        raise ValueError(
            f"広告ジャンル設定ファイルが見つかりません: {genres_file}\n"
            "genres.example.txt を参考に genres.txt を作成してください。"
        )

    genres = []
    for line in genres_file.read_text(encoding="utf-8").splitlines():
        genre = line.strip()
        if genre and not genre.startswith("#"):
            genres.append(genre)

    if not genres:
        raise ValueError("広告ジャンル設定ファイルに1件以上のジャンルを設定してください。")

    if len(genres) != len(set(genres)):
        raise ValueError("広告ジャンル設定ファイルに重複したジャンルがあります。")

    return genres


def create_trend_evaluation_batch_model(allowed_ad_genres):
    """読み込んだジャンルだけを許可するAI出力モデルを作る。"""
    ad_genre_enum = Enum(
        "AdGenre",
        {f"GENRE_{index}": genre for index, genre in enumerate(allowed_ad_genres, start=1)},
        type=str,
    )
    trend_evaluation_model = create_model(
        "TrendEvaluation",
        __config__=ConfigDict(extra="forbid"),
        trend_name=(str, ...),
        impact_score=(int, Field(ge=0, le=10)),
        ad_genres=(list[ad_genre_enum], ...),
        reason=(str, ...),
    )
    return create_model(
        "TrendEvaluationBatch",
        __config__=ConfigDict(extra="forbid"),
        evaluations=(list[trend_evaluation_model], ...),
    )


def get_google_sheets_settings():
    """Google Sheets保存に必要な設定を読み込む。"""
    load_dotenv()
    credential_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    spreadsheet_id = os.getenv("GOOGLE_SPREADSHEET_ID")
    sheet_name = os.getenv("GOOGLE_SHEET_NAME", "トレンド履歴")

    if not credential_file or not spreadsheet_id:
        raise ValueError(
            ".env に GOOGLE_SERVICE_ACCOUNT_FILE と GOOGLE_SPREADSHEET_ID を設定してください。"
        )

    credential_path = Path(credential_file)
    if not credential_path.is_file():
        raise ValueError(f"認証JSONファイルが見つかりません: {credential_path}")

    return credential_path, spreadsheet_id, sheet_name


def fetch_trends(bearer_token, woeid):
    """X APIから指定した場所のトレンドを取得する。"""
    url = f"https://api.x.com/2/trends/by/woeid/{woeid}"
    headers = {"Authorization": f"Bearer {bearer_token}"}
    params = {"max_trends": 20, "trend.fields": "trend_name"}

    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()

    data = response.json().get("data", [])
    if not data:
        raise ValueError("トレンドが取得できませんでした。APIの応答を確認してください。")
    return data


def evaluate_trends(trends, openai_api_key, openai_model, allowed_ad_genres):
    """トレンドが検索行動と広告需要へ与える影響をAIで判定する。"""
    trend_inputs = []
    for rank, trend in enumerate(trends, start=1):
        trend_inputs.append(
            {
                "rank": rank,
                "trend_name": trend.get("trend_name", "名称なし"),
            }
        )

    allowed_genres_text = "\n".join(f"- {genre}" for genre in allowed_ad_genres)
    trend_evaluation_batch_model = create_trend_evaluation_batch_model(allowed_ad_genres)
    instructions = f"""
あなたはリスティング広告の需要変化を分析する担当者です。
X上で話題かどうか自体ではなく、その話題が日本のGoogleなどの検索行動を変化させ、
下記の広告ジャンルに関する検索語句や広告クリック数へ影響する可能性を評価してください。

判定対象の広告ジャンルは次の一覧だけです。これ以外のジャンルは絶対に返さないでください。
{allowed_genres_text}

影響度は0から10の整数です。
0〜2: 上記6ジャンルの検索・クリックへの影響はほぼ見込めない。
3〜5: 上記6ジャンルの一部で、限定的または一時的な変化の可能性がある。
6〜8: 上記6ジャンルの一部で、検索需要や広告クリックが明確に変化しそう。
9〜10: 上記6ジャンルの複数または重要なジャンルで、大きな変化が起きそう。

トレンド自体がどの業界に属するかを分類するのではありません。
次の「直接性テスト」を満たす場合だけ、そのジャンルを選んでください。
「そのトレンドを見たユーザーが、対象ジャンルに関する具体的な検索語句や申込み行動へ、
途中の推測を挟まず自然に進む流れ」を1段階で説明できる必要があります。

業界内で一般的に関連する商品・サービス・制度・企業・市場・ニュース・専門用語は、
ジャンル名がトレンドに含まれなくても直接的な関連として扱えます。
ただし、具体的な検索・比較・申込み行動を自然に説明できる場合だけ関連ありとしてください。

単なる連想では関連付けないでください。ストレス、気分、興奮、生活習慣などを経由した
間接的な推測や、複数段階の因果関係は原則として無視してください。
直接性テストを満たさない場合は、ad_genresを空配列、impact_scoreを0にしてください。
スポーツ大会や有名人名だけの話題も、直接性テストを満たさなければ空配列にしてください。

impact_scoreが3以上の場合、reasonには「どの対象ジャンルについて」「どのような具体的な検索語句、
比較、申込み行動」が変化しそうかを必ず書いてください。
合理的な検索行動の変化を具体的に説明できない場合はimpact_scoreを0〜2にし、
関連がほぼない場合は0にしてください。
不確実な場合は、推測を断定せず低めに評価してください。
ad_genresには、上記一覧のうち関連が見込めるものだけを入れてください。
関連が見込めない場合は必ず空の配列にし、impact_scoreも0〜2にしてください。
入力にあるすべてのトレンドを、同じ順番で必ず1件ずつ返してください。
""".strip()

    client = OpenAI(api_key=openai_api_key)
    response = client.responses.parse(
        model=openai_model,
        input=[
            {"role": "system", "content": instructions},
            {
                "role": "user",
                "content": "判定対象のトレンド:\n"
                + json.dumps(trend_inputs, ensure_ascii=False),
            },
        ],
        text_format=trend_evaluation_batch_model,
    )

    if response.output_parsed is None:
        raise ValueError("AI判定結果を構造化データとして受け取れませんでした。")

    return response.output_parsed


def print_evaluations(evaluations, woeid=None):
    """AI判定結果を、後続連携にも使えるJSONで表示する。"""
    output = {"evaluations": [item.model_dump(mode="json") for item in evaluations.evaluations]}
    if woeid is not None:
        output["woeid"] = woeid
    print(json.dumps(output, ensure_ascii=False, indent=2))


def get_notification_threshold():
    """Windows通知を出すimpact_scoreの閾値を読み込む。"""
    load_dotenv()
    raw_threshold = os.getenv("NOTIFICATION_IMPACT_SCORE_THRESHOLD")
    if raw_threshold is None:
        return DEFAULT_NOTIFICATION_IMPACT_SCORE_THRESHOLD

    try:
        threshold = int(raw_threshold)
    except ValueError as error:
        raise ValueError(
            "NOTIFICATION_IMPACT_SCORE_THRESHOLD には整数を設定してください。"
        ) from error

    if threshold < 0 or threshold > 10:
        raise ValueError(
            "NOTIFICATION_IMPACT_SCORE_THRESHOLD には0から10の整数を設定してください。"
        )

    return threshold


def build_notification_message(target_evaluations):
    """通知対象のAI判定結果を1つの本文にまとめる。"""
    blocks = []
    for evaluation in target_evaluations:
        ad_genres_text = (
            "、".join(genre.value for genre in evaluation.ad_genres)
            if evaluation.ad_genres
            else "なし"
        )
        blocks.append(
            "\n".join(
                [
                    f"トレンド名: {evaluation.trend_name}",
                    f"impact_score: {evaluation.impact_score}",
                    f"ad_genres: {ad_genres_text}",
                    f"reason: {evaluation.reason}",
                ]
            )
        )

    return "\n\n".join(blocks)


def notify_high_impact_trends(evaluations, threshold):
    """impact_scoreが閾値以上のトレンドがあればWindows通知を出す。"""
    target_evaluations = [
        evaluation
        for evaluation in evaluations.evaluations
        if evaluation.impact_score >= threshold
    ]
    if not target_evaluations:
        return

    title = "広告影響トレンド検知"
    message = build_notification_message(target_evaluations)
    ctypes.windll.user32.MessageBoxW(None, message, title, 0x40)


def create_sheet_rows(trends, evaluations, woeid):
    """XトレンドとAI判定を結合し、Sheetsへ保存する行を作る。"""
    if len(trends) != len(evaluations.evaluations):
        raise ValueError("Xトレンド数とAI判定数が一致しないため、保存を中止しました。")

    jst = timezone(timedelta(hours=9))
    now_jst = datetime.now(jst)
    fetched_at = now_jst.strftime("%Y-%m-%d %H:%M:%S")
    run_id = now_jst.strftime("%Y%m%d-%H%M%S")
    rows = []

    for rank, (trend, evaluation) in enumerate(
        zip(trends, evaluations.evaluations), start=1
    ):
        trend_name = trend.get("trend_name", "名称なし")
        if trend_name != evaluation.trend_name:
            raise ValueError("Xトレンド名とAI判定のトレンド名が一致しないため、保存を中止しました。")

        rows.append(
            [
                fetched_at,
                run_id,
                woeid,
                rank,
                trend_name,
                evaluation.impact_score,
                " | ".join(genre.value for genre in evaluation.ad_genres),
                evaluation.reason,
                f"https://x.com/search?q={quote(trend_name, safe='')}",
            ]
        )

    return rows


def get_sheet_range(sheet_name, cell_range):
    """シート名を安全に含めたA1形式の範囲を作る。"""
    escaped_sheet_name = sheet_name.replace("'", "''")
    return f"'{escaped_sheet_name}'!{cell_range}"


def save_rows_to_google_sheets(rows, credential_path, spreadsheet_id, sheet_name):
    """行データをGoogle Sheetsへ追記する。"""
    credentials = Credentials.from_service_account_file(
        credential_path, scopes=[SHEETS_SCOPE]
    )
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    values_api = service.spreadsheets().values()
    header_range = get_sheet_range(sheet_name, "1:1")
    current_header = values_api.get(
        spreadsheetId=spreadsheet_id, range=header_range
    ).execute().get("values", [])

    if not current_header:
        values_api.update(
            spreadsheetId=spreadsheet_id,
            range=get_sheet_range(sheet_name, "A1:I1"),
            valueInputOption="RAW",
            body={"values": [SHEET_HEADERS]},
        ).execute()
    elif current_header[0] != SHEET_HEADERS:
        raise ValueError(
            "Google Sheetsの1行目が想定した見出しと一致しません。"
            "既存の見出しを確認してください。"
        )

    result = values_api.append(
        spreadsheetId=spreadsheet_id,
        range=get_sheet_range(sheet_name, "A:I"),
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()
    updated_cells = result.get("updates", {}).get("updatedCells", 0)
    print(f"Google Sheetsへ{len(rows)}件を保存しました（更新セル数: {updated_cells}）。")


def run_ai_test():
    """X APIを使わず、固定のテストデータをAI判定へ渡す。"""
    openai_api_key, openai_model = get_openai_settings()
    allowed_ad_genres = load_ad_genres()
    evaluations = evaluate_trends(
        AI_TEST_TRENDS, openai_api_key, openai_model, allowed_ad_genres
    )
    print_evaluations(evaluations)


def run_notification_test():
    """X APIを使わず、AI判定からWindows通知まで確認する。"""
    openai_api_key, openai_model = get_openai_settings()
    threshold = get_notification_threshold()
    allowed_ad_genres = load_ad_genres()
    evaluations = evaluate_trends(
        AI_TEST_TRENDS, openai_api_key, openai_model, allowed_ad_genres
    )
    print_evaluations(evaluations)
    notify_high_impact_trends(evaluations, threshold)


def main():
    """設定の読込、取得、表示を順番に実行する。"""
    parser = argparse.ArgumentParser(description="Xトレンドの広告影響をAIで判定します。")
    parser.add_argument(
        "--test-ai",
        action="store_true",
        help="X APIを呼ばず、テスト用トレンドをAI判定します。",
    )
    parser.add_argument(
        "--test-notification",
        action="store_true",
        help="X APIとGoogle Sheetsを使わず、AI判定後にWindows通知を確認します。",
    )
    args = parser.parse_args()

    try:
        if args.test_ai:
            run_ai_test()
            return
        if args.test_notification:
            run_notification_test()
            return

        bearer_token, woeid, openai_api_key, openai_model = get_settings()
        allowed_ad_genres = load_ad_genres()
        trends = fetch_trends(bearer_token, woeid)
        evaluations = evaluate_trends(
            trends, openai_api_key, openai_model, allowed_ad_genres
        )
        print_evaluations(evaluations, woeid)
        credential_path, spreadsheet_id, sheet_name = get_google_sheets_settings()
        rows = create_sheet_rows(trends, evaluations, woeid)
        save_rows_to_google_sheets(rows, credential_path, spreadsheet_id, sheet_name)
        threshold = get_notification_threshold()
        notify_high_impact_trends(evaluations, threshold)
    except ValueError as error:
        print(f"設定エラー: {error}", file=sys.stderr)
        sys.exit(1)
    except requests.RequestException as error:
        response = getattr(error, "response", None)
        if response is not None:
            print(
                f"X APIの呼び出しに失敗しました（HTTP {response.status_code}）。"
                f"\n詳細: {response.text}",
                file=sys.stderr,
            )
        else:
            print(f"通信エラー: {error}", file=sys.stderr)
        sys.exit(1)
    except OpenAIError as error:
        print(f"OpenAI APIの呼び出しに失敗しました: {error}", file=sys.stderr)
        sys.exit(1)
    except (GoogleAuthError, HttpError) as error:
        print(f"Google Sheetsへの保存に失敗しました: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
