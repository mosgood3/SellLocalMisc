import csv
import sys
import time
from pathlib import Path

import resend
from dotenv import load_dotenv
import os

MISC_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(MISC_ROOT / ".env")

resend.api_key = os.getenv("RESEND_API_KEY")
FROM_EMAIL = os.getenv("FROM_EMAIL")
REPLY_TO = os.getenv("REPLY_TO")


def load_contacts(csv_path: str) -> list[str]:
    """Load email addresses from a CSV file with an 'email' column."""
    emails = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            emails.append(row["email"].strip())
    return emails


def load_template(template_path: str) -> tuple[str, str]:
    """Load an HTML template file and extract the subject from the first line.

    Templates should start with: <!--subject: Your subject here -->
    Returns (subject, html_body).
    """
    import re

    content = Path(template_path).read_text()
    match = re.match(r"^<!--subject:\s*(.+?)\s*-->\n?", content)
    if not match:
        print(f"Error: Template missing subject line. Add <!--subject: Your subject --> at the top of {template_path}")
        sys.exit(1)
    subject = match.group(1)
    html = content[match.end():]
    return subject, html


def send_emails(
    contacts_csv: str = "contacts.csv",
    template: str = "templates/new-docs.html",
    delay: float = 0.5,
    dry_run: bool = False,
):
    """Send individual emails to each contact in the CSV."""
    if not resend.api_key:
        print("Error: RESEND_API_KEY is not set in .env")
        sys.exit(1)
    if not FROM_EMAIL:
        print("Error: FROM_EMAIL is not set in .env")
        sys.exit(1)

    contacts = load_contacts(contacts_csv)
    subject, html_template = load_template(template)

    print(f"Subject: {subject}")
    print(f"Sending to {len(contacts)} contacts...")
    if dry_run:
        print("(DRY RUN - no emails will actually be sent)\n")

    sent = 0
    failed = 0

    for email in contacts:
        if dry_run:
            print(f"  [DRY RUN] Would send to {email}")
            sent += 1
            continue

        try:
            params = {
                    "from": FROM_EMAIL,
                    "to": email,
                    "subject": subject,
                    "html": html_template,
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
    import argparse

    parser = argparse.ArgumentParser(description="Send emails via Resend")
    parser.add_argument("--contacts", default="contacts.csv", help="Path to contacts CSV")
    parser.add_argument("--template", default="templates/new-docs.html", help="Path to HTML template")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between sends (default: 0.5)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without sending")

    args = parser.parse_args()
    send_emails(
        contacts_csv=args.contacts,
        template=args.template,
        delay=args.delay,
        dry_run=args.dry_run,
    )
