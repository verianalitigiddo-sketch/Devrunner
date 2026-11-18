@echo off
rem Devrunner kurulumu (Windows): sanal ortam, bağımlılıklar ve masaüstü kısayolu.
rem Tekrar çalıştırmak güvenlidir (güncellemeden sonra bağımlılıkları yeniler).
chcp 65001 >nul
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")

if not exist ".venv\Scripts\python.exe" (
    echo Sanal ortam oluşturuluyor...
    %PY% -m venv .venv || goto :hata
)

echo Bağımlılıklar kuruluyor...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :hata

echo Masaüstü kısayolu oluşturuluyor...
set "DR_ROOT=%CD%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$r=$env:DR_ROOT; $d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut($d + '\Devrunner.lnk'); $s.TargetPath=$r + '\.venv\Scripts\pythonw.exe'; $s.Arguments=[char]34 + $r + '\Devrunner.pyw' + [char]34; $s.WorkingDirectory=$r; $s.IconLocation=$r + '\assets\devrunner.ico'; $s.Save()" || goto :hata

echo.
echo Kurulum tamam. Masaüstündeki "Devrunner" kısayolu ile açın.
echo Hata ayıklamak için: .venv\Scripts\python.exe Devrunner.pyw --debug   (F12 geliştirici araçları)
pause
exit /b 0

:hata
echo.
echo Kurulum başarısız oldu. Yukarıdaki hata mesajına bakın (PyPI erişimi / proxy ayarı?).
pause
exit /b 1
