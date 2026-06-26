import random
import pandas as pd
from seleniumbase import Driver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException
import time
from datetime import datetime
from dateutil import parser as dateparser
import requests
import sys
from geopy.geocoders import Nominatim
from scipy.spatial import KDTree
from geopy.extra.rate_limiter import RateLimiter
import re
import os
from dotenv import load_dotenv
import smtplib
from email.message import EmailMessage
import build_email as build

pd.set_option('display.max_columns', None)
load_dotenv()


class ApartmentScraper:
    BASE_URL = "https://www.apartments.com"

    def __init__(self, location: str):
        self.location = location.lower().replace(", ", "-").replace(" ", "-")
        self.min_beds = None
        self.max_price = None
        self.laundry = None
        self.pets = None
        self.parking = None
        self.move_in_date = None

        # apartment.com specific strings
        self.bed_map = {0: "studios", 1: "1-bedrooms", 2: "2-bedrooms", 3: "3-bedrooms"}

        # desired apartment types
        self.allowed_types = ["houses", "condos", "townhomes"]

    def with_min_bedrooms(self, beds: int):
        if beds in self.bed_map:
            self.min_beds = self.bed_map[beds]
        return self

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
    COLUMNS = ["Address", "Price", "Available_Raw", "Available_Date",
               "Available_Now", "Raw_Amenities", "Last_Updated", "URL"]

    df = pd.DataFrame(listings, columns=COLUMNS)

    # coerce converts None -> NaT for datetime comparison
    df["Available_Date"] = pd.to_datetime(df["Available_Date"], errors="coerce")
    df["Available_Now"] = df["Available_Now"].fillna(False).astype(bool)

    mask = df["Available_Now"] | (df["Available_Date"] <= pd.Timestamp(move_in))
    df_cleaned = df[mask].drop_duplicates(subset=["Address"], keep="first")

    return df_cleaned


def get_transit(api_key):
    url = "https://api-v3.mbta.com/stops"
    headers = {
        "X-API-Key": api_key
    }

    lines = ["Red", "Green", "Orange", "Silver"]
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
    chill_geolocator = RateLimiter(geolocator.geocode, min_delay_seconds=1.5)

    # function blueprint of how df should calculate address locations
    def get_cords(addr):
        loc = chill_geolocator(addr)
        return (loc.latitude, loc.longitude) if loc else (None, None)

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

    def calculate_rank(d_score, p_score):
        return round(0.65 * d_score + 0.35 * p_score, 2)

    df['ranking'] = [calculate_rank(d, p) for d, p in zip(dist_score, price_score)]

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

    if links:
        # FOR TESTING
        # links_temp = links[0:10]
        apartments_df = scrape_indiv_listing(links)
        # print(f"Found {len(apartments_df)} apartments\n {apartments_df}")

    api_key = os.getenv("TRANSIT_API_KEY")
    if not api_key:
        print("Missing TRANSIT_API_KEY. Add it to your .env file.")
        quit()

    if apartments_df.empty:
        all_info = apartments_df.copy()
    else:
        stops_df = get_transit(api_key)

        stop_info_df = get_closest_stop(stops_df, apartments_df)
        all_info = apartments_df.merge(stop_info_df, on="URL", how="left")

        # rank choices
        ranked_info = rank_matches(all_info)
        print(f"Final Apartment Dataframe:\n{ranked_info}")

    return ranked_info


def send_summary(df, subtitle=""):
    html = build.build_html(df, subtitle=subtitle)
    text = build.build_plaintext(df)

    msg = EmailMessage()
    msg["Subject"] = "Apartment Hunt Digest"
    msg["From"] = os.getenv("SENDER_EMAIL")
    msg["To"] = os.getenv("RECIPIENT_EMAIL")
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

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
    target_cities = ["allston-ma", "cambridge-ma", "somerville-ma"]

    # for testing
    # target_cities = ["allston-ma"]
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
        total_listings.append(part)

    email_df = pd.concat(total_listings, ignore_index=True)
    send_summary(email_df, subtitle="Budget $3,000 · 2 bed · move-in Aug 1, 2026")

