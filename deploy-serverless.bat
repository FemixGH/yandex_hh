@echo off
chcp 65001 >nul
setlocal ENABLEDELAYEDEXPANSION

rem =============================
rem Yandex Cloud Serverless deploy for all services
rem Requires: yc CLI logged-in (yc init), docker, PowerShell
rem =============================

where yc >nul 2>nul || (
  echo [ERROR] yc CLI not found. Install and run 'yc init'.
  exit /b 1
)
where docker >nul 2>nul || (
  echo [ERROR] docker not found. Install Docker and log in.
  exit /b 1
)

for /f %%i in ('yc config get folder-id') do set FOLDER_ID=%%i
for /f %%i in ('yc config get cloud-id') do set CLOUD_ID=%%i
if "%FOLDER_ID%"=="" (
  echo [ERROR] folder-id is not set. Run 'yc init' or 'yc config set folder-id <id>'.
  exit /b 1
)

rem SECRET_ID must be provided (matches Lockbox secret with keys like .env)
if "%SECRET_ID%"=="" set SECRET_ID=e6q8vbbldqor67ogn9ne
if "%SECRET_ID%"=="" (
  echo [ERROR] SECRET_ID is not set.
  exit /b 1
)

echo [INFO] Folder: %FOLDER_ID%  Cloud: %CLOUD_ID%  Secret: %SECRET_ID%

rem Optional: webhook secret token for Telegram (if set, will be passed to container)
set WEBHOOK_SECRET_TOKEN=%WEBHOOK_SECRET_TOKEN%

rem -------- Resolve or create Service Account --------
set SA_NAME=sc-containers
for /f "usebackq tokens=* delims=" %%i in (`powershell -NoProfile -Command "try{ (yc iam service-account get --name %SA_NAME% --format json ^| ConvertFrom-Json).id }catch{''}"`) do set SA_ID=%%i
if "%SA_ID%"=="" (
  echo [INFO] Creating service account %SA_NAME%...
  for /f "usebackq tokens=* delims=" %%i in (`powershell -NoProfile -Command "(yc iam service-account create --name %SA_NAME% --format json ^| ConvertFrom-Json).id"`) do set SA_ID=%%i
) else (
  echo [INFO] Using existing SA %SA_NAME%: %SA_ID%
)
if "%SA_ID%"=="" (
  echo [ERROR] Failed to resolve service account id.
  exit /b 1
)

rem -------- Container Registry setup --------
yc container registry configure-docker >nul 2>nul
for /f "usebackq tokens=* delims=" %%i in (`powershell -NoProfile -Command "(yc container registry list --format json ^| ConvertFrom-Json ^| Select-Object -First 1).id"`) do set REGISTRY_ID=%%i
if "%REGISTRY_ID%"=="" (
  echo [INFO] Creating container registry yandex-hh-reg...
  yc container registry create --name yandex-hh-reg >nul || (
    echo [ERROR] Failed to create container registry.
    exit /b 1
  )
  for /f "usebackq tokens=* delims=" %%i in (`powershell -NoProfile -Command "(yc container registry list --format json ^| ConvertFrom-Json ^| Select-Object -First 1).id"`) do set REGISTRY_ID=%%i
)
set REGISTRY=cr.yandex/%REGISTRY_ID%
echo [INFO] Registry: %REGISTRY%

rem Grant SA pull rights on registry (idempotent)
yc container registry add-access-binding --id %REGISTRY_ID% --role container-registry.images.puller --subject serviceAccount:%SA_ID% >nul 2>nul

rem Grant LLM API usage on folder (idempotent)
yc resource-manager folder add-access-binding --id %FOLDER_ID% --role ai.languageModels.user --subject serviceAccount:%SA_ID% >nul 2>nul

rem Grant Lockbox payloadViewer on the secret (idempotent)
yc lockbox secret add-access-binding --id %SECRET_ID% --role lockbox.payloadViewer --subject serviceAccount:%SA_ID% >nul 2>nul

rem -------- Optional non-secret envs --------
if "%S3_BUCKET%"=="" set S3_BUCKET=vedroo
if "%S3_PREFIX%"=="" set S3_PREFIX=
set S3_ENDPOINT=https://storage.yandexcloud.net

echo [INFO] Using S3: bucket=%S3_BUCKET% prefix=%S3_PREFIX%

rem -------- Build and push images --------
echo [INFO] Building images...
docker build -t %REGISTRY%/lockbox:latest   -f services/lockbox/Dockerfile . || goto :docker_fail
docker build -t %REGISTRY%/logging:latest   -f services/logging/Dockerfile . || goto :docker_fail
docker build -t %REGISTRY%/validation:latest -f services/validation/Dockerfile . || goto :docker_fail
docker build -t %REGISTRY%/yandex:latest    -f services/yandex/Dockerfile . || goto :docker_fail
docker build -t %REGISTRY%/rag:latest       -f services/rag/Dockerfile . || goto :docker_fail
docker build -t %REGISTRY%/gateway:latest   -f services/gateway/Dockerfile . || goto :docker_fail
docker build -t %REGISTRY%/telegram:latest  -f services/telegram/Dockerfile . || goto :docker_fail

echo [INFO] Pushing images...
docker push %REGISTRY%/lockbox:latest   || goto :docker_fail
docker push %REGISTRY%/logging:latest   || goto :docker_fail
docker push %REGISTRY%/validation:latest || goto :docker_fail
docker push %REGISTRY%/yandex:latest    || goto :docker_fail
docker push %REGISTRY%/rag:latest       || goto :docker_fail
docker push %REGISTRY%/gateway:latest   || goto :docker_fail
docker push %REGISTRY%/telegram:latest  || goto :docker_fail

goto :deploy

:docker_fail
echo [ERROR] Docker build/push failed.
exit /b 1

:deploy
rem -------- Create containers (idempotent) --------
for %%S in (lockbox logging validation yandex rag gateway telegram) do (
  yc serverless container get --name %%S >nul 2>nul || yc serverless container create --name %%S >nul
)

rem -------- Deploy backends --------

echo [INFO] Deploying lockbox...
yc serverless container revision deploy --container-name lockbox --image %REGISTRY%/lockbox:latest --service-account-id %SA_ID% --cores 1 --memory 256MB --concurrency 16 --execution-timeout 10s --environment LOCKBOX_SERVICE_HOST=0.0.0.0,LOCKBOX_SERVICE_PORT=8080,SECRET_ID=%SECRET_ID% || goto :deploy_fail
rem do NOT open public access to lockbox
rem yc serverless container allow-unauthenticated-invoke --name lockbox >nul 2>nul


echo [INFO] Deploying logging...
yc serverless container revision deploy --container-name logging --image %REGISTRY%/logging:latest --service-account-id %SA_ID% --cores 1 --memory 256MB --concurrency 16 --execution-timeout 10s --environment LOGGING_SERVICE_HOST=0.0.0.0,LOGGING_SERVICE_PORT=8080,SECRET_ID=%SECRET_ID% || goto :deploy_fail
yc serverless container allow-unauthenticated-invoke --name logging >nul 2>nul


echo [INFO] Deploying validation...
yc serverless container revision deploy --container-name validation --image %REGISTRY%/validation:latest --service-account-id %SA_ID% --cores 1 --memory 256MB --concurrency 16 --execution-timeout 10s --environment VALIDATION_SERVICE_HOST=0.0.0.0,VALIDATION_SERVICE_PORT=8080,FOLDER_ID=%FOLDER_ID%,SECRET_ID=%SECRET_ID% || goto :deploy_fail
yc serverless container allow-unauthenticated-invoke --name validation >nul 2>nul


echo [INFO] Deploying yandex...
yc serverless container revision deploy --container-name yandex --image %REGISTRY%/yandex:latest --service-account-id %SA_ID% --cores 1 --memory 512MB --concurrency 16 --execution-timeout 15s --environment YANDEX_SERVICE_HOST=0.0.0.0,YANDEX_SERVICE_PORT=8080,FOLDER_ID=%FOLDER_ID%,SECRET_ID=%SECRET_ID% || goto :deploy_fail
yc serverless container allow-unauthenticated-invoke --name yandex >nul 2>nul


echo [INFO] Deploying rag...
rem Используем тот же путь, что в образе: /app/vectorstore
set RAG_ENV=RAG_SERVICE_HOST=0.0.0.0,RAG_SERVICE_PORT=8080,VECTORSTORE_DIR=/app/vectorstore,FOLDER_ID=%FOLDER_ID%,S3_ENDPOINT=https://storage.yandexcloud.net,S3_BUCKET=%S3_BUCKET%,S3_PREFIX=%S3_PREFIX%,SECRET_ID=%SECRET_ID%

yc serverless container revision deploy --container-name rag --image %REGISTRY%/rag:latest --service-account-id %SA_ID% --cores 2 --memory 1024MB --concurrency 8 --execution-timeout 60s --environment !RAG_ENV! || goto :deploy_fail
yc serverless container allow-unauthenticated-invoke --name rag >nul 2>nul

rem -------- Read URLs for backends --------
for /f "usebackq tokens=* delims=" %%i in (`powershell -NoProfile -Command "(yc serverless container get --name lockbox --format json ^| ConvertFrom-Json).url"`) do set LOCKBOX_URL=%%i
for /f "usebackq tokens=* delims=" %%i in (`powershell -NoProfile -Command "(yc serverless container get --name logging --format json ^| ConvertFrom-Json).url"`) do set LOGGING_URL=%%i
for /f "usebackq tokens=* delims=" %%i in (`powershell -NoProfile -Command "(yc serverless container get --name validation --format json ^| ConvertFrom-Json).url"`) do set VALIDATION_URL=%%i
for /f "usebackq tokens=* delims=" %%i in (`powershell -NoProfile -Command "(yc serverless container get --name yandex --format json ^| ConvertFrom-Json).url"`) do set YANDEX_URL=%%i
for /f "usebackq tokens=* delims=" %%i in (`powershell -NoProfile -Command "(yc serverless container get --name rag --format json ^| ConvertFrom-Json).url"`) do set RAG_URL=%%i

echo [INFO] URLs:
echo   LOCKBOX_URL   = %LOCKBOX_URL%
echo   LOGGING_URL   = %LOGGING_URL%
echo   VALIDATION_URL= %VALIDATION_URL%
echo   YANDEX_URL    = %YANDEX_URL%
echo   RAG_URL       = %RAG_URL%

rem -------- Deploy gateway --------

echo [INFO] Deploying gateway (initial)...
yc serverless container revision deploy --container-name gateway --image %REGISTRY%/gateway:latest --service-account-id %SA_ID% --cores 1 --memory 512MB --concurrency 16 --execution-timeout 15s --environment GATEWAY_HOST=0.0.0.0,GATEWAY_PORT=8080,LOCKBOX_SERVICE_URL=%LOCKBOX_URL%,LOGGING_SERVICE_URL=%LOGGING_URL%,VALIDATION_SERVICE_URL=%VALIDATION_URL%,YANDEX_SERVICE_URL=%YANDEX_URL%,RAG_SERVICE_URL=%RAG_URL%,SECRET_ID=%SECRET_ID%,EXPOSE_LOCKBOX_PROXY=false || goto :deploy_fail
yc serverless container allow-unauthenticated-invoke --name gateway >nul 2>nul
for /f "usebackq tokens=* delims=" %%i in (`powershell -NoProfile -Command "(yc serverless container get --name gateway --format json ^| ConvertFrom-Json).url"`) do set GATEWAY_URL=%%i

echo [INFO] GATEWAY_URL = %GATEWAY_URL%

rem -------- Deploy telegram --------

echo [INFO] Deploying telegram...
set TELEGRAM_ENV=TELEGRAM_SERVICE_HOST=0.0.0.0,TELEGRAM_SERVICE_PORT=8080,GATEWAY_URL=%GATEWAY_URL%,SECRET_ID=%SECRET_ID%,USE_WEBHOOK=true,WEBHOOK_URL=%GATEWAY_URL%/telegram/webhook
if not "%WEBHOOK_SECRET_TOKEN%"=="" set TELEGRAM_ENV=%TELEGRAM_ENV%,WEBHOOK_SECRET_TOKEN=%WEBHOOK_SECRET_TOKEN%

yc serverless container revision deploy --container-name telegram --image %REGISTRY%/telegram:latest --service-account-id %SA_ID% --cores 1 --memory 512MB --concurrency 4 --execution-timeout 300s --environment %TELEGRAM_ENV% || goto :deploy_fail
yc serverless container allow-unauthenticated-invoke --name telegram >nul 2>nul
for /f "usebackq tokens=* delims=" %%i in (`powershell -NoProfile -Command "(yc serverless container get --name telegram --format json ^| ConvertFrom-Json).url"`) do set TELEGRAM_URL=%%i
echo [INFO] TELEGRAM_URL = %TELEGRAM_URL%

echo [INFO] Updating gateway with TELEGRAM URL...
yc serverless container revision deploy --container-name gateway --image %REGISTRY%/gateway:latest --service-account-id %SA_ID% --cores 1 --memory 512MB --concurrency 16 --execution-timeout 15s --environment GATEWAY_HOST=0.0.0.0,GATEWAY_PORT=8080,LOCKBOX_SERVICE_URL=%LOCKBOX_URL%,LOGGING_SERVICE_URL=%LOGGING_URL%,VALIDATION_SERVICE_URL=%VALIDATION_URL%,YANDEX_SERVICE_URL=%YANDEX_URL%,RAG_SERVICE_URL=%RAG_URL%,TELEGRAM_SERVICE_URL=%TELEGRAM_URL%,SECRET_ID=%SECRET_ID%,EXPOSE_LOCKBOX_PROXY=false || goto :deploy_fail

echo.
echo ================= Deployment completed =================
echo GATEWAY_URL   = %GATEWAY_URL%
echo LOCKBOX_URL   = %LOCKBOX_URL%
echo LOGGING_URL   = %LOGGING_URL%
echo VALIDATION_URL= %VALIDATION_URL%
echo YANDEX_URL    = %YANDEX_URL%
echo RAG_URL       = %RAG_URL%
echo TELEGRAM_URL  = %TELEGRAM_URL%
echo ========================================================
echo Use:  curl "%GATEWAY_URL%/health"
echo.
exit /b 0

:deploy_fail
echo [ERROR] Deploy failed. Check the preceding output.
exit /b 1
