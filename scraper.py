import random
import geopandas as gpd
import contextily as ctx
import pandas as pd
import matplotlib.pyplot as plt
from seleniumbase import Driver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException
import time
from datetime import datetime
from dateutil import parser as dateparser
import requests
from geopy.geocoders import Nominatim
from scipy.spatial import KDTree
from geopy.extra.rate_limiter import RateLimiter
import re
import os
from dotenv import load_dotenv
import smtplib
from email.message import EmailMessage
import build_email as build
from pathlib import Path

pd.set_option('display.max_columns', None)
load_dotenv(Path(__file__).parent / ".env")


class ApartmentScraper:
    BASE_URL = "https://www.apartments.com"

    def __init__(self, location: str):
        self.location = location.lower().replace(", ", "-").replace(" ", "-")
        self.min_beds = None
        self.min_baths = None
        self.max_price = None
        self.laundry = None
        self.pets = None
        self.parking = None
        self.move_in_date = None
        self.flag_new = True

        # apartment.com specific strings
        self.bed_map = {0: "studios", 1: "1-bedrooms", 2: "2-bedrooms", 3: "3-bedrooms"}

        # desired apartment types
        self.allowed_types = ["houses", "condos", "townhomes"]

    def with_min_bedrooms(self, beds: int):
        if beds in self.bed_map:
            self.min_beds = self.bed_map[beds]
        return self

    # todo: add with_min_bathroom

    def with_max_price(self, price: int):
        self.max_price = f"under-{price}"
        return self

    def with_move_in(self, move_in_date: datetime):
        self.move_in_date = move_in_date
        return self

    def build_url(self) -> str:
        urls = []
        for housing_type in self.allowed_types:
            components = [housing_type, self.location]
            modifiers = []

            if self.min_beds:
                modifiers.append(f"min-{self.min_beds}")
            if self.max_price:
                modifiers.append(self.max_price)
            if modifiers:
                components.append("-".join(modifiers))

            final_path = "/".join(components) + "/"

            if self.flag_new:
                final_path = final_path + "new/"

            url = f"{self.BASE_URL}/{final_path}"

            urls.append(url)
        return urls


def scrape_apartmentsdotcom(search_urls):
    # make that baby undetected
    driver = Driver(uc=True, headless2=True)

    all_links = []
    try:
        for search_url in search_urls:
            print(f"Starting the scrape for {search_url} ...")
            driver.get(search_url)

            time.sleep(5)

            # stop the search if no results are found
            expanded = driver.find_elements(By.CSS_SELECTOR, "div.no-results h3")
            if expanded:
                print(f"  Search area was auto-expanded for {search_url} -- skipping.")
                continue

            for _ in range(2):
                driver.execute_script("window.scrollBy(0, 1200);")
                time.sleep(1)

            # might need to change based on site
            listings = driver.find_elements(By.CSS_SELECTOR, "a.property-link")

            for link in listings:
                url = link.get_attribute("href")
                # dedupe
                if url and url not in all_links:
                    all_links.append(url)

        print(f"Successfully collected {len(all_links)} unique listing URLs.")
        return all_links

    finally:
        driver.quit()


# parse css selectors and return the first non empty
def first_text(driver, selectors):
    for sel in selectors:
        try:
            txt = driver.find_element(By.CSS_SELECTOR, sel).text.strip()
            if txt:
                return txt
        except NoSuchElementException:
            continue

    return None


# read apartment info grid as {label: value} map so missing / reordered pages don't break script
def parse_info_grid(driver):
    grid = {}
    cells = driver.find_elements(By.CSS_SELECTOR,
                                 "#priceBedBathAreaInfoWrapper li.column")
    for cell in cells:
        try:
            label = cell.find_element(By.CSS_SELECTOR, ".rentInfoLabel").text.strip()
            detail = cell.find_element(By.CSS_SELECTOR, ".rentInfoDetail").text.strip()

            if label:
                grid[label.lower()] = detail
        except NoSuchElementException:
            continue
    return grid


def get_address(driver):
    street = first_text(driver, [".propertyAddressContainer .delivery-address h1"]) or ""
    locally = first_text(driver, [".propertyAddressContainer h2"]) or ""
    full = f"{street.strip()}, {locally.strip()}"
    return " ".join(full.replace(" ,", ",").split()).strip(", ") or "Unknown"


def parse_availability(raw_txt, today):
    if not raw_txt:
        return None, False

    lil_t = raw_txt.lower()
    if any(word in lil_t for word in ("now", "immediate", "today")):
        return today, True
    if "soon" in lil_t:
        return None, False

    try:
        dt = dateparser.parse(raw_txt, fuzzy=True,
                              default=datetime(today.year, today.month, 1))
    except (ValueError, OverflowError):
        return None, False

    if dt is None:
        return None, False
    # ensure listings are in the future not the past
    if dt < today and (today - dt).days > 30:
        dt = dt.replace(year=dt.year + 1)
        return dt, False


# re-try pages that might be blocked from bot captcha
def looks_blocked(driver):
    src = driver.page_source.lower()
    return any(msg in src for msg in
               ("captcha", "press & hold", "access denied",
                "unusual activity", "verify you are"))


# pull description to get amenities
def pull_description(driver):
    full_desc = []

    for selector in ["#descriptionSection", "section.descriptionSection"]:
        try:
            element = driver.find_element(By.CSS_SELECTOR, selector)
            txt = element.get_attribute("textContent") or ""
            if txt.strip():
                full_desc.append(txt)
        except NoSuchElementException:
            continue

    # pull additional details from 'Home Details' section
    try:
        section = driver.find_element(
            By.XPATH,
            "//section[contains(@class,'feature-section')]"
            "[.//h3[normalize-space()='Home Details']]"
        )

        lines = ["Home Details:"]
        # home details are stored as stacked divs, must extract every row separately
        for row in section.find_elements(By.CSS_SELECTOR, ".detail-row"):
            try:
                title = (row.find_element(By.CSS_SELECTOR, ".detail-title")
                         .get_attribute("textContent") or "").strip()
            except NoSuchElementException:
                title = ""

            items = [
                (s.get_attribute("textContent") or "").strip()
                for s in row.find_elements(By.CSS_SELECTOR, ".detail-items span")
            ]
            items = [i for i in items if i]

            if title and items:
                lines.append(f"{title}: {', '.join(items)}")
            elif items:
                lines.append(", ".join(items))

        if len(lines) > 1:
            full_desc.append("\n".join(lines))
    except NoSuchElementException:
        pass

    return "\n\n".join(full_desc)


def get_movein_cost(description_txt):
    txt_block = description_txt or ""

    # $3,000 deposit,
    DEPOSIT_AMOUNT = re.compile(
        r'\$\s?([\d,]+(?:\.\d{1,2})?)\s+([A-Za-z][A-Za-z &/.\-]*?)(?=[,.;\n]|$)',
        re.IGNORECASE,
    )

    fees = {}

    for amount, label in DEPOSIT_AMOUNT.findall(txt_block):
        label = label.strip()
        if not label or label.lower() in {"mo", "month", "mo.", "/ mo"}:
            continue # skip monthly rent (pulled elsewhere)
        fees[label] = float(amount.replace(",", ""))

    # "Deposit:$3,000" / "Rent:$3,000" — label BEFORE amount
    LABEL_FIRST = re.compile(
        r'([A-Za-z][A-Za-z &/.\-]*?)\s*:\s*\$\s?([\d,]+(?:\.\d{1,2})?)'
    )

    for label, amount in LABEL_FIRST.findall(txt_block):
        label = label.lower.strip()
        if label.lower() == "rent":
            continue
        fees.setdefault(label, float(amount.replace(",", "")))

    return fees


def scan_billing(desc_text):
    low = (desc_text or "").lower()

    BILLS = {
        "Heat": ["heat"], "Hot Water": ["hot water"], "Water": ["water"],
        "Gas": ["gas"], "Electric": ["electric"], "Sewer": ["sewer"],
        "Trash": ["trash", "garbage"], "Internet": ["internet", "wifi", "wi-fi"],
        "Cable": ["cable"],
    }

    included = []
    # phrases like "heat and hot water included" / "...inc"
    for m in re.finditer(r'([a-z &/]{3,40}?)\s+(?:are |is )?inc(?:luded|\b)', low):
        ctx = m.group(1)
        for name, terms in BILLS.items():
            if any(t in ctx for t in terms) and name not in included:
                included.append(name)
    # "Included:Water" field style
    field = re.search(r'included?\s*:\s*([^\n]+)', low)
    if field:
        for name, terms in BILLS.items():
            if any(t in field.group(1) for t in terms) and name not in included:
                included.append(name)

    no_fee = bool(re.search(r'\bno\s+(?:broker\s+)?fee\b', low))

    return {"Included_Bills": included or None, "No_Added_Fees": no_fee or None}


def cost_summary(fees):
    RECURRING_KEYWORDS = ("utilit", "water", "sewer", "trash", "garbage", "gas",
                          "electric", "heat", "parking", "cable", "internet", "pet rent",
                          "amenity fee", "common area", "cam", "lock")

    first_month = last_month = deposit = 0.0
    one_time_fees, recurring_fees = {}, {}

    for label, value in fees.items():
        sml_l = label.lower()
        if "first month" in sml_l:
            first_month = value
        elif "last month" in sml_l:
            last_month = value
        elif "deposit" in sml_l:
            deposit = value
        elif any(k in sml_l for k in RECURRING_KEYWORDS):
            recurring_fees[label] = value
        else:
            one_time_fees[label] = value

    return {
        "First_Month_Rent": first_month or None,
        "Last_Month_Rent": last_month or None,
        "Security_Deposit": deposit or None,
        "Extra_Move_In_Fees": one_time_fees or None,
        "Recurring_Fees": recurring_fees or None,
    }


def scan_amenities(desc_text):
    low = desc_text.lower()
    def has_any(terms): return any(t in low for t in terms)

    if has_any(["in unit laundry", "in-unit laundry", "in unit washer",
                "washer/dryer", "washer & dryer", "laundry in unit"]):
        laundry = "In-unit"
    elif has_any(["laundry in building", "on-site laundry", "shared laundry",
                  "coin laundry", "laundry facilities"]):
        laundry = "On-site/shared"
    elif has_any(["laundry hookup", "w/d hookup", "washer/dryer hookup"]):
        laundry = "Hookups only"
    elif re.search(r'\b(laundry|washer)\b', low):
        laundry = "Mentioned"
    else:
        laundry = None

    if has_any(["no parking", "street parking only"]):
        parking = "No / street only"
    elif has_any(["garage", "off-street parking", "off street parking",
                  "covered parking", "assigned parking", "deeded parking",
                  "parking included", "parking available", "driveway"]):
        parking = "Available"
    elif "parking" in low:
        parking = "Mentioned"
    else:
        parking = None

    if has_any(["no pets", "pets not allowed", "no dogs", "no cats", "pet-free"]):
        pets = "No pets"
    elif has_any(["pet friendly", "pet-friendly", "pets allowed", "pets ok",
                  "pets welcome", "cats ok", "dogs ok", "cats and dogs"]):
        pets = "Pets allowed"
    elif re.search(r'\b(pet|cat|dog)s?\b', low):
        pets = "See listing"
    else:
        pets = None

    return {"Laundry": laundry, "Parking": parking, "Pets": pets}


# extract the apartment info
def extract_listing(driver, url, today):
    # headlines could be a building name or price, check for $ to handle differences properly
    headline = first_text(driver, ["#propertyName", ".propertyName"]) or ""
    if headline.startswith("$"):
        price = headline
    else:
        price = first_text(driver, [".priceRange"]) or "Contact Property"

    grid = parse_info_grid(driver)
    avail_raw = grid.get("available") or first_text(driver, [".availabilityInfo"]) or ""
    avail_dt, is_now = parse_availability(avail_raw, today)

    desc_text = pull_description(driver)
    raw_costs = get_movein_cost(desc_text)
    total_costs = cost_summary(raw_costs)
    amenities = scan_amenities(desc_text)
    billing = scan_billing(desc_text) # search for hidden parking / extra billing fees

    address = get_address(driver)
    address = re.sub(r'\bopen\b', 'Boston', address, flags=re.IGNORECASE)

    return {
        "Address": address,
        "Price": price,
        "Bedrooms": grid.get("bedrooms"),
        "Bathrooms": grid.get("bathrooms"),
        "SqFt": grid.get("square feet"),
        "Available_Raw": avail_raw,
        "Available_Date": avail_dt,
        "Available_Now": is_now,
        "Laundry": amenities["Laundry"],
        "Parking": amenities["Parking"],
        "Pets": amenities["Pets"],
        **total_costs,
        **billing,
        "URL": url,
    }


def scrape_indiv_listing(url_list):
    driver = Driver(uc=True, headless2=True)
    today = datetime.now()
    listings, blocked = [], []

    try:
        for idx, url in enumerate(url_list):
            try:
                print(f"[{idx + 1}/{len(url_list)}] Opening: {url}")
                driver.get(url)

                # wait on real anchors instead of sleep (better bot hiding)
                try:
                    WebDriverWait(driver, 15).until(EC.presence_of_element_located((
                        By.CSS_SELECTOR, "#propertyName, .propertyAddressContainer"
                    )))
                except TimeoutException:
                    pass

                listings.append(extract_listing(driver, url, today))
                time.sleep(random.uniform(1.5, 3.5))

            except Exception as e:
                print(f"Skipping broken detail page: {url}\n Error: {e}")
                continue
    finally:
        driver.quit()

    # define columns so if one is completely empty mask doesn't break
    COLUMNS = [
        "Address", "Price",
        "Bedrooms", "Bathrooms", "SqFt",
        "Available_Raw", "Available_Date", "Available_Now",
        "Laundry", "Parking", "Pets",
        "First_Month_Rent", "Last_Month_Rent", "Security_Deposit",
        "Extra_Move_In_Fees", "Recurring_Fees", "Total_Move_In_Cost",
        "Raw_Amenities", "Last_Updated",
        "URL",
    ]

    df = pd.DataFrame(listings, columns=COLUMNS)

    price_int = pd.to_numeric(
        df["Price"].astype(str).str.replace(r"[^0-9.]", "", regex=True)
        .replace("", pd.NA),
        errors="coerce",
    )

    df["First_Month_Rent"] = df["First_Month_Rent"].fillna(price_int)

    # coerce converts None -> NaT for datetime comparison
    df["Available_Date"] = pd.to_datetime(df["Available_Date"], errors="coerce")
    df["Available_Now"] = df["Available_Now"].fillna(False).astype(bool)

    # calculate total move in cost
    scalar_cols = ["First_Month_Rent", "Last_Month_Rent", "Security_Deposit"]
    scalar_total = df[scalar_cols].apply(pd.to_numeric, errors="coerce").sum(axis=1)

    def sum_fees(d):
        if not isinstance(d, dict):  # None / NaN / anything unexpected -> 0
            return 0.0
        return float(sum(v for v in d.values() if isinstance(v, (int, float))))

    extra_total = df["Extra_Move_In_Fees"].apply(sum_fees)
    total = scalar_total + extra_total
    df["Total_Move_In_Cost"] = total.round(2).where(total > 0, None)

    # normalize move in time stamp
    mask = df["Available_Now"] | (df["Available_Date"] <= pd.Timestamp(move_in))
    df_cleaned = df[mask].drop_duplicates(subset=["Address"], keep="first")

    return df_cleaned


def get_transit(api_key):
    url = "https://api-v3.mbta.com/stops"
    headers = {
        "X-API-Key": api_key
    }

    lines = ["Red", "Green", "Orange", "Silver", "Blue"]
    stops_list = []

    for line in lines:
        params = {"filter[route]": line}
        response = requests.get(url, params=params, headers=headers)

        if response.status_code == 200:
            json_data = response.json()

            for stop in json_data.get('data', []):
                attributes = stop.get('attributes', {})
                stops_list.append({
                    'Stop Name': attributes.get('name'),
                    'Address': attributes.get('address'),
                    "Line": line
                })

    stops_df = pd.DataFrame(stops_list).dropna(subset=['Address']).drop_duplicates().reset_index(drop=True)
    return stops_df


def get_closest_stop(stops_df, apartments_df):
    geolocator = Nominatim(user_agent="my_distance_calculator", timeout=15)
    chill_geolocator = RateLimiter(geolocator.geocode, min_delay_seconds=1.5, swallow_exceptions=False)

    # strip units from listings for more accurate encoding
    UNIT_RE = re.compile(
        r',?\s*'
        r'(?:unit|apt\.?|apartment|suite|ste\.?|fl\.?|floor|rm\.?|room|no\.?|#)'
        r'\s*#?\s*'  # allow an optional "#" between keyword and value
        r'[\w-]+',
        flags=re.IGNORECASE,
    )
    def strip_unit(addr):
        cleaned = UNIT_RE.sub('', addr)
        return re.sub(r'\s{2,}', ' ', cleaned).strip()

    # function blueprint of how df should calculate address locations
    def get_cords(addr):
        if not isinstance(addr, str) or not addr.strip():
            return (None, None)

        # try full address and fallback to unit stripped on failure
        for query in dict.fromkeys([addr, strip_unit(addr)]):
            try:
                loc = chill_geolocator(query)
            except Exception as e:
                continue
            if loc:
                return loc.latitude, loc.longitude
        return None, None

    apartments_df[['lat', 'lng']] = apartments_df['Address'].apply(get_cords).tolist()
    stops_df[['lat', 'lng']] = stops_df['Address'].apply(get_cords).tolist()

    # drop empty lat lng rows and reset the dataframe index
    apartments_df = apartments_df.dropna(subset=['lat', 'lng']).reset_index(drop=True)
    stops_df = stops_df.dropna(subset=['lat', 'lng']).reset_index(drop=True)

    # get index of all stop destinations
    coords_tstop = stops_df[['lat', 'lng']].values
    tree = KDTree(coords_tstop)

    # find the index of the closest 2 T stops to every apartment in the df
    K_NEIGHBORS = 2
    distances, indcs = tree.query(apartments_df[['lat', 'lng']].values, k=K_NEIGHBORS)

    def get_driving_dist(lon1, lat1, lon2, lat2):
        url = f"http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}"
        try:
            res = requests.get(url, params={"overview": "false"}).json()
            if res.get("routes"):
                return res["routes"][0]["distance"] * 0.000621371  # meters to miles
        except Exception:
            pass
        return float('inf')

    closest_matches = []

    for idx1, row1 in apartments_df.iterrows():
        best_stops_address = None
        min_drive_dist = float('inf')

        poss_indicies = indcs[idx1] if K_NEIGHBORS > 1 else [indcs[idx1]]

        for idx2 in poss_indicies:
            row2 = stops_df.iloc[idx2]

            driving_dist = get_driving_dist(row1['lng'], row1['lat'], row2['lng'], row2['lat'])

            if driving_dist < min_drive_dist:
                min_drive_dist = driving_dist
                best_stops_address = row2['Address']

        if best_stops_address:
            stop_color = stops_df.loc[stops_df['Address'] == best_stops_address, 'Line'].values[0]
            stop_name = stops_df.loc[stops_df['Address'] == best_stops_address, 'Stop Name'].values[0]
        else:
            stop_color = None
            stop_name = None

        closest_matches.append({
            'URL': row1['URL'],
            'closest_tstop_address': best_stops_address,
            'tstop_line': stop_color,
            'tstop_name': stop_name,
            'driving_distance_miles': round(min_drive_dist, 2)
        })

    COLUMNS = ["URL", "closest_tstop_address",
               "tstop_line", "tstop_name", "driving_distance_miles"]
    result_df = pd.DataFrame(closest_matches, columns=COLUMNS)

    if result_df.empty:
        print("No apartment/stop matches found.")
        return None

    cleaned_df = result_df.sort_values(by='driving_distance_miles', ascending=True)
    return cleaned_df


def rank_matches(full_df):
    df = full_df.copy()

    AMENITY_WEIGHTS = {
        "Laundry": 2,
        "Parking": 3,
        "Pets":    1,
    }

    def _truthy(series, col_type):
        # convert mixed type cols to bool for scoring
        if col_type == "Laundry":
            return (series.astype(str).str.strip().str.lower()
                    .isin({"In-unit"}))
        if col_type == "Parking":
            return (series.astype(str).str.strip().str.lower()
                    .isin({"Mentioned"}))
        if col_type == "Pets":
            return (series.astype(str).str.strip().str.lower()
                    .isin({"Pets allowed"}))

    def score_amenities(df, amenity_weights):
        max_possible = sum(amenity_weights.values())
        if max_possible == 0:
            return pd.Series(0.0, index=df.index)

        earned = pd.Series(0.0, index=df.index)
        for col, weight in amenity_weights.items():
            if col not in df.columns:  # amenity not scraped for this run — skip
                continue
            earned += _truthy(df[col], col) * weight

        # scale earned/max_possible (0–1) up to the same 0–5 range as the others
        return (earned / max_possible) * 5.0

    # convert price from string to value
    price_num = (df['Price'].astype(str)
                 .str.replace(r'[^0-9.]', '', regex=True)
                 .replace('', None)
                 .astype(float))
    dist_num = pd.to_numeric(df['driving_distance_miles'], errors='coerce')

    # score out of 5
    def normalize(series):
        s = series.astype(float)
        valid = s.dropna()

        if valid.empty:
            return pd.Series(0.0, index=s.index)
        lo, hi = valid.min(), valid.max()

        if hi == lo:
            scored = pd.Series(5.0, index=s.index)
        else:
            scored = (hi - s) / (hi - lo) * 5.0
        return scored.fillna(0.0)  # if price or distance is missing give it bad score

    dist_score = normalize(dist_num)
    price_score = normalize(price_num)
    amenity_score = score_amenities(full_df, AMENITY_WEIGHTS)

    def calculate_rank(d_score, p_score, a_score):
        return round(0.50 * d_score + 0.30 * p_score + 0.20 * a_score, 2)

    df['ranking'] = [calculate_rank(d, p, a) for d, p, a in zip(dist_score, price_score, amenity_score)]

    return df.sort_values(by='ranking', ascending=False).reset_index(drop=True)


def aggregate_nbr(loc, budget, beds, move_in_date):
    # Dynamically build the search query
    query = (ApartmentScraper(location=loc)
             .with_min_bedrooms(beds)
             .with_max_price(budget)
             .with_move_in(move_in_date))

    generated_url = query.build_url()

    # Run the robust scraper
    links = scrape_apartmentsdotcom(generated_url)

    if len(links) != 0:
        # FOR TESTING
        # links_temp = links[0:5]
        apartments_df = scrape_indiv_listing(links)
        if not apartments_df.empty:
            print(f"Found {len(apartments_df)} apartments\n {apartments_df}")
    else:
        return None


    api_key = os.getenv("TRANSIT_API_KEY")
    if not api_key:
        print("Missing TRANSIT_API_KEY. Add it to your .env file.")
        quit()

    if apartments_df.empty:
        all_info = apartments_df.copy()
        return all_info
    else:
        stops_df = get_transit(api_key)

        stop_info_df = get_closest_stop(stops_df, apartments_df)
        all_info = apartments_df.merge(stop_info_df, on="URL", how="left")

        # rank choices
        ranked_info = rank_matches(all_info)
        print(f"Final Apartment Dataframe:\n{ranked_info}")

    return ranked_info


def build_map(df, location, width_px=600, height_px=300, dpi=100, pad_frac=0.25):
    gdf = gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df['lng'], df['lat']), crs="EPSG:4326"
    ).to_crs(epsg=3857)

    fig_w, fig_h = width_px / dpi, height_px / dpi
    fig = plt.figure(figsize=(fig_w, fig_h), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])

    xmin, ymin, xmax, ymax = gdf.total_bounds
    cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2
    xspan, yspan = xmax - xmin, ymax - ymin

    DEFAULT_SPAN = 1500  # meters
    xspan = xspan or DEFAULT_SPAN
    yspan = yspan or DEFAULT_SPAN
    xspan *= (1 + pad_frac)
    yspan *= (1 + pad_frac)

    # expand the smaller dimension so the window aspect == figure aspect
    target = fig_w / fig_h
    if xspan / yspan < target:
        xspan = yspan * target
    else:
        yspan = xspan / target

    ax.set_xlim(cx - xspan / 2, cx + xspan / 2)
    ax.set_ylim(cy - yspan / 2, cy + yspan / 2)
    ax.set_aspect('equal')

    gdf.plot(ax=ax, color="blue", markersize=40)
    ctx.add_basemap(ax, source=ctx.providers.OpenStreetMap.Mapnik)
    ax.set_axis_off()

    title = f"{location}_map.png"
    fig.savefig(title, dpi=dpi)
    plt.close(fig)


def send_summary(df, subtitle=""):
    html, map_attachments = build.build_html(df, subtitle=subtitle)
    text = build.build_plaintext(df)

    msg = EmailMessage()
    msg["Subject"] = "Apartment Hunt Digest"
    msg["From"] = os.getenv("SENDER_EMAIL")
    msg["To"] = os.getenv("RECIPIENT_EMAIL")
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    html_part = msg.get_payload()[1]
    for cid, path in map_attachments:
        with open(path, "rb") as f:
            html_part.add_related(
                f.read(),
                "image", "png",
                cid=f"<{cid}>",
            )

    try:
        with smtplib.SMTP(os.getenv("SMTP_SERVER"), int(os.getenv("SMTP_PORT"))) as server:
            server.starttls()
            server.login(os.getenv("SENDER_EMAIL"), os.getenv("SENDER_PASSWORD"))
            server.send_message(msg)
        print("Email sent successfully!")
    except smtplib.SMTPAuthenticationError as e:
        print(f"Auth failed (check the Gmail App Password): {e}")
    except Exception as e:
        print(f"Failed to send email. Error: {e}")


if __name__ == "__main__":
    # "south-end-boston-ma", "beacon-hill-boston-ma", "back-bay-boston-ma", "allston-ma",
    target_cities = ["mid-cambridge-cambridge-ma", "the-port-cambridge-ma",
                     "kendall-square-cambridge-ma", "inman-square-cambridge-ma",
                     "south-end-boston-ma", "beacon-hill-boston-ma", "back-bay-boston-ma",
                     "spring-hill-somerville-ma", "davis-square-somerville-ma", "union-square-somerville-ma"
                     ]

    # for testing
    # target_cities = ["somerville-ma"]
    max_budget = 3000
    required_beds = 2
    move_in = datetime(2026, 8, 1)

    total_listings = []
    for location in target_cities:
        part = aggregate_nbr(loc=location, budget=max_budget, beds=required_beds, move_in_date=move_in)
        if part is None or part.empty:
            print(f"No listings for {location}, skipping.")
            continue
        part["neighborhood"] = location

        build_map(part, location)
        total_listings.append(part)

    email_df = pd.concat(total_listings, ignore_index=True)
    send_summary(email_df, subtitle="Budget $3,000 · 2 bed · move-in Aug 1, 2026")

