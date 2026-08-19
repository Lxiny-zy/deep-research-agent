@echo off
setlocal
cd /d "%~dp0" || exit /b 1

echo === [1/10] Install locked development dependencies ===
python -m pip install --require-hashes -r requirements-dev.lock || goto :failed

echo.
echo === [2/10] Check dependency lock consistency ===
python scripts\check_dependency_locks.py || goto :failed

echo.
echo === [3/10] Ruff lint and format check ===
python -m ruff check deep_research tests alembic scripts eval || goto :failed
python -m ruff format --check deep_research tests alembic scripts eval || goto :failed

echo.
echo === [4/10] Mypy ===
python -m mypy deep_research || goto :failed

if not defined TEMP set "TEMP=%CD%"
:choose_verify_db
set "VERIFY_DB=%TEMP%\deep-research-agent-verify-%RANDOM%-%RANDOM%.db"
if exist "%VERIFY_DB%" goto :choose_verify_db
set "VERIFY_DB_URL=%VERIFY_DB:\=/%"
set "DATABASE_URL=sqlite+aiosqlite:///%VERIFY_DB_URL%"

echo.
echo === [5/10] Alembic upgrade and model drift check ===
python -m deep_research.migrate || goto :failed
python -m alembic check || goto :failed

echo.
echo === [6/10] Alembic latest revision downgrade and replay ===
python -m alembic downgrade -1 || goto :failed
python -m alembic upgrade head || goto :failed

echo.
echo === [7/10] Pytest with coverage gate ===
python -m pytest -m "not pg" --cov=deep_research --cov-report=term-missing --cov-fail-under=80 || goto :failed

echo.
echo === [8/10] Frontend reproducible install, lint, build, and tests ===
pushd frontend || goto :failed
call npm ci
if errorlevel 1 (popd & goto :failed)
call npm run lint
if errorlevel 1 (popd & goto :failed)
call npm run build
if errorlevel 1 (popd & goto :failed)
call npm run test
if errorlevel 1 (popd & goto :failed)
popd

echo.
echo === [9/10] Wheel build and isolated install smoke ===
if exist dist\deep_research_agent-*.whl del /q dist\deep_research_agent-*.whl
if exist .wheel-smoke rmdir /s /q .wheel-smoke
python -m pip wheel . --no-deps --no-build-isolation --wheel-dir dist || goto :failed
python -m venv .wheel-smoke || goto :failed
.wheel-smoke\Scripts\python.exe -m pip install --require-hashes -r requirements.lock || goto :failed
set "VERIFY_WHEEL="
for %%F in (dist\deep_research_agent-*.whl) do set "VERIFY_WHEEL=%%F"
if not defined VERIFY_WHEEL goto :failed
.wheel-smoke\Scripts\python.exe -m pip install --no-deps "%VERIFY_WHEEL%" || goto :failed
.wheel-smoke\Scripts\python.exe -m pip check || goto :failed
.wheel-smoke\Scripts\deep-research.exe --help >nul || goto :failed
.wheel-smoke\Scripts\python.exe scripts\smoke_installed_package.py || goto :failed

echo.
echo === [10/10] Docker Compose configuration ===
if not defined POSTGRES_PASSWORD set "POSTGRES_PASSWORD=verify-postgres-password"
if not defined API_KEY set "API_KEY=verify-api-key"
if not defined CATALOG_ENCRYPTION_KEY set "CATALOG_ENCRYPTION_KEY=MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
docker compose config --quiet || goto :failed

echo.
echo ===== ALL CORE GATES PASSED =====
del /q "%VERIFY_DB%" "%VERIFY_DB%-shm" "%VERIFY_DB%-wal" 2>nul
endlocal
exit /b 0

:failed
set "VERIFY_EXIT=%ERRORLEVEL%"
if "%VERIFY_EXIT%"=="0" set "VERIFY_EXIT=1"
echo.
echo [X] Verification failed with exit code %VERIFY_EXIT%.
if defined VERIFY_DB del /q "%VERIFY_DB%" "%VERIFY_DB%-shm" "%VERIFY_DB%-wal" 2>nul
endlocal & exit /b %VERIFY_EXIT%
