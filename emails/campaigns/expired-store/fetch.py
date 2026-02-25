"""
Fetch expired/lapsed SellLocal tenants from Supabase and write contacts.csv.

Finds tenants where:
  - subscription_status != 'active'
  - trial_ends_at < now OR subscription_ends_at < now

Usage:
  python fetch.py              # writes contacts.csv in this directory
  python fetch.py --dry-run    # preview without writing
"""

import argparse
import csv
import sys
from datetime import datetime, timezone
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


def fetch_expired_tenants() -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    resp = (
        supabase.table("sell_local_tenants")
        .select("owner_email, name, slug, domain, subscription_status, subscription_ends_at")
        .neq("subscription_status", "active")
        .eq("email_opt_in", True)
        .or_(f"trial_ends_at.lt.{now},subscription_ends_at.lt.{now}")
        .execute()
    )
    return resp.data or []


def write_csv(tenants: list[dict], output_path: Path) -> None:
    fieldnames = ["email", "name", "slug", "domain", "subscription_status", "subscription_ends_at"]
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
                "subscription_ends_at": t["subscription_ends_at"],
            })


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch expired SellLocal tenants to contacts.csv")
    parser.add_argument("--dry-run", action="store_true", help="Preview results without writing CSV")
    args = parser.parse_args()

    print("Fetching expired tenants from Supabase...")
    tenants = fetch_expired_tenants()

    if not tenants:
        print("No expired tenants found.")
        sys.exit(0)

    print(f"Found {len(tenants)} expired tenant(s):\n")
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
