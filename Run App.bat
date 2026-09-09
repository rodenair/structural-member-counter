@echo off
cd /d "%~dp0"
echo Starting Structural Member Counter...
echo (This window must stay open while you use the app. Close it to stop the app.)
echo.
".venv\Scripts\streamlit.exe" run app.py
echo.
echo The app stopped or failed to start. See any error above.
pause
