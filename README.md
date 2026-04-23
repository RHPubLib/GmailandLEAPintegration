# Polaris Patron Check for Gmail

A privacy-safe patron verification tool for public libraries using **Polaris ILS** and **Google Workspace**. When staff open an email from a known patron, a sidebar card appears in Gmail showing the patron's account type and a one-click deep-link to their Polaris LEAP record.

Built by [Rochester Hills Public Library](https://www.rhpl.org) · Rochester, MI · Shared freely for the library community

---

## Why We Built This

Staff regularly receive emails from patrons and need to quickly understand who they're speaking with — full cardholder, digital-only account, or restricted card — without manually looking them up in LEAP.

The key design constraint is **privacy**: any patron status indicator embedded in an email body travels with forwards and replies, potentially exposing patron information to unintended recipients.

This tool solves that with a **Gmail Add-on sidebar card** — it appears only in the reading staff member's Gmail view and is never part of the email body. It cannot be forwarded. Discussed at IUG and built with direct input from Google engineers at Google Cloud Next 2026.

---

## How It Works

```
Polaris SQL Server
        ↓  nightly (SSRS subscriptions, staggered)
SSRS Reports (3 patron groups)
  Each report emails a 2-column CSV to a dedicated Gmail inbox
  Columns: EmailAddress, PatronID
        ↓  nightly (systemd timer or cron, after SSRS completes)
patron_sync.py on Linux server
  • Authenticates to Gmail API via service account + domain-wide delegation
  • Extracts CSV attachments from SSRS report emails
  • Normalizes emails, builds LEAP deep-link URLs
  • Cross-list dedup: Full > Digital > Restricted
    (shared/family emails assigned to highest-priority group only)
  • Writes to Google Sheet (3 tabs)
  • Trashes processed report emails from inbox
        ↓
Google Sheet (3 tabs, written by service account)
  Full         → full cardholders + LEAP URLs
  Digital Card → digital/eContent accounts + LEAP URLs
  Restricted   → restricted/limited accounts + LEAP URLs
        ↓
Gmail Add-on (Google Workspace Marketplace, private)
  Admin force-installed for staff — one-time permission prompt on first use
  Authenticates to Sheet via service account JWT (staff accounts need no Sheet access)
  Reads Sheet on each email open, shows sidebar card
```

---

## Security Design

All authentication uses a single **GCP service account** — no user passwords, no app passwords, no OAuth tokens tied to individual staff accounts.

| Component | Auth method |
|-----------|-------------|
| `patron_sync.py` → Gmail | Service account + domain-wide delegation (impersonates inbox) |
| `patron_sync.py` → Google Sheet | Service account credentials from JSON key file |
| Gmail Add-on → Google Sheet | Service account JWT signed in Apps Script (via Script Properties) |

**Staff Google accounts are never granted Sheet access.** The Sheet is shared only with the service account. If a staff member's account is compromised, patron data in the Sheet is unaffected.

The Google Sheet itself should have download/print/copy disabled (Sheets → Share → restrict access) and service account access limited to Editor only.

---

## What Staff See

When a staff member opens an email from a known patron:

- **Patron type badge** — VERIFIED PATRON / DIGITAL CARD HOLDER / RESTRICTED CARD
- **Status and access level** — plain-language description
- **Open in LEAP button** — one click opens the patron's record in Polaris LEAP

If the sender is not in the patron database, the card shows "Not a known patron."

---

## Prerequisites

| Component | Notes |
|-----------|-------|
| Polaris ILS | SSRS (SQL Server Reporting Services) required |
| Google Workspace | Business/Education — Admin Console access needed |
| Linux server | For nightly sync script |
| Dedicated Gmail account | Receives SSRS report emails via subscription |
| GCP project | Linked to your Google Workspace domain |

---

## Setup

### 1. Polaris SSRS Reports

Create three reports in SSRS. SQL queries are in [`ssrs-queries/`](ssrs-queries/). **Before using them:**

- Replace `%@yourdomain.org` with your staff email domain
- Replace `PatronCodeID IN (...)` with your library's patron code IDs for each group

For each report, configure a **nightly subscription**:

| Setting | Value |
|---------|-------|
| Render format | CSV (comma delimited) |
| Delivery | Email to your dedicated Gmail address |
| Subject | Unique subject per report — must match `.env` exactly |
| Include report | Yes (attached as `.csv`) |
| Schedule | Stagger 5 min apart; complete before sync script runs |

> The report table layout must have **two columns**: `[EmailAddress]` and `[PatronID]`. A single-column report will sync emails but produce no LEAP URLs.

### 2. Google Sheet

Create a Google Sheet with three tabs named **exactly**:
- `Full`
- `Digital Card`
- `Restricted`

Note the Sheet ID from the URL: `https://docs.google.com/spreadsheets/d/**SHEET_ID**/edit`

### 3. GCP Service Account

This service account handles **both** Gmail access (for `patron_sync.py`) and Sheet access (for the Gmail Add-on). Staff accounts need no Sheet access at all.

1. [console.cloud.google.com](https://console.cloud.google.com) → **IAM & Admin → Service Accounts → Create**
2. Download a JSON key — store it on your server, path goes in `.env` as `GOOGLE_SERVICE_ACCOUNT_KEY`
3. Keep the key file out of version control (it's in `.gitignore`)
4. Share your Google Sheet with the service account email as **Editor**

**Domain-wide delegation** (required for `patron_sync.py` to access the Gmail inbox):

5. GCP → **IAM & Admin → Service Accounts** → click the service account → **Advanced settings → Domain-wide delegation → Enable**
6. Note the **Client ID** shown
7. [admin.google.com](https://admin.google.com) → **Security → API controls → Manage Domain Wide Delegation → Add new**
8. Client ID: paste from step 6
9. OAuth scope: `https://www.googleapis.com/auth/gmail.modify`

### 4. Sync Script

```bash
git clone https://github.com/RHPubLib/GmailandLEAPintegration.git
cd GmailandLEAPintegration
pip install -r requirements.txt
cp .env.template .env
chmod 600 .env
```

Fill in `.env`:

```env
GMAIL_ADDRESS=patron-sync@yourdomain.org
STAFF_EMAIL_DOMAIN=yourdomain.org

SSRS_SUBJECT_FULL=Full Patron Export
SSRS_SUBJECT_DIGITAL=Digital Patron Export
SSRS_SUBJECT_LIMITED=Limited Patron Export

LEAP_BASE_URL=https://catalog.yourdomain.org/leapwebapp/staff/default#patrons/

GOOGLE_SERVICE_ACCOUNT_KEY=/path/to/service-account-key.json
GOOGLE_SHEET_ID=<Sheet ID from the URL>
```

> Auth is handled entirely by the service account via domain-wide delegation — no Gmail app password or user OAuth token needed.

Test manually:
```bash
python3 patron_sync.py
```

Successful output looks like:
```
INFO Polaris → Google Sheet sync started
INFO Connected to Gmail API as patron-sync@yourdomain.org
INFO Subject "Full Patron Export": found 1 email(s) — using most recent
INFO   CSV format: 2 column(s) — LEAP URL present
INFO Sheet tab "Full": wrote 12,450 rows (12450 with LEAP URLs)
INFO Sync completed successfully
```

Schedule nightly. On systemd/immutable systems:
```bash
sudo cp systemd/patron-sync.service systemd/patron-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now patron-sync.timer
```

Or via cron:
```bash
bash setup_cron.sh
```

### 5. Gmail Add-on

1. [script.google.com](https://script.google.com) → **New project**
2. Replace `Code.gs` with [`gmail-addon/Code.gs`](gmail-addon/Code.gs)
3. Replace `appsscript.json` with [`gmail-addon/appsscript.json`](gmail-addon/appsscript.json)
4. Update `buildHeader_()` in `Code.gs` and `logoUrl` in `appsscript.json` with your library's name and icon URL
5. **Project Settings (gear icon) → Script Properties → Add three properties:**

| Property | Value |
|----------|-------|
| `SHEET_ID` | Your Google Sheet ID |
| `SERVICE_ACCOUNT_EMAIL` | Service account email (e.g. `patron-sync@project.iam.gserviceaccount.com`) |
| `SERVICE_ACCOUNT_KEY` | Private key from the JSON file — paste as a **single line** with literal `\n` sequences (not real newlines). Extract with: `python3 -c "import json; key=json.load(open('key.json'))['private_key']; print(key.replace('\n','\\n'),end='')"` |

6. **Deploy → New deployment → Add-on** → Deploy → copy the Deployment ID

### 6. Publish to Google Workspace Marketplace (Private)

1. GCP Console → **APIs & Services → Library** → enable **Google Workspace Marketplace SDK**
2. **Marketplace SDK → App Configuration:**
   - App Visibility: **Private** *(cannot be changed after saving)*
   - Installation Settings: **Admin Only Install**
   - App Integrations: **Google Workspace add-on → Apps Script** → paste Deployment ID
   - OAuth scopes:
     ```
     https://www.googleapis.com/auth/gmail.addons.execute
     https://www.googleapis.com/auth/gmail.readonly
     https://www.googleapis.com/auth/script.external_request
     ```
3. **Store Listing tab** — fill in name, description, category, icons, screenshot, support links → Save Draft → **Publish**

> When scopes change (e.g. after updating `appsscript.json`), create a new deployment version, update the Deployment ID in Marketplace SDK App Configuration, and save. Staff will see a one-time re-authorization prompt.

### 7. Force-Install via Admin Console

1. [admin.google.com](https://admin.google.com) → **Apps → Google Workspace Marketplace apps**
2. Select your staff OU in the left sidebar → **Install App**
3. Find your private app → **Admin install** → Continue
4. Select specific OUs → Select → **Finish**

Staff will have the add-on appear automatically in Gmail. On first use, each staff member will see a one-time Google OAuth consent prompt — this is expected and only happens once per user.

---

## Customizing Patron Groups

Edit the `LISTS` array in `patron_sync.py` and the `PATRON_TYPES` object in `Code.gs` to match your library's patron codes. You can use fewer or more groups — the Sheet tab names and SSRS subject lines just need to match across all three files.

The priority order (Full > Digital > Restricted) means a patron email that appears in multiple groups is assigned only to the highest-priority group in the Sheet.

---

## File Reference

| File | Purpose |
|------|---------|
| `patron_sync.py` | Nightly sync script |
| `requirements.txt` | Python dependencies |
| `.env.template` | Credential template (copy to `.env`, never commit `.env`) |
| `.gitignore` | Excludes secrets, key file, logs |
| `setup_cron.sh` | Register cron job (Linux) |
| `setup_task_scheduler.ps1` | Register scheduled task (Windows) |
| `systemd/` | systemd service + timer for immutable/bootc systems |
| `ssrs-queries/` | Parameterized SQL for the three SSRS reports |
| `gmail-addon/Code.gs` | Gmail Add-on source |
| `gmail-addon/appsscript.json` | Add-on manifest |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `No email found with subject "..."` | Subject mismatch or subscription hasn't run | Verify subject in `.env` matches SSRS subscription exactly |
| Email found but no CSV attachment | Wrong render format | Set SSRS render format to "CSV (comma delimited)" |
| `0 with LEAP URLs` in log | Report table missing PatronID column | Add `[PatronID]` column to Report Builder table layout |
| `Service account token exchange failed` | Bad key format in Script Properties | Re-extract key as single line: `python3 -c "import json; key=json.load(open('key.json'))['private_key']; print(key.replace('\n','\\n'),end='')"` |
| Add-on shows "Add-on error" | Missing Script Property or wrong key format | Check all three Script Properties are set; verify key has `\n` not spaces |
| `urlFetchWhitelist` error on deploy | Missing from `appsscript.json` | Ensure `urlFetchWhitelist` block is present (see `gmail-addon/appsscript.json`) |
| `Specified permissions are not sufficient` | Missing `script.external_request` scope | Add scope to `appsscript.json` and Marketplace SDK, redeploy |
| Add-on shows "Permission required" loop | Scopes changed — stale authorization | Click Allow; if it loops, update Deployment ID in Marketplace SDK App Configuration |
| Add-on doesn't appear for staff | Propagation delay | Allow up to 24 hours after admin force-install |
| Two add-on icons in sidebar | Both test and production deployments active | Remove test deployment from Apps Script → Manage deployments |

---

## Contributing

PRs welcome. If you adapt this for a different ILS, email platform, or patron code structure, we'd love to hear about it — open an issue to share your setup.

---

*Built at [Rochester Hills Public Library](https://www.rhpl.org), Rochester, MI.*
*Presented and discussed at IUG and Google Cloud Next 2026.*
