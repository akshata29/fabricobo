@echo off
REM Start frontend (defaults to .NET API at https://localhost:7180)
REM To use Python API instead: set API_TARGET=http://localhost:5180
cd client-app && npx vite