"""
Fetch active SellLocal subscribers and write contacts.csv.

Finds tenants where:
  - subscription_status = 'active'
  - email_opt_in = true

Usage:
  python fetch.py              # writes contacts.csv in this directory
  python fetch.py --dry-run    # preview without writing
"""

import argparse
import csv
import sys
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client
import os

MISC_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(MISC_ROOT / ".env")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

CAMPAIGN_DIR = Path(__file__).resolve().parent


def fetch_active_subscribers() -> list[dict]:
    resp = (
        supabase.table("sell_local_tenants")
        .select("owner_email, name, slug, domain, subscription_status")
        .eq("subscription_status", "active")
        .eq("email_opt_in", True)
        .execute()
    )
    return resp.data or []


def write_csv(tenants: list[dict], output_path: Path) -> None:
    fieldnames = ["email", "name", "slug", "domain", "subscription_status"]
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in tenants:
            writer.writerow({
                "email": t["owner_email"],
                "name": t["name"],
                "slug": t["slug"],
                "domain": t.get("domain") or "",
                "subscription_status": t["subscription_status"],
            })


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch active SellLocal subscribers to contacts.csv")
    parser.add_argument("--dry-run", action="store_true", help="Preview results without writing CSV")
    args = parser.parse_args()

    print("Fetching active subscribers from Supabase...")
    tenants = fetch_active_subscribers()

    if not tenants:
        print("No active subscribers found.")
        sys.exit(0)

    print(f"Found {len(tenants)} active subscriber(s):\n")
    for t in tenants:
        print(f"  {t['owner_email']:40s}  {t['subscription_status']:12s}  {t.get('slug', '')}")

    if args.dry_run:
        print("\n[DRY RUN] No file written.")
        return

    output = CAMPAIGN_DIR / "contacts.csv"
    write_csv(tenants, output)
    print(f"\nWritten to {output}")


if __name__ == "__main__":
    main()
