@echo off
setlocal

REM Put this .bat file next to generate_mod.py and balance_plan.json.
REM This generates directly into this server mod folder.
set "PA_ROOT=D:\SteamLibrary\steamapps\common\Planetary Annihilation Titans"
set "LOG_FILE=generate_mod_output.txt"

if exist "%LOG_FILE%" del "%LOG_FILE%"

echo Running Tallboys mod generator...
echo Running Tallboys mod generator... > "%LOG_FILE%"
echo. >> "%LOG_FILE%"

py -3 generate_mod.py ^
  --plan balance_plan.json ^
  --pa-root "%PA_ROOT%" ^
  --output-dir "%CD%" ^
  --zip "%CD%\com.pa.lockyaw.tallboys.zip" ^
  --no-clean ^
  --clean-generated >> "%LOG_FILE%" 2>&1

set "EXIT_CODE=%ERRORLEVEL%"

type "%LOG_FILE%"

echo.
echo Output was also saved to: %CD%\%LOG_FILE%
echo Output was also saved to: %CD%\%LOG_FILE% >> "%LOG_FILE%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo Generation failed. Copy the text above, or open %LOG_FILE%.
  echo Generation failed. Copy the text above, or open %LOG_FILE%. >> "%LOG_FILE%"
) else (
  echo.
  echo Generation complete.
  echo Generated into: %CD%
  echo Zip: %CD%\com.pa.lockyaw.tallboys.zip
  echo Generation complete. >> "%LOG_FILE%"
  echo Generated into: %CD% >> "%LOG_FILE%"
  echo Zip: %CD%\com.pa.lockyaw.tallboys.zip >> "%LOG_FILE%"
)

echo.
echo The window will stay open so you can copy the full output.
echo Close this window manually when done.
cmd /k
