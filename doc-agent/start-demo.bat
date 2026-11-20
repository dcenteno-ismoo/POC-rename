@echo off
cd /d "%~dp0"
set "DOCAGENT_DEMO_MODE=1"
echo Project RICH local demo - token prompts disabled; source allowed roots still apply.
call start-local-ai.bat %*
