"""
Reusable email sender for SellLocal campaigns.

Reads a contacts.csv and template.html from a campaign folder, then sends
individual emails via Resend.

Usage:
  python send.py expired-store --dry-run
  python send.py expired-store
  python send.py new-docs --delay 1.0
"""

import argparse
import csv
import re
import sys
import time
from pathlib import Path

import resend
from dotenv import load_dotenv
import os

EMAILS_DIR = Path(__file__).resolve().parent
MISC_ROOT = EMAILS_DIR.parent
CAMPAIGNS_DIR = EMAILS_DIR / "campaigns"

load_dotenv(MISC_ROOT / ".env")

resend.api_key = os.getenv("RESEND_API_KEY")
FROM_EMAIL = os.getenv("FROM_EMAIL")
REPLY_TO = os.getenv("REPLY_TO")


def list_campaigns() -> list[str]:
    """List available campaign folder names."""
    if not CAMPAIGNS_DIR.exists():
        return []
    return sorted(d.name for d in CAMPAIGNS_DIR.iterdir() if d.is_dir() and (d / "template.html").exists())


def load_contacts(csv_path: Path) -> list[dict]:
    """Load contacts from a CSV file. Must have an 'email' column; extra columns
    are available as {{placeholder}} values in the template."""
    contacts = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["email"] = row["email"].strip()
            contacts.append(row)
    return contacts


def load_template(template_path: Path) -> tuple[str, str]:
    """Load an HTML template and extract the subject from the first line.

    Templates should start with: <!--subject: Your subject here -->
    Returns (subject, html_body).
    """
    content = template_path.read_text()
    match = re.match(r"^<!--subject:\s*(.+?)\s*-->\n?", content)
    if not match:
        print(f"Error: Template missing subject line.")
        print(f"  Add <!--subject: Your subject --> at the top of {template_path}")
        sys.exit(1)
    subject = match.group(1)
    html = content[match.end():]
    return subject, html


def send_campaign(campaign: str, delay: float = 0.5, dry_run: bool = False) -> None:
    """Send emails for a campaign."""
    if not resend.api_key:
        print("Error: RESEND_API_KEY is not set in .env")
        sys.exit(1)
    if not FROM_EMAIL:
        print("Error: FROM_EMAIL is not set in .env")
        sys.exit(1)

    campaign_dir = CAMPAIGNS_DIR / campaign

    if not campaign_dir.exists():
        print(f"Error: Campaign '{campaign}' not found at {campaign_dir}")
        available = list_campaigns()
        if available:
            print(f"  Available campaigns: {', '.join(available)}")
        sys.exit(1)

    template_path = campaign_dir / "template.html"
    contacts_path = campaign_dir / "contacts.csv"

    if not template_path.exists():
        print(f"Error: No template.html in {campaign_dir}")
        sys.exit(1)
    if not contacts_path.exists():
        fetch_script = campaign_dir / "fetch.py"
        if fetch_script.exists():
            print(f"Error: No contacts.csv in {campaign_dir}")
            print(f"  Run the fetch script first: python {fetch_script}")
        else:
            print(f"Error: No contacts.csv in {campaign_dir}")
            print(f"  Create a contacts.csv with an 'email' column.")
        sys.exit(1)

    contacts = load_contacts(contacts_path)
    subject, html_template = load_template(template_path)

    # Detect placeholders used in the template
    placeholders = set(re.findall(r"\{\{(\w+)\}\}", html_template))

    print(f"Campaign:  {campaign}")
    print(f"Subject:   {subject}")
    print(f"Contacts:  {len(contacts)}")
    if placeholders:
        print(f"Variables: {', '.join(sorted(placeholders))}")
    if dry_run:
        print("Mode:      DRY RUN (no emails will be sent)\n")
    else:
        print()

    sent = 0
    failed = 0

    for contact in contacts:
        email = contact["email"]

        # Replace {{placeholders}} with values from the CSV row
        html = html_template
        for key, value in contact.items():
            html = html.replace(f"{{{{{key}}}}}", value or "")

        if dry_run:
            print(f"  [DRY RUN] Would send to {email}")
            sent += 1
            continue

        try:
            params = {
                "from": FROM_EMAIL,
                "to": email,
                "subject": subject,
                "html": html,
                "headers": {
                    "List-Unsubscribe": f"<mailto:{REPLY_TO or FROM_EMAIL}?subject=Unsubscribe>",
                },
            }
            if REPLY_TO:
                params["reply_to"] = REPLY_TO
            r = resend.Emails.send(params)
            print(f"  Sent to {email} - id: {r['id']}")
            sent += 1
        except Exception as e:
            print(f"  FAILED for {email}: {e}")
            failed += 1

        time.sleep(delay)

    print(f"\nDone. Sent: {sent}, Failed: {failed}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Send a SellLocal email campaign",
        epilog="Available campaigns: " + ", ".join(list_campaigns()) if list_campaigns() else None,
    )
    parser.add_argument("campaign", help="Campaign folder name (e.g. expired-store)")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between sends (default: 0.5)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without sending")

    args = parser.parse_args()
    send_campaign(
        campaign=args.campaign,
        delay=args.delay,
        dry_run=args.dry_run,
    )
