@echo off
title Solana Trading Bot
cd /d "%~dp0"

echo ============================================
echo    SOLANA TRADING BOT
echo ============================================
echo.

REM Verificar que existe .env
if not exist ".env" (
    echo [!] No existe el archivo .env
    echo     Copiando .env.example a .env...
    copy .env.example .env
    echo.
    echo [!] IMPORTANTE: Abre .env y configura tu PRIVATE_KEY
    echo     Luego vuelve a ejecutar este archivo.
    echo.
    pause
    exit /b
)

REM Crear entorno virtual si no existe
if not exist "venv\" (
    echo [*] Creando entorno virtual...
    python -m venv venv
)

echo [*] Activando entorno virtual...
call venv\Scripts\activate.bat

echo [*] Instalando dependencias...
pip install -q -r requirements.txt

echo.
echo [*] Iniciando servidor en http://localhost:8000
echo     Abre esa direccion en tu navegador.
echo     Presiona Ctrl+C para detener.
echo.

python -m uvicorn main:app --host 0.0.0.0 --port 8000

pause
