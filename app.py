import requests
from bs4 import BeautifulSoup
import pandas as pd
import streamlit as st

SOURCE_URL = "https://www.chittorgarh.com/report/upcoming-ipo-in-india/80/"

@st.cache_data(ttl=3600)
@st.cache_data(ttl=3600)
def fetch_ipos():
    """
    Scrape upcoming IPOs from Chittorgarh and return as a DataFrame.
    Cached for 1 hour to avoid hammering the site.
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(SOURCE_URL, headers=headers, timeout=15)

    # If the site blocks us or errors out:
    if resp.status_code != 200:
        st.warning(f"Source returned status code: {resp.status_code}")
        return pd.DataFrame()

    soup = BeautifulSoup(resp.text, "html.parser")

    # Get ALL tables instead of just the first
    tables = soup.find_all("table")
    if not tables:
        st.warning("No tables found on source page.")
        return pd.DataFrame()

    # Heuristic: pick the table whose header contains 'Company' or 'IPO'
    target_table = None
    for t in tables:
        header_row = t.find("tr")
        if not header_row:
            continue
        header_cells = header_row.find_all(["th", "td"])
        header_text = " ".join(c.get_text(strip=True).lower() for c in header_cells)
        if ("company" in header_text or "ipo" in header_text) and ("open" in header_text or "close" in header_text):
            target_table = t
            break

    # Fallback: first table
    if target_table is None:
        target_table = tables[0]

    # Try to get rows either from <tbody> or directly
    body = target_table.find("tbody")
    rows = body.find_all("tr") if body else target_table.find_all("tr")[1:]  # skip header row

    data = []
    for r in rows:
        cols = [c.get_text(strip=True) for c in r.find_all("td")]
        if not cols:
            continue

        # Be defensive about number of columns
        ipo = {
            "Company": cols[0] if len(cols) > 0 else "",
            "Open Date": cols[1] if len(cols) > 1 else "",
            "Close Date": cols[2] if len(cols) > 2 else "",
            "Price Band": cols[3] if len(cols) > 3 else "",
            "Issue Size (Cr)": cols[4] if len(cols) > 4 else "",
            "Issue Type": cols[5] if len(cols) > 5 else "",
            "Exchange": cols[6] if len(cols) > 6 else "",
            "Status": cols[7] if len(cols) > 7 else "",
        }
        data.append(ipo)

    if not data:
        st.warning("Parsed table but found no IPO rows. The page structure may have changed.")
        return pd.DataFrame()

    return pd.DataFrame(data)



st.set_page_config(page_title="India IPO Tracker", layout="wide")

st.title("🇮🇳 India IPO Tracker (Upcoming & Scheduled)")
st.caption("Scraped from public sources for learning/demo purposes only. Not investment advice.")

with st.spinner("Fetching latest IPO data..."):
    df = fetch_ipos()

if df.empty:
    st.error("Could not load IPO data. The source layout may have changed.")
else:
    # Simple filters
    col1, col2 = st.columns(2)

    with col1:
        status_filter = st.multiselect(
            "Filter by Status",
            sorted(df["Status"].dropna().unique()),
            default=list(sorted(df["Status"].dropna().unique()))
        )

    with col2:
        exchange_filter = st.multiselect(
            "Filter by Exchange",
            sorted(df["Exchange"].dropna().unique()),
            default=list(sorted(df["Exchange"].dropna().unique()))
        )

    filtered = df.copy()
    if status_filter:
        filtered = filtered[filtered["Status"].isin(status_filter)]
    if exchange_filter:
        filtered = filtered[filtered["Exchange"].isin(exchange_filter)]

    st.subheader(f"Showing {len(filtered)} IPOs")
    st.dataframe(filtered, use_container_width=True)

    with st.expander("View raw data"):
        st.dataframe(df, use_container_width=True)

st.markdown(
    f"""
    <div style="font-size:0.8rem; color:#666; margin-top:1rem;">
    Source: <a href="{SOURCE_URL}" target="_blank">Chittorgarh – Upcoming IPOs</a><br>
    Built with ❤️ as a demo tool. Data may be delayed or inaccurate.
    </div>
    """,
    unsafe_allow_html=True,
)
