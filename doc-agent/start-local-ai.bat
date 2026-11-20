@echo off
cd /d "%~dp0"
set "DOCAGENT_AI_URL=http://127.0.0.1:11434/v1/chat/completions"
if not defined DOCAGENT_AI_MODEL set "DOCAGENT_AI_MODEL=qwen2.5:3b"
set "DOCAGENT_AI_KEY="
echo Local AI: %DOCAGENT_AI_MODEL% via Ollama
call start.bat %*
