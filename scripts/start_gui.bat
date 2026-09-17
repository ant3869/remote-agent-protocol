@echo off
cd /d "%~dp0.."
echo Starting Remote Agent Protocol -- desktop agent switchboard...
echo.
echo   Pick a persona, override the voice, mute the mic, watch the transcript.
echo   Audio stays 100%% local (mic + speakers) so the back-and-forth stays snappy.
echo.
echo Use Quit, close the last app tab, or close this launcher to stop.
echo.
".venv\Scripts\python" -u -m remote_agent_protocol
pause
