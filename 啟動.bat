@echo off
chcp 65001 >nul
setlocal
rem 雙擊啟動 API Proxy + 本機示範頁面。
rem 這個檔案只負責「準備好 Python 環境」，真正的啟動邏輯在 tools\launcher.py。
rem 刻意把邏輯放在 Python 而不是 bat：bat 處理中文、錯誤分支都很脆弱。

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto :run

echo.
echo [首次啟動] 找不到 .venv，開始建立 Python 環境（只有第一次需要，約 1-3 分鐘）
echo.

rem 依序找 Python 3.11 / 3.12 / 3.10。語意層用的 torch 2.3.1 不支援 3.13 以上。
set "PYEXE="
for %%V in (3.11 3.12 3.10) do (
    if not defined PYEXE (
        py -%%V -c "import sys" >nul 2>&1 && set "PYEXE=py -%%V"
    )
)
if not defined PYEXE (
    python -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else 1)" >nul 2>&1 && set "PYEXE=python"
)
if not defined PYEXE (
    echo [錯誤] 找不到 Python 3.10 ~ 3.12。
    echo        請到 https://www.python.org/downloads/ 安裝 Python 3.11，
    echo        安裝時勾選 "Add python.exe to PATH"，然後再雙擊一次這個檔案。
    pause
    exit /b 1
)

echo 使用 %PYEXE% 建立虛擬環境 .venv ...
%PYEXE% -m venv .venv || goto :fail
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r proxy\requirements.txt || goto :fail
echo 環境建立完成。
echo.

:run
".venv\Scripts\python.exe" tools\launcher.py %*
echo.
pause
exit /b 0

:fail
echo.
echo [錯誤] 環境建立失敗，請把上面的訊息截圖給開發者。
echo        要重來的話，先刪掉 .venv 資料夾再雙擊一次。
pause
exit /b 1
