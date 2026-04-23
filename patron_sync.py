#!/usr/bin/env python3
"""
patron_sync.py
Nightly sync: Polaris patron emails → Google Sheet (3 tabs by patron type).

Flow:
  1. Connect to Gmail via IMAP (dedicated inbox that receives SSRS report emails)
  2. For each of 3 SSRS subscription emails (by subject line), extract the CSV attachment
  3. Parse and normalize email addresses (and LEAP URLs) from each CSV
  4. Apply cross-list priority dedup: FULL > DIGITAL > LIMITED
     (a patron with multiple accounts only appears in their highest-priority list)
  5. Write each list to its Google Sheet tab (email + LEAP URL)
  6. Delete processed report emails from inbox

Groups:
  Full    — Full Cardholder   (PatronCodes 1,3,6,8,14,24,25,26,29)
  Digital — Digital Card      (PatronCodes 15,20,27)
  Limited — Restricted Card   (PatronCodes 2,4,9,17)
  Excluded (no card) — Staff  (PatronCodes 7,19,22)

SSRS reports run on a nightly subscription and email CSVs to the dedicated Gmail inbox.
Each report has a unique subject line so this script can identify which list it feeds.

Runs via systemd timer (or cron) on a Linux server.
Credentials loaded from .env in the same directory.
"""

import csv
import email
import imaplib
import io
import logging
import logging.handlers
import os
import sys

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GMAIL_ADDRESS      = os.environ['GMAIL_ADDRESS']
GMAIL_APP_PASSWORD = os.environ['GMAIL_APP_PASSWORD']
STAFF_EMAIL_DOMAIN = os.environ.get('STAFF_EMAIL_DOMAIN', 'rhpl.org')

SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
LOG_FILE      = os.path.join(SCRIPT_DIR, 'patron_sync.log')
LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB

GOOGLE_SERVICE_ACCOUNT_KEY = os.environ.get('GOOGLE_SERVICE_ACCOUNT_KEY', '')
GOOGLE_SHEET_ID            = os.environ.get('GOOGLE_SHEET_ID', '')

# One entry per patron group — order defines priority (index 0 = highest)
LISTS = [
    {
        'name':      'patron_emails_full',
        'subject':   os.environ.get('SSRS_SUBJECT_FULL',    'Full Patron Export'),
        'sheet_tab': 'Full',
    },
    {
        'name':      'patron_emails_digital',
        'subject':   os.environ.get('SSRS_SUBJECT_DIGITAL', 'Digital Patron Export'),
        'sheet_tab': 'Digital Card',
    },
    {
        'name':      'patron_emails_limited',
        'subject':   os.environ.get('SSRS_SUBJECT_LIMITED', 'Limited Patron Export'),
        'sheet_tab': 'Restricted',
    },
]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging() -> logging.Logger:
    logger = logging.getLogger('patron_sync')
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s')

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=3, encoding='utf-8'
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)

    return logger


# ---------------------------------------------------------------------------
# Gmail / IMAP helpers
# ---------------------------------------------------------------------------
def connect_gmail(logger: logging.Logger) -> imaplib.IMAP4_SSL:
    logger.info(f'Connecting to Gmail as {GMAIL_ADDRESS}...')
    mail = imaplib.IMAP4_SSL('imap.gmail.com', 993)
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    mail.select('INBOX')
    return mail


def fetch_csv_for_subject(mail: imaplib.IMAP4_SSL, subject: str,
                          logger: logging.Logger) -> tuple[str | None, list]:
    """
    Find emails matching `subject`, extract CSV from the most recent one.
    Returns (csv_text, all_msg_ids) — csv_text is None if no matching email found.
    """
    status, data = mail.search(None, f'SUBJECT "{subject}"')
    if status != 'OK' or not data[0]:
        logger.warning(f'No email found with subject "{subject}" — skipping this list')
        return None, []

    msg_ids = data[0].split()
    logger.info(f'Subject "{subject}": found {len(msg_ids)} email(s) — using most recent')

    latest_id = msg_ids[-1]
    status, msg_data = mail.fetch(latest_id, '(RFC822)')
    raw_email = msg_data[0][1]
    msg = email.message_from_bytes(raw_email)

    for part in msg.walk():
        content_type = part.get_content_type()
        filename     = part.get_filename() or ''
        if content_type == 'text/csv' or filename.lower().endswith('.csv'):
            payload  = part.get_payload(decode=True)
            csv_text = payload.decode('utf-8-sig')
            logger.info(f'  CSV attachment: {filename} ({len(payload)} bytes)')
            return csv_text, msg_ids

    logger.warning(f'Email found for "{subject}" but no CSV attachment — skipping')
    return None, msg_ids


def cleanup_gmail(mail: imaplib.IMAP4_SSL, all_msg_ids: list,
                  logger: logging.Logger) -> None:
    """Delete all collected report emails after a successful sync."""
    if not all_msg_ids:
        return
    try:
        for msg_id in all_msg_ids:
            mail.store(msg_id, '+FLAGS', '\\Deleted')
        mail.expunge()
        logger.info(f'Deleted {len(all_msg_ids)} processed report email(s) from inbox')
    except Exception as exc:
        logger.warning(f'Email cleanup failed (non-fatal): {exc}')
    finally:
        mail.logout()


# ---------------------------------------------------------------------------
# Patron data parsing
# ---------------------------------------------------------------------------
LEAP_BASE = 'https://catalog.rhpl.org/leapwebapp/staff/default#patrons/'


def parse_patron_data(csv_text: str, logger: logging.Logger) -> dict:
    """
    Parse a two-column CSV (email, patronid) into a dict of email → LEAP URL.
    Falls back gracefully to email-only if the second column is absent (legacy format).
    When multiple rows share the same email the SSRS query already picks the most
    recently active PatronID — this function just trusts whichever row arrives first.
    """
    reader  = csv.reader(io.StringIO(csv_text))
    result  = {}
    skipped = 0
    first_data_row_logged = False

    for row in reader:
        if not row:
            continue
        addr = row[0].strip()
        if '@' not in addr:   # skip header rows and blanks
            continue
        normalized = addr.lower()
        if normalized.endswith(f'@{STAFF_EMAIL_DOMAIN}'):
            skipped += 1
            continue

        leap_url = ''
        if len(row) > 1:
            patron_id = row[1].strip()
            try:
                leap_url = LEAP_BASE + str(int(float(patron_id)))
            except (ValueError, OverflowError):
                pass  # header row or empty/non-numeric PatronID

        if not first_data_row_logged:
            has_leap = bool(leap_url)
            logger.info(f'  CSV format: {len(row)} column(s) — LEAP URL {"present" if has_leap else "MISSING (PatronID column not found)"}')
            first_data_row_logged = True

        if normalized not in result:   # first row wins (SSRS already ranked by recency)
            result[normalized] = leap_url

    if skipped:
        logger.info(f'  Filtered {skipped} @{STAFF_EMAIL_DOMAIN} address(es)')
    return result   # email → leap_url


# ---------------------------------------------------------------------------
# Google Sheets helpers
# ---------------------------------------------------------------------------
def get_sheets_client() -> gspread.Client:
    creds = Credentials.from_service_account_file(
        GOOGLE_SERVICE_ACCOUNT_KEY,
        scopes=['https://www.googleapis.com/auth/spreadsheets'],
    )
    return gspread.authorize(creds)


def push_to_sheet(client: gspread.Client, tab_name: str,
                  patron_data: list, logger: logging.Logger) -> None:
    """
    Write patron list to a Sheet tab.
    patron_data is a list of (email, leap_url) tuples — LEAP URLs come from SSRS PatronID.
    """
    sh = client.open_by_key(GOOGLE_SHEET_ID)
    ws = sh.worksheet(tab_name)

    ws.clear()
    if patron_data:
        ws.update(patron_data, 'A1')

    url_count = sum(1 for _, u in patron_data if u)
    logger.info(f'Sheet tab "{tab_name}": wrote {len(patron_data):,} rows '
                f'({url_count} with LEAP URLs)')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    logger = setup_logging()
    logger.info('=' * 60)
    logger.info('Polaris → Google Sheet sync started')

    try:
        # Step 1 — Fetch CSVs from Gmail
        mail        = connect_gmail(logger)
        buckets     = {}   # list_name → set of emails
        all_msg_ids = []   # accumulate all report email IDs for cleanup

        for lst in LISTS:
            csv_text, msg_ids = fetch_csv_for_subject(mail, lst['subject'], logger)
            all_msg_ids.extend(msg_ids)
            if csv_text:
                buckets[lst['name']] = parse_patron_data(csv_text, logger)
                logger.info(f'  {lst["name"]}: {len(buckets[lst["name"]]):,} raw emails')
            else:
                buckets[lst['name']] = {}

        # Step 2 — Cross-list priority dedup: FULL > DIGITAL > LIMITED
        # A patron with multiple accounts shares one email across groups.
        # Each email must appear in exactly one list — the highest-priority match.
        full_map    = buckets['patron_emails_full']
        digital_map = buckets['patron_emails_digital']
        limited_map = buckets['patron_emails_limited']

        full_keys    = set(full_map)
        digital_keys = set(digital_map)
        limited_keys = set(limited_map)

        removed = len(digital_keys & full_keys) + len(limited_keys & (full_keys | digital_keys))
        if removed:
            logger.info(
                f'Cross-list dedup: removed {removed} email(s) from lower-priority '
                f'list(s) — highest-priority group wins'
            )

        final_keys = {
            'patron_emails_full':    sorted(full_keys),
            'patron_emails_digital': sorted(digital_keys - full_keys),
            'patron_emails_limited': sorted(limited_keys - full_keys - digital_keys),
        }

        final_tuples = {
            name: [(e, buckets[name].get(e, '')) for e in keys]
            for name, keys in final_keys.items()
        }

        # Step 3 — Push each list to Google Sheet (email + LEAP URL)
        if GOOGLE_SERVICE_ACCOUNT_KEY and GOOGLE_SHEET_ID:
            logger.info('Updating Google Sheet...')
            sheets = get_sheets_client()
            for lst in LISTS:
                push_to_sheet(sheets, lst['sheet_tab'], final_tuples[lst['name']], logger)
            logger.info('Google Sheet updated successfully')
        else:
            logger.warning('GOOGLE_SERVICE_ACCOUNT_KEY or GOOGLE_SHEET_ID not set — skipping Sheet update')

        # Step 4 — Delete processed emails from inbox
        cleanup_gmail(mail, all_msg_ids, logger)

        logger.info('Sync completed successfully')
        logger.info('=' * 60)

    except Exception as exc:
        logger.error(f'Sync FAILED: {exc}', exc_info=True)
        logger.info('=' * 60)
        sys.exit(1)


if __name__ == '__main__':
    main()
