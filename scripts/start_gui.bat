@echo off
cd /d "%~dp0"
echo Starting Remote Agent Protocol -- desktop agent switchboard...
echo.
echo   Pick a persona, override the voice, mute the mic, watch the transcript.
echo   Audio stays 100%% local (mic + speakers) so the back-and-forth stays snappy.
echo.
echo Close the window to quit.
echo.
".venv\Scripts\python" -u -m remote_agent_protocol
pause
