"""Durable lead receipts and one notification attempt per request identity."""
import datetime
import aiosqlite

async def deliver_lead(path, lead_id, user_id, format_label, text, send):
    now=datetime.datetime.now(datetime.timezone.utc).isoformat()
    async with aiosqlite.connect(path) as db:
        await db.execute('CREATE TABLE IF NOT EXISTS application_receipts (lead_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, format_label TEXT NOT NULL, request_text TEXT NOT NULL, delivery_status TEXT NOT NULL, created_at TEXT NOT NULL, delivered_at TEXT, error_type TEXT)')
        await db.execute('BEGIN IMMEDIATE')
        row=await (await db.execute('SELECT delivery_status FROM application_receipts WHERE lead_id=?',(lead_id,))).fetchone()
        if row:
            await db.commit()
            return row[0]=='sent'
        await db.execute('INSERT INTO application_receipts VALUES (?,?,?,?,?,?,NULL,NULL)',(lead_id,user_id,format_label,text,'sending',now))
        await db.commit()
    try:
        await send()
    except Exception as error:
        # A timeout may have delivered externally; never blindly send it again.
        async with aiosqlite.connect(path) as db:
            await db.execute('UPDATE application_receipts SET delivery_status=?,error_type=? WHERE lead_id=?',('unconfirmed',type(error).__name__,lead_id))
            await db.commit()
        return False
    async with aiosqlite.connect(path) as db:
        await db.execute('UPDATE application_receipts SET delivery_status=?,delivered_at=? WHERE lead_id=?',('sent',now,lead_id))
        await db.commit()
    return True
