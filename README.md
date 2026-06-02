# 🔐 Zalo QR Login Web

Web app lấy cookie Zalo qua quét mã QR.

## Cài đặt local

```bash
pip install -r requirements.txt
python server.py
```
Mở http://localhost:8000

## Deploy lên Render / Railway

1. Push code lên GitHub
2. Tạo project mới trên [Render](https://render.com) hoặc [Railway](https://railway.app)
3. Connect repo → Deploy
4. Dùng link được cấp

## Cấu trúc

```
zalo-web/
├── server.py        # FastAPI backend + WebSocket
├── zalo_login.py    # Zalo QR login logic
├── static/
│   └── index.html   # Frontend UI
├── requirements.txt
└── Procfile
```

## Tính năng

- 📱 Hiển thị mã QR Zalo realtime
- ✅ Thông báo khi quét & xác nhận
- 🍪 Lấy cookie string đầy đủ
- { } Xuất cookie dạng JSON
- 📋 Copy 1 click
