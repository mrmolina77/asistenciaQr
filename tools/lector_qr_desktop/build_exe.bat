@echo off
setlocal
py -m pip install -r requirements-build.txt || exit /b 1
py -m PyInstaller --clean --noconfirm --onefile --console --name EdukadoLector run.py || exit /b 1
echo Ejecutable creado en dist\EdukadoLector.exe
