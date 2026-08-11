@echo off
cd /d "%~dp0.."
echo Starting the Remote Agent Protocol voice stack...
echo.
echo   Brain + GUI here, speech-to-speech owns the microphone and speakers.
echo   Each part opens its own window; close this one to stop them all.
echo.
".venv\Scripts\python" -u -m remote_agent_protocol.voice_stack
pause
