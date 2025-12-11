import datetime as dt
import urllib.parse

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

# -----------------------------
# Config: data sources
# -----------------------------

UPCOMING_URL = "https://www.screener.in/ipo/"
RECENT_URL = "https://www.screener.in/ipo/recent/"

SEBI_LISTING_URL = "https://www.sebi.gov.in/sebiweb/home/HomeAction.do"

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# -----------------------------
# Generic helpers
# -----------------------------


def get_3_months_ago() -> dt.date:
    """Return date ~3 months ago (approx 90 days)."""
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


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Flatten MultiIndex columns and drop Sr No / unnamed columns."""
    # Flatten multi-row headers if needed
    if isinstance(df.columns, pd.MultiIndex):
        flat_cols = []
        for tup in df.columns:
            parts = [
                str(x).strip()
                for x in tup
                if x is not None and str(x).strip().lower() != "nan"
            ]
            flat_cols.append(" ".join(parts).strip())
        df.columns = flat_cols

    # Drop Sr No / unnamed columns
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


# -----------------------------
# Screener: Upcoming & Recent IPO tables
# -----------------------------


@st.cache_data(ttl=600)
def fetch_upcoming_raw() -> pd.DataFrame:
    """Fetch raw upcoming IPO table from Screener.in."""
    tables = pd.read_html(UPCOMING_URL)
    target = None

    for df in tables:
        df = normalize_columns(df)
        cols = [str(c).strip() for c in df.columns]
        if "Name" in cols and "Subscription Period" in cols and "Listing Date" in cols:
            df.columns = cols
            target = df
            break

    if target is None:
        return pd.DataFrame()

    return target


@st.cache_data(ttl=600)
def fetch_recent_raw() -> pd.DataFrame:
    """Fetch raw recent IPO table from Screener.in."""
    tables = pd.read_html(RECENT_URL)
    target = None

    for df in tables:
        df = normalize_columns(df)
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

    # Identify the M.Cap column
    mcap_col = None
    for c in df.columns:
        if "m.cap" in str(c).lower():
            mcap_col = c
            break

    keep_cols = ["Name", "Subscription Period", "Listing Date"]
    if mcap_col:
        keep_cols.append(mcap_col)

    df = df[keep_cols].copy()
    if mcap_col:
        df = df.rename(columns={mcap_col: "M.Cap Cr"})

    # Filter out SME / non-EQ IPOs where marked in name
    df = df[~df["Name"].str.contains("sme", case=False, na=False)].reset_index(drop=True)

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


# -----------------------------
# SEBI smart search: RHP first, then DRHP
# -----------------------------


def _sebi_list_page(params: dict) -> BeautifulSoup | None:
    """Call SEBI filings listing with given params and return BeautifulSoup tree."""
    try:
        r = requests.get(
            SEBI_LISTING_URL, params=params, headers=HTTP_HEADERS, timeout=15
        )
        r.raise_for_status()
        return BeautifulSoup(r.text, "lxml")
    except Exception:
        return None


def _pick_doc_from_listing(soup: BeautifulSoup, company_name: str) -> str | None:
    """
    From a SEBI listing page, pick the best matching URL for the company.
    Prefer RHP, then DRHP/draft, then anything else.
    """
    if soup is None:
        return None

    company_lc = company_name.lower()
    first_word = company_lc.split()[0] if company_lc else ""

    best_rhp = None
    best_drhp = None
    best_other = None

    for a in soup.select("table a"):
        title = (a.get_text(strip=True) or "").lower()
        href = a.get("href") or ""
        if not href:
            continue

        # Only filings/public-issues
        if "/filings/public-issues/" not in href:
            continue

        if first_word and first_word not in title:
            continue

        if href.startswith("/"):
            url = "https://www.sebi.gov.in" + href
        else:
            url = href

        if "rhp" in title:
            if best_rhp is None:
                best_rhp = url
        elif "drhp" in title or "draft" in title:
            if best_drhp is None:
                best_drhp = url
        else:
            if best_other is None:
                best_other = url

    return best_rhp or best_drhp or best_other


@st.cache_data(ttl=3600)
def get_best_sebi_ipo_doc(company_name: str) -> str | None:
    """
    Try to find the best SEBI public-issues filing for this company.
    1) Look in 'Red Herring Documents filed with ROC' (RHP)
    2) If not found, look in 'Draft Offer Documents filed with SEBI' (DRHP)
    3) Fallback to generic Public Issues listing.
    Returns a full https://www.sebi.gov.in/filings/public-issues/... URL or None.
    """
    # 1) RHP listing (smid=11)
    params_rhp = {
        "doListing": "yes",
        "sid": "3",   # Filings
        "smid": "11", # Red Herring Documents filed with ROC
        "ssid": "15",
        "search": company_name,
    }
    soup_rhp = _sebi_list_page(params_rhp)
    url = _pick_doc_from_listing(soup_rhp, company_name)
    if url:
        return url

    # 2) DRHP listing (smid=10)
    params_drhp = {
        "doListing": "yes",
        "sid": "3",
        "smid": "10",  # Draft Offer Documents filed with SEBI
        "ssid": "15",
        "search": company_name,
    }
    soup_drhp = _sebi_list_page(params_drhp)
    url = _pick_doc_from_listing(soup_drhp, company_name)
    if url:
        return url

    # 3) Fallback: generic Public Issues filings
    params_all = {
        "doListing": "yes",
        "sid": "3",
        "smid": "0",
        "ssid": "15",
        "search": company_name,
    }
    soup_all = _sebi_list_page(params_all)
    url = _pick_doc_from_listing(soup_all, company_name)
    return url


# -----------------------------
# DRHP-focused analysis panel
# -----------------------------


def render_ai_analysis(company_name: str):
    """Render the DRHP/RHP-focused analysis skeleton for a selected company."""
    st.markdown("---")
    st.markdown(f"### 🔍 DRHP / RHP-Focused Analysis — **{company_name}**")

    sebi_url = get_best_sebi_ipo_doc(company_name)
    if sebi_url:
        st.markdown(
            f"[Open SEBI filing (RHP if available, else DRHP / Draft)]({sebi_url})"
        )
    else:
        # Fallback: generic SEBI search link
        params = {
            "doListing": "yes",
            "sid": "3",
            "smid": "0",
            "ssid": "15",
            "search": company_name,
        }
        generic_url = f"{SEBI_LISTING_URL}?{urllib.parse.urlencode(params)}"
        st.markdown(
            f"[Search SEBI Public Issues filings for this company]({generic_url})"
        )

    st.markdown("#### 1. DRHP / RHP-Centric Sections")

    st.markdown("**a. Risk Factors**")
    st.markdown(
        "- Internal risks (business concentration, execution, dependence on key people)\n"
        "- External risks (competition, technology shifts, demand cycles)\n"
        "- Financial risks (leverage, liquidity, cash flow volatility)\n"
        "- Legal & regulatory risks\n"
        "- Customer / supplier concentration"
    )

    st.markdown("**b. Objects of the Issue**")
    st.markdown(
        "- Fresh issue utilisation (capex, working capital, debt repayment)\n"
        "- Offer for Sale (OFS) – selling shareholders and quantum\n"
        "- Post-issue capital allocation and impact on balance sheet"
    )

    st.markdown("**c. Business Overview (from DRHP)**")
    st.markdown(
        "- Business model and revenue streams\n"
        "- Key products / services and segments\n"
        "- Key customers and geographic mix\n"
        "- Competitive positioning and moats"
    )

    st.markdown("**d. Financials (from DRHP)**")
    st.markdown(
        "- Revenue and EBITDA trend (3–5 years)\n"
        "- Margin profile and ROE / ROCE (if disclosed)\n"
        "- Working capital cycle and cash conversion\n"
        "- Related Party Transactions\n"
        "- Auditor qualifications or emphases of matter"
    )

    st.markdown("**e. Promoters & Ownership**")
    st.markdown(
        "- Promoter background and track record\n"
        "- Group structure and related entities\n"
        "- Pre-IPO vs post-IPO shareholding\n"
        "- Any pledges / encumbrances or special rights"
    )

    st.markdown("**f. Key DRHP Disclosures**")
    st.markdown(
        "- Material litigation and contingent liabilities\n"
        "- Regulatory and compliance matters\n"
        "- Lock-in details (promoters / pre-IPO investors)\n"
        "- Any special shareholder agreements"
    )

    st.markdown("#### 2. Macroeconomic & Sector Context")
    st.markdown(
        "- Sector outlook and linkage to GDP growth\n"
        "- Interest rate sensitivity\n"
        "- Inflation impact on costs / pricing power\n"
        "- Regulatory tailwinds and headwinds\n"
        "- Global trends relevant to the business"
    )

    st.markdown("#### 3. Peer Valuation Context")
    st.markdown(
        "- IPO valuation multiples vs listed peers (P/E, EV/EBITDA, P/BV, etc.)\n"
        "- Premium / discount vs peer median\n"
        "- Growth vs valuation trade-off\n"
        "- Structural differences vs peer set (balance sheet, risk)"
    )

    st.markdown("#### 4. Investment Considerations")
    st.markdown(
        "- **Key positives:** structural strengths, quality of franchise, balance sheet, macro tailwinds\n"
        "- **Key concerns:** concentration, governance, leverage, execution risk, regulatory overhang\n"
        "- **Post-listing watchlist:** KPIs to track in quarterly results\n"
        "- **Macro timing assessment:** where this IPO sits in the liquidity / rate cycle"
    )

    st.info(
        "This is a DRHP/RHP-first analysis template. "
        "In a full Cosalpha version, an AI agent would ingest the SEBI PDFs, "
        "populate each section with extracted data and peer comps, and generate a final memo."
    )


# -----------------------------
# Streamlit UI
# -----------------------------

st.set_page_config(page_title="🇮🇳 India IPO Tracker", layout="wide")

st.title("🇮🇳 India IPO Tracker (Screener.in + SEBI DRHP/RHP)")
st.caption(
    "Currently ongoing, upcoming and recent equity IPO data from Screener.in. "
    "SEBI links go to RHP where available, otherwise DRHP/draft. "
    "For learning/demo only; not investment advice."
)

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

        search_text = st.text_input("Search by company name", key="ongoing_search")
        if search_text:
            df_view = df_view[
                df_view["Name"].str.contains(search_text, case=False, na=False)
            ]

        # Add SEBI link column
        if not df_view.empty:
            df_view["SEBI DRHP/RHP"] = df_view["Name"].apply(get_best_sebi_ipo_doc)

        # Index from 1
        df_view = df_view.reset_index(drop=True)
        df_view.index = df_view.index + 1

        st.dataframe(
            df_view[display_cols + (["SEBI DRHP/RHP"] if "SEBI DRHP/RHP" in df_view.columns else [])],
            use_container_width=True,
            column_config={
                "SEBI DRHP/RHP": st.column_config.LinkColumn(
                    "SEBI DRHP/RHP",
                    help="Opens SEBI public-issues filing (RHP if available, else DRHP/draft).",
                )
            },
        )

        if not df_view.empty:
            selected_company = st.selectbox(
                "Select a company for DRHP-focused analysis",
                options=df_view["Name"].unique(),
                key="ongoing_analysis_select",
            )
            render_ai_analysis(selected_company)


# ---- Upcoming IPOs – planned but not launched ----
with tab_upcoming:
    st.subheader("Upcoming IPOs – Planned but not launched (Subscription not started)")

    df_up = fetch_upcoming_processed()
    if df_up.empty:
        st.warning("Could not load IPO data from Screener.in.")
    else:
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

        search_text = st.text_input("Search by company name", key="upcoming_search")
        if search_text:
            df_view = df_view[
                df_view["Name"].str.contains(search_text, case=False, na=False)
            ]

        if not df_view.empty:
            df_view["SEBI DRHP/RHP"] = df_view["Name"].apply(get_best_sebi_ipo_doc)

        df_view = df_view.reset_index(drop=True)
        df_view.index = df_view.index + 1

        st.dataframe(
            df_view[display_cols + (["SEBI DRHP/RHP"] if "SEBI DRHP/RHP" in df_view.columns else [])],
            use_container_width=True,
            column_config={
                "SEBI DRHP/RHP": st.column_config.LinkColumn(
                    "SEBI DRHP/RHP",
                    help="Opens SEBI public-issues filing (RHP if available, else DRHP/draft).",
                )
            },
        )

        if not df_view.empty:
            selected_company = st.selectbox(
                "Select a company for DRHP-focused analysis",
                options=df_view["Name"].unique(),
                key="upcoming_analysis_select",
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

        search_text = st.text_input("Search by company name", key="past_search")
        if search_text:
            df_past = df_past[
                df_past["Name"].str.contains(search_text, case=False, na=False)
            ]

        if not df_past.empty:
            df_past["SEBI DRHP/RHP"] = df_past["Name"].apply(get_best_sebi_ipo_doc)

        df_past = df_past.reset_index(drop=True)
        df_past.index = df_past.index + 1

        st.dataframe(
            df_past[display_cols + (["SEBI DRHP/RHP"] if "SEBI DRHP/RHP" in df_past.columns else [])],
            use_container_width=True,
            column_config={
                "SEBI DRHP/RHP": st.column_config.LinkColumn(
                    "SEBI DRHP/RHP",
                    help="Opens SEBI public-issues filing (RHP if available, else DRHP/draft).",
                )
            },
        )

        if not df_past.empty:
            selected_company = st.selectbox(
                "Select a company for DRHP-focused analysis",
                options=df_past["Name"].unique(),
                key="past_analysis_select",
            )
            render_ai_analysis(selected_company)


st.markdown(
    """
    <div style="font-size:0.8rem; color:#666; margin-top:1rem;">
    IPO data source: <a href="https://www.screener.in/ipo/" target="_blank">Screener.in IPO pages</a> (equity IPOs only; SME labels filtered where obvious).<br>
    DRHP/RHP links scraped via SEBI's public 'Filings & Public Issues' search. For personal / demo / educational use only. Not investment advice.
    </div>
    """,
    unsafe_allow_html=True,
)
