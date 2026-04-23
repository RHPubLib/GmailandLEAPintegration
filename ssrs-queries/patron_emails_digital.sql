-- patron_emails_digital.sql
-- Returns one row per unique email address for digital/eContent-only cardholders.
-- When multiple patron records share an email, the most recently active PatronID wins.
--
-- PatronCodes (customize for your Polaris configuration):
--   15, 20, 27 = Digital/eContent card types at RHPL
--
-- Output columns: EmailAddress, PatronID
-- Used by: SSRS subscription → CSV email → patron_sync.py → Google Sheet "Digital Card" tab

WITH RankedPatrons AS (
    SELECT
        LOWER(LTRIM(RTRIM(PR.EmailAddress))) AS EmailAddress,
        P.PatronID,
        ROW_NUMBER() OVER (
            PARTITION BY LOWER(LTRIM(RTRIM(PR.EmailAddress)))
            ORDER BY P.LastActivityDate DESC, P.PatronID DESC
        ) AS rn
    FROM polaris.PatronRegistration AS PR WITH (NOLOCK)
    JOIN polaris.Patrons AS P WITH (NOLOCK) ON P.PatronID = PR.PatronID
    WHERE PR.EmailAddress IS NOT NULL
      AND PR.EmailAddress <> ''
      AND LOWER(RTRIM(LTRIM(PR.EmailAddress))) NOT LIKE '%@yourdomain.org'
      AND P.PatronCodeID IN (15, 20, 27)
      AND P.RecordStatusID = 1
)
SELECT EmailAddress, PatronID
FROM RankedPatrons
WHERE rn = 1
ORDER BY EmailAddress
