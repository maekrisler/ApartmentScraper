from seleniumbase import Driver
from selenium.webdriver.common.by import By
import time

def scrape_apartmentsdotcom(search_url):
    # make that baby undetected
    driver = Driver(uc=True, headless2=True)

    try:
        print("Starting the scrape ...")
        driver.get(search_url)

        time.sleep(5)

        for _ in range(3):
            driver.execute_script("window.scrollBy(0, 1000);")
            time.sleep(1.5)

        # might need to change based on site
        listings = driver.find_elements(By.CSS_SELECTOR, "article.placard")

        print(f"Found {len(listings)} listings on this page.\n")

        for index, property in enumerate(listings):
            try:
                title = property.find_element(By.CSS_SELECTOR, ".property-address").text
                price = property.find_element(By.CSS_SELECTOR, ".property-pricing").text
                link = property.find_element(By.CSS_SELECTOR, "a.property-link").get_attribute("href")

                print(f"[{index + 1}] {title}")
                print(f"    Price: {price}")
                print(f"    URL: {link}\n")

            except Exception as e:
                continue
    finally:
        driver.quit()


if __name__ == "__main__":
    # Example Target URL (Replace with your actual filtered search URL)
    target_url = "https://www.apartments.com/rochester-ny/min-1-bedrooms-under-1500/"
    scrape_apartmentsdotcom(target_url)