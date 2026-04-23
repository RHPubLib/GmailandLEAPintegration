// RHPL Patron Check — Gmail Add-on
// Displays patron status in the Gmail sidebar when staff open an email.
// The card is UI-only and never travels with replies or forwards.
//
// SETUP: Set the following in Apps Script Project Settings → Script Properties:
//   SHEET_ID               — Google Sheet ID from the URL
//   SERVICE_ACCOUNT_EMAIL  — service account email (from GCP IAM)
//   SERVICE_ACCOUNT_KEY    — private_key value from the service account JSON file

function getSheetId_() {
  return PropertiesService.getScriptProperties().getProperty('SHEET_ID');
}

// Patron type display config — customize labels/descriptions to match your library's terminology
const PATRON_TYPES = {
  'Full':         { label: 'VERIFIED PATRON',     status: 'Full Cardholder', access: 'Full library services' },
  'Digital Card': { label: 'DIGITAL CARD HOLDER', status: 'Digital Card',    access: 'eContent only'         },
  'Restricted':   { label: 'RESTRICTED CARD',     status: 'Restricted Card', access: 'Limited access'        },
};

function onGmailMessage(e) {
  GmailApp.setCurrentMessageAccessToken(e.gmail.accessToken);
  const msg   = GmailApp.getMessageById(e.gmail.messageId);
  const from  = msg.getFrom();
  const match = from.match(/<(.+?)>/) || from.match(/(\S+@\S+)/);
  const senderEmail = match ? match[1].toLowerCase() : null;

  if (!senderEmail) return buildUnknownCard_();

  const result = lookupPatron_(senderEmail);
  return result ? buildPatronCard_(result) : buildUnknownCard_();
}

// ---------------------------------------------------------------------------
// Service account token
// Cached for 55 minutes so each email open doesn't re-generate a JWT.
// ---------------------------------------------------------------------------
function getServiceAccountToken_() {
  const cache  = CacheService.getScriptCache();
  const cached = cache.get('sa_access_token');
  if (cached) return cached;

  const props   = PropertiesService.getScriptProperties();
  const saEmail = props.getProperty('SERVICE_ACCOUNT_EMAIL');
  const saKey   = (props.getProperty('SERVICE_ACCOUNT_KEY') || '').replace(/\\n/g, '\n');

  if (!saEmail || !saKey) {
    throw new Error('SERVICE_ACCOUNT_EMAIL or SERVICE_ACCOUNT_KEY not set in Script Properties.');
  }

  const now    = Math.floor(Date.now() / 1000);
  const header = Utilities.base64EncodeWebSafe(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).replace(/=+$/, '');
  const claim  = Utilities.base64EncodeWebSafe(JSON.stringify({
    iss:   saEmail,
    scope: 'https://www.googleapis.com/auth/spreadsheets.readonly',
    aud:   'https://oauth2.googleapis.com/token',
    exp:   now + 3600,
    iat:   now,
  })).replace(/=+$/, '');

  const toSign    = `${header}.${claim}`;
  const signature = Utilities.computeRsaSha256Signature(toSign, saKey);
  const jwt       = `${toSign}.${Utilities.base64EncodeWebSafe(signature).replace(/=+$/, '')}`;

  const resp = UrlFetchApp.fetch('https://oauth2.googleapis.com/token', {
    method:             'post',
    payload:            { grant_type: 'urn:ietf:params:oauth:grant-type:jwt-bearer', assertion: jwt },
    muteHttpExceptions: true,
  });

  const json = JSON.parse(resp.getContentText());
  if (!json.access_token) {
    throw new Error('Service account token exchange failed: ' + resp.getContentText());
  }

  cache.put('sa_access_token', json.access_token, 3300);
  return json.access_token;
}

// ---------------------------------------------------------------------------
// Patron lookup — calls Sheets REST API as service account
// Staff Google accounts require no Sheet access at all.
// ---------------------------------------------------------------------------
function lookupPatron_(email) {
  const sheetId = getSheetId_();
  if (!sheetId) throw new Error('SHEET_ID not set in Script Properties.');

  const token = getServiceAccountToken_();

  for (const type of ['Full', 'Digital Card', 'Restricted']) {
    const range = type.includes(' ') ? `'${type}'` : type;
    const url   = `https://sheets.googleapis.com/v4/spreadsheets/${sheetId}/values/${encodeURIComponent(range)}`;
    const resp  = UrlFetchApp.fetch(url, {
      headers:            { Authorization: `Bearer ${token}` },
      muteHttpExceptions: true,
    });

    if (resp.getResponseCode() !== 200) continue;

    const rows = JSON.parse(resp.getContentText()).values || [];
    for (const row of rows) {
      if (row[0] && row[0].toLowerCase().trim() === email) {
        return { type: type, leapUrl: (row[1] || '').trim() || null };
      }
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Card builders
// ---------------------------------------------------------------------------
function buildPatronCard_(result) {
  const info    = PATRON_TYPES[result.type];
  const section = CardService.newCardSection()
    .setHeader(info.label)
    .addWidget(CardService.newDecoratedText().setTopLabel('Status').setText(info.status))
    .addWidget(CardService.newDecoratedText().setTopLabel('Access').setText(info.access));

  if (result.leapUrl) {
    section.addWidget(
      CardService.newButtonSet().addButton(
        CardService.newTextButton()
          .setText('Open in LEAP')
          .setOpenLink(CardService.newOpenLink().setUrl(result.leapUrl))
      )
    );
  }

  return CardService.newCardBuilder()
    .setHeader(buildHeader_())
    .addSection(section)
    .build();
}

function buildUnknownCard_() {
  return CardService.newCardBuilder()
    .setHeader(buildHeader_())
    .addSection(
      CardService.newCardSection()
        .addWidget(CardService.newTextParagraph().setText('Not a known patron.'))
    )
    .build();
}

function buildHeader_() {
  // TODO: update title and icon URL to match your library
  return CardService.newCardHeader()
    .setTitle('Patron Check')
    .setImageUrl('https://yourdomain.org/favicon.ico')
    .setImageStyle(CardService.ImageStyle.CIRCLE);
}
