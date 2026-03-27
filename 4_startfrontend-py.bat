@echo off
REM Start frontend pointed at the Python API (http://localhost:5180)
set API_TARGET=http://localhost:5180
cd client-app && npx vite
