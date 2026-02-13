"""
逆張り決算分析ツール
====================
決算発表後のパニック売り局面で、AI分析により「拾い時」かどうかを判定するStreamlitアプリ。

機能:
1. やのしんAPI で最新決算資料のURLリンクを取得・表示
2. EDINET API v2 で過去数年分の業績トレンドデータを取得
3. Gemini API で統合分析（悪材料特定 / 隠れた好材料 / 投資妙味判定）
"""

import io
import json
import logging
import os
import time
import zipfile
from datetime import datetime, timedelta

import fitz  # PyMuPDF
import google.generativeai as genai
import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

# =============================================================================
# .env ファイルからAPIキーを読み込み
# =============================================================================
load_dotenv()
EDINET_API_KEY = os.getenv("EDINET_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# =============================================================================
# ロギング設定
# =============================================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =============================================================================
# ページ設定（最初に呼ぶ必要がある）
# =============================================================================
st.set_page_config(
    page_title="逆張り決算分析ツール",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =============================================================================
# 定数
# =============================================================================
YANOSHIN_DATE_URL = "https://webapi.yanoshin.jp/webapi/tdnet/list/{date}.json"
EDINET_DOC_LIST_URL = "https://api.edinet-fsa.go.jp/api/v2/documents.json"
EDINET_DOC_GET_URL = "https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}"

# 決算関連キーワード
KESSAN_KEYWORDS = ["決算短信", "四半期決算短信"]
SETSUMAI_KEYWORDS = ["決算説明資料", "決算説明会資料", "決算説明会", "説明資料"]
HOSOKU_KEYWORDS = ["補足説明資料", "補足資料", "決算補足"]

# EDINET formCode（書類種別コード）
FORM_CODES_ANNUAL = ["030000"]  # 有価証券報告書
FORM_CODES_QUARTERLY = ["043000"]  # 四半期報告書

# XBRL タグ（業績データ用）— 主要な財務指標
FINANCIAL_TAGS = {
    "売上高": [
        "jppfs_cor:NetSales",
        "jppfs_cor:Revenue",
        "jppfs_cor:OperatingRevenue1",
        "jppfs_cor:NetSalesOfCompletedConstructionContracts",
    ],
    "営業利益": [
        "jppfs_cor:OperatingIncome",
        "jppfs_cor:OperatingProfit",
    ],
    "経常利益": [
        "jppfs_cor:OrdinaryIncome",
        "jppfs_cor:OrdinaryProfit",
    ],
    "純利益": [
        "jppfs_cor:ProfitLossAttributableToOwnersOfParent",
        "jppfs_cor:NetIncome",
        "jppfs_cor:ProfitLoss",
    ],
}

# =============================================================================
# カスタムCSS
# =============================================================================
st.markdown(
    """
    <style>
    /* メイン背景 */
    .stApp {
        background: linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%);
    }

    /* サイドバー */
    section[data-testid="stSidebar"] {
        background: rgba(15, 12, 41, 0.95);
        border-right: 1px solid rgba(130, 100, 255, 0.2);
    }

    /* カード風コンテナ */
    .analysis-card {
        background: rgba(255, 255, 255, 0.05);
        border: 1px solid rgba(130, 100, 255, 0.25);
        border-radius: 16px;
        padding: 24px;
        margin: 12px 0;
        backdrop-filter: blur(10px);
    }

    /* リンク一覧 */
    .doc-link {
        display: block;
        background: rgba(100, 180, 255, 0.08);
        border: 1px solid rgba(100, 180, 255, 0.2);
        border-radius: 10px;
        padding: 12px 18px;
        margin: 8px 0;
        color: #7ecfff !important;
        text-decoration: none !important;
        transition: all 0.25s ease;
        font-size: 0.95rem;
    }
    .doc-link:hover {
        background: rgba(100, 180, 255, 0.18);
        border-color: rgba(100, 180, 255, 0.5);
        transform: translateX(6px);
    }

    /* 評価バッジ */
    .rating-badge {
        display: inline-block;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 8px 20px;
        border-radius: 30px;
        font-weight: 700;
        font-size: 1.1rem;
        margin: 8px 4px;
    }

    /* ヘッダー装飾 */
    .tool-header {
        text-align: center;
        padding: 20px 0 10px 0;
    }
    .tool-header h1 {
        background: linear-gradient(90deg, #7ecfff, #c084fc, #f472b6);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: 2.2rem;
        font-weight: 800;
    }
    .tool-header p {
        color: rgba(255,255,255,0.5);
        font-size: 0.95rem;
    }

    /* セクションタイトル */
    .section-title {
        color: #c084fc;
        font-size: 1.3rem;
        font-weight: 700;
        margin: 24px 0 12px 0;
        padding-bottom: 8px;
        border-bottom: 2px solid rgba(192, 132, 252, 0.3);
    }

    /* 業績テーブル */
    .dataframe {
        border-radius: 8px !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# =============================================================================
# ヘッダー
# =============================================================================
st.markdown(
    """
    <div class="tool-header">
        <h1>📊 逆張り決算分析ツール</h1>
        <p>決算発表後のパニック売りを冷静に分析し、投資チャンスを見極める</p>
    </div>
    """,
    unsafe_allow_html=True,
)


# =============================================================================
# ユーティリティ関数
# =============================================================================
def safe_request(url, params=None, timeout=30, stream=False):
    """安全なHTTPリクエスト。エラー時はNoneを返す。"""
    try:
        resp = requests.get(url, params=params, timeout=timeout, stream=stream)
        resp.raise_for_status()
        return resp
    except requests.exceptions.Timeout:
        logger.warning(f"タイムアウト: {url}")
        return None
    except requests.exceptions.ConnectionError:
        logger.warning(f"接続エラー: {url}")
        return None
    except requests.exceptions.HTTPError as e:
        logger.warning(f"HTTPエラー ({e.response.status_code}): {url}")
        return None
    except Exception as e:
        logger.warning(f"リクエストエラー: {e}")
        return None


# =============================================================================
# 1. やのしんAPI — 最新決算資料リンク取得
# =============================================================================
@st.cache_data(ttl=300, show_spinner=False)
def fetch_yanoshin_disclosures(stock_code):
    """
    やのしんAPIの日付指定エンドポイント(YYYYMMDD.json)から
    本日と過去2日分の全適時開示情報を取得し、
    指定銘柄の決算関連資料をフィルタリングして返す。

    やのしんAPIの company_code は5桁（証券コード4桁 + 末尾 "0"）のため、
    ユーザー入力の4桁コードに "0" を付加して照合する。

    Returns:
        list[dict]: 開示情報のリスト。各要素は
            { "title", "url", "datetime", "category", "company_name" } を含む。
        None: 取得失敗時。
    """
    # 4桁の証券コードを5桁に変換（やのしんAPIの形式に合わせる）
    code_5digit = stock_code.zfill(4) + "0"

    # 本日と過去2日分を取得（決算発表が翌日に反映されるケースに対応）
    all_items = []
    for days_ago in range(3):
        target_date = (datetime.now() - timedelta(days=days_ago)).strftime("%Y%m%d")
        url = YANOSHIN_DATE_URL.format(date=target_date)
        resp = safe_request(url)
        if resp is None:
            continue

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            continue

        if isinstance(data, dict) and "items" in data:
            all_items.extend(data["items"])
        elif isinstance(data, list):
            all_items.extend(data)

    if not all_items:
        return None

    results = []
    seen_urls = set()  # 重複排除
    for item in all_items:
        # やのしんAPIは { "Tdnet": { ... } } 形式
        tdnet = item.get("Tdnet", item)

        company_code = str(tdnet.get("company_code", ""))
        # 指定銘柄のみフィルタ
        if company_code != code_5digit:
            continue

        title = tdnet.get("title", "")
        doc_url = tdnet.get("document_url", "")
        dt_str = tdnet.get("pubdate", "")
        company_name = tdnet.get("company_name", "")

        if not title or not doc_url:
            continue

        # 重複を排除
        if doc_url in seen_urls:
            continue
        seen_urls.add(doc_url)

        # カテゴリ分類
        category = "その他"
        if any(kw in title for kw in KESSAN_KEYWORDS):
            category = "決算短信"
        elif any(kw in title for kw in SETSUMAI_KEYWORDS):
            category = "決算説明資料"
        elif any(kw in title for kw in HOSOKU_KEYWORDS):
            category = "補足説明資料"

        results.append(
            {
                "title": title,
                "url": doc_url,
                "datetime": dt_str,
                "category": category,
                "company_name": company_name,
            }
        )

    return results if results else None


def display_disclosure_links(disclosures):
    """開示資料リンクをカテゴリ別に表示する。"""
    if not disclosures:
        st.warning("📭 決算関連の開示資料が見つかりませんでした。")
        return

    # カテゴリ別に分類
    categories = {
        "決算短信": [],
        "決算説明資料": [],
        "補足説明資料": [],
        "その他": [],
    }
    for d in disclosures:
        cat = d.get("category", "その他")
        if cat in categories:
            categories[cat].append(d)
        else:
            categories["その他"].append(d)

    # カテゴリ別に表示
    icons = {
        "決算短信": "📄",
        "決算説明資料": "📑",
        "補足説明資料": "📋",
        "その他": "📎",
    }

    for cat_name, docs in categories.items():
        if not docs:
            continue
        st.markdown(
            f'<div class="section-title">{icons.get(cat_name, "📎")} {cat_name}</div>',
            unsafe_allow_html=True,
        )
        for doc in docs:
            dt_display = doc.get("datetime", "")
            label = f"{doc['title']}"
            if dt_display:
                label += f"  （{dt_display}）"
            st.markdown(
                f'<a class="doc-link" href="{doc["url"]}" target="_blank">'
                f"🔗 {label}</a>",
                unsafe_allow_html=True,
            )


# =============================================================================
# 2. 決算短信 PDF テキスト抽出
# =============================================================================
@st.cache_data(ttl=600, show_spinner=False)
def extract_text_from_pdf_url(pdf_url, max_pages=10):
    """
    URLからPDFをダウンロードし、テキストを抽出する。
    失敗時は空文字列を返す。
    """
    try:
        resp = safe_request(pdf_url, timeout=60, stream=True)
        if resp is None:
            return ""

        pdf_bytes = resp.content
        if len(pdf_bytes) < 100:
            return ""

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text_parts = []
        page_count = min(len(doc), max_pages)
        for page_num in range(page_count):
            page = doc[page_num]
            text_parts.append(page.get_text())
        doc.close()

        full_text = "\n".join(text_parts)
        # テキストが長すぎる場合は切り詰める（Gemini APIのトークン制限対策）
        if len(full_text) > 15000:
            full_text = full_text[:15000] + "\n...(以下省略)..."
        return full_text

    except Exception as e:
        logger.warning(f"PDF解析エラー: {e}")
        return ""


# =============================================================================
# 3. EDINET API — 過去業績データ取得
# =============================================================================
@st.cache_data(ttl=3600, show_spinner=False)
def search_edinet_documents(stock_code, years_back=3):
    """
    EDINET APIで指定銘柄の有価証券報告書・四半期報告書を検索する。
    過去 years_back 年分を2日間隔でスキャンし、該当する書類IDリストを返す。

    Returns:
        list[dict]: 書類情報のリスト。各要素は
            { "docID", "docDescription", "periodEnd", "formCode", ... } を含む。
    """
    # 5桁の証券コードにする（EDINET APIのsecCodeは5桁）
    sec_code_5 = stock_code.zfill(4) + "0"

    # 該当する formCode を拡張（有報 + 四半期報告書 + 半期 + 訂正版）
    target_form_codes = {
        "030000",  # 有価証券報告書
        "030001",  # 有価証券報告書（訂正）
        "043000",  # 四半期報告書
        "043001",  # 四半期報告書（訂正）
        "050000",  # 半期報告書
        "050001",  # 半期報告書（訂正）
    }

    found_docs = []
    today = datetime.now()

    # 2日間隔で過去3年分をスキャン
    total_days = years_back * 365
    check_dates = []
    for days_ago in range(0, total_days, 2):
        check_date = today - timedelta(days=days_ago)
        # 土日はスキップ（EDINET提出なし）
        if check_date.weekday() < 5:  # 月〜金
            check_dates.append(check_date.strftime("%Y-%m-%d"))

    progress_bar = st.progress(0, text="EDINET書類を検索中...")
    total_checks = len(check_dates)

    for i, date_str in enumerate(check_dates):
        progress_bar.progress(
            (i + 1) / total_checks,
            text=f"EDINET書類を検索中... ({i + 1}/{total_checks}) — {len(found_docs)}件発見",
        )

        params = {
            "date": date_str,
            "type": 2,
            "Subscription-Key": EDINET_API_KEY,
        }

        resp = safe_request(EDINET_DOC_LIST_URL, params=params)
        if resp is None:
            continue

        try:
            result = resp.json()
        except (json.JSONDecodeError, ValueError):
            continue

        doc_list = result.get("results", [])
        if not doc_list:
            continue

        for doc in doc_list:
            doc_sec_code = str(doc.get("secCode", "") or "")
            doc_form_code = str(doc.get("formCode", "") or "")

            # 証券コードが一致し、対象の書類種別であるものを抽出
            if doc_sec_code == sec_code_5 and doc_form_code in target_form_codes:
                doc_id = doc.get("docID", "")
                if doc_id and doc_id not in [d["docID"] for d in found_docs]:
                    found_docs.append(
                        {
                            "docID": doc_id,
                            "docDescription": doc.get("docDescription", ""),
                            "periodStart": doc.get("periodStart", ""),
                            "periodEnd": doc.get("periodEnd", ""),
                            "formCode": doc_form_code,
                            "filerName": doc.get("filerName", ""),
                            "submitDateTime": doc.get("submitDateTime", ""),
                        }
                    )

        # 十分なデータが集まったら早期終了
        if len(found_docs) >= 15:
            break

        # API負荷軽減のため少し待機
        time.sleep(0.2)

    progress_bar.empty()
    return found_docs


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_edinet_financial_data(doc_id):
    """
    EDINET APIから書類をダウンロードし、財務データを抽出する。
    CSVデータ（type=4）を優先し、取得できない場合はXBRL（type=1）、
    さらにフォールバックとしてPDF（type=2）からテキスト抽出する。

    Returns:
        dict: { "売上高": value, "営業利益": value, ... } もしくは空dict
    """
    # --- 方法1: CSVデータ（type=4）を試す ---
    csv_data = _try_csv_extraction(EDINET_API_KEY, doc_id)
    if csv_data:
        return csv_data

    # --- 方法2: XBRL（type=1）を試す ---
    xbrl_data = _try_xbrl_extraction(EDINET_API_KEY, doc_id)
    if xbrl_data:
        return xbrl_data

    # --- 方法3: PDF（type=2）のテキストから数値を推測 ---
    pdf_data = _try_pdf_extraction(EDINET_API_KEY, doc_id)
    if pdf_data:
        return pdf_data

    return {}


def _try_csv_extraction(api_key, doc_id):
    """EDINET CSVフォーマットからの財務データ抽出を試みる。"""
    params = {"type": 4, "Subscription-Key": api_key}
    url = EDINET_DOC_GET_URL.format(doc_id=doc_id)
    resp = safe_request(url, params=params, timeout=60)
    if resp is None:
        return None

    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            csv_files = [f for f in zf.namelist() if f.endswith(".csv")]
            for csv_file in csv_files:
                try:
                    with zf.open(csv_file) as f:
                        # CSVの中身を読み込む
                        content = f.read()
                        # エンコーディング判定
                        for enc in ["utf-8", "cp932", "shift_jis"]:
                            try:
                                text = content.decode(enc)
                                break
                            except UnicodeDecodeError:
                                continue
                        else:
                            continue

                        result = _parse_financial_csv(text)
                        if result:
                            return result
                except Exception:
                    continue
    except (zipfile.BadZipFile, Exception) as e:
        logger.warning(f"CSV ZIP解析エラー (docID={doc_id}): {e}")

    return None


def _parse_financial_csv(csv_text):
    """EDINETのCSVテキストから財務指標を抽出する。"""
    result = {}
    lines = csv_text.split("\n")

    for line in lines:
        cols = line.split(",")
        if len(cols) < 5:
            continue

        # EDINETのCSVフォーマット: タグ名が含まれる列を探す
        line_str = line.lower()
        for metric_name, tags in FINANCIAL_TAGS.items():
            if metric_name in result:
                continue
            for tag in tags:
                tag_lower = tag.lower().split(":")[-1]
                if tag_lower in line_str:
                    # 数値を探す
                    for col in cols:
                        col = col.strip().strip('"')
                        try:
                            val = float(col.replace(",", ""))
                            if abs(val) > 0:
                                result[metric_name] = val
                                break
                        except ValueError:
                            continue
                    break

    return result if result else None


def _try_xbrl_extraction(api_key, doc_id):
    """EDINET XBRLデータからの財務データ抽出を試みる。"""
    params = {"type": 1, "Subscription-Key": api_key}
    url = EDINET_DOC_GET_URL.format(doc_id=doc_id)
    resp = safe_request(url, params=params, timeout=60)
    if resp is None:
        return None

    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xbrl_files = [
                f
                for f in zf.namelist()
                if f.endswith(".xbrl") or f.endswith(".xml")
            ]

            result = {}
            for xf in xbrl_files:
                try:
                    with zf.open(xf) as f:
                        content = f.read().decode("utf-8", errors="ignore")
                        partial = _parse_xbrl_content(content)
                        result.update(partial)
                except Exception:
                    continue

            return result if result else None

    except (zipfile.BadZipFile, Exception) as e:
        logger.warning(f"XBRL ZIP解析エラー (docID={doc_id}): {e}")
        return None


def _parse_xbrl_content(xml_text):
    """XBRLテキストから財務指標を簡易抽出する（正規表現ベース）。"""
    import re

    result = {}
    for metric_name, tags in FINANCIAL_TAGS.items():
        if metric_name in result:
            continue
        for tag in tags:
            # タグ名の名前空間を除去
            tag_local = tag.split(":")[-1]
            # <jppfs_cor:NetSales ...>12345...</jppfs_cor:NetSales> 形式を探す
            pattern = rf"<[^>]*{re.escape(tag_local)}[^>]*>([^<]+)</[^>]*{re.escape(tag_local)}[^>]*>"
            matches = re.findall(pattern, xml_text, re.IGNORECASE)
            for match in matches:
                try:
                    val = float(match.strip().replace(",", ""))
                    if abs(val) > 0:
                        result[metric_name] = val
                        break
                except ValueError:
                    continue
            if metric_name in result:
                break

    return result


def _try_pdf_extraction(api_key, doc_id):
    """EDINET PDF書類からの財務データ抽出を試みる。"""
    params = {"type": 2, "Subscription-Key": api_key}
    url = EDINET_DOC_GET_URL.format(doc_id=doc_id)
    resp = safe_request(url, params=params, timeout=60)
    if resp is None:
        return None

    try:
        doc = fitz.open(stream=resp.content, filetype="pdf")
        full_text = ""
        for page_num in range(min(len(doc), 5)):
            full_text += doc[page_num].get_text()
        doc.close()

        return _extract_financials_from_text(full_text)

    except Exception as e:
        logger.warning(f"PDF解析エラー (docID={doc_id}): {e}")
        return None


def _extract_financials_from_text(text):
    """テキストから主要財務指標を簡易抽出する。"""
    import re

    result = {}
    metrics_patterns = {
        "売上高": r"売上高[^\d]*?([\d,]+)",
        "営業利益": r"営業利益[^\d]*?([\d,]+)",
        "経常利益": r"経常利益[^\d]*?([\d,]+)",
        "純利益": r"(?:当期純利益|親会社株主に帰属する[^\d]*?当期純利益)[^\d]*?([\d,]+)",
    }

    for metric_name, pattern in metrics_patterns.items():
        match = re.search(pattern, text)
        if match:
            try:
                val = float(match.group(1).replace(",", ""))
                if val > 0:
                    result[metric_name] = val
            except ValueError:
                continue

    return result if result else None


def build_performance_trend(found_docs):
    """
    見つかった書類群から業績トレンドデータを構築する。

    Returns:
        pd.DataFrame: 期間ごとの業績データ
    """
    records = []
    progress_bar = st.progress(0, text="業績データを抽出中...")

    for i, doc in enumerate(found_docs):
        progress_bar.progress(
            (i + 1) / len(found_docs),
            text=f"業績データを抽出中... ({i + 1}/{len(found_docs)})",
        )

        financial = fetch_edinet_financial_data(doc["docID"])
        if financial:
            record = {
                "期間終了": doc.get("periodEnd", "不明"),
                "書類": doc.get("docDescription", ""),
            }
            record.update(financial)
            records.append(record)

        # API負荷軽減
        time.sleep(0.5)

    progress_bar.empty()

    if records:
        df = pd.DataFrame(records)
        # 期間終了日でソート
        if "期間終了" in df.columns:
            df = df.sort_values("期間終了", ascending=True).reset_index(drop=True)
        return df
    else:
        return pd.DataFrame()


# =============================================================================
# 4. Gemini API — 統合分析
# =============================================================================
def run_gemini_analysis(kessan_text, trend_df, stock_code):
    """
    Gemini APIで統合分析を実行する。

    Args:
        kessan_text: 決算短信PDFのテキスト
        trend_df: 過去業績トレンドのDataFrame
        stock_code: 証券コード

    Returns:
        str: 分析結果テキスト、またはNone
    """
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-flash")
    except Exception as e:
        st.error(f"❌ Gemini API初期化エラー: {e}")
        return None

    # トレンドデータをテキスト化
    if trend_df is not None and not trend_df.empty:
        trend_text = trend_df.to_string(index=False)
    else:
        trend_text = "（過去業績データは取得できませんでした）"

    # 決算短信テキストの準備
    if not kessan_text or len(kessan_text.strip()) < 50:
        kessan_text = "（決算短信テキストは取得できませんでした）"

    prompt = f"""あなたは、日本の株式市場に精通したプロのファンダメンタルアナリストです。
以下の情報を基に、証券コード {stock_code} の決算内容を「逆張り投資」の観点から詳細に分析してください。

## 分析に使うデータ

### 【最新の決算短信テキスト】
{kessan_text}

### 【過去の業績トレンドデータ】
{trend_text}

## 分析してほしいこと（必ず以下の3項目すべてに回答してください）

### ① 悪材料の特定
- なぜPTSや翌日の寄付きで売られるような「悪い数字」が出たのか？
- その要因は **一過性** のものか、それとも **構造的な問題** か？
- 具体的な数字やファクトを引用して説明してください。

### ② 隠れた好材料と過去比較
- 過去の業績トレンドと比較して、本業の成長性や収益の中身は実は健全ではないか？
- 市場が見落としている可能性のあるポジティブな要素は何か？
- セグメント別や利益率の変化など、表面的な数字には現れていない改善点はあるか？

### ③ 投資妙味の判定（5段階評価）
以下の5段階で「逆張り買いチャンス」としての評価を行ってください：
- ⭐⭐⭐⭐⭐ : 絶好の買い場（パニック売りは過剰反応）
- ⭐⭐⭐⭐ : 良い買い場（悪材料は限定的）
- ⭐⭐⭐ : 中立（好悪材料が拮抗）
- ⭐⭐ : 注意が必要（構造的な問題の可能性）
- ⭐ : 見送り推奨（深刻な悪材料）

**重要**: 評価の最初に、選んだ星の数を明記してください（例：「⭐⭐⭐⭐ 4/5」）。

各項目について、根拠となる数字を挙げながら、初心者にも分かりやすい日本語で説明してください。
"""

    max_retries = 3
    for attempt in range(max_retries):
        try:
            with st.spinner(f"🤖 Gemini AIが分析中...{f' (リトライ {attempt}/{max_retries-1})' if attempt > 0 else ''}"):
                response = model.generate_content(prompt)

            if response and response.text:
                return response.text
            else:
                st.error("❌ Gemini APIからの応答が空でした。")
                return None

        except Exception as e:
            error_msg = str(e)
            if "API_KEY" in error_msg.upper() or "INVALID" in error_msg.upper():
                st.error("❌ Gemini APIキーが無効です。正しいキーを .env に設定してください。")
                return None
            elif "QUOTA" in error_msg.upper() or "RATE" in error_msg.upper() or "429" in error_msg:
                if attempt < max_retries - 1:
                    wait_time = 2 ** (attempt + 1)  # 2, 4 秒
                    st.info(f"⏳ レート制限のため {wait_time} 秒待機中... (リトライ {attempt + 1}/{max_retries - 1})")
                    time.sleep(wait_time)
                    continue
                else:
                    st.error("❌ Gemini APIの利用制限に達しました。1分ほど待ってから再試行してください。")
                    return None
            else:
                st.error(f"❌ Gemini API分析エラー: {e}")
                return None
    return None


# =============================================================================
# 銘柄名 → 証券コード変換
# =============================================================================
@st.cache_data(ttl=300, show_spinner=False)
def search_company_by_name(query):
    """
    やのしんAPIの recent.json から会社名を検索し、
    部分一致する銘柄の候補リストを返す。

    Returns:
        list[dict]: [{"code": "7203", "name": "トヨタ自動車(株)"}, ...]
    """
    # 本日の全開示を日付指定で取得
    today_str = datetime.now().strftime("%Y%m%d")
    url = YANOSHIN_DATE_URL.format(date=today_str)
    resp = safe_request(url)
    if resp is None:
        return []

    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        return []

    items = []
    if isinstance(data, dict) and "items" in data:
        items = data["items"]
    elif isinstance(data, list):
        items = data

    seen_codes = set()
    results = []
    for item in items:
        tdnet = item.get("Tdnet", item)
        company_name = tdnet.get("company_name", "")
        company_code = str(tdnet.get("company_code", ""))

        if not company_name or not company_code or len(company_code) < 4:
            continue

        # 5桁→4桁に変換
        code_4digit = company_code[:4]

        if code_4digit in seen_codes:
            continue

        # 部分一致検索
        if query.lower() in company_name.lower():
            seen_codes.add(code_4digit)
            results.append({"code": code_4digit, "name": company_name})

    return results


# =============================================================================
# サイドバー — ユーザー入力
# =============================================================================
st.sidebar.markdown("## 🔍 銘柄を検索")

# APIキー設定状況の表示
api_status_ok = bool(EDINET_API_KEY) and bool(GEMINI_API_KEY)
if api_status_ok:
    st.sidebar.success("✅ APIキー設定済み")
else:
    missing = []
    if not EDINET_API_KEY:
        missing.append("EDINET")
    if not GEMINI_API_KEY:
        missing.append("Gemini")
    st.sidebar.error(
        f"⛔ {', '.join(missing)} APIキーが未設定です\n\n"
        "`.env` ファイルにキーを設定してください。\n\n"
        "```\n"
        "EDINET_API_KEY=あなたのキー\n"
        "GEMINI_API_KEY=あなたのキー\n"
        "```"
    )

st.sidebar.markdown("---")

search_input = st.sidebar.text_input(
    "🏢 証券コード or 銘柄名",
    placeholder="例: 7203 または トヨタ",
    help="4桁の証券コード、または会社名の一部を入力してください",
)

# 銘柄名で検索した場合、候補を表示
resolved_code = None
if search_input:
    search_input = search_input.strip()
    if search_input.isdigit() and len(search_input) == 4:
        # 4桁数字ならそのまま証券コードとして使用
        resolved_code = search_input
    else:
        # 銘柄名として検索
        with st.sidebar:
            with st.spinner("銘柄を検索中..."):
                candidates = search_company_by_name(search_input)

        if candidates:
            options = [f"{c['code']} - {c['name']}" for c in candidates]
            selected = st.sidebar.selectbox(
                "📋 該当銘柄を選択",
                options=options,
                help="検索結果から分析する銘柄を選んでください",
            )
            if selected:
                resolved_code = selected.split(" - ")[0]
        else:
            st.sidebar.warning(
                "該当する銘柄が見つかりません。\n\n"
                "💡 やのしんAPIは最新の開示約300件の銘柄のみ検索できます。"
                "証券コード4桁を直接入力してみてください。"
            )

st.sidebar.markdown("---")

run_analysis = st.sidebar.button(
    "🚀 分析を実行",
    use_container_width=True,
    type="primary",
    disabled=not api_status_ok,
)

# 注意書き
st.sidebar.markdown(
    """
    <div style="font-size: 0.75rem; color: rgba(255,255,255,0.4); margin-top: 16px; line-height: 1.6;">
    ⚠️ 本ツールは投資助言ではありません。<br>
    投資判断はご自身の責任でお願いします。
    </div>
    """,
    unsafe_allow_html=True,
)


# =============================================================================
# メイン処理
# =============================================================================
if run_analysis:
    # --- バリデーション ---
    if not resolved_code:
        st.error("⛔ 証券コードまたは銘柄名を入力してください。")
    else:
        stock_code = resolved_code

        st.markdown("---")

        # =====================================================================
        # STEP 1: 最新決算資料リンク取得
        # =====================================================================
        st.markdown(
            '<div class="section-title">📄 STEP 1: 最新の決算関連資料</div>',
            unsafe_allow_html=True,
        )

        with st.spinner("やのしんAPIから開示情報を取得中..."):
            disclosures = fetch_yanoshin_disclosures(stock_code)

        if disclosures:
            st.markdown(
                '<div class="analysis-card">',
                unsafe_allow_html=True,
            )
            display_disclosure_links(disclosures)
            st.markdown("</div>", unsafe_allow_html=True)

            # 決算短信のPDFテキストを取得（Gemini分析用）
            kessan_docs = [
                d for d in disclosures if d["category"] == "決算短信"
            ]
            kessan_text = ""
            if kessan_docs:
                with st.spinner("決算短信PDFのテキストを抽出中..."):
                    kessan_text = extract_text_from_pdf_url(kessan_docs[0]["url"])
                if kessan_text:
                    st.success("✅ 決算短信テキストの抽出に成功しました。")
                else:
                    st.info("ℹ️ 決算短信PDFからのテキスト抽出ができませんでした。AI分析は限定的になります。")
            else:
                st.info("ℹ️ 決算短信が見つかりませんでした。他の資料から分析を試みます。")
                # 他の資料からテキスト抽出を試みる
                for d in disclosures[:3]:
                    if d.get("url", "").endswith(".pdf") or "pdf" in d.get("url", "").lower():
                        with st.spinner(f"'{d['title']}' からテキスト抽出中..."):
                            kessan_text = extract_text_from_pdf_url(d["url"])
                        if kessan_text:
                            break
        else:
            st.warning(
                "📭 この銘柄の決算関連資料が見つかりませんでした。\n\n"
                "考えられる原因:\n"
                "- やのしんAPIは最新の開示情報約300件のみ取得します。"
                "決算発表が集中する時間帯では、該当銘柄の開示が"
                "リストに含まれていない場合があります\n"
                "- この銘柄が直近で決算を発表していない\n"
                "- 証券コードが正しくない\n\n"
                "💡 決算発表直後に再度お試しください。"
            )
            kessan_text = ""

        # =====================================================================
        # STEP 2: EDINET過去業績データ取得
        # =====================================================================
        st.markdown("---")
        st.markdown(
            '<div class="section-title">📈 STEP 2: 過去の業績トレンド</div>',
            unsafe_allow_html=True,
        )

        with st.spinner("EDINET APIから書類を検索中..."):
            found_docs = search_edinet_documents(stock_code)

        trend_df = pd.DataFrame()
        if found_docs:
            st.info(f"📚 {len(found_docs)} 件の有価証券報告書 / 四半期報告書を発見しました。")
            trend_df = build_performance_trend(found_docs)

            if not trend_df.empty:
                st.markdown(
                    '<div class="analysis-card">',
                    unsafe_allow_html=True,
                )
                st.dataframe(
                    trend_df,
                    use_container_width=True,
                    hide_index=True,
                )
                st.markdown("</div>", unsafe_allow_html=True)

                # 簡易グラフ
                numeric_cols = [
                    c
                    for c in ["売上高", "営業利益", "経常利益", "純利益"]
                    if c in trend_df.columns
                ]
                if numeric_cols and "期間終了" in trend_df.columns:
                    st.markdown(
                        '<div class="section-title">📊 業績推移グラフ</div>',
                        unsafe_allow_html=True,
                    )
                    chart_df = trend_df.set_index("期間終了")[numeric_cols]
                    st.line_chart(chart_df)
            else:
                st.warning("⚠️ 書類は見つかりましたが、財務データの抽出に失敗しました。")
        else:
            st.warning(
                "⚠️ EDINET APIから該当する書類が見つかりませんでした。\n\n"
                "考えられる原因:\n"
                "- EDINET APIキーが正しくない\n"
                "- 該当銘柄の報告書がまだ登録されていない\n"
                "- 検索期間内に報告書の提出がない"
            )

        # =====================================================================
        # STEP 3: Gemini AI 統合分析
        # =====================================================================
        st.markdown("---")
        st.markdown(
            '<div class="section-title">🤖 STEP 3: AI統合分析</div>',
            unsafe_allow_html=True,
        )

        # 少なくとも何らかのデータがあれば分析を実行
        if kessan_text or not trend_df.empty:
            analysis_result = run_gemini_analysis(
                kessan_text, trend_df, stock_code
            )

            if analysis_result:
                st.markdown(
                    '<div class="analysis-card">',
                    unsafe_allow_html=True,
                )
                st.markdown(analysis_result)
                st.markdown("</div>", unsafe_allow_html=True)
            else:
                st.error("❌ AI分析を実行できませんでした。Gemini APIキーを確認してください。")
        else:
            st.error(
                "❌ 分析に必要なデータが不足しています。\n\n"
                "決算短信テキストまたは過去業績データのいずれかが必要です。\n"
                "証券コードとAPIキーを確認して再度お試しください。"
            )

        # フッター
        st.markdown("---")
        st.markdown(
            """
            <div style="text-align: center; color: rgba(255,255,255,0.3); font-size: 0.8rem; padding: 16px 0;">
                ⚠️ 本ツールの分析結果は投資助言ではありません。投資判断はご自身の責任で行ってください。<br>
                データソース: やのしんAPI (TDnet) / EDINET API v2 / Google Gemini API
            </div>
            """,
            unsafe_allow_html=True,
        )

else:
    # 初期表示
    st.markdown(
        """
        <div class="analysis-card" style="text-align: center; padding: 48px 24px;">
            <h2 style="color: #c084fc; margin-bottom: 16px;">🚀 はじめに</h2>
            <p style="color: rgba(255,255,255,0.6); font-size: 1.05rem; line-height: 1.8;">
                左のサイドバーに <strong>証券コード</strong> または <strong>銘柄名</strong> を入力し、<br>
                <strong>「分析を実行」</strong> ボタンをクリックしてください。
            </p>
            <div style="margin-top: 32px; display: flex; justify-content: center; gap: 24px; flex-wrap: wrap;">
                <div style="background: rgba(126, 207, 255, 0.1); border-radius: 12px; padding: 20px; width: 200px;">
                    <div style="font-size: 2rem;">📄</div>
                    <div style="color: #7ecfff; font-weight: 600; margin-top: 8px;">STEP 1</div>
                    <div style="color: rgba(255,255,255,0.5); font-size: 0.85rem; margin-top: 4px;">
                        最新決算資料の取得
                    </div>
                </div>
                <div style="background: rgba(192, 132, 252, 0.1); border-radius: 12px; padding: 20px; width: 200px;">
                    <div style="font-size: 2rem;">📈</div>
                    <div style="color: #c084fc; font-weight: 600; margin-top: 8px;">STEP 2</div>
                    <div style="color: rgba(255,255,255,0.5); font-size: 0.85rem; margin-top: 4px;">
                        過去業績トレンド分析
                    </div>
                </div>
                <div style="background: rgba(244, 114, 182, 0.1); border-radius: 12px; padding: 20px; width: 200px;">
                    <div style="font-size: 2rem;">🤖</div>
                    <div style="color: #f472b6; font-weight: 600; margin-top: 8px;">STEP 3</div>
                    <div style="color: rgba(255,255,255,0.5); font-size: 0.85rem; margin-top: 4px;">
                        AI逆張り分析
                    </div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
