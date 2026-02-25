# SellLocal Email Campaigns

Reusable system for sending targeted email campaigns to SellLocal tenants via [Resend](https://resend.com).

## Structure

```
emails/
├── send.py                         # Shared sender — sends any campaign
├── campaigns/
│   ├── new-docs/                   # Tutorial videos announcement
│   │   ├── template.html           # Email template
│   │   └── contacts.csv            # Manually curated contact list
│   └── expired-store/              # Win-back for expired tenants
│       ├── template.html           # Email template
│       └── fetch.py                # Queries Supabase → generates contacts.csv
```

Each campaign lives in its own folder under `campaigns/` and contains:

| File | Required | Purpose |
|------|----------|---------|
| `template.html` | Yes | Email body. First line must be `<!--subject: Your subject -->` |
| `contacts.csv` | Yes | CSV with an `email` column. Created manually or by `fetch.py` |
| `fetch.py` | No | Script to query Supabase and generate `contacts.csv` |

## Usage

### 1. Prepare contacts

If the campaign has a `fetch.py`, run it to generate the contact list:

```bash
cd emails/campaigns/expired-store
python fetch.py              # writes contacts.csv
python fetch.py --dry-run    # preview without writing
```

If there's no `fetch.py`, create `contacts.csv` manually with an `email` column.

### 2. Send

```bash
cd emails

# Preview first
python send.py expired-store --dry-run

# Send for real
python send.py expired-store

# Custom delay between sends (default 0.5s)
python send.py expired-store --delay 1.0
```

### 3. List available campaigns

```bash
python send.py --help
```

The help text shows all available campaign names.

## Creating a New Campaign

1. Create a new folder under `campaigns/`:
   ```
   campaigns/my-campaign/
   ```

2. Add a `template.html` with the subject line embedded:
   ```html
   <!--subject: Your email subject here -->
   <html>
   ...
   </html>
   ```

   Templates support `{{placeholder}}` variables that get replaced per-contact
   using columns from the CSV. For example, `{{domain}}` gets replaced with
   each contact's `domain` column value.

3. Either:
   - Add a `contacts.csv` manually (must have an `email` column), or
   - Add a `fetch.py` that queries Supabase and writes `contacts.csv`

   Any extra columns beyond `email` are available as template variables.

4. If `fetch.py` generates the CSV, add the path to `.gitignore`:
   ```
   emails/campaigns/my-campaign/contacts.csv
   ```

5. Send it:
   ```bash
   python send.py my-campaign --dry-run
   ```

## Email Opt-In

The `sell_local_tenants` table has an `email_opt_in` column (defaults to `true`).
All `fetch.py` scripts must filter on `email_opt_in = true` so opted-out tenants never receive campaigns.

To opt a tenant out:

```sql
UPDATE sell_local_tenants SET email_opt_in = false WHERE owner_email = 'someone@example.com';
```

Migration: `SellLocal/supabase/migrations/022_email_opt_in.sql`

## Environment Variables

Set in `misc/.env` (see `.env.example`):

| Variable | Used by | Purpose |
|----------|---------|---------|
| `RESEND_API_KEY` | `send.py` | Resend API key |
| `FROM_EMAIL` | `send.py` | Sender address |
| `REPLY_TO` | `send.py` | Reply-to address (optional) |
| `SUPABASE_URL` | `fetch.py` | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | `fetch.py` | Service role key for querying tenants |
