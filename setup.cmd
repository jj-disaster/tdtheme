@echo off
rem Native Windows installer for tdtheme. Double-click it, or run it from
rem CMD/PowerShell: no Git Bash interaction needed.
rem
rem It delegates the real install work to ./setup (sh) through the Git Bash
rem that ships with git for Windows - one implementation of the shim logic,
rem not two - then finishes the two jobs that need native Windows: adding
rem the bin directory to the user PATH, and granting write access to
rem TouchDesigner's Config tree (one UAC click).
rem
rem Usage:
rem   setup.cmd                 install shims, PATH entry and write access
rem   setup.cmd --yes           same, without prompting (for scripts)
rem   setup.cmd --check [DIR]   report what it would do, change nothing
rem   setup.cmd --uninstall DIR remove the shims it created
setlocal
set "HERE=%~dp0"
set "AUTOYES="
set "MODE=install"
set "BINDIR_ARG="

:parse
if "%~1"=="" goto parsed
if "%~1"=="--yes" set "AUTOYES=1"
if "%~1"=="--check" set "MODE=check"
if "%~1"=="--uninstall" set "MODE=uninstall"
if not "%~1"=="--yes" if not "%~1"=="--check" if not "%~1"=="--uninstall" set "BINDIR_ARG=%~1"
shift
goto parse
:parsed

if "%BINDIR_ARG%"=="" set "BINDIR_ARG=%USERPROFILE%\.local\bin"
if not exist "%BINDIR_ARG%" mkdir "%BINDIR_ARG%" || (
    echo setup.cmd: could not create %BINDIR_ARG% 1>&2
    exit /b 1
)

call :find_bash
if not defined BASH (
    echo setup.cmd: Git Bash not found. Install git for Windows, then re-run. 1>&2
    exit /b 1
)

rem MSYS understands C:/... so only the backslashes need converting.
set "BINDIR_POSIX=%BINDIR_ARG:\=/%"
rem setup.cmd appends PATH itself afterwards, so the sh script's setx hint
rem would be stale advice by the time it is read.
set "TDSETUP_HANDLES_PATH=1"
pushd "%HERE%"
if "%MODE%"=="install" (
    "%BASH%" -c "./setup ""%BINDIR_POSIX%"""
) else (
    "%BASH%" -c "./setup --%MODE% ""%BINDIR_POSIX%"""
)
set "SETUP_EXIT=%ERRORLEVEL%"
popd
if not "%SETUP_EXIT%"=="0" exit /b %SETUP_EXIT%
if not "%MODE%"=="install" exit /b 0

call :ensure_permissions
call :ensure_path

echo.
echo Done. Open a new terminal, then from any directory:
echo.
echo     tdtheme status          # check the install
echo     tdtheme list            # see the themes
echo     tdtheme apply midnight  # try one
echo.
exit /b 0

rem ---- subroutines (all communicate via environment, return with exit /b) ---

:find_bash
set "BASH="
if exist "%ProgramFiles%\Git\bin\bash.exe" set "BASH=%ProgramFiles%\Git\bin\bash.exe"
if not defined BASH if exist "%ProgramFiles(x86)%\Git\bin\bash.exe" set "BASH=%ProgramFiles(x86)%\Git\bin\bash.exe"
if not defined BASH for /f "delims=" %%B in ('where bash 2^>nul') do if not defined BASH set "BASH=%%B"
if defined BASH exit /b 0
exit /b 1

:ensure_permissions
set "CFG=%ProgramFiles%\Derivative\TouchDesigner\Config"
if not exist "%CFG%" (
    echo note: TouchDesigner is not at the default location, so there is
    echo note: nothing to grant access to. If it lives elsewhere, point
    echo note: TDTHEME_CONFIG at its Config dir or re-install it by default.
    exit /b 0
)
echo test>"%CFG%\.tdtheme-write-test" 2>nul
if exist "%CFG%\.tdtheme-write-test" (
    del "%CFG%\.tdtheme-write-test"
    echo ok    write access to TouchDesigner Config already in place
    exit /b 0
)
echo.
echo Write access to TouchDesigner Config needs one admin approval.
if defined AUTOYES goto do_elevate
choice /m "Grant it now (UAC prompt)"
if errorlevel 2 goto no_grant
:do_elevate
rem Inner double quotes are deliberate: batch passes them through (no &|<>
rem on the line), PowerShell single-quotes them away, icacls gets quoted args.
powershell -NoProfile -Command "Start-Process icacls -ArgumentList '"%CFG%"','/grant','"%USERNAME%:(OI)(CI)M"','/T' -Verb RunAs -Wait"
echo test>"%CFG%\.tdtheme-write-test" 2>nul
if exist "%CFG%\.tdtheme-write-test" (
    del "%CFG%\.tdtheme-write-test"
    echo ok    write access granted
    exit /b 0
)
:no_grant
echo.
echo   PROBLEM  Config is still not writable; apply will fail until it is. 1>&2
echo   Run once from an elevated prompt, then re-run this: 1>&2
echo     icacls '%CFG%' /grant "%USERNAME%:(OI)(CI)M" /T 1>&2
exit /b 0

:ensure_path
for /f "delims=" %%P in ('powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','User')"') do set "UPATH=%%P"
echo ";%UPATH%;" | findstr /i /c:";%BINDIR_ARG%;" >nul
if not errorlevel 1 (
    echo ok    %BINDIR_ARG% is already on the user PATH
    exit /b 0
)
echo.
echo %BINDIR_ARG% is not on the user PATH, so the bare tdtheme name
echo will not resolve until it is added. New terminals pick it up.
if defined AUTOYES goto do_path
choice /m "Add it now"
if errorlevel 2 exit /b 0
:do_path
powershell -NoProfile -Command "$p=[Environment]::GetEnvironmentVariable('Path','User'); if ($p) { $n=$p+';%BINDIR_ARG%' } else { $n='%BINDIR_ARG%' }; [Environment]::SetEnvironmentVariable('Path',$n,'User')"
rem Re-read rather than trust the write: this cannot pass unless the entry
rem is really there.
for /f "delims=" %%P in ('powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','User')"') do set "UPATH2=%%P"
echo ";%UPATH2%;" | findstr /i /c:";%BINDIR_ARG%;" >nul
if errorlevel 1 (
    echo   PROBLEM  the PATH entry did not stick; add it by hand under 1>&2
    echo   Settings ^> System ^> About ^> Advanced system settings. 1>&2
    exit /b 0
)
echo ok    added to the user PATH; open a new terminal to use it
exit /b 0
