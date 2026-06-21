# Google Sheets analytics setup

## Sheets to create

Create a Google Sheet with these tabs:

- `events`
- `users`
- `daily_summary`
- `payments`
- `errors`

## Headers

### events

`created_at | event_name | user_id | telegram_username | telegram_name | stage | day | trainer_key | skill_id | pattern | bucket | button_text | result | metadata_json`

### users

`first_seen | last_seen | user_id | telegram_username | telegram_name | trainer_key | language | bucket | main_pattern | payment_status | current_day | is_test_user`

### daily_summary

`date | total_users | new_users | diagnosis_completed | first_action_sent | action_done | action_failed | downscale_triggered | day2_returned | day3_reached | offer_shown | payment_click_20 | payment_click_40 | payment_completed | crisis_clicked`

### payments

`created_at | user_id | telegram_username | payment_event | offer_type | amount | payment_status | metadata_json`

### errors

`created_at | event_name | user_id | telegram_username | telegram_name | stage | error_type | error_source | metadata_json`

## Apps Script webhook

Open the sheet, then go to **Extensions → Apps Script** and deploy a web app with this code:

```javascript
function doPost(e) {
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const payload = JSON.parse(e.postData.contents);
    const sheetName = payload.sheet || "events";
    const sheet = ss.getSheetByName(sheetName);

    if (!sheet) {
      throw new Error("Sheet not found: " + sheetName);
    }

    const rows = payload.rows || [];
    if (!rows.length) {
      return ContentService
        .createTextOutput(JSON.stringify({ ok: true, inserted: 0 }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    sheet.getRange(sheet.getLastRow() + 1, 1, rows.length, rows[0].length)
      .setValues(rows);

    return ContentService
      .createTextOutput(JSON.stringify({ ok: true, inserted: rows.length }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ ok: false, error: String(err) }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}
```

Deploy settings:

- Execute as: `Me`
- Who has access: `Anyone with the link`

## Railway env

```env
SHEETS_WEBHOOK_URL=https://script.google.com/macros/s/XXXXX/exec
SHEETS_SYNC_ENABLED=true
SHEETS_SYNC_INTERVAL_SECONDS=60
SHEETS_SYNC_BATCH_SIZE=50
ADMIN_IDS=123456789,987654321
```

Do not commit the real webhook URL. Store it only in Railway/env.

## Troubleshooting

- `TelegramConflictError: Conflict: terminated by other getUpdates request` means the same `BOT_TOKEN` is already being polled by another running bot process. Stop the duplicate local/Railway/container instance and leave only one active deployment.
- `Sheet not found: daily_summary` means the optional analytics tab is missing. Core `events` sync can still work; create the `daily_summary` tab with the headers above if you want daily aggregate rows.
