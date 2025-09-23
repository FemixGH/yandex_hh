@echo off
chcp 65001 >nul
setlocal ENABLEDELAYEDEXPANSION

rem =============================================================
rem Monitor Yandex Cloud Serverless Containers (Windows)
rem - Checks health of all services
rem - Optionally opens real-time logs in separate windows
rem Requirements: yc CLI (yc init), curl, PowerShell
rem Usage: double-click or run in terminal; answer Y to open logs
rem =============================================================

where yc >nul 2>nul || (
  echo [ERROR] yc CLI not found. Install and run 'yc init'.
  exit /b 1
)
where curl >nul 2>nul || (
  echo [ERROR] curl not found. Install curl (comes with Git for Windows).
  exit /b 1
)

echo [INFO] Resolving service URLs from Serverless Containers...
for %%S in (lockbox logging validation yandex rag gateway telegram) do (
  for /f "usebackq tokens=* delims=" %%I in (`powershell -NoProfile -Command "(yc serverless container get --name %%S --format json ^| ConvertFrom-Json).url"`) do set %%S_URL=%%I
)

echo [INFO] URLs:
echo   LOCKBOX_URL   = %LOCKBOX_URL%
echo   LOGGING_URL   = %LOGGING_URL%
echo   VALIDATION_URL= %VALIDATION_URL%
echo   YANDEX_URL    = %YANDEX_URL%
echo   RAG_URL       = %RAG_URL%
echo   GATEWAY_URL   = %GATEWAY_URL%
echo   TELEGRAM_URL  = %TELEGRAM_URL%

echo.
echo [HEALTH] Checking /health endpoints (10s timeout):
for %%S in (gateway telegram rag validation yandex logging lockbox) do (
  set URL=!%%S_URL!
  if not "!URL!"=="" (
    echo   %%S: !URL!/health
    curl -s --max-time 10 "!URL!/health" >nul 2>&1
    if errorlevel 1 (
      echo     [FAIL]
    ) else (
      echo     [OK]
    )
  ) else (
    echo   %%S: [SKIP] URL not found
  )
)

echo.
echo [TEST] Round-trip via Gateway /bartender/ask:
if "%GATEWAY_URL%"=="" (
  echo   [SKIP] GATEWAY_URL is empty
) else (
  powershell -NoProfile -Command ^
    "$resp = Invoke-WebRequest -UseBasicParsing -TimeoutSec 15 -Uri '%GATEWAY_URL%/bartender/ask' -Method Post -ContentType 'application/json' -Body '{\"query\":\"рецепт Мохито\",\"user_id\":\"selftest\"}';" ^
    "; if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 300) {" ^
    "  $b = $resp.Content; if ($b -match 'answer' -or $b -match 'blocked') { Write-Host '  [OK] request processed' } else { Write-Host '  [WARN] unexpected payload'; Write-Output $b }" ^
    "} else { Write-Host '  [FAIL] status' $resp.StatusCode }"
)

echo.
set /p OPENLOGS=Open real-time logs in separate windows? (Y/N) :
if /I "%OPENLOGS%"=="Y" (
  for %%S in (gateway telegram rag validation yandex logging lockbox) do (
    echo [LOGS] Opening window: %%S
    start "logs %%S" cmd /k yc serverless container logs read --name %%S --follow --since 10m
  )
  echo [LOGS] Windows opened. Close them to stop tails.
) else (
  echo [LOGS] Skipped.
)

echo.
pause

