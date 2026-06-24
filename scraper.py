from seleniumbase import Driver
from selenium.webdriver.common.by import By
import time

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

    try:
        print("Starting the scrape ...")
        driver.get(search_url)

        time.sleep(5)

        for _ in range(2):
            driver.execute_script("window.scrollBy(0, 1200);")
            time.sleep(1)

        # might need to change based on site
        listings = driver.find_elements(By.CSS_SELECTOR, "article.placard")

        print(f"Current page title: {driver.get_title()}")
        driver.save_screenshot("debug_view.png")

        print(f"Found {len(listings)} listings on this page.\n")

        for index, property in enumerate(listings):
            try:
                title = property.find_element(By.CSS_SELECTOR, ".property-address").text
                price = property.find_element(By.CSS_SELECTOR, ".property-pricing").text
                link = property.find_element(By.CSS_SELECTOR, "a.property-link").get_attribute("href")

                print(f"[{index + 1}] {title}")
                print(f"    Price: {price}")
                print(f"    URL: {link}\n")

            except:
                continue
    finally:
        driver.quit()


if __name__ == "__main__":
    target_city = "Boston, MA"
    max_budget = 3000
    required_beds = 2

    # Dynamically build the search query
    query = (ApartmentScraper(location=target_city)
             .with_min_bedrooms(required_beds)
             .with_max_price(max_budget))

    generated_url = query.build_url()

    # Run the robust scraper
    scrape_apartmentsdotcom(generated_url)