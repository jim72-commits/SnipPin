@echo off
echo === PinShot Builder (v1.3 - Free) ===
echo.
echo Installing dependencies...
pip install -r requirements.txt
echo.

echo === 1/2: Building PinShot.exe (onedir for fast cold-start) ===
python -m PyInstaller ^
  --onedir ^
  --windowed ^
  --name PinShot ^
  --icon=pinshot.ico ^
  --add-data "pinshot.ico;." ^
  --hidden-import=winrt.windows.media.ocr ^
  --hidden-import=winrt.windows.graphics.imaging ^
  --hidden-import=winrt.windows.storage.streams ^
  --hidden-import=winrt.windows.foundation ^
  --hidden-import=pystray ^
  --hidden-import=pystray._win32 ^
  --exclude-module tkinter.test ^
  --exclude-module test ^
  --exclude-module unittest ^
  --exclude-module pydoc ^
  --exclude-module doctest ^
  --noupx ^
  -y screenshot_tool.py
if errorlevel 1 goto :error

echo.
echo === 2/2: Packaging dist\PinShot folder into dist\PinShot.zip ===
powershell -NoProfile -Command "Compress-Archive -Path dist\PinShot\* -DestinationPath dist\PinShot.zip -Force"

echo.
echo Done.
echo  - User distributable: dist\PinShot.zip            (extract + run)
echo  - User binary:        dist\PinShot\PinShot.exe    (folder build)
echo.
echo To distribute: send dist\PinShot.zip - no installer or Python needed.
goto :end

:error
echo.
echo *** Build failed - see PyInstaller output above ***

:end
