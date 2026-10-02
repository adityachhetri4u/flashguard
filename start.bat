@echo off
echo Starting FlashGuard using Docker Compose...
docker compose up --build -d
echo.
echo ====================================================
echo FlashGuard is running!
echo Frontend UI: http://localhost:5173
echo Backend API Docs: http://localhost:8000/docs
echo ====================================================
echo.
pause
