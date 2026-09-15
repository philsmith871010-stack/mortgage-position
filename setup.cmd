@echo off
setlocal
REM Usage:  setup.cmd https://github.com/YOUR-USER/YOUR-REPO.git
REM Run from inside the unzipped folder. Pushes everything to your (already created, empty) repo
REM and turns on GitHub Pages from the main branch root.

if "%~1"=="" (
  echo Usage: setup.cmd https://github.com/YOUR-USER/YOUR-REPO.git
  exit /b 1
)
set REPO=%~1

where git >nul 2>nul || (echo git not found. Install from https://git-scm.com/download/win and rerun. & exit /b 1)

if not exist .git (
  git init -b main || git init
  git checkout -B main
)
git add .
git commit -m "Mortgage position mock-up" 2>nul || echo (nothing new to commit)
git remote remove origin 2>nul
git remote add origin %REPO%
git push -u origin main --force
if errorlevel 1 (echo Push failed. Check the repo URL and that you are signed in to GitHub. & exit /b 1)

echo.
where gh >nul 2>nul
if errorlevel 1 (
  echo Pushed. GitHub CLI not found, so enable Pages by hand:
  echo   repo ^> Settings ^> Pages ^> Source: Deploy from a branch ^> main, / ^(root^) ^> Save
  goto :done
)

for /f "tokens=*" %%i in ('gh repo view %REPO% --json nameWithOwner -q .nameWithOwner') do set NWO=%%i
gh api -X POST repos/%NWO%/pages -f "source[branch]=main" -f "source[path]=/" >nul 2>nul || gh api -X PUT repos/%NWO%/pages -f "source[branch]=main" -f "source[path]=/" >nul 2>nul
echo Pages enabled. Give it a minute, then open:
gh api repos/%NWO%/pages -q .html_url

:done
echo.
echo To update later: replace index.html, then run   git add . ^&^& git commit -m "update" ^&^& git push
endlocal
