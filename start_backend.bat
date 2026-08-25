@echo off
set "PATH=C:\Users\28891\AppData\Local\Programs\Python\Python314;C:\Windows\System32;%PATH%"
set "http_proxy=http://127.0.0.1:7890"
set "https_proxy=http://127.0.0.1:7890"
cd /d C:\Users\28891\Desktop\WORK\InsightFlow\backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8002
