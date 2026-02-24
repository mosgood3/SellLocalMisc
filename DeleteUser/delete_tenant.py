"""
SellLocal Tenant Deletion Script

Removes a SellLocal tenant given their email address:
  - Archives tenant info (Stripe IDs, metadata) to sell_local_deleted_tenants
  - Removes Vercel custom domain
  - Deletes Supabase Storage files (gallery images)
  - Deletes all database records (child tables first)
  - Deletes Supabase auth user

Stripe data (subscriptions, customers, Connect accounts) is intentionally
preserved for payment audit/history purposes. Key IDs are archived in the
sell_local_deleted_tenants table before deletion.

Usage:
  python delete_tenant.py --email someone@example.com --dry-run
  python delete_tenant.py --email someone@example.com
  python delete_tenant.py --email someone@example.com --force
"""

import argparse
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from supabase import create_client
import os

MISC_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(MISC_ROOT / ".env")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
VERCEL_API_TOKEN = os.environ.get("VERCEL_API_TOKEN", "")
SELLLOCAL_VERCEL_PROJECT_ID = os.environ.get("SELLLOCAL_VERCEL_PROJECT_ID", "")
VERCEL_TEAM_ID = os.environ.get("VERCEL_TEAM_ID", "")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# ---------------------------------------------------------------------------
# Database tables to delete, in dependency order (children first).
#
# Each entry is (table_name, key_column, lookup_mode):
#   "direct"  — DELETE WHERE key_column = tenant_id
#   "via_subscribers" — DELETE WHERE subscriber_id IN (subscriber ids for tenant)
#   "via_pickups" — DELETE WHERE pickup_id IN (pickup ids for tenant)
# ---------------------------------------------------------------------------

TABLES_TO_DELETE = [
    # Newsletter sends reference subscribers (FK subscriber_id)
    ("sell_local_newsletter_sends", "subscriber_id", "via_subscribers"),
    ("sell_local_newsletter_subscribers", "tenant_id", "direct"),
    # Pickup products reference pickups + products
    ("sell_local_pickup_products", "pickup_id", "via_pickups"),
    # Orders
    ("sell_local_pending_orders", "tenant_id", "direct"),
    ("sell_local_orders", "tenant_id", "direct"),
    ("sell_local_pickups", "tenant_id", "direct"),
    # Recipes — purchases before recipes
    ("sell_local_recipe_purchases", "tenant_id", "direct"),
    ("sell_local_recipes", "tenant_id", "direct"),
    # Menu mode tables
    ("sell_local_menu_inventory", "tenant_id", "direct"),
    ("sell_local_menu_schedule", "tenant_id", "direct"),
    # Categories (has CASCADE but explicit is safer)
    ("sell_local_categories", "tenant_id", "direct"),
    # Products (after pickup_products, menu_inventory, categories)
    ("sell_local_products", "tenant_id", "direct"),
    # Settings & config
    ("sell_local_settings", "tenant_id", "direct"),
    ("sell_local_store_settings", "tenant_id", "direct"),
    # Content & branding
    ("sell_local_affiliate_links", "tenant_id", "direct"),
    ("sell_local_social_links", "tenant_id", "direct"),
    ("sell_local_hero_content", "tenant_id", "direct"),
    ("sell_local_about_content", "tenant_id", "direct"),
    ("sell_local_branding", "tenant_id", "direct"),
    ("sell_local_site_theme", "tenant_id", "direct"),
    ("sell_local_notification_banner", "tenant_id", "direct"),
    # Audit log
    ("sell_local_audit_log", "tenant_id", "direct"),
    # The tenant row itself (last)
    ("sell_local_tenants", "id", "direct"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(f"  {msg}")


def log_step(step: int, title: str) -> None:
    print(f"\n[Step {step}] {title}")


def count_rows(table: str, column: str, value: str) -> int:
    """Return the count of rows matching column = value."""
    resp = supabase.table(table).select("id", count="exact").eq(column, value).execute()
    return resp.count or 0


def count_rows_in(table: str, column: str, values: list[str]) -> int:
    """Return the count of rows where column is in values."""
    if not values:
        return 0
    resp = supabase.table(table).select("id", count="exact").in_(column, values).execute()
    return resp.count or 0


# ---------------------------------------------------------------------------
# Step 1: Look up tenant
# ---------------------------------------------------------------------------

def lookup_tenant(email: str) -> dict | None:
    resp = supabase.table("sell_local_tenants").select("*").eq("owner_email", email).execute()
    if not resp.data:
        return None
    return resp.data[0]


def print_tenant_summary(tenant: dict) -> None:
    print("\n--- Tenant Summary ---")
    fields = [
        ("ID", "id"),
        ("Slug", "slug"),
        ("Domain", "domain"),
        ("Name", "name"),
        ("Owner Email", "owner_email"),
        ("User ID", "user_id"),
        ("Subscription Status", "subscription_status"),
    ]
    for label, key in fields:
        print(f"  {label}: {tenant.get(key) or '(none)'}")
    print("----------------------")


# ---------------------------------------------------------------------------
# Step 2: Archive tenant info
# ---------------------------------------------------------------------------

def archive_tenant(tenant: dict, dry_run: bool) -> None:
    log_step(2, "Archive tenant to sell_local_deleted_tenants")
    record = {
        "tenant_id": tenant["id"],
        "slug": tenant.get("slug"),
        "name": tenant.get("name"),
        "owner_email": tenant.get("owner_email"),
        "user_id": tenant.get("user_id"),
        "domain": tenant.get("domain"),
        "stripe_customer_id": tenant.get("stripe_customer_id"),
        "stripe_subscription_id": tenant.get("stripe_subscription_id"),
        "stripe_connect_account_id": tenant.get("stripe_connect_account_id"),
        "subscription_status": tenant.get("subscription_status"),
        "tenant_created_at": tenant.get("created_at"),
    }

    if dry_run:
        log("[DRY RUN] Would archive tenant info:")
        for k, v in record.items():
            log(f"  {k}: {v or '(none)'}")
        return

    supabase.table("sell_local_deleted_tenants").insert(record).execute()
    log("Archived tenant info")


# ---------------------------------------------------------------------------
# Step 3: Remove Vercel domain
# ---------------------------------------------------------------------------

def remove_vercel_domain(tenant: dict, dry_run: bool) -> None:
    log_step(3, "Remove Vercel domain")
    domain = tenant.get("domain")

    if not domain:
        log("No custom domain to remove")
        return

    if not VERCEL_API_TOKEN or not SELLLOCAL_VERCEL_PROJECT_ID:
        log("Vercel credentials not configured — skipping domain removal")
        return

    if dry_run:
        log(f"[DRY RUN] Would remove domain: {domain}")
        return

    url = f"https://api.vercel.com/v10/projects/{SELLLOCAL_VERCEL_PROJECT_ID}/domains/{domain}"
    params = {}
    if VERCEL_TEAM_ID:
        params["teamId"] = VERCEL_TEAM_ID
    headers = {"Authorization": f"Bearer {VERCEL_API_TOKEN}"}

    resp = requests.delete(url, headers=headers, params=params, timeout=30)
    if resp.status_code in (200, 204):
        log(f"Removed domain: {domain}")
    elif resp.status_code == 404:
        log(f"Domain not found (already removed?): {domain}")
    else:
        log(f"Domain removal failed ({resp.status_code}): {resp.text}")


# ---------------------------------------------------------------------------
# Step 4: Delete Supabase Storage files
# ---------------------------------------------------------------------------

def delete_storage_files(tenant: dict, dry_run: bool) -> None:
    log_step(4, "Delete Supabase Storage files")
    tenant_id = tenant["id"]
    prefix = f"gallery/{tenant_id}"
    bucket = "images"

    try:
        files = supabase.storage.from_(bucket).list(prefix)
    except Exception as e:
        log(f"Could not list storage files ({e})")
        return

    if not files:
        log("No storage files found")
        return

    paths = [f"{prefix}/{f['name']}" for f in files if f.get("name")]
    log(f"Found {len(paths)} file(s) in {bucket}/{prefix}")

    if dry_run:
        for p in paths:
            log(f"  [DRY RUN] Would delete: {p}")
        return

    try:
        supabase.storage.from_(bucket).remove(paths)
        log(f"Deleted {len(paths)} file(s)")
    except Exception as e:
        log(f"Storage deletion error: {e}")


# ---------------------------------------------------------------------------
# Step 5: Delete database records (child tables first)
# ---------------------------------------------------------------------------

def delete_database_records(tenant: dict, dry_run: bool) -> None:
    log_step(5, "Delete database records")
    tenant_id = tenant["id"]

    # Pre-fetch IDs needed for "via" lookups
    subscriber_ids = _get_ids("sell_local_newsletter_subscribers", "tenant_id", tenant_id)
    pickup_ids = _get_ids("sell_local_pickups", "tenant_id", tenant_id)

    for table, column, mode in TABLES_TO_DELETE:
        if mode == "direct":
            value = tenant_id
            count = count_rows(table, column, value)
        elif mode == "via_subscribers":
            value = subscriber_ids
            count = count_rows_in(table, column, value)
        elif mode == "via_pickups":
            value = pickup_ids
            count = count_rows_in(table, column, value)
        else:
            continue

        if count == 0:
            log(f"  {table}: 0 rows (skip)")
            continue

        if dry_run:
            log(f"  [DRY RUN] {table}: {count} row(s) would be deleted")
        else:
            _delete_rows(table, column, mode, value)
            log(f"  {table}: deleted {count} row(s)")


def _get_ids(table: str, column: str, value: str) -> list[str]:
    resp = supabase.table(table).select("id").eq(column, value).execute()
    return [row["id"] for row in (resp.data or [])]


def _delete_rows(table: str, column: str, mode: str, value) -> None:
    if mode == "direct":
        supabase.table(table).delete().eq(column, value).execute()
    elif mode in ("via_subscribers", "via_pickups"):
        if value:
            supabase.table(table).delete().in_(column, value).execute()


# ---------------------------------------------------------------------------
# Step 6: Delete Supabase auth user
# ---------------------------------------------------------------------------

def delete_auth_user(tenant: dict, dry_run: bool) -> None:
    log_step(6, "Delete Supabase auth user")
    user_id = tenant.get("user_id")

    if not user_id:
        log("No user_id on tenant — skipping auth deletion")
        return

    if dry_run:
        log(f"[DRY RUN] Would delete auth user: {user_id}")
    else:
        try:
            supabase.auth.admin.delete_user(user_id)
            log(f"Deleted auth user: {user_id}")
        except Exception as e:
            log(f"Auth user deletion failed: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Delete a SellLocal tenant completely.")
    parser.add_argument("--email", required=True, help="Tenant owner email address")
    parser.add_argument("--dry-run", action="store_true", help="Preview what would be deleted without making changes")
    parser.add_argument("--force", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    print(f"Looking up tenant with email: {args.email}")
    tenant = lookup_tenant(args.email)

    if not tenant:
        print(f"No tenant found with owner_email = {args.email}")
        sys.exit(1)

    print_tenant_summary(tenant)

    if args.dry_run:
        print("\n*** DRY RUN MODE — no changes will be made ***")

    if not args.force and not args.dry_run:
        confirm = input("\nType 'DELETE' to confirm permanent deletion: ")
        if confirm != "DELETE":
            print("Aborted.")
            sys.exit(0)

    archive_tenant(tenant, args.dry_run)
    remove_vercel_domain(tenant, args.dry_run)
    delete_storage_files(tenant, args.dry_run)
    delete_database_records(tenant, args.dry_run)
    delete_auth_user(tenant, args.dry_run)

    if args.dry_run:
        print("\n[DRY RUN COMPLETE] No changes were made.")
    else:
        print(f"\nTenant '{tenant['name']}' ({args.email}) has been fully deleted.")


if __name__ == "__main__":
    main()
