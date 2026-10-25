@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
    echo Creando entorno virtual e instalando dependencias...
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install -r requirements.txt
)
start "" .venv\Scripts\pythonw.exe src\main.py
