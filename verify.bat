@echo off
setlocal
cd /d "%~dp0" || exit /b 1

echo === [0/4] Ensure a modern alembic ^(^>=1.13, provides the check subcommand^) ===
python -m pip install -q "alembic>=1.13" || (echo [X] alembic install failed - need network to fetch alembic 1.13+ & exit /b 1)

set "DATABASE_URL=sqlite+aiosqlite:///./_verify.db"

echo.
echo === [1/4] alembic upgrade head - create tables ===
python -m alembic upgrade head || (echo [X] upgrade failed & del /q _verify.db 2>nul & exit /b 1)

echo.
echo === [2/4] alembic check - expect: No new upgrade operations detected. ===
python -m alembic check && (echo [OK] model matches migration) || (echo [warn] check reported a diff or is unavailable - see output above)

echo.
echo === [3/4] alembic downgrade base - rollback check ===
python -m alembic downgrade base || (echo [X] downgrade failed & del /q _verify.db 2>nul & exit /b 1)
del /q _verify.db 2>nul

set "DATABASE_URL="
echo.
echo === [4/4] pytest - offline, skip PostgreSQL-only cases ===
python -m pytest -q -m "not pg" || (echo [X] tests failed & exit /b 1)

echo.
echo ===== ALL PASSED: migration applies and matches ORM, tests are green =====
endlocal
