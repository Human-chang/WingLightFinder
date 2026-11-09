@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"
set "OUT=WingLightFinder_綠色免安裝版"

echo ========================================================
echo 正在打包 WingLightFinder 綠色免安裝版 (供無 Python 電腦直接解壓使用)
echo ========================================================

rem 使用專案內的乾淨打包環境 .venv，避免把全域 Python 的其他套件一起打包
if not exist "%PY%" (
  echo 第一次打包：建立打包專用環境 .venv ...
  python -m venv .venv || goto :fail
)
echo 確認打包環境套件...
"%PY%" -m pip install -q pyinstaller || goto :fail

echo [1/4] 執行單元測試...
"%PY%" -m unittest discover -s tests -q || goto :fail

echo [2/4] 編譯程式 (設定見 WingLightFinder.spec)...
if exist "build_tmp" rmdir /s /q "build_tmp"
if exist "_dist_tmp" rmdir /s /q "_dist_tmp"
"%PY%" -m PyInstaller --noconfirm --clean --log-level WARN --distpath "_dist_tmp" --workpath "build_tmp" WingLightFinder.spec || goto :fail

echo [3/4] 執行打包成品自我測試 (不會顯示視窗)...
start "" /wait "_dist_tmp\WingLightFinder\WingLightFinder.exe" --self-test "%ROOT%build_tmp\selftest_result.json"
if errorlevel 1 (
  echo 自我測試失敗！詳細內容請看 build_tmp\selftest_result.json
  goto :fail
)

echo [4/4] 組裝 %OUT% ...
if exist "%OUT%" rmdir /s /q "%OUT%"
rem 自我測試剛結束時，防毒軟體可能短暫鎖住剛執行過的檔案，移動失敗就稍等重試
set /a TRIES=0
:move_retry
move "_dist_tmp\WingLightFinder" "%OUT%" >nul 2>&1 && goto :moved
set /a TRIES+=1
if %TRIES% geq 15 (
  echo 無法移動 _dist_tmp\WingLightFinder（可能被防毒軟體暫時鎖定），請稍後再執行一次。
  goto :fail
)
ping -n 2 127.0.0.1 >nul
goto :move_retry
:moved
rmdir /s /q "_dist_tmp"
rmdir /s /q "build_tmp"

copy /y "使用說明.txt" "%OUT%\使用說明.txt" >nul
copy /y "LICENSE" "%OUT%\LICENSE.txt" >nul
mkdir "%OUT%\source_code\core" "%OUT%\source_code\ui" >nul 2>&1
copy /y "main.py" "%OUT%\source_code\" >nul
copy /y "WingLightFinder.spec" "%OUT%\source_code\" >nul
copy /y "README.md" "%OUT%\source_code\" >nul
copy /y "core\*.py" "%OUT%\source_code\core\" >nul
copy /y "ui\*.py" "%OUT%\source_code\ui\" >nul

echo ========================================================
echo 打包完成！%OUT% 資料夾壓縮成 .zip 就可以分享。
echo 舊版「專案檔案速查儀表板」的 explorer_config.json 放進這個資料夾，第一次開啟會自動匯入我的最愛。
echo ========================================================
if /i not "%~1"=="--no-pause" pause
exit /b 0

:fail
echo 打包失敗，請看上面的錯誤訊息。
if /i not "%~1"=="--no-pause" pause
exit /b 1
