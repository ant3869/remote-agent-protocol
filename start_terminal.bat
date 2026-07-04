@echo off
cd /d "%~dp0"
echo Starting Remote Agent Protocol -- terminal mode (no GUI)...
echo.
echo   Same voice pipeline as the desktop app: STT, Ollama, TTS, agent bridge.
echo   Speak after the assistant introduces itself. Press Ctrl+C to quit.
echo.
".venv\Scripts\python" -u -m remote_agent_protocol.terminal
pause
