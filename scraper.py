import random
import pandas as pd
from seleniumbase import Driver
from selenium.webdriver.common.by import By
import time
import requests
import sys
from geopy.geocoders import Nominatim
from scipy.spatial import KDTree
from geopy.extra.rate_limiter import RateLimiter


class ApartmentScraper:
    BASE_URL = "https://www.apartments.com"

    def __init__(self, location: str):
        self.location = location.lower().replace(", ", "-").replace(" ", "-")
        self.min_beds = None
        self.max_price = None
        self.laundry = None
        self.pets = None
        self.parking = None

        # apartment.com specific strings
        self.bed_map = {0: "studios", 1: "1-bedrooms", 2: "2-bedrooms", 3: "3-bedrooms"}

    def with_min_bedrooms(self, beds:int):
        if beds in self.bed_map:
            self.min_beds = self.bed_map[beds]
        return self

    def with_max_price(self, price:int):
        self.max_price = f"under-{price}"
        return self

    def build_url(self) -> str:
        components = [self.location]

        modifiers = []
        if self.min_beds:
            modifiers.append(f"min-{self.min_beds}")
        if self.max_price:
            modifiers.append(self.max_price)
        if modifiers:
            components.append("-".join(modifiers))

        final_path = "/".join(components) + "/"
        return f"{self.BASE_URL}/{final_path}"

def scrape_apartmentsdotcom(search_url):
    # make that baby undetected
    driver = Driver(uc=True, headless2=True)

    all_links = []
    try:
        print("Starting the scrape ...")
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
                    addy = driver.find_element(By.CSS_SELECTOR, ".propertyAddressContainer").text.strip

                # clean up
                full_addy = " ".join(addy.split())

                try:
                    price = driver.find_element(By.CSS_SELECTOR, ".rentInfoLabel, .priceRange").text.strip()
                except:
                    price = "Contact Property"

                try:
                    amenities_block = driver.find_element(By.ID, "amenitiesSection").text.lower()
                except:
                    amenities_block = ""

                record = {
                    "Address": full_addy,
                    "Price": price,
                    "Raw_Amenities": amenities_block,
                    "URL": url
                }

                listings.append(record)

                # don't get caught as a bot hehe
                time.sleep(random.uniform(1.5, 3.5))

            except Exception as e:
                print(f"Skipping broken detail page: {url}")
                continue
    finally:
        driver.quit()

    return pd.DataFrame(listings)


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
    print(stops_df.head())
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
    result_df.sort_values(by='driving_distance_miles', inplace=True, ascending=False)
    print(result_df.head())
    return result_df




if __name__ == "__main__":
    target_city = "cambridge-ma"
    max_budget = 3000
    required_beds = 2

    if len(sys.argv) < 2:
        print("Usage: python scraper.py <api_key>")
        quit()

    # Dynamically build the search query
    query = (ApartmentScraper(location=target_city)
             .with_min_bedrooms(required_beds)
             .with_max_price(max_budget))

    generated_url = query.build_url()

    # Run the robust scraper
    links = scrape_apartmentsdotcom(generated_url)

    if links:
        # FOR TESTING
        links_temp = links[0:9]
        apartments_df = scrape_indiv_listing(links_temp)

    api_key = sys.argv[1]
    stops_df = get_transit(api_key)

    full_df = get_closest_stop(stops_df, apartments_df)
    print(full_df)
