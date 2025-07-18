from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dataclasses import dataclass
from typing import List

@dataclass
class FlightOption:
    airline: str
    depart_airport: str
    depart_time: str
    arrive_airport: str
    arrive_time: str
    stops: str
    price: str

def search_google_flights(origin: str, dest: str, date: str, max_results: int = 5) -> List[FlightOption]:
    """
    origin, dest: IATA codes (e.g. 'JFK', 'LHR')
    date: 'YYYY-MM-DD'
    """
    # 1. Build URL
    url = (
        "https://www.google.com/travel/flights"
        f"?q=Flights%20from%20{origin}%20to%20{dest}%20on%20{date}"
    )

    # 2. Start Chrome in visible mode
    opts = Options()
    opts.add_argument("--disable-blink-features=AutomationControlled")
    # comment out headless so you see the browser:
    # opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    driver = webdriver.Chrome(options=opts)

    driver.get(url)
    wait = WebDriverWait(driver, 30)

    # 3. Wait for flight cards (the price element is a good proxy)
    wait.until(EC.visibility_of_element_located(
        (By.CSS_SELECTOR, "div[role=listitem] .gws-flights-results__cheapest-price")
    ))

    cards = driver.find_elements(By.CSS_SELECTOR, "div[role=listitem]")[:max_results]
    out = []
    for card in cards:
        # Airline name
        airline = card.find_element(By.CSS_SELECTOR, ".gws-flights-results__carriers").text

        # Departure airport & time
        dep_air = card.find_element(By.CSS_SELECTOR,
            ".gws-flights-results__location gws-flights-results__location-airport:first-child"
        ).text
        dep_time = card.find_element(By.CSS_SELECTOR,
            ".gws-flights-results__times-row .gws-flights-results__time"
        ).text

        # Arrival airport & time
        arr_air = card.find_element(By.CSS_SELECTOR,
            ".gws-flights-results__location gws-flights-results__location-airport:last-child"
        ).text
        arr_time = card.find_elements(By.CSS_SELECTOR,
            ".gws-flights-results__times-row .gws-flights-results__time"
        )[-1].text

        # Stops
        stops = card.find_element(By.CSS_SELECTOR, ".gws-flights-results__stops").text

        # Price
        price = card.find_element(By.CSS_SELECTOR, ".gws-flights-results__cheapest-price").text

        out.append(FlightOption(
            airline=airline,
            depart_airport=dep_air,
            depart_time=dep_time,
            arrive_airport=arr_air,
            arrive_time=arr_time,
            stops=stops,
            price=price
        ))

    driver.quit()
    return out

if __name__ == "__main__":
    flights = search_google_flights("JFK", "LHR", "2025-08-15", max_results=5)
    for i, f in enumerate(flights, 1):
        print(f"Option {i}: {f}")
