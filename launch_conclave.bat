@echo off
setlocal
pushd "%~dp0"

set "CONCLAVE_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "CONCLAVE_PYTHON=.venv\Scripts\python.exe"

start "" msedge --app="http://localhost:8501"
"%CONCLAVE_PYTHON%" -m streamlit run conclave.py --server.headless true --server.address 127.0.0.1

set "CONCLAVE_EXIT=%ERRORLEVEL%"
popd
exit /b %CONCLAVE_EXIT%
