import random
import pandas as pd
from seleniumbase import Driver
from selenium.webdriver.common.by import By
import time
import requests
import sys

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

                title = driver.find_element(By.CSS_SELECTOR, "h1.propertyName").text.strip()

                try:
                    price = driver.find_element(By.CSS_SELECTOR, ".rentInfoLabel, .priceRange").text.strip()
                except:
                    price = "Contact Property"

                try:
                    amenities_block = driver.find_element(By.ID, "amenitiesSection").text.lower()
                except:
                    amenities_block = ""

                record = {
                    "Address": title,
                    "Price": price,
                    "Raw_Amenities": amenities_block,
                    "URL": url
                }

                listings.append(record)

                # don't get caught as a bot hehe
                time.sleep(random.uniform(1.5, 3.5))

            except Exception as e:
                print(f"Skipping broken detail page {url}: {e}")
                continue
    finally:
        driver.quit()

    return pd.DataFrame(listings)


def get_transit(api_key):
    url = "https://api-v3.mbta.com/stops"

    params = {
        "filter[route]": "Red,Green,Silver"
    }

    headers = {
        "X-API-Key": api_key
    }

    response = requests.get(url, params=params, headers=headers)

    if response.status_code == 200:
        json_data = response.json()

        stops_list = []
        for stop in json_data.get('data', []):
            attributes = stop.get('attributes', {})
            stops_list.append({
                'Stop Name': attributes.get('name'),
                'Address': attributes.get('address')
            })

        stops_df = pd.DataFrame(stops_list).dropna(subset=['Address']).drop_duplicates().reset_index(drop=True)

        print(stops_df.head())
        return stops_df
    else:
        print(f"Failed to fetch data: {response.status_code}")



if __name__ == "__main__":
    target_city = "cambridge-ma"
    max_budget = 3000
    required_beds = 2

    # Dynamically build the search query
    query = (ApartmentScraper(location=target_city)
             .with_min_bedrooms(required_beds)
             .with_max_price(max_budget))

    generated_url = query.build_url()

    # Run the robust scraper
    # links = scrape_apartmentsdotcom(generated_url)
    #
    # if links:
    #     final_df = scrape_indiv_listing(links)
    #     print(final_df.head())

    if len(sys.argv) < 3:
        print("Usage: python scraper.py <api_key>")
        api_key = sys.argv[1]
        stops_df = get_transit(api_key)
