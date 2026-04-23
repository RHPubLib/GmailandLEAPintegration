#!/usr/bin/env bash
# setup_cron.sh
# Registers a cron job to run patron_sync.py nightly at 2:00 AM.
# SSRS delivers the reports between 11:00–11:10 PM — the 2 AM gap ensures all emails have arrived.
# Run once: bash setup_cron.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$(which python3)"
CRON_JOB="0 2 * * * $PYTHON $SCRIPT_DIR/patron_sync.py >> $SCRIPT_DIR/patron_sync.log 2>&1"
MARKER="patron_sync"

# Remove any existing entry for this script, then add the updated one
( crontab -l 2>/dev/null | grep -v "$MARKER"; echo "$CRON_JOB" ) | crontab -

echo "Cron job registered:"
echo "  $CRON_JOB"
echo ""
echo "To view:   crontab -l"
echo "To remove: crontab -l | grep -v '$MARKER' | crontab -"
