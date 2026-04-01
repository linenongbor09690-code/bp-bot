from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx, base64, json, os, re
from datetime import datetime
from anthropic import Anthropic

app = FastAPI()
client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
APPS_SCRIPT_URL = os.environ["APPS_SCRIPT_URL"]

def classify(sys, dia):
    if sys >= 180 or dia >= 110:
        return "red", "วิกฤต / Crisis"
    if sys >= 140 or dia >= 90:
        return "orange", "ป่วย / Stage 1-2 HTN"
    if sys >= 130 or dia >= 80:
        return "yellow", "เสี่ยง / Elevated"
    return "green", "ปกติ / Normal"

ADVICE = {
    "green":  "✅ ความดันอยู่ในเกณฑ์ดีเยี่ยม รักษาพฤติกรรมสุขภาพที่ดีต่อไปนะครับ 💪",
    "yellow": "⚠️ ความดันสูงกว่าปกติเล็กน้อย ควรลดเค็ม ออกกำลังกาย และติดตามสม่ำเสมอ",
    "orange": "🔶 ความดันสูง ควรพบแพทย์ งดสูบบุหรี่ ลดความเครียด และรับประทานยาตามที่แพทย์สั่ง",
    "red":    "🚨 อันตราย! ความดันสูงวิกฤต กรุณาพบแพทย์หรือโทร 1669 ทันที!",
}

ICONS = {
    "green": "🟢",
    "yellow": "🟡",
    "orange": "🟠",
    "red": "🔴",
}

COLORS = {
    "green":  {"bg": "#1a7a3c", "body": "#f0faf4"},
    "yellow": {"bg": "#8a6d00", "body": "#fffdf0"},
    "orange": {"bg": "#b54500", "body": "#fff8f3"},
    "red":    {"bg": "#a01010", "body": "#fff5f5"},
}

async def save_to_sheet(user_id, name, sys, dia, pulse, level):
    payload = {
        "user_id": user_id,
        "display_name": name,
        "sys": sys,
        "dia": dia,
        "pulse": pulse,
        "level": level,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")
    }
    async with httpx.AsyncClient() as c:
        await c.post(APPS_SCRIPT_URL, json=payload, follow_redirects=True)

async def get_history(user_id):
    async with httpx.AsyncClient() as c:
        r = await c.get(APPS_SCRIPT_URL, params={"user_id": user_id}, follow_redirects=True)
        return r.json()

def parse_text(text):
    nums = re.findall(r'\d+', text)
    if len(nums) >= 2:
        s, d = int(nums[0]), int(nums[1])
        p = int(nums[2]) if len(nums) >= 3 else None
        if 60 <= s <= 250 and 40 <= d <= 150:
            return s, d, p
    return None

def read_image(img, mime):
    b64 = base64.standard_b64encode(img).decode()
    r = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=200,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
            {"type": "text", "text": 'Read the blood pressure monitor. Reply JSON only:\n{"sys":120,"dia":80,"pulse":72,"ok":true}\nIf unreadable: {"ok":false}'}
        ]}]
    )
    text = r.content[0].text.strip().replace("```json", "").replace("```", "")
    return json.loads(text)

HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

async def get_image(mid):
    async with httpx.AsyncClient() as c:
        r = await c.get(
            f"https://api-data.line.me/v2/bot/message/{mid}/content",
            headers={"Authorization": f"Bearer {TOKEN}"}
        )
        return r.content, r.headers.get("content-type", "image/jpeg")

async def get_profile(uid):
    async with httpx.AsyncClient() as c:
        r = await c.get(f"https://api.line.me/v2/bot/profile/{uid}", headers=HEADERS)
        return r.json().get("displayName", "คุณ")

async def reply(token, messages):
    async with httpx.AsyncClient() as c:
        await c.post(
            "https://api.line.me/v2/bot/message/reply",
            headers=HEADERS,
            json={"replyToken": token, "messages": messages}
        )

def build_flex(name, sys, dia, pulse, level_key, level_label, advice, dt):
    c = COLORS[level_key]
    icon = ICONS[level_key]

    pulse_row = []
    if pulse is not None:
        pulse_row = [{
            "type": "box", "layout": "horizontal", "contents": [
                {"type": "text", "text": "ชีพจร (Pulse)", "size": "xs", "color": "#888888", "flex": 3},
                {"type": "text", "text": f"{pulse} bpm", "size": "sm", "weight": "bold", "color": "#1a1a1a", "align": "end", "flex": 2}
            ]
        }]

    return {
        "type": "flex",
        "altText": f"ผลความดัน: {sys}/{dia} mmHg",
        "contents": {
            "type": "bubble",
            "size": "kilo",
            "header": {
                "type": "box", "layout": "vertical",
                "backgroundColor": c["bg"],
                "paddingAll": "14px",
                "contents": [
                    {"type": "box", "layout": "horizontal", "contents": [
                        {"type": "text", "text": "ผลการวัดความดัน", "size": "xs", "color": "#ffffff99", "weight": "bold", "flex": 1},
                        {"type": "text", "text": icon, "size": "xl", "align": "end", "flex": 0}
                    ]},
                    {"type": "text", "text": level_label, "size": "lg", "weight": "bold", "color": "#ffffff", "margin": "sm"},
                    {"type": "text", "text": f"คุณ{name}", "size": "xs", "color": "#ffffff88"}
                ]
            },
            "body": {
                "type": "box", "layout": "vertical",
                "backgroundColor": c["body"],
                "paddingAll": "14px", "spacing": "sm",
                "contents": [
                    {"type": "box", "layout": "horizontal", "contents": [
                        {"type": "text", "text": "ความดันตัวบน (SYS)", "size": "xs", "color": "#888888", "flex": 3},
                        {"type": "text", "text": f"{sys} mmHg", "size": "sm", "weight": "bold", "color": "#1a1a1a", "align": "end", "flex": 2}
                    ]},
                    {"type": "box", "layout": "horizontal", "contents": [
                        {"type": "text", "text": "ความดันตัวล่าง (DIA)", "size": "xs", "color": "#888888", "flex": 3},
                        {"type": "text", "text": f"{dia} mmHg", "size": "sm", "weight": "bold", "color": "#1a1a1a", "align": "end", "flex": 2}
                    ]},
                    *pulse_row,
                    {"type": "separator", "margin": "md"},
                    {"type": "text", "text": advice, "size": "xs", "color": "#333333", "wrap": True, "margin": "md"},
                    {"type": "text", "text": f"📅 {dt}", "size": "xxs", "color": "#aaaaaa", "align": "end", "margin": "sm"}
                ]
            }
        }
    }

@app.post("/webhook")
async def webhook(req: Request):
    body = await req.json()
    for ev in body.get("events", []):
        rt = ev.get("replyToken")
        uid = ev.get("source", {}).get("userId", "")
        msg = ev.get("message", {})

        if ev["type"] == "message" and msg.get("type") == "image":
            img, mime = await get_image(msg["id"])
            try:
                res = read_image(img, mime)
            except Exception:
                await reply(rt, [{"type": "text", "text": "❌ ไม่สามารถวิเคราะห์รูปได้ ลองถ่ายให้เห็นหน้าจอชัดๆ แล้วส่งใหม่นะครับ"}])
                continue

            if not res.get("ok"):
                await reply(rt, [{"type": "text", "text": "🔍 อ่านค่าจากรูปไม่ได้ กรุณาถ่ายให้เห็นตัวเลขชัดเจนครับ"}])
                continue

            sys, dia, pulse = res["sys"], res["dia"], res.get("pulse")
            name = await get_profile(uid)
            level_key, level_label = classify(sys, dia)
            advice = ADVICE[level_key]
            dt = datetime.now().strftime("%d %b %Y · %H:%M")
            await save_to_sheet(uid, name, sys, dia, pulse, level_key)
            flex = build_flex(name, sys, dia, pulse, level_key, level_label, advice, dt)
            await reply(rt, [flex])

        elif ev["type"] == "message" and msg.get("type") == "text":
            text = msg.get("text", "").strip()
            parsed = parse_text(text)

            if parsed:
                sys, dia, pulse = parsed
                name = await get_profile(uid)
                level_key, level_label = classify(sys, dia)
                advice = ADVICE[level_key]
                dt = datetime.now().strftime("%d %b %Y · %H:%M")
                await save_to_sheet(uid, name, sys, dia, pulse, level_key)
                flex = build_flex(name, sys, dia, pulse, level_key, level_label, advice, dt)
                await reply(rt, [flex])

            elif "ประวัติ" in text:
                try:
                    rows = await get_history(uid)
                except Exception:
                    rows = []

                if not rows:
                    await reply(rt, [{"type": "text", "text": "ยังไม่มีประวัติครับ ลองส่งรูปหรือพิมพ์ค่าเช่น 120/80 ได้เลย 📸"}])
                else:
                    txt = "📋 ประวัติ 5 ครั้งล่าสุด\n" + "-" * 22 + "\n"
                    for r in rows:
                        icon = ICONS.get(str(r.get("level")), "⚪")
                        txt += f"{icon} {r.get('sys')}/{r.get('dia')} mmHg"
                        if r.get("pulse"):
                            txt += f" 💓{r.get('pulse')}"
                        txt += f"\n   {r.get('created_at')}\n"
                    await reply(rt, [{"type": "text", "text": txt}])

            else:
                await reply(rt, [{"type": "text", "text": "👋 สวัสดีครับ! ส่งรูปเครื่องวัด หรือพิมพ์ค่าเช่น\n\n📝 120/80\n📝 120/80/72 (มีชีพจร)\n\nพิมพ์ 'ประวัติ' เพื่อดูผลย้อนหลัง"}])

    return JSONResponse({"status": "ok"})

@app.get("/")
def root():
    return {"status": "BP Bot running"}
