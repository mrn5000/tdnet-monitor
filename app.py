"""
Stock Analysis Dashboard with AgGrid
=====================================
TDNET（適時開示情報）の決算発表銘柄を一覧表示し、
財務指標（PER, PBR等）と四半期業績を一括で確認できるダッシュボード。
データソース: Yanoshin TDnet API + J-Quants API (Free plan)
"""

import calendar
import logging
import os
import time
from datetime import date, timedelta

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode, GridUpdateMode

# ---------------------------------------------------------------------------
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# J-Quants API キー
# ---------------------------------------------------------------------------
JQUANTS_API_KEY = os.getenv("JQUANTS_API_KEY", "")

# ---------------------------------------------------------------------------
# ページ設定
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="📊 TDNET 決算ダッシュボード",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# カスタム CSS
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;600;700;800&display=swap');
    .stApp { background: #0f1117; font-family: 'Noto Sans JP', sans-serif; }
    * { font-family: 'Noto Sans JP', sans-serif !important; }
    section[data-testid="stSidebar"] { background: #161b22; border-right: 1px solid #30363d; }
    section[data-testid="stSidebar"] .stMarkdown p, section[data-testid="stSidebar"] label { color: #c9d1d9; }
    .dashboard-header { text-align: center; padding: 20px 0 8px; }
    .dashboard-header h1 {
        background: linear-gradient(90deg, #58a6ff, #bc8cff, #f778ba);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        font-size: 2.2rem; font-weight: 800; margin-bottom: 0;
    }
    .dashboard-header p { color: #8b949e; font-size: 0.95rem; margin-top: 4px; }
    .metric-row { display: flex; gap: 12px; margin: 10px 0 16px; }
    .metric-card { flex: 1; background: #161b22; border: 1px solid #30363d; border-radius: 12px; padding: 14px 18px; text-align: center; }
    .metric-card .label { color: #8b949e; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.06em; }
    .metric-card .value { color: #e6edf3; font-size: 1.6rem; font-weight: 700; margin-top: 2px; }
    div[data-testid="stTextInput"] input { background: #161b22 !important; border: 1px solid #30363d !important; border-radius: 8px !important; color: #e6edf3 !important; }
    .stButton > button { background: linear-gradient(135deg, #238636 0%, #2ea043 100%) !important; color: white !important; border: none !important; border-radius: 8px !important; padding: 10px 20px !important; font-weight: 600 !important; width: 100%; }
    .stButton > button:hover { box-shadow: 0 4px 14px rgba(46,160,67,0.4) !important; }
    .stElementContainer, .element-container { max-width: 100% !important; }
    .delay-note { color: #d29922; font-size: 0.8rem; margin-bottom: 8px; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# ヘッダー
# ---------------------------------------------------------------------------
st.markdown(
    """
    <div class="dashboard-header">
        <h1>📊 TDNET 決算ダッシュボード</h1>
        <p>適時開示情報 × J-Quants 財務指標を一括チェック</p>
    </div>
    """,
    unsafe_allow_html=True,
)


# ==========================================================================
# J-Quants API クライアント
# ==========================================================================
@st.cache_resource
def get_jq_client(api_key: str):
    """J-Quants ClientV2 を初期化。"""
    import jquantsapi
    return jquantsapi.ClientV2(api_key=api_key)


# ==========================================================================
# レートリミッター (5 calls / min for Free plan)
# ==========================================================================
class RateLimiter:
    """Free plan: 5 API calls/min"""

    def __init__(self, max_calls: int = 5, period: int = 60):
        self.max_calls = max_calls
        self.period = period
        self.timestamps: list[float] = []

    def wait(self):
        now = time.time()
        # 古いタイムスタンプを除去
        self.timestamps = [t for t in self.timestamps if now - t < self.period]
        if len(self.timestamps) >= self.max_calls:
            wait_sec = self.period - (now - self.timestamps[0]) + 1
            time.sleep(wait_sec)
        self.timestamps.append(time.time())


rate_limiter = RateLimiter(max_calls=5, period=60)


def _safe_float(val) -> float | None:
    """数値を安全にfloatに変換。失敗時はNone。"""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ==========================================================================
# データ取得: Yanoshin API (TDNET 開示一覧)
# ==========================================================================
@st.cache_data(ttl=300, show_spinner=False)
def fetch_tdnet_list(target_date: date) -> pd.DataFrame:
    """Yanoshin API から指定日の適時開示一覧を取得。"""
    date_str = target_date.strftime("%Y%m%d")
    url = f"https://webapi.yanoshin.jp/webapi/tdnet/list/{date_str}.json2"
    params = {"limit": 500}

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"Yanoshin API error: {e}")
        st.error(f"⚠️ TDNET データの取得に失敗しました: {e}")
        return pd.DataFrame()

    items = data.get("items", [])
    if not items:
        return pd.DataFrame()

    code_map: dict[str, dict] = {}
    for item in items:
        code_raw = item.get("company_code", "")
        code = code_raw.strip()[:4] if code_raw else ""
        if not code:
            continue

        title = item.get("title", "")
        doc_url = item.get("document_url", "")

        if code not in code_map:
            code_map[code] = {
                "証券コード": code,
                "銘柄名": item.get("company_name", ""),
                "決算短信": "-",
                "説明資料": "-",
                "業績修正": "-",
                "補足資料": "-",
            }

        # タイトルから資料カテゴリを判定（補足→業績修正→説明→決算短信の順）
        SUPPL_KW = ["補足", "補足説明", "補足資料", "補足情報", "参考資料",
                     "データブック", "ファクトブック", "ファクトシート",
                     "統計資料", "参考データ"]
        REVISE_KW = ["業績予想の修正", "業績修正", "上方修正", "下方修正",
                     "予想の修正", "予想修正", "配当予想の修正", "配当修正",
                     "通期業績予想", "業績予想",
                     "見通しの修正", "見通し修正"]
        EXPLAIN_KW = ["説明資料", "説明会", "決算説明", "プレゼンテーション",
                      "プレゼン資料", "IR資料", "IR説明", "投資家向け",
                      "アナリスト", "決算概況", "決算ハイライト",
                      "業績ハイライト", "サマリー", "スライド",
                      "概要資料", "要約", "決算資料"]
        TANSHIN_KW = ["決算短信", "四半期報告", "四半期決算", "中間決算",
                      "通期決算", "連結決算", "個別決算",
                      "決算概要", "決算発表",
                      "Financial Results", "Financial Statements",
                      "Earnings", "Annual Results",
                      "有価証券報告", "半期報告"]

        if any(kw in title for kw in SUPPL_KW):
            code_map[code]["補足資料"] = doc_url
        elif any(kw in title for kw in REVISE_KW):
            code_map[code]["業績修正"] = doc_url
        elif any(kw in title for kw in EXPLAIN_KW):
            code_map[code]["説明資料"] = doc_url
        elif any(kw in title for kw in TANSHIN_KW):
            code_map[code]["決算短信"] = doc_url

    if not code_map:
        return pd.DataFrame()

    # 決算関連の資料が1つもない企業を除外
    code_map = {
        k: v for k, v in code_map.items()
        if v["決算短信"] != "-" or v["説明資料"] != "-" or v["業績修正"] != "-" or v["補足資料"] != "-"
    }

    if not code_map:
        return pd.DataFrame()
    return pd.DataFrame(list(code_map.values()))


# ==========================================================================
# データ取得: J-Quants API (財務データ + 株価)
# ==========================================================================
@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_fin_summary(api_key: str, code: str) -> pd.DataFrame | None:
    """1銘柄の財務サマリーを取得（1時間キャッシュ）。"""
    try:
        cli = get_jq_client(api_key)
        df = cli.get_fin_summary(code=code)
        if df is not None and not df.empty:
            logger.info(f"J-Quants fin_summary OK ({code}): {len(df)} rows")
            return df
        logger.warning(f"J-Quants fin_summary empty ({code})")
        return None
    except Exception as e:
        logger.error(f"J-Quants fin_summary FAIL ({code}): {e}")
        return None


@st.cache_data(ttl=86400, show_spinner=False)
def _fetch_daily_price(api_key: str, code: str) -> pd.DataFrame:
    """1銘柄の株価日足を取得（24時間キャッシュ）。"""
    try:
        cli = get_jq_client(api_key)
        rate_limiter.wait()
        df = cli.get_eq_bars_daily(code=code)
        return df
    except Exception as e:
        logger.warning(f"J-Quants price error ({code}): {e}")
        return pd.DataFrame()


def _safe_float(val) -> float | None:
    """文字列や数値を安全にfloatに変換。"""
    if val is None or val == "" or val == "-":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def fetch_market_data(codes: list[str], api_key: str) -> pd.DataFrame:
    """
    株価・指標: yfinance（リアルタイム）
    四半期業績: J-Quants API（Free plan, 12週間遅延）
    """
    import yfinance as yf

    rows = []
    total = len(codes)
    progress = st.progress(0, text="データを取得中...")
    status_text = st.empty()

    for i, code in enumerate(codes):
        status_text.markdown(
            f"<span style='color:#8b949e; font-size:0.85rem;'>"
            f"📡 {code} ({i+1}/{total})"
            f"</span>",
            unsafe_allow_html=True,
        )

        row = {
            "証券コード": code,
            "株価": "-",
            "時価総額": "-",
            "PER": "-",
            "PBR": "-",
            "配当利回り(%)": "-",
            "売上(Q)": "-",
            "営業利益(Q)": "-",
            "経常利益(Q)": "-",
            "純利益(Q)": "-",
        }

        # ===== yfinance: 現在株価・指標 =====
        sym = f"{code}.T"
        try:
            t = yf.Ticker(sym)
            info = t.info or {}

            price = _safe_float(info.get("currentPrice"))
            if price is None:
                price = _safe_float(info.get("regularMarketPrice"))
            if price is not None:
                row["株価"] = price

            mcap = _safe_float(info.get("marketCap"))
            if mcap is not None:
                row["時価総額"] = mcap

            per = _safe_float(info.get("trailingPE"))
            if per is not None:
                row["PER"] = round(per, 2)

            pbr = _safe_float(info.get("priceToBook"))
            if pbr is not None:
                row["PBR"] = round(pbr, 2)

            div_rate = _safe_float(info.get("dividendRate"))
            if div_rate and price and price > 0:
                row["配当利回り(%)"] = round(div_rate / price * 100, 2)

        except Exception as e:
            logger.warning(f"yfinance error {sym}: {e}")

        # ===== J-Quants: 四半期業績（百万円表示）=====
        if api_key:
            rate_limiter.wait()
            df_fin = _fetch_fin_summary(api_key, code)
            if df_fin is not None and not df_fin.empty:
                # 直近の開示を取得
                df_fin_sorted = df_fin.sort_values(
                    "DiscDate", ascending=False
                ).drop_duplicates(subset=["CurPerType", "CurFYSt"], keep="first")

                if len(df_fin_sorted) >= 1:
                    latest = df_fin_sorted.iloc[0]
                    period = str(latest.get("CurPerType", ""))
                    fy_start = latest.get("CurFYSt", "")

                    # 同じ事業年度の前Qを探す
                    prev = None
                    if period not in ("1Q", "FY"):
                        same_fy = df_fin_sorted[
                            df_fin_sorted["CurFYSt"] == fy_start
                        ]
                        if len(same_fy) >= 2:
                            prev = same_fy.iloc[1]

                    for col_out, col_jq in [
                        ("売上(Q)", "Sales"),
                        ("営業利益(Q)", "OP"),
                        ("経常利益(Q)", "OdP"),
                        ("純利益(Q)", "NP"),
                    ]:
                        cur = _safe_float(latest.get(col_jq))
                        if cur is not None:
                            if period in ("1Q", "FY") or prev is None:
                                row[col_out] = int(cur / 1_000_000)
                            else:
                                prv = _safe_float(prev.get(col_jq))
                                if prv is not None:
                                    row[col_out] = int(
                                        (cur - prv) / 1_000_000
                                    )
                                else:
                                    row[col_out] = int(cur / 1_000_000)

        rows.append(row)
        progress.progress((i + 1) / total)

    progress.empty()
    status_text.empty()
    return pd.DataFrame(rows)


# ==========================================================================
# AgGrid 表示
# ==========================================================================
def render_aggrid(df: pd.DataFrame, quick_filter: str):
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(
        filterable=True, sortable=True, resizable=True, suppressSizeToFit=False,
    )

    # 固定列
    gb.configure_column("証券コード", pinned="left", width=95, suppressSizeToFit=True)
    gb.configure_column("銘柄名", pinned="left", width=200, suppressSizeToFit=True)

    # 株価
    num_fmt = JsCode("""function(p){
        if(p.value==='-'||p.value==null)return '-';
        return Number(p.value).toLocaleString('ja-JP');
    }""")
    gb.configure_column("株価", type=["numericColumn"], valueFormatter=num_fmt)

    # 時価総額 (億円)
    cap_fmt = JsCode("""function(p){
        if(p.value==='-'||p.value==null)return '-';
        var v=Number(p.value)/100000000;
        if(v>=10000) return (v/10000).toFixed(1)+'兆';
        return Math.round(v).toLocaleString('ja-JP')+'億';
    }""")
    gb.configure_column("時価総額", type=["numericColumn"], valueFormatter=cap_fmt)

    # PER / PBR
    dec_fmt = JsCode("""function(p){
        if(p.value==='-'||p.value==null)return '-';
        return Number(p.value).toFixed(2);
    }""")
    gb.configure_column("PER", type=["numericColumn"], valueFormatter=dec_fmt)
    gb.configure_column("PBR", type=["numericColumn"], valueFormatter=dec_fmt)

    # 配当利回り
    yld_fmt = JsCode("""function(p){
        if(p.value==='-'||p.value==null)return '-';
        return Number(p.value).toFixed(2)+'%';
    }""")
    gb.configure_column("配当利回り(%)", type=["numericColumn"], valueFormatter=yld_fmt)

    # 業績 (百万円)
    mil_fmt = JsCode("""function(p){
        if(p.value==='-'||p.value==null)return '-';
        var m=Math.round(Number(p.value)/1000000);
        return m.toLocaleString('ja-JP')+' 百万';
    }""")
    for col in ["売上(Q)", "営業利益(Q)", "経常利益(Q)", "純利益(Q)"]:
        gb.configure_column(col, type=["numericColumn"], valueFormatter=mil_fmt)

    # PDF リンク
    link_renderer = JsCode("""
        class LinkCellRenderer {
            init(params) {
                this.eGui = document.createElement('span');
                if (params.value && params.value !== '-') {
                    var a = document.createElement('a');
                    a.href = params.value;
                    a.target = '_blank';
                    a.rel = 'noopener noreferrer';
                    a.innerText = '📄 開く';
                    a.style.color = '#58a6ff';
                    a.style.textDecoration = 'none';
                    a.style.fontWeight = '500';
                    a.addEventListener('mouseenter', function(){ a.style.textDecoration='underline'; });
                    a.addEventListener('mouseleave', function(){ a.style.textDecoration='none'; });
                    this.eGui.appendChild(a);
                } else {
                    this.eGui.innerText = '-';
                    this.eGui.style.color = '#484f58';
                }
            }
            getGui() { return this.eGui; }
        }
    """)
    for col in ["決算短信", "説明資料", "業績修正", "補足資料"]:
        gb.configure_column(col, cellRenderer=link_renderer, suppressSizeToFit=True, width=110)

    opts = gb.build()
    opts["autoSizeStrategy"] = {"type": "fitGridWidth"}
    if quick_filter:
        opts["quickFilterText"] = quick_filter

    AgGrid(
        df,
        gridOptions=opts,
        height=680,
        theme="streamlit",
        update_mode=GridUpdateMode.NO_UPDATE,
        allow_unsafe_jscode=True,
        fit_columns_on_grid_load=True,
        custom_css={
            ".ag-root-wrapper": {
                "border-radius": "8px", "border": "1px solid #30363d",
                "background": "#0d1117", "font-family": "'Noto Sans JP', sans-serif",
                "font-size": "13px", "width": "100%",
            },
            ".ag-header": {"background": "#161b22 !important", "border-bottom": "2px solid #30363d"},
            ".ag-header-cell-text": {"color": "#58a6ff !important", "font-weight": "600", "font-size": "12px", "white-space": "nowrap"},
            ".ag-row": {"border-bottom": "1px solid #21262d", "color": "#c9d1d9"},
            ".ag-row-even": {"background": "#0d1117"},
            ".ag-row-odd": {"background": "#161b22"},
            ".ag-row-hover": {"background": "#1c2433 !important"},
            ".ag-cell": {"line-height": "40px", "padding": "0 10px", "white-space": "nowrap", "overflow": "hidden", "text-overflow": "ellipsis"},
            ".ag-header-cell": {"padding": "0 10px"},
            ".ag-pinned-left-header, .ag-cell-last-left-pinned": {"border-right": "2px solid #30363d !important"},
        },
    )


# ==========================================================================
# サイドバー
# ==========================================================================
with st.sidebar:
    st.markdown("### 🔍 検索設定")
    st.markdown("---")

    # APIキー設定
    api_key = JQUANTS_API_KEY
    if not api_key:
        api_key = st.text_input(
            "🔑 J-Quants APIキー",
            type="password",
            help="J-Quants のダッシュボードで発行したAPIキーを入力",
        )
    else:
        st.success("✅ J-Quants APIキー連携済み")

    st.markdown("---")
    if st.button("🗑️ キャッシュをクリア"):
        st.cache_data.clear()
        st.success("キャッシュをクリアしました！")
        time.sleep(1)
        st.rerun()

    st.markdown("---")

    # 日付選択
    today = date.today()
    sel_year = st.selectbox(
        "📅 年", list(range(today.year, today.year - 3, -1)),
        index=0, format_func=lambda y: f"{y}年",
    )
    col_m, col_d = st.columns(2)
    with col_m:
        sel_month = st.selectbox(
            "月", list(range(1, 13)), index=today.month - 1,
            format_func=lambda m: f"{m}月",
        )
    with col_d:
        max_day = calendar.monthrange(sel_year, sel_month)[1]
        default_day = min(today.day, max_day) - 1
        sel_day = st.selectbox(
            "日", list(range(1, max_day + 1)), index=default_day,
            format_func=lambda d: f"{d}日",
        )
    selected_date = date(sel_year, sel_month, sel_day)

    st.markdown("")
    fetch_clicked = st.button("🚀 データ取得", use_container_width=True)

    st.markdown("---")
    st.markdown(
        """
        <div style="color:#8b949e; font-size:0.78rem; line-height:1.6;">
        <b>データソース</b><br>
        📡 <a href="https://webapi.yanoshin.jp" target="_blank" style="color:#58a6ff;">Yanoshin TDnet API</a><br>
        📈 <a href="https://jpx-jquants.com" target="_blank" style="color:#58a6ff;">J-Quants API (Free)</a><br>
        <span style="color:#d29922;">⚠️ Free: 12週間遅延 / 5回/分</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ==========================================================================
# メインロジック
# ==========================================================================
if fetch_clicked:
    if not api_key:
        st.error("⚠️ J-Quants APIキーが設定されていません。サイドバーで入力するか `.env` ファイルに `JQUANTS_API_KEY` を設定してください。")
        st.stop()

    with st.spinner("📡 TDNET データを取得中..."):
        df_tdnet = fetch_tdnet_list(selected_date)

    if df_tdnet.empty:
        st.warning(f"⚠️ {selected_date.strftime('%Y/%m/%d')} の開示は見つかりませんでした。")
        st.info("💡 別の日付を選択してみてください。休日・祝日は開示がありません。")
        st.stop()

    n_codes = len(df_tdnet)
    st.markdown(
        f"""
        <div class="metric-row">
            <div class="metric-card">
                <div class="label">対象日</div>
                <div class="value">{selected_date.strftime('%Y/%m/%d')}</div>
            </div>
            <div class="metric-card">
                <div class="label">開示銘柄数</div>
                <div class="value">{n_codes}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 市場データ取得
    codes = df_tdnet["証券コード"].tolist()
    df_market = fetch_market_data(codes, api_key)

    # データ結合
    df = df_tdnet.merge(df_market, on="証券コード", how="left").fillna("-")
    col_order = [
        "証券コード", "銘柄名", "株価", "PER", "PBR", "配当利回り(%)",
        "売上(Q)", "営業利益(Q)", "経常利益(Q)", "純利益(Q)",
        "決算短信", "説明資料", "業績修正", "補足資料",
    ]
    df = df[[c for c in col_order if c in df.columns]]

    st.markdown("### 📋 決算開示銘柄一覧")
    st.markdown(
        '<p class="delay-note">⚠️ 四半期業績は J-Quants Free プラン（12週間遅延・百万円単位） / 株価・PER等は yfinance（リアルタイム）</p>',
        unsafe_allow_html=True,
    )
    qf = st.text_input("検索", placeholder="銘柄名・証券コードでフィルタ...", label_visibility="collapsed")
    render_aggrid(df, qf)

    st.markdown("---")
    st.download_button(
        "📥 CSVダウンロード",
        data=df.to_csv(index=False, encoding="utf-8-sig"),
        file_name=f"tdnet_{selected_date.strftime('%Y%m%d')}.csv",
        mime="text/csv",
    )

else:
    st.markdown(
        """
        <div style="text-align:center; padding:80px 20px; color:#8b949e;">
            <div style="font-size:3.5rem; margin-bottom:12px;">📊</div>
            <h3 style="color:#c9d1d9; font-weight:600;">
                サイドバーから日付を選択し<br>「データ取得」をクリック
            </h3>
            <p style="margin-top:10px; font-size:0.9rem;">
                TDNET 開示銘柄の財務指標を<br>
                J-Quants API で一覧表示します
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
