# Google Sheets analytics setup

## Sheets to create

Create a Google Sheet with these tabs:

- `events`
- `users`
- `daily_summary`
- `payments`
- `errors`
- `behavioral_kpi`

## Headers

### events

`created_at | event_name | user_id | telegram_username | telegram_name | stage | day | trainer_key | skill_id | pattern | bucket | button_text | result | metadata_json`

### users

`first_seen | last_seen | anonymous_user_id | current_day | is_test_user`

Each user is appended once. Telegram ID, username, name, free text, diagnosis, and profile content are never exported.

### daily_summary

`date | total_users | new_users | diagnosis_completed | first_action_sent | action_done | action_failed | downscale_triggered | day2_returned | day3_reached | offer_shown | payment_click_20 | payment_click_40 | payment_completed | crisis_clicked`

### payments

`created_at | user_id | telegram_username | payment_event | offer_type | amount | payment_status | metadata_json`

### errors

`created_at | event_name | user_id | telegram_username | telegram_name | stage | error_type | error_source | metadata_json`

### behavioral_kpi

`created_at | event_name | anonymous_user_id | situation_id | experiment_id | skill_id | mechanism_code | context_domain | outcome_label | count_value | policy_version | ranking_version | skill_version`

This tab never receives Telegram identity, raw text, voice/crisis content, prompts, or personal stories.

## Apps Script webhook

Open the sheet, then go to **Extensions → Apps Script** and deploy a web app with this code:

```javascript
function doPost(e) {
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const payload = JSON.parse(e.postData.contents);
    const sheetName = payload.sheet || "events";
    const allowedHeaders = {
      users: ["first_seen", "last_seen", "anonymous_user_id", "current_day", "is_test_user"],
      behavioral_kpi: ["created_at", "event_name", "anonymous_user_id", "situation_id", "experiment_id", "skill_id", "mechanism_code", "context_domain", "outcome_label", "count_value", "policy_version", "ranking_version", "skill_version"]
    };
    if (!allowedHeaders[sheetName]) {
      throw new Error("Unsupported sheet: " + sheetName);
    }
    let sheet = ss.getSheetByName(sheetName);
    if (!sheet) {
      sheet = ss.insertSheet(sheetName);
      sheet.getRange(1, 1, 1, allowedHeaders[sheetName].length)
        .setValues([allowedHeaders[sheetName]]);
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
ANALYTICS_ID_SALT=replace-with-a-long-private-random-value
ADMIN_IDS=123456789,987654321
```

Do not commit the real webhook URL. Store it only in Railway/env.

## Troubleshooting

- `TelegramConflictError: Conflict: terminated by other getUpdates request` means the same `BOT_TOKEN` is already being polled by another running bot process. Stop the duplicate local/Railway/container instance and leave only one active deployment.
- If no users appear, verify all three Railway variables are set: `SHEETS_WEBHOOK_URL`, `SHEETS_SYNC_ENABLED=true`, and a non-empty private `ANALYTICS_ID_SALT`.
- Redeploy the Apps Script after replacing the old webhook code. The current exporter uses only `users` and `behavioral_kpi`; the webhook creates either tab with safe headers when missing.
