@echo off

setlocal

cd /d "%~dp0\.."

set PYTHONIOENCODING=utf-8

set PYTHONUTF8=1
rem The OpenAI/S2S bridge is a text brain endpoint, not the web GUI.
rem Keep lifecycle WS disabled here so it can coexist with the RAP GUI,
rem which owns LIFECYCLE_WS_PORT for browser status/events.
set LIFECYCLE_WS_ENABLED=false

".venv\Scripts\python.exe" -m remote_agent_protocol.openai_bridge
