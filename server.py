import asyncio, uuid, json, base64
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from zalo_login import login_qr_with_unique_imei, LoginQRCallbackEventType

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="static"), name="static")

sessions: dict[str, WebSocket] = {}

@app.get("/")
async def root():
    return FileResponse("static/index.html")

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(ws: WebSocket, session_id: str):
    await ws.accept()
    sessions[session_id] = ws

    async def callback(event):
        etype = event["type"]

        if etype == LoginQRCallbackEventType.QRCodeGenerated:
            qr_b64 = event["data"]["image"]
            await ws.send_json({"type": "qr", "image": qr_b64})

        elif etype == LoginQRCallbackEventType.QRCodeScanned:
            name = event["data"].get("display_name", "")
            await ws.send_json({"type": "scanned", "name": name})

        elif etype == LoginQRCallbackEventType.QRCodeExpired:
            await ws.send_json({"type": "expired"})

        elif etype == LoginQRCallbackEventType.QRCodeDeclined:
            await ws.send_json({"type": "declined"})

        elif etype == LoginQRCallbackEventType.GotLoginInfo:
            pass

    try:
        await ws.send_json({"type": "status", "msg": "Đang tạo mã QR..."})
        result = await login_qr_with_unique_imei(callback=callback)

        if result:
            cookies = result["cookies"]
            user    = result["user_info"]
            imei    = result["imei"]

            cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())

            await ws.send_json({
                "type":    "success",
                "name":    user.get("displayName", "N/A"),
                "phone":   user.get("phoneNumber", "N/A"),
                "user_id": user.get("userId", "N/A"),
                "imei":    imei,
                "cookies": cookies,
                "cookie_str": cookie_str,
            })
        else:
            await ws.send_json({"type": "error", "msg": "Đăng nhập thất bại hoặc bị từ chối."})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "msg": str(e)})
        except:
            pass
    finally:
        sessions.pop(session_id, None)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
