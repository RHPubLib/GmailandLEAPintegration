// RHPL Patron Check — Gmail Add-on
// Displays patron status in the Gmail sidebar when staff open an email.
// The card is UI-only and never travels with replies or forwards.
//
// SETUP: Set your Google Sheet ID in Apps Script project settings:
//   Project Settings → Script Properties → Add property:
//   Key: SHEET_ID   Value: <your Google Sheet ID from the URL>

function getSheetId() {
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
  const msg = GmailApp.getMessageById(e.gmail.messageId);
  const from = msg.getFrom();
  const match = from.match(/<(.+?)>/) || from.match(/(\S+@\S+)/);
  const senderEmail = match ? match[1].toLowerCase() : null;

  if (!senderEmail) return buildUnknownCard();

  const result = lookupPatron(senderEmail);
  return result ? buildPatronCard(result) : buildUnknownCard();
}

function lookupPatron(email) {
  const sheetId = getSheetId();
  if (!sheetId) {
    throw new Error('SHEET_ID script property not set. See setup instructions.');
  }
  const ss = SpreadsheetApp.openById(sheetId);
  for (const type of ['Full', 'Digital Card', 'Restricted']) {
    const sheet = ss.getSheetByName(type);
    if (!sheet || sheet.getLastRow() === 0) continue;
    const data = sheet.getRange(1, 1, sheet.getLastRow(), 2).getValues();
    for (const row of data) {
      if (String(row[0]).toLowerCase().trim() === email) {
        return { type: type, leapUrl: String(row[1]).trim() || null };
      }
    }
  }
  return null;
}

function buildPatronCard(result) {
  const info = PATRON_TYPES[result.type];
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
    .setHeader(buildHeader())
    .addSection(section)
    .build();
}

function buildUnknownCard() {
  return CardService.newCardBuilder()
    .setHeader(buildHeader())
    .addSection(
      CardService.newCardSection()
        .addWidget(CardService.newTextParagraph().setText('Not a known patron.'))
    )
    .build();
}

function buildHeader() {
  // TODO: update title and icon URL to match your library
  return CardService.newCardHeader()
    .setTitle('Patron Check')
    .setImageUrl('https://yourdomain.org/favicon.ico')
    .setImageStyle(CardService.ImageStyle.CIRCLE);
}
