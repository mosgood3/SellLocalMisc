"""
Fetch restaurants in a target city via Google Places API (New), then scrape
their websites for contact emails. Writes contacts.csv for send.py to use.

Requires GOOGLE_PLACES_API_KEY in misc/.env (Places API New must be enabled
in Google Cloud Console for that key).

Usage:
  python fetch.py                                        # runs all default queries (restaurants, cafes, bakeries, pizza) in Portland, ME
  python fetch.py --query "tacos in Portland, ME"        # one custom query instead of the defaults
  python fetch.py --cities cities.txt                    # fan out across multiple cities (one per line; '#' lines ignored)
  python fetch.py --dry-run                              # preview, no CSV written
  python fetch.py --max-pages 3                          # cap pagination per query (20 results per page, up to 3 pages = 60 places)
"""

import argparse
import csv
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from dotenv import load_dotenv

# Force stdout to utf-8 so restaurant names with accents don't crash on Windows cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MISC_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(MISC_ROOT / ".env")

# Fallback: if the Places key isn't in misc/.env, look in SellLocal/.env.local
if not (os.getenv("GOOGLE_PLACES_API_KEY") or os.getenv("NEXT_PUBLIC_GOOGLE_PLACES_API_KEY")):
    selllocal_env = MISC_ROOT.parent / "SellLocal" / ".env.local"
    if selllocal_env.exists():
        load_dotenv(selllocal_env)

API_KEY = os.getenv("GOOGLE_PLACES_API_KEY") or os.getenv("NEXT_PUBLIC_GOOGLE_PLACES_API_KEY")

CAMPAIGN_DIR = Path(__file__).resolve().parent
DEFAULT_CITY = "Portland, ME"
QUERY_TYPES = ["restaurants", "cafes", "bakeries", "pizza"]
DEFAULT_QUERIES = [f"{q} in {DEFAULT_CITY}" for q in QUERY_TYPES]


def load_cities(path: Path) -> list[str]:
    """Read a list of cities from a text file (one per line, '#' lines ignored)."""
    cities: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cities.append(line)
    return cities

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.websiteUri",
    "places.nationalPhoneNumber",
    "places.formattedAddress",
    "nextPageToken",
])

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Emails we never want to surface as outreach contacts.
EMAIL_DENYLIST_SUBSTRINGS = (
    "wix", "wixpress", "sentry", "godaddy", "squarespace", "wordpress",
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "example.com", "example.org", "yourdomain", "domain.com",
    "@sentry.io", "@2x", "u003e", "u003c",
)

# Pages we'll probe on each restaurant site looking for contact info.
CONTACT_PATHS = ("", "contact", "contact-us", "contact/", "about", "about-us")

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SellLocalLeadFinder/1.0; +https://selllocal.app)",
}


def search_places(query: str, max_pages: int) -> list[dict]:
    """Hit Places API Text Search and paginate up to max_pages (20 places per page)."""
    results: list[dict] = []
    page_token: str | None = None

    for page in range(max_pages):
        body: dict = {"textQuery": query}
        if page_token:
            body["pageToken"] = page_token

        resp = requests.post(
            PLACES_SEARCH_URL,
            json=body,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": API_KEY,
                "X-Goog-FieldMask": FIELD_MASK,
            },
            timeout=15,
        )

        if resp.status_code != 200:
            print(f"Places API error {resp.status_code}: {resp.text[:300]}")
            sys.exit(1)

        data = resp.json()
        places = data.get("places", [])
        results.extend(places)
        print(f"  Page {page + 1}: {len(places)} places")

        page_token = data.get("nextPageToken")
        if not page_token:
            break

        # Google requires a brief delay before nextPageToken becomes valid.
        time.sleep(2)

    return results


def extract_emails(html: str, site_domain: str) -> list[str]:
    """Pull plausible contact emails from a page's HTML."""
    found = []
    seen = set()
    for match in EMAIL_REGEX.findall(html):
        email = match.lower().strip(".")
        if email in seen:
            continue
        if any(bad in email for bad in EMAIL_DENYLIST_SUBSTRINGS):
            continue
        if email.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
            continue
        # Common false positives from CSS/JS tokens
        if "@" not in email or "." not in email.split("@")[1]:
            continue
        seen.add(email)
        found.append(email)

    # Prefer emails on the same domain as the site itself.
    if site_domain:
        found.sort(key=lambda e: 0 if site_domain in e.split("@")[1] else 1)
    return found


def find_email_for_site(website: str) -> str | None:
    """Probe a restaurant's website for a contact email. Returns the first hit."""
    if not website:
        return None

    try:
        parsed = urlparse(website if "://" in website else f"https://{website}")
    except ValueError:
        return None

    base = f"{parsed.scheme or 'https'}://{parsed.netloc}"
    site_domain = parsed.netloc.lower().lstrip("www.")

    for path in CONTACT_PATHS:
        url = urljoin(base + "/", path)
        try:
            r = requests.get(url, headers=REQUEST_HEADERS, timeout=8, allow_redirects=True)
        except requests.RequestException:
            continue
        if r.status_code != 200 or not r.text:
            continue

        emails = extract_emails(r.text, site_domain)
        if emails:
            return emails[0]

    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Find restaurant leads via Google Places and scrape contact emails")
    parser.add_argument("--query", default=None, help="One custom Places query. If omitted, runs all defaults: " + "; ".join(DEFAULT_QUERIES))
    parser.add_argument("--cities", default=None, help="Path to a text file listing cities (one per line). Runs all query types across every city.")
    parser.add_argument("--max-pages", type=int, default=3, help="Max Places pages per query (20 results each, up to 3)")
    parser.add_argument("--dry-run", action="store_true", help="Preview results without writing CSV")
    parser.add_argument("--scrape-delay", type=float, default=0.5, help="Delay between site scrapes (seconds)")
    args = parser.parse_args()

    if not API_KEY:
        print("Error: GOOGLE_PLACES_API_KEY is not set in misc/.env")
        print("  Copy it from SellLocal/.env.local (NEXT_PUBLIC_GOOGLE_PLACES_API_KEY) and")
        print("  make sure 'Places API (New)' is enabled for that key in Google Cloud Console.")
        sys.exit(1)

    if args.cities:
        cities = load_cities(Path(args.cities))
        if not cities:
            print(f"Error: no cities found in {args.cities}")
            sys.exit(1)
        queries = [f"{q} in {city}" for city in cities for q in QUERY_TYPES]
        print(f"Loaded {len(cities)} cities -> {len(queries)} queries\n")
    elif args.query:
        queries = [args.query]
    else:
        queries = DEFAULT_QUERIES

    places: list[dict] = []
    seen_ids: set[str] = set()
    for q in queries:
        print(f"Query: {q}")
        print(f"  Fetching (up to {args.max_pages * 20})...")
        for place in search_places(q, args.max_pages):
            pid = place.get("id")
            if pid and pid in seen_ids:
                continue
            if pid:
                seen_ids.add(pid)
            places.append(place)
        print()

    print(f"Got {len(places)} unique place(s) across {len(queries)} quer{'y' if len(queries) == 1 else 'ies'}.\n")

    if not places:
        print("No places returned. Try a different query.")
        sys.exit(0)

    rows: list[dict] = []
    with_email = 0
    no_website = 0

    for i, place in enumerate(places, start=1):
        name = (place.get("displayName") or {}).get("text") or ""
        website = place.get("websiteUri") or ""
        phone = place.get("nationalPhoneNumber") or ""
        restaurant_address = place.get("formattedAddress") or ""

        if not website:
            no_website += 1
            print(f"  [{i}/{len(places)}] {name}: no website on Google")
            rows.append({"email": "", "name": name, "website": "", "phone": phone, "restaurant_address": restaurant_address})
            continue

        email = find_email_for_site(website)
        if email:
            with_email += 1
            print(f"  [{i}/{len(places)}] {name}: {email}")
        else:
            print(f"  [{i}/{len(places)}] {name}: no email found on {website}")

        rows.append({
            "email": email or "",
            "name": name,
            "website": website,
            "phone": phone,
            "restaurant_address": restaurant_address,
        })
        time.sleep(args.scrape_delay)

    sendable = [r for r in rows if r["email"]]
    all_rows_path = CAMPAIGN_DIR / "all_places.csv"
    contacts_path = CAMPAIGN_DIR / "contacts.csv"

    print()
    print(f"Total places:      {len(rows)}")
    print(f"  With email:      {with_email}")
    print(f"  No website:      {no_website}")
    print(f"  Website, no email: {len(rows) - with_email - no_website}")
    print(f"Sendable contacts: {len(sendable)}")

    if args.dry_run:
        print("\n[DRY RUN] No files written.")
        return

    fieldnames = ["email", "name", "website", "phone", "restaurant_address"]
    with open(all_rows_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    with open(contacts_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sendable)

    print(f"\nWrote {all_rows_path.name} (full list, for review)")
    print(f"Wrote {contacts_path.name} ({len(sendable)} contacts ready for send.py)")


if __name__ == "__main__":
    main()
