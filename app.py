import requests
import pandas as pd
import streamlit as st

# ---------- Config ----------

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "*/*",
    "Referer": "https://www.nseindia.com/market-data/all-upcoming-issues-ipo",
}

API_CURRENT = "https://www.nseindia.com/api/ipo-current-issue"
API_UPCOMING = "https://www.nseindia.com/api/ipo-upcoming-issue"


# ---------- Data fetching ----------

@st.cache_data(ttl=300)
def fetch_nse_ipo(url: str) -> pd.DataFrame:
    """
    Call NSE IPO API (current / upcoming) and return a cleaned DataFrame.
    Works whether JSON is a plain list or {data: [...]}
    """
    s = requests.Session()
    s.headers.update(HEADERS)

    # Warm up – needed so NSE sets cookies
    s.get("https://www.nseindia.com", timeout=10)

    r = s.get(url, timeout=10)
    r.raise_for_status()
    raw = r.json()

    # Handle both shapes: list or {data: [...]}
    if isinstance(raw, list):
        records = raw
    elif isinstance(raw, dict) and "data" in raw:
        records = raw["data"]
    else:
        records = []

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)

    # Normalise / rename a few columns
    rename_map = {
        "companyName": "Company",
        "symbol": "Symbol",
        "series": "Series",
        "issueStartDate": "Issue Start",
        "issueEndDate": "Issue End",
        "issuePrice": "Price Band",
        "issueSize": "Issue Size (shares)",
        "noOfSharesOffered": "Shares Offered",
        "noOfsharesBid": "Shares Bid",
        "noOfTime": "Times Subscribed",
        "status": "Status",
    }
    df = df.rename(columns=rename_map)

    # Convert numeric-ish columns
    for col in ["Issue Size (shares)", "Shares Offered", "Shares Bid", "Times Subscribed"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Ensure dates are treated nicely (optional)
    for col in ["Issue Start", "Issue End"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], format="%d-%b-%Y", errors="coerce")

    return df


# ---------- UI ----------

st.set_page_config(page_title="🇮🇳 NSE IPO Tracker", layout="wide")

st.title("🇮🇳 NSE IPO Tracker")
st.caption("Live data from NSE IPO APIs for learning/demo only. Not investment advice.")

tab_current, tab_upcoming = st.tabs(["📈 Current Issues", "🕒 Upcoming Issues"])


# ---- Current IPOs ----
with tab_current:
    st.subheader("Current IPO Issues (NSE)")
    try:
        df_current = fetch_nse_ipo(API_CURRENT)
        if df_current.empty:
            st.warning("No current IPO data returned from NSE.")
        else:
            # Basic filters
            col1, col2 = st.columns([1, 2])

            with col1:
                series_filter = st.multiselect(
                    "Series (EQ / SME)",
                    options=sorted(df_current["Series"].dropna().unique()),
                    default=list(sorted(df_current["Series"].dropna().unique())),
                )

            with col2:
                search = st.text_input("Search by company or symbol")

            df_view = df_current.copy()

            if series_filter:
                df_view = df_view[df_view["Series"].isin(series_filter)]

            if search:
                mask = (
                    df_view["Company"].str.contains(search, case=False, na=False)
                    | df_view["Symbol"].str.contains(search, case=False, na=False)
                )
                df_view = df_view[mask]

            st.dataframe(df_view, use_container_width=True)
    except Exception as e:
        st.error(f"Error loading current IPOs: {e}")


# ---- Upcoming IPOs ----
with tab_upcoming:
    st.subheader("Upcoming IPO Issues (NSE)")
    try:
        df_upcoming = fetch_nse_ipo(API_UPCOMING)
        if df_upcoming.empty:
            st.warning("No upcoming IPO data returned from NSE.")
        else:
            col1, col2 = st.columns([1, 2])

            with col1:
                series_filter_u = st.multiselect(
                    "Series (EQ / SME)",
                    options=sorted(df_upcoming["Series"].dropna().unique()),
                    default=list(sorted(df_upcoming["Series"].dropna().unique())),
                )

            with col2:
                search_u = st.text_input(
                    "Search by company or symbol ", key="search_upcoming"
                )

            df_view_u = df_upcoming.copy()

            if series_filter_u:
                df_view_u = df_view_u[df_view_u["Series"].isin(series_filter_u)]

            if search_u:
                mask_u = (
                    df_view_u["Company"].str.contains(search_u, case=False, na=False)
                    | df_view_u["Symbol"].str.contains(search_u, case=False, na=False)
                )
                df_view_u = df_view_u[mask_u]

            st.dataframe(df_view_u, use_container_width=True)
    except Exception as e:
        st.error(f"Error loading upcoming IPOs: {e}")


st.markdown(
    """
    <div style="font-size:0.8rem; color:#666; margin-top:1rem;">
    Source: NSE India IPO APIs (`/api/ipo-current-issue`, `/api/ipo-upcoming-issue`).<br>
    For personal / demo use only. Check NSE terms before any production/commercial usage.
    </div>
    """,
    unsafe_allow_html=True,
)
