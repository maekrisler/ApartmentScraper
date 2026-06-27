# Apartment Hunt Digest

A self-contained apartment-hunting pipeline that scrapes [apartments.com](https://www.apartments.com),
enriches each listing with transit and cost data, ranks the results, and emails you a
formatted digest with per-neighborhood maps.

For every run it will:

1. **Scrape** listings (houses, condos, townhomes) for one or more neighborhoods, filtered by
   bedrooms, max price, and move-in date.
2. **Extract** price, beds/baths/sq-ft, availability, amenities (laundry / parking / pets), and
   itemized move-in costs from each listing page.
3. **Geocode** every address (Nominatim / OpenStreetMap).
4. **Pull MBTA stops** for the Red, Green, Orange, Silver, and Blue lines, then compute the
   driving distance from each apartment to its nearest stops (OSRM).
5. **Rank** the listings with a weighted score (distance + price + amenities).
6. **Render** a static map per neighborhood and **email** an HTML + plaintext digest.

---

## Requirements

- **Python 3.9+**
- **Google Chrome / Chromium** — `seleniumbase` drives an undetected Chrome instance, so a
  Chrome binary must be installed locally.
- A **Gmail account with an App Password** (or another SMTP provider) for sending the digest.
- A free **MBTA v3 API key** — https://api-v3.mbta.com.

Install dependencies (see `pyproject.toml`):

```bash
pip install -e .
# or, if you prefer a plain requirements-style install:
pip install seleniumbase pandas geopandas contextily matplotlib \
            geopy scipy requests python-dateutil python-dotenv
```

The first `seleniumbase` run may download a matching ChromeDriver automatically.

---

## Setting up your `.env` file

The script reads all credentials and secrets from a local `.env` file via
[`python-dotenv`](https://pypi.org/project/python-dotenv/). **`.env` is never committed** — keep it
out of version control (add it to `.gitignore`).

Create a file named `.env` in the project root:

```dotenv
# --- Transit (MBTA v3) ---
TRANSIT_API_KEY=your_mbta_api_key_here

# --- Email delivery (SMTP) ---
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SENDER_EMAIL=you@gmail.com
SENDER_PASSWORD=your_16_char_gmail_app_password
RECIPIENT_EMAIL=destination@example.com
```

| Variable           | Used for                                                        | Notes                                                                                          |
| ------------------ | --------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `TRANSIT_API_KEY`  | Authenticating to the MBTA v3 API to fetch stop locations.      | Required. The script prints a warning and exits if it is missing.                              |
| `SMTP_SERVER`      | SMTP host for sending the digest.                               | `smtp.gmail.com` for Gmail.                                                                     |
| `SMTP_PORT`        | SMTP port.                                                      | `587` for STARTTLS (the script calls `server.starttls()`).                                     |
| `SENDER_EMAIL`     | The "From" address **and** the SMTP login user.                 | For Gmail this is your full address.                                                            |
| `SENDER_PASSWORD`  | SMTP login password.                                            | For Gmail use an **App Password**, *not* your normal password (requires 2FA enabled).          |
| `RECIPIENT_EMAIL`  | The "To" address that receives the digest.                      | Can be the same as `SENDER_EMAIL`.                                                              |

### Getting the values

- **MBTA API key:** register at https://api-v3.mbta.com and copy the key into `TRANSIT_API_KEY`.
- **Gmail App Password:** enable 2-Step Verification on your Google account, then create an
  App Password under *Google Account → Security → App passwords*. Use the 16-character value
  (spaces removed) as `SENDER_PASSWORD`. If auth fails, the script prints a hint to re-check this.

> Nominatim geocoding and the public OSRM routing server require **no API key**, but both are
> rate-limited public services. The script already throttles geocoding (1.5 s between calls);
> please be a courteous citizen and avoid hammering them.

---

## Running the script

Everything runs from `scraper.py`:

```bash
python scraper.py
```

There are no command-line arguments — the search parameters live in the `if __name__ == "__main__":`
block at the bottom of `scraper.py`. Edit them there.

### Adjusting the location and search parameters

```python
if __name__ == "__main__":
    target_cities = ["mid-cambridge-cambridge-ma", "the-port-cambridge-ma",
                     "kendall-square-cambridge-ma", "ward-two-cambridge-ma", "avon-hill-cambridge-ma"]

    max_budget = 3000
    required_beds = 2
    move_in = datetime(2026, 8, 1)
```

| Parameter        | What it controls                                                                                  | Format / allowed values                                                                                                                                                  |
| ---------------- | ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `target_cities`  | The neighborhoods/areas to search. Each is scraped, ranked, and mapped separately.                | apartments.com **URL slugs**, e.g. `"south-end-boston-ma"`, `"allston-ma"`, `"mid-cambridge-cambridge-ma"`. Find the slug by browsing apartments.com and copying it from the URL. |
| `max_budget`     | Maximum monthly rent.                                                                              | Integer dollars. Becomes the `under-<price>` URL filter.                                                                                                                  |
| `required_beds`  | Minimum bedrooms.                                                                                  | `0`–`3` only (`0`=studios, `1`–`3`=bedrooms). Mapped via `bed_map`; values outside this range are ignored.                                                                |
| `move_in`        | Earliest acceptable availability date. Listings available after this date are filtered out.       | `datetime(YYYY, M, D)`.                                                                                                                                                   |

Also worth knowing:

- **Housing types** are set in `ApartmentScraper.allowed_types` (`["houses", "condos", "townhomes"]`).
  Edit that list to change which property categories are searched.
- The digest **subtitle** is passed in the final `send_summary(...)` call — update it to match your
  parameters so the email header stays accurate.
- Friendly display names for neighborhood slugs live in `NEIGHBORHOOD_NAMES` inside `build_email.py`.
  Add an entry there if you want a pretty title instead of the raw slug.
- The MBTA lines fetched are hard-coded in `get_transit()` (`Red, Green, Orange, Silver, Blue`).

> **Tip — testing without a full run:** scraping every detail page is slow and bandwidth-heavy.
> While iterating, point `target_cities` at a single neighborhood. There is also a commented
> `links_temp = links[0:15]` slice in `aggregate_nbr()` you can wire in to cap the number of detail
> pages scraped per run.

When the run finishes you'll get a `<neighborhood>_map.png` for each area in the working directory
and an **"Apartment Hunt Digest"** email at `RECIPIENT_EMAIL`.

---

## How the score is calculated

Ranking happens in `rank_matches()` in `scraper.py`. Every listing gets a **score on a 0–5 scale**,
and listings are sorted from highest to lowest. The final score is a weighted blend of three
sub-scores:

```
score = 0.50 · distance_score      (50%)
      + 0.30 · price_score         (30%)
      + 0.20 · amenity_score       (20%)
```

(rounded to two decimals).

### 1. Distance and price sub-scores (`normalize`)

Both the driving distance to the nearest T stop and the monthly price are run through the same
min–max `normalize()` helper, which maps each value onto a **0–5** scale where **lower is better**:

```
normalized = (max - value) / (max - min) · 5
```

- The **closest** apartment (or the **cheapest**) scores **5**; the farthest/most expensive scores **0**.
- If every value is identical, everyone gets **5**.
- A **missing** price or distance is filled with **0** (i.e. treated as the worst possible).

Because this is a *relative* min–max normalization, scores are only comparable **within the set of
listings being ranked together**. Ranking is performed per neighborhood (each call to
`aggregate_nbr` ranks that neighborhood's listings), so a `5.0` in one neighborhood is not
necessarily equivalent to a `5.0` in another.

### 2. Amenity sub-score (`score_amenities`)

The amenity score is intended to reward in-unit laundry, parking, and pet-friendliness using these
weights:

| Amenity          | Weight |
| ---------------- | ------ |
| In-unit laundry  | 2      |
| Parking          | 3      |
| Pets allowed     | 1      |

The earned weight is normalized against the maximum possible (`2 + 3 + 1 = 6`) and scaled to the
same 0–5 range:

```
amenity_score = (earned_weight / 6) · 5
```

### 3. Final ranking

The three sub-scores are combined with the 50 / 30 / 20 weights above and rounded. Distance is the
dominant factor by design — proximity to transit matters most, price second, amenities third.
---

## Project structure

```
.
├── scraper.py        # Main pipeline: scrape → enrich → rank → map → email
├── build_email.py    # HTML + plaintext digest renderer (cards, maps, MBTA line colors)
├── pyproject.toml    # Project metadata & dependencies
├── .env              # Your secrets (NOT committed)
└── <slug>_map.png    # Generated per-neighborhood maps (output)
```

### External services used

| Service                         | Auth                | Purpose                              |
| ------------------------------- | ------------------- | ------------------------------------ |
| apartments.com                  | none (scraped)      | Listing source                       |
| MBTA v3 API                     | `TRANSIT_API_KEY`   | Transit stop locations               |
| Nominatim (OpenStreetMap)       | none (rate-limited) | Geocoding addresses → lat/lng        |
| OSRM public routing server      | none (rate-limited) | Driving distance apartment → stop    |
| SMTP (e.g. Gmail)               | `SENDER_PASSWORD`   | Sending the digest email             |

---

## Notes & caveats

- **Scraping is fragile by nature.** apartments.com may change its markup or serve CAPTCHAs; the
  script includes a `looks_blocked()` check and randomized delays, but selectors may need updating
  over time.
- **Be respectful of free services.** Nominatim and the public OSRM demo server are not meant for
  heavy load. Keep batch sizes reasonable and the built-in delays in place.
- **Geocoding accuracy varies.** Addresses are geocoded with a full-address attempt and a
  unit-stripped fallback; some listings may still fail to resolve and are dropped from ranking.
- This tool is for **personal use**. Review the terms of service of any site or API you query.