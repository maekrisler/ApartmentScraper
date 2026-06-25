import random
import pandas as pd
from seleniumbase import Driver
from selenium.webdriver.common.by import By
import time
from datetime import datetime
from dateutil import parser as dateparser
import requests
import sys
from geopy.geocoders import Nominatim
from scipy.spatial import KDTree
from geopy.extra.rate_limiter import RateLimiter

pd.set_option('display.max_columns', None)


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

    def with_min_bedrooms(self, beds:int):
        if beds in self.bed_map:
            self.min_beds = self.bed_map[beds]
        return self

    def with_max_price(self, price:int):
        self.max_price = f"under-{price}"
        return self

    def with_move_in(self, move_in_date:datetime):
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

        if dt < today and (today - dt).days > 30:
            dt = dt.replace(year=dt.year + 1)
            return dt, False
    except (ValueError, OverflowError):
        return None, False


def scrape_indiv_listing(url_list):
    driver = Driver(uc=True, headless2=True)

    listings = []

    try:
        for idx, url in enumerate(url_list):
            try:
                print(f"[{idx + 1}/{len(url_list)}] Opening: {url}")
                driver.get(url)
                time.sleep(3)

                # grab listing title
                title = driver.find_element(By.CSS_SELECTOR, "h1.propertyName").text.strip()

                # get specific address (some listings have building name instead of addy)
                try:
                    addy = driver.find_element(By.CSS_SELECTOR, ".propertyAddress").text.strip()
                except:
                    addy = driver.find_element(By.CSS_SELECTOR, ".propertyAddressContainer").text.strip()

                # clean up
                full_addy = " ".join(addy.split())

                try:
                    price = driver.find_element(By.CSS_SELECTOR, ".rentInfoDetail, .priceRange").text.strip()
                except:
                    price = "Contact Property"

                try:
                    amenities_block = driver.find_element(By.ID, "amenitiesSection").text.lower()
                except:
                    amenities_block = ""

                try:
                    last_updated = driver.find_element(By.CSS_SELECTOR, "span.lastUpdated > span").text.strip()
                except:
                    last_updated = "Unknown"

                try:
                    available_raw = driver.find_element(By.CSS_SELECTOR, ".availabilityInfo").text.strip()
                except:
                    available_raw = ""

                avail_dt, is_now = parse_availability(available_raw, datetime.now())

                record = {
                    "Address": full_addy,
                    "Price": price,
                    "Available_Raw": available_raw,  # date is not always consistent
                    "Available_Date": avail_dt,      # better to add all and check manually
                    "Available_Now": is_now,
                    "Raw_Amenities": amenities_block,
                    "Last_Updated": last_updated,
                    "URL": url
                }

                listings.append(record)

                # don't get caught as a bot hehe
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
    apartments_df = apartments_df.dropna().reset_index(drop=True)
    stops_df = stops_df.dropna().reset_index(drop=True)

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
                return res["routes"][0]["distance"] * 0.000621371 # meters to miles
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
        else:
            stop_color = None

        closest_matches.append({
            'apartment_address': row1['Address'],
            'closest_tstop_address': best_stops_address,
            'tstop_line': stop_color,
            'driving_distance_miles': round(min_drive_dist, 2)
        })

    result_df = pd.DataFrame(closest_matches)
    cleaned_df = result_df.sort_values(by='driving_distance_miles', ascending=True)

    return cleaned_df




if __name__ == "__main__":
    target_city = "allston-ma"
    max_budget = 3000
    required_beds = 2
    move_in = datetime(2026, 8, 1)

    if len(sys.argv) < 2:
        print("Usage: python scraper.py <api_key>")
        quit()

    # Dynamically build the search query
    query = (ApartmentScraper(location=target_city)
             .with_min_bedrooms(required_beds)
             .with_max_price(max_budget)
             .with_move_in(move_in))

    generated_url = query.build_url()

    # Run the robust scraper
    links = scrape_apartmentsdotcom(generated_url)

    if links:
        # FOR TESTING
        # links_temp = links[0:4]
        apartments_df = scrape_indiv_listing(links)
        # print(f"Found {len(apartments_df)} apartments\n {apartments_df}")

    api_key = sys.argv[1]
    stops_df = get_transit(api_key)

    if apartments_df.empty:
        all_info = apartments_df.copy()
    else:
        stop_info_df = get_closest_stop(stops_df, apartments_df)
        all_info = apartments_df.merge(stop_info_df, on="Address", how="left")
        print(f"Final Apartment Dataframe:\n{all_info}")