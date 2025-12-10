import datetime as dt
import urllib.parse

import pandas as pd
import streamlit as st

UPCOMING_URL = "https://www.screener.in/ipo/"
RECENT_URL = "https://www.screener.in/ipo/recent/"


# ---------- Date helpers ----------

def get_3_months_ago() -> dt.date:
    """Return date 3 months ago (approx 90 days back)."""
    return dt.date.today() - dt.timedelta(days=90)


def strip_ordinal_suffix(text: str) -> str:
    """Convert '8th Dec' -> '8 Dec' etc."""
    if not isinstance(text, str):
        return text
    return (
        text.replace("st", "")
        .replace("nd", "")
        .replace("rd", "")
        .replace("th", "")
    )


def parse_day_month(text: str, year: int | None = None) -> dt.date | None:
    """
    Parse things like '8th Dec', '15 Dec', '15 Dec 2025'.
    If year missing, use provided year or current year.
    """
    if not isinstance(text, str):
        return None

    text = strip_ordinal_suffix(text).strip()
    parts = text.split()
    if not parts:
        return None

    if len(parts) == 2:
        # '8 Dec' -> use given year or current year
        d, m = parts
        y = year or dt.date.today().year
    elif len(parts) == 3:
        d, m, y = parts
        try:
            y = int(y)
        except ValueError:
            y = dt.date.today().year
    else:
        return None

    try:
        return dt.datetime.strptime(f"{d} {m} {y}", "%d %b %Y").date()
    except ValueError:
        return None


def parse_listing_date_screener(val: str) -> dt.date | None:
    """
    Screener 'Listing Date' can be 'today', 'tomorrow', 'yesterday' or '15 Dec 2025'.
    """
    if pd.isna(val):
        return None
    text = str(val).strip().lower()
    today = dt.date.today()

    if text == "today":
        return today
    if text == "tomorrow":
        return today + dt.timedelta(days=1)
    if text == "yesterday":
        return today - dt.timedelta(days=1)

    # Try normal date formats
    for fmt in ("%d %b %Y", "%d-%b-%Y", "%d %b"):
        try:
            dt_obj = dt.datetime.strptime(text, fmt)
            # If year missing, use current year
            if dt_obj.year == 1900:
                dt_obj = dt_obj.replace(year=today.year)
            return dt_obj.date()
        except ValueError:
            continue
    return None


def drop_sr_no_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Remove Sr No / unnamed index style columns, works for normal & MultiIndex columns."""
    # If columns are MultiIndex, flatten first
    if isinstance(df.columns, pd.MultiIndex):
        flat_cols = []
        for tup in df.columns:
            parts = [str(x).strip() for x in tup if x is not None and str(x) != "nan"]
            flat_cols.append(" ".join(parts).strip())
        df.columns = flat_cols

    to_drop = []
    for c in df.columns:
        name = str(c).strip().lower()
        if "sr" in name and "no" in name:
            to_drop.append(c)
        if name.startswith("unnamed"):
            to_drop.append(c)
    if to_drop:
        df = df.drop(columns=to_drop)
    return df



# ---------- DRHP link helper ----------

def get_sebi_drhp_url(company_name: str) -> str:
    """
    Build a SEBI offer document search URL for this company.
    This will show DRHP/RHP/offer docs for the name where available.
    """
    base = "https://www.sebi.gov.in/sebiweb/home/HomeAction.do"
    params = {
        "doListing": "yes",
        "searchString": company_name,
        "categoryId": "17",  # Public issue offer documents
    }
    return f"{base}?{urllib.parse.urlencode(params)}"


# ---------- Data fetching from Screener ----------

@st.cache_data(ttl=600)

def fetch_upcoming_raw() -> pd.DataFrame:
    """
    Fetch raw upcoming IPO table from Screener.in.
    Handles both normal and MultiIndex (multi-row header) tables.
    """
    tables = pd.read_html(UPCOMING_URL, header=0)
    target = None

    for df in tables:
        df = drop_sr_no_columns(df)

        # After flattening in drop_sr_no_columns, columns should be simple Index
        cols = [str(c).strip() for c in df.columns]

        # Debug pattern: look for key columns
        if "Name" in cols and "Subscription Period" in cols and "Listing Date" in cols:
            df.columns = cols
            target = df
            break

    if target is None:
        return pd.DataFrame()

    return target



@st.cache_data(ttl=600)
def fetch_recent_raw() -> pd.DataFrame:
    """
    Fetch raw recent IPO table from Screener.in.
    Handles both normal and MultiIndex (multi-row header) tables.
    """
    tables = pd.read_html(RECENT_URL, header=0)
    target = None

    for df in tables:
        df = drop_sr_no_columns(df)

        cols = [str(c).strip() for c in df.columns]

        if (
            "Name" in cols
            and "Listing Date" in cols
            and any("IPO MCap" in c for c in cols)
        ):
            df.columns = cols
            target = df
            break

    if target is None:
        return pd.DataFrame()

    return target



@st.cache_data(ttl=600)
def fetch_upcoming_processed() -> pd.DataFrame:
    """
    Return upcoming IPOs with cleaned columns:
    Name, Subscription Period, Listing Date, M.Cap Cr, Price band (blank for now),
    plus parsed subscription start/end dates for logic.
    """
    df = fetch_upcoming_raw()
    if df.empty:
        return df

    # Identify the M.Cap column (could be 'M.Cap Cr' or similar)
    mcap_col = None
    for c in df.columns:
        if "m.cap" in str(c).lower():
            mcap_col = c
            break

    # Keep only required visible columns
    keep_cols = ["Name", "Subscription Period", "Listing Date"]
    if mcap_col:
        keep_cols.append(mcap_col)

    df = df[keep_cols].copy()
    if mcap_col:
        df = df.rename(columns={mcap_col: "M.Cap Cr"})

    # Filter out SME / non-EQ IPOs where marked in name
    df = df[~df["Name"].str.contains("SME", case=False, na=False)].reset_index(drop=True)

    # Add placeholder Price band
    df["Price band"] = ""

    # Parse listing date to deduce year
    df["_listing_date"] = df["Listing Date"].apply(parse_listing_date_screener)

    # Parse subscription start/end
    starts = []
    ends = []
    for _, row in df.iterrows():
        sub_text = str(row["Subscription Period"])
        listing_date = row["_listing_date"]
        listing_year = listing_date.year if isinstance(listing_date, dt.date) else None

        if "-" in sub_text:
            left, right = sub_text.split("-", 1)
            start = parse_day_month(left.strip(), year=listing_year)
            end = parse_day_month(right.strip(), year=listing_year)
        else:
            start = parse_day_month(sub_text.strip(), year=listing_year)
            end = start

        starts.append(start)
        ends.append(end)

    df["_sub_start"] = starts
    df["_sub_end"] = ends

    return df


@st.cache_data(ttl=600)
def fetch_recent_processed() -> pd.DataFrame:
    """
    Return recent IPOs with cleaned/renamed columns and parsed listing date.
    Columns: Name, Listing Date, IPO MCap Rs. Cr, IPO Price, Current Price, % Change
    """
    df = fetch_recent_raw()
    if df.empty:
        return df

    # Normalise column names we care about
    rename_map = {}
    for c in df.columns:
        sc = str(c).strip()
        if sc.startswith("IPO MCap"):
            rename_map[c] = "IPO MCap Rs. Cr"
        elif sc.startswith("IPO Price"):
            rename_map[c] = "IPO Price"
        elif sc.startswith("Current Price"):
            rename_map[c] = "Current Price"
        elif sc.startswith("% Change"):
            rename_map[c] = "% Change"

    df = df.rename(columns=rename_map)

    keep_cols = [
        "Name",
        "Listing Date",
        "IPO MCap Rs. Cr",
        "IPO Price",
        "Current Price",
        "% Change",
    ]
    keep_cols = [c for c in keep_cols if c in df.columns]
    df = df[keep_cols].copy()

    # Parse listing date
    df["_listing_date"] = df["Listing Date"].apply(parse_listing_date_screener)

    return df


@st.cache_data(ttl=600)
def fetch_past_last_3_months() -> pd.DataFrame:
    """
    Filter recent IPOs to last 3 months (listing date between today-3m and today).
    """
    df = fetch_recent_processed()
    if df.empty:
        return df

    three_months_ago = get_3_months_ago()
    today = dt.date.today()

    mask = df["_listing_date"].between(three_months_ago, today)
    df = df[mask].copy()

    return df


# ---------- AI/DRHP analysis layout ----------

def render_ai_analysis(company_name: str):
    """Render the AI / DRHP-focused analysis skeleton for a selected company."""
    st.markdown("---")
    st.markdown(f"### 🔍 AI / DRHP Analysis — **{company_name}**")

    sebi_url = get_sebi_drhp_url(company_name)
    st.markdown(
        f"[Open DRHP / RHP documents on SEBI]({sebi_url})  \n"
        f"_This link searches SEBI’s ‘Public Issue Offer Documents’ for **{company_name}**._"
    )

    st.markdown("#### 1. DRHP / RHP-Focused Analysis")

    st.markdown("**a. Risk Factors**")
    st.markdown(
        "- Internal risks (business concentration, execution dependencies)\n"
        "- External risks (competition, technology, demand cycles)\n"
        "- Financial risks (leverage, liquidity, cash flow)\n"
        "- Legal & regulatory risks\n"
        "- Customer / supplier concentration"
    )

    st.markdown("**b. Objects of the Issue**")
    st.markdown(
        "- Fresh issue utilisation (capex, working capital, debt repayment)\n"
        "- Offer for Sale (OFS) details and selling shareholders\n"
        "- Post-issue capital allocation and impact on balance sheet"
    )

    st.markdown("**c. Business Overview (from DRHP)**")
    st.markdown(
        "- Business model & revenue streams\n"
        "- Key products / services and segments\n"
        "- Key customers and geographic mix\n"
        "- Moats, differentiation, and competitive positioning"
    )

    st.markdown("**d. Financials (from DRHP)**")
    st.markdown(
        "- Revenue and EBITDA trend (3–5 years)\n"
        "- Profitability metrics (margins, ROE/ROCE wherever available)\n"
        "- Related Party Transactions (RPTs)\n"
        "- Auditor qualifications / observations\n"
        "- Working capital cycle and cash flow quality"
    )

    st.markdown("**e. Promoter & Ownership Analysis**")
    st.markdown(
        "- Promoter background and track record\n"
        "- Group structure and related entities\n"
        "- Pre-IPO vs post-IPO shareholding\n"
        "- Any pledges or encumbrances disclosed"
    )

    st.markdown("**f. Key DRHP Disclosures**")
    st.markdown(
        "- Litigation and contingent liabilities\n"
        "- Regulatory and compliance matters\n"
        "- Lock-in details (promoter / pre-IPO investors)\n"
        "- Any special rights or shareholder agreements disclosed"
    )

    st.markdown("#### 2. Macroeconomic & Sector Context")
    st.markdown(
        "- Sector outlook and linkage to GDP growth\n"
        "- Interest rate sensitivity (capex intensity, leverage)\n"
        "- Inflation impact on input costs / pricing power\n"
        "- Regulatory tailwinds and headwinds\n"
        "- Relevant global trends and external shocks"
    )

    st.markdown("#### 3. Peer Valuation Context")
    st.markdown(
        "- IPO valuation multiples (P/E, EV/EBITDA, P/BV, etc.) vs listed peers\n"
        "- Premium / discount vs peer median\n"
        "- Growth vs valuation trade-off\n"
        "- Any structural differences vs peer set (business mix, balance sheet, risk)"
    )

    st.markdown("#### 4. Investment Considerations Summary")
    st.markdown(
        "- **Key positives:** structural strengths, quality of franchise, balance sheet, macro tailwinds\n"
        "- **Key concerns:** concentration, governance, leverage, execution risk, regulatory overhang\n"
        "- **Post-listing watchlist:** what to track in quarterly results & disclosures\n"
        "- **Macro timing assessment:** where this IPO sits in the broader cycle (rates, liquidity, risk appetite)"
    )

    st.info(
        "In the next version, this section can be auto-populated by an AI agent that reads the DRHP/RHP, "
        "extracts key disclosures, builds peer comps, and generates a structured memo."
    )


# ---------- UI ----------

st.set_page_config(page_title="🇮🇳 India IPO Tracker", layout="wide")

st.title("🇮🇳 India IPO Tracker (Screener.in + DRHP Lens)")
st.caption(
    "Currently ongoing, upcoming and recent IPO data scraped from Screener.in for learning/demo only. "
    "AI analysis is designed to be DRHP/RHP-focused. Not investment advice."
)

# Order: ongoing, upcoming, past 3 months
tab_ongoing, tab_upcoming, tab_past = st.tabs(
    [
        "⏳ Currently Ongoing IPOs",
        "📋 Upcoming IPOs – Planned but not launched",
        "📜 Past IPOs – Last 3 Months",
    ]
)

today = dt.date.today()

# ---- Currently Ongoing IPOs ----
with tab_ongoing:
    st.subheader("Currently Ongoing IPOs (Subscription window open now)")
    df_up = fetch_upcoming_processed()

    if df_up.empty:
        st.warning("Could not load IPO data from Screener.in.")
    else:
        # Ongoing = start <= today <= end
        def is_ongoing(row):
            s = row["_sub_start"]
            e = row["_sub_end"]
            return isinstance(s, dt.date) and isinstance(e, dt.date) and s <= today <= e

        mask_ongoing = df_up.apply(is_ongoing, axis=1)
        df_view = df_up[mask_ongoing].copy()

        display_cols = [
            "Name",
            "Subscription Period",
            "Listing Date",
            "M.Cap Cr" if "M.Cap Cr" in df_up.columns else None,
            "Price band",
        ]
        display_cols = [c for c in display_cols if c is not None]

        search_o = st.text_input("Search by company name", key="search_ongoing")
        if search_o:
            df_view = df_view[
                df_view["Name"].str.contains(search_o, case=False, na=False)
            ]

        # Index should start at 1
        df_view = df_view.reset_index(drop=True)
        df_view.index = df_view.index + 1

        st.dataframe(df_view[display_cols], use_container_width=True)

        if not df_view.empty:
            selected_company = st.selectbox(
                "Click a company for DRHP-focused AI analysis",
                options=df_view["Name"].unique(),
                key="analysis_ongoing",
            )
            render_ai_analysis(selected_company)


# ---- Upcoming IPOs – planned but not launched ----
with tab_upcoming:
    st.subheader("Upcoming IPOs – Planned but not launched (Subscription not started)")
    df_up = fetch_upcoming_processed()

    if df_up.empty:
        st.warning("Could not load IPO data from Screener.in.")
    else:
        # Upcoming = subscription start > today
        mask_upcoming = df_up["_sub_start"].apply(
            lambda d: isinstance(d, dt.date) and d > today
        )
        df_view = df_up[mask_upcoming].copy()

        display_cols = [
            "Name",
            "Subscription Period",
            "Listing Date",
            "M.Cap Cr" if "M.Cap Cr" in df_up.columns else None,
            "Price band",
        ]
        display_cols = [c for c in display_cols if c is not None]

        search_u = st.text_input("Search by company name", key="search_upcoming")
        if search_u:
            df_view = df_view[
                df_view["Name"].str.contains(search_u, case=False, na=False)
            ]

        # Index from 1
        df_view = df_view.reset_index(drop=True)
        df_view.index = df_view.index + 1

        st.dataframe(df_view[display_cols], use_container_width=True)

        if not df_view.empty:
            selected_company = st.selectbox(
                "Click a company for DRHP-focused AI analysis",
                options=df_view["Name"].unique(),
                key="analysis_upcoming",
            )
            render_ai_analysis(selected_company)


# ---- Past IPOs – Last 3 Months ----
with tab_past:
    three_months_ago = get_3_months_ago()
    st.subheader("Past IPOs – Last 3 Months")
    st.caption(
        f"Listing dates between {three_months_ago.strftime('%d %b %Y')} "
        f"and {today.strftime('%d %b %Y')}."
    )

    df_past = fetch_past_last_3_months()

    if df_past.empty:
        st.warning(
            "No IPOs found for the last 3 months on Screener.in, "
            "or the table structure has changed."
        )
    else:
        display_cols = [
            "Name",
            "Listing Date",
            "IPO MCap Rs. Cr",
            "IPO Price",
            "Current Price",
            "% Change",
        ]
        display_cols = [c for c in display_cols if c in df_past.columns]

        search_p = st.text_input("Search by company name", key="search_past")
        if search_p:
            df_past = df_past[
                df_past["Name"].str.contains(search_p, case=False, na=False)
            ]

        # Index from 1
        df_past = df_past.reset_index(drop=True)
        df_past.index = df_past.index + 1

        st.dataframe(df_past[display_cols], use_container_width=True)

        if not df_past.empty:
            selected_company = st.selectbox(
                "Click a company for DRHP-focused AI analysis",
                options=df_past["Name"].unique(),
                key="analysis_past",
            )
            render_ai_analysis(selected_company)

st.markdown(
    """
    <div style="font-size:0.8rem; color:#666; margin-top:1rem;">
    Source: <a href="https://www.screener.in/ipo/" target="_blank">Screener.in IPO pages</a> (equity IPOs only, SME rows filtered out where marked).<br>
    DRHP links redirect to SEBI's public 'Offer Documents' search. For personal / demo / educational use only.
    </div>
    """,
    unsafe_allow_html=True,
)
