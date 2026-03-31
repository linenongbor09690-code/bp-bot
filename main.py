from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import httpx
import base64
import json
import sqlite3
import os
from datetime import datetime
from anthropic import Anthropic

app = FastAPI()
anthropic_client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

# ──────────────────────────────────────────
# DATABASE
# ──────────────────────────────────────────
def init_db():
    conn = sqlite3.connect("bp_records.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            systolic INTEGER,
            diastolic INTEGER,
            pulse INTEGER,
            raw_text TEXT,
            advice TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

def save_record(user_id, systolic, diastolic, pulse, raw_text, advice):
    conn = sqlite3.connect("bp_records.db")
    c = conn.cursor()
    c.execute("""
        INSERT INTO records (user_id, systolic, diastolic, pulse, raw_text, advice, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (user_id, systolic, diastolic, pulse, raw_text, advice, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_history(user_id, limit=5):
    conn = sqlite3.connect("bp_records.db")
    c = conn.cursor()
    c.execute("""
        SELECT systolic, diastolic, pulse, created_at 
        FROM records WHERE user_id=? ORDER BY created_at DESC LIMIT ?
    """, (user_id, limit))
    rows = c.fetchall()
    conn.close()
    return rows

# ──────────────────────────────────────────
# CLAUDE VISION — อ่านค่าความดันจากรูป
# ──────────────────────────────────────────
def analyze_bp_image(image_data: bytes, media_type: str) -> dict:
    image_b64 = base64.standard_b64encode(image_data).decode("utf-8")

    response = anthropic_client.messages.create(
        model="claude-opus-4-5",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_b64
                    }
                },
                {
                    "type": "text",
                    "text": """วิเคราะห์รูปเครื่องวัดความดันนี้ แล้วตอบกลับเป็น JSON เท่านั้น (ไม่มีข้อความอื่น):
{
  "systolic": <ค่าบน เช่น 120>,
  "diastolic": <ค่าล่าง เช่น 80>,
  "pulse": <ชีพจร หรือ null ถ้าไม่มี>,
  "readable": <true/false ว่าอ่านค่าได้ชัดเจน>,
  "note": "<หมายเหตุถ้ามี>"
}
ถ้าอ่านค่าไม่ได้ให้ใส่ readable: false และ systolic/diastolic เป็น null"""
                }
            ]
        }]
    )

    text = response.content[0].text.strip()
    # ล้าง markdown ถ้ามี
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)

# ──────────────────────────────────────────
# สร้างคำแนะนำสุขภาพ
# ──────────────────────────────────────────
def get_bp_advice(systolic: int, diastolic: int, pulse=None, history=None) -> str:
    history_text = ""
    if history:
        history_text = "\n\nประวัติการวัดล่าสุด:\n"
        for row in history:
            history_text += f"- {row[0]}/{row[1]} mmHg, ชีพจร {row[2] or '-'} bpm ({row[3][:10]})\n"

    prompt = f"""ผู้ใช้วัดความดันได้:
- ความดันตัวบน (Systolic): {systolic} mmHg
- ความดันตัวล่าง (Diastolic): {diastolic} mmHg
- ชีพจร: {pulse or 'ไม่ทราบ'} bpm
{history_text}

กรุณา:
1. บอกระดับความดัน (ปกติ/เฝ้าระวัง/สูง/สูงมาก)
2. ให้คำแนะนำสั้นๆ ที่เป็นประโยชน์ (ไม่เกิน 3 ข้อ)
3. บอกว่าควรพบแพทย์หรือไม่

ตอบภาษาไทย กระชับ เป็นมิตร ไม่เกิน 150 คำ"""

    response = anthropic_client.messages.create(
        model="claude-opus-4-5",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text.strip()

# ──────────────────────────────────────────
# LINE API HELPERS
# ──────────────────────────────────────────
async def reply_message(reply_token: str, messages: list):
    async with httpx.AsyncClient() as client:
        await client.post(
            "https://api.line.me/v2/bot/message/reply",
            headers={
                "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
                "Content-Type": "application/json"
            },
            json={"replyToken": reply_token, "messages": messages}
        )

async def get_line_image(message_id: str) -> tuple[bytes, str]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api-data.line.me/v2/bot/message/{message_id}/content",
            headers={"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
        )
        content_type = resp.headers.get("content-type", "image/jpeg")
        return resp.content, content_type

# ──────────────────────────────────────────
# WEBHOOK ENDPOINT
# ──────────────────────────────────────────
@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    events = body.get("events", [])

    for event in events:
        reply_token = event.get("replyToken")
        user_id = event.get("source", {}).get("userId", "unknown")
        event_type = event.get("type")
        msg = event.get("message", {})

        # รูปภาพ
        if event_type == "message" and msg.get("type") == "image":
            image_data, media_type = await get_line_image(msg["id"])

            try:
                result = analyze_bp_image(image_data, media_type)
            except Exception as e:
                await reply_message(reply_token, [{
                    "type": "text",
                    "text": "❌ ไม่สามารถวิเคราะห์รูปได้ กรุณาถ่ายรูปเครื่องวัดให้ชัดเจนและลองใหม่อีกครั้งนะครับ"
                }])
                continue

            if not result.get("readable") or result.get("systolic") is None:
                await reply_message(reply_token, [{
                    "type": "text",
                    "text": "🔍 อ่านค่าจากรูปไม่ได้ชัดเจน กรุณาถ่ายรูปให้เห็นหน้าจอเครื่องวัดชัดๆ แล้วส่งใหม่นะครับ"
                }])
                continue

            systolic = result["systolic"]
            diastolic = result["diastolic"]
            pulse = result.get("pulse")

            history = get_history(user_id, limit=4)
            advice = get_bp_advice(systolic, diastolic, pulse, history)
            save_record(user_id, systolic, diastolic, pulse, json.dumps(result), advice)

            pulse_text = f"\n💓 ชีพจร: {pulse} bpm" if pulse else ""
            reply_text = (
                f"📊 ผลการวัดความดัน\n"
                f"━━━━━━━━━━━━\n"
                f"🔴 ความดันตัวบน: {systolic} mmHg\n"
                f"🔵 ความดันตัวล่าง: {diastolic} mmHg"
                f"{pulse_text}\n"
                f"━━━━━━━━━━━━\n\n"
                f"{advice}"
            )

            await reply_message(reply_token, [{"type": "text", "text": reply_text}])

        # ข้อความ "ประวัติ"
        elif event_type == "message" and msg.get("type") == "text":
            text = msg.get("text", "").strip().lower()

            if "ประวัติ" in text or "history" in text:
                rows = get_history(user_id, limit=5)
                if not rows:
                    reply_text = "ยังไม่มีประวัติการวัดความดันครับ ลองส่งรูปผลวัดความดันมาได้เลย 📸"
                else:
                    reply_text = "📋 ประวัติการวัดความดัน 5 ครั้งล่าสุด\n━━━━━━━━━━━━\n"
                    for i, row in enumerate(rows, 1):
                        reply_text += f"{i}. {row[0]}/{row[1]} mmHg"
                        if row[2]:
                            reply_text += f" 💓{row[2]}"
                        reply_text += f"\n   📅 {row[3][:16]}\n"
                await reply_message(reply_token, [{"type": "text", "text": reply_text}])

            else:
                await reply_message(reply_token, [{
                    "type": "text",
                    "text": "👋 สวัสดีครับ! ส่งรูปผลวัดความดันมาได้เลย จะวิเคราะห์และให้คำแนะนำให้ครับ 📸\n\nพิมพ์ 'ประวัติ' เพื่อดูผลวัดย้อนหลัง"
                }])

    return JSONResponse(content={"status": "ok"})

@app.get("/")
def root():
    return {"status": "BP Bot is running 🩺"}
```

