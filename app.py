"""
TDNET（適時開示情報）シンプルビューア
=====================================
Yanoshin TDnet API を使用して、指定した日付の適時開示情報を瞬時に一覧表示します。
財務データや株価情報の取得処理（J-Quants/yfinance）を排除し、高速化を実現しました。
"""

import calendar
import logging
import time
import streamlit.components.v1 as components
from datetime import date

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
# ページ設定
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="📊 TDNET 適時開示ビューア",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
        /* スマホ用: 全体の余白を詰める */
        .block-container {
            padding-top: 1rem !important;
            padding-bottom: 2rem !important;
            padding-left: 0.5rem !important;
            padding-right: 0.5rem !important;
        }
        /* iframeの幅調整 */
        iframe {
            width: 100% !important;
            min-width: 100% !important;
        }
        /* デプロイボタンのみ隠す */
        .stDeployButton {display: none;}
        
        /* サイドバー開閉ボタン（ハンバーガー/矢印）のデザイン変更 */
        [data-testid="stSidebarCollapsedControl"] {
            background-color: #238636 !important; /* GitHubの緑色 */
            color: white !important;
            border-radius: 8px !important;
            padding: 4px !important;
            margin: 10px !important;
            box-shadow: 0 4px 6px rgba(0,0,0,0.3) !important;
        }
        /* アイコンの色 */
        [data-testid="stSidebarCollapsedControl"] > section {
            color: white !important;
        }
        [data-testid="stSidebarCollapsedControl"]:hover {
            background-color: #2ea043 !important;
        }
    </style>
    """,
    unsafe_allow_html=True,
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
        <h1>📊 TDNET 適時開示ビューア</h1>
        <p>企業の開示資料（PDF）を素早くチェック</p>
    </div>
    """,
    unsafe_allow_html=True,
)


# ==========================================================================
# データ取得: Yanoshin API (TDNET 開示一覧)
# ==========================================================================
@st.cache_data(ttl=300, show_spinner=False)
def fetch_tdnet_list(target_date: date) -> pd.DataFrame:
    """Yanoshin API から指定日の適時開示一覧を取得。"""
    date_str = target_date.strftime("%Y%m%d")
    url = f"https://webapi.yanoshin.jp/webapi/tdnet/list/{date_str}.json2"
    params = {"limit": 5000}

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
                "その他": "-",
            }

        # タイトルから資料カテゴリを判定
        SUPPL_KW = ["補足", "補足説明", "補足資料", "補足情報", "参考資料",
                     "データブック", "ファクトブック", "ファクトシート", "参考データ"]
        REVISE_KW = ["業績予想の修正", "業績修正", "上方修正", "下方修正",
                     "予想の修正", "予想修正", "配当予想の修正", "配当修正",
                     "通期業績予想", "業績予想", "見通しの修正"]
        EXPLAIN_KW = ["説明資料", "説明会", "決算説明", "プレゼンテーション",
                      "プレゼン資料", "IR資料", "IR説明", "投資家向け",
                      "アナリスト", "決算概況", "決算ハイライト", "概要資料", "要約", "決算資料"]
        TANSHIN_KW = ["決算短信", "四半期報告", "四半期決算", "中間決算",
                      "通期決算", "連結決算", "個別決算", "決算概要", "決算発表"]

        if any(kw in title for kw in SUPPL_KW):
            code_map[code]["補足資料"] = doc_url
        elif any(kw in title for kw in REVISE_KW):
            code_map[code]["業績修正"] = doc_url
        elif any(kw in title for kw in EXPLAIN_KW):
            code_map[code]["説明資料"] = doc_url
        elif any(kw in title for kw in TANSHIN_KW):
            code_map[code]["決算短信"] = doc_url
        else:
            # その他の開示（API抽出以外のもの）
            code_map[code]["その他"] = doc_url

    if not code_map:
        return pd.DataFrame()

    # 全件表示（フィルタリングなし）
    return pd.DataFrame(list(code_map.values()))


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
    gb.configure_column("銘柄名", pinned="left", width=220, suppressSizeToFit=True)

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
    for col in ["決算短信", "説明資料", "業績修正", "補足資料", "その他"]:
        gb.configure_column(col, cellRenderer=link_renderer, suppressSizeToFit=True, width=110)

    opts = gb.build()
    opts["autoSizeStrategy"] = {"type": "fitGridWidth"}
    # opts["domLayout"] = "autoHeight"  # 全画面スクロール用
    if quick_filter:
        opts["quickFilterText"] = quick_filter

    AgGrid(
        df,
        gridOptions=opts,
        height=600,
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

    if st.button("🗑️ キャッシュをクリア"):
        st.cache_data.clear()
        st.success("キャッシュをクリアしました！")
        time.sleep(0.5)
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
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("---")
    
    # 検索欄をサイドバーに配置
    qf = st.text_input("銘柄検索", placeholder="銘柄名・コード...", label_visibility="collapsed")


# ==========================================================================
# メインロジック
# ==========================================================================
if fetch_clicked:
    with st.spinner("🚀 データを取得しています..."):
        df_tdnet = fetch_tdnet_list(selected_date)

    if df_tdnet.empty:
        st.warning(f"⚠️ {selected_date.strftime('%Y/%m/%d')} の開示は見つかりませんでした。")
        st.info("💡 別の日付を選択してみてください。休日・祝日は開示がありません。")
    else:
        # データ保存
        df = df_tdnet
        col_order = [
            "証券コード", "銘柄名",
            "決算短信", "説明資料", "業績修正", "補足資料", "その他",
        ]
        # 存在するカラムだけにフィルタリング
        df = df[[c for c in col_order if c in df.columns]]

        # session_state に保存
        st.session_state.df_result = df
        st.session_state.res_date = selected_date
        st.session_state.res_n = len(df)
        
        # 完了通知
        st.toast("データ取得が完了しました！", icon="✅")
        
        # ブラウザ通知 (JS)
        notification_js = """
        <script>
        function notify() {
            var title = "データ取得完了！";
            var options = { body: "最新データの準備ができました。" };
            if (!("Notification" in window)) {
                console.log("No support");
            } else if (Notification.permission === "granted") {
                new Notification(title, options);
            } else if (Notification.permission !== "denied") {
                Notification.requestPermission().then(function (permission) {
                    if (permission === "granted") { new Notification(title, options); }
                });
            }
        }
        notify();
        </script>
        """
        components.html(notification_js, height=0, width=0)

# 結果表示 (session_state から)
if "df_result" in st.session_state and st.session_state.df_result is not None:
    df = st.session_state.df_result
    res_date = st.session_state.res_date
    res_n = st.session_state.res_n

    st.markdown(
        f"""
        <div class="metric-row">
            <div class="metric-card">
                <div class="label">対象日</div>
                <div class="value">{res_date.strftime('%Y/%m/%d')}</div>
            </div>
            <div class="metric-card">
                <div class="label">開示銘柄数</div>
                <div class="value">{res_n}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("### 📋 決算開示一覧")

    render_aggrid(df, qf)

    st.markdown("---")
    st.download_button(
        "📥 CSVダウンロード",
        data=df.to_csv(index=False, encoding="utf-8-sig"),
        file_name=f"tdnet_{res_date.strftime('%Y%m%d')}.csv",
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
                企業の開示資料を素早く検索できます
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
