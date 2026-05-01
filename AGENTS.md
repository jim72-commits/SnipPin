# PinShot Agent Guide

Use this file to quickly understand PinShot when continuing development.

## Product Summary

PinShot is a lightweight Windows screenshot pinning tool. Its key marketing promise is:

> Capture up to 10 screenshots and keep them floating above all other windows.

The app is free, has no paywall, no license key, no watermark, no ads, and no telemetry. End users should be able to download a zip, extract it, and run `PinShot.exe` without installing Python.

## Core User Value

- Fast rectangular screenshot capture across multiple monitors.
- Up to 10 pinned screenshot windows at once.
- Pinned screenshots stay always on top of other windows.
- Each pinned screenshot is resizable and can be closed independently.
- Useful for comparing Excel sheets, dashboards, bug reports, documents, meetings, and reference material while working.

## Important Features

- Timed capture: 0, 3, 5, and 10 seconds.
- Auto-copy captured screenshots to clipboard.
- Copy edited screenshots to clipboard for Outlook, Teams, Word, and web apps.
- Save As PNG, WebP, JPEG, or BMP.
- Markup tools: highlight, rectangle, ellipse, arrow, number, text, spotlight, blur/redact.
- `Ctrl+Z` undo inside a screenshot window.
- OCR text extraction using native Windows OCR when available.
- System tray support.
- Persistent preferences in `%LOCALAPPDATA%\PinShot\pinshot.json`.

## Repository Layout

- `screenshot_tool.py`: main application, intentionally single-file for easy distribution and maintenance.
- `PinShot.spec`: PyInstaller build spec.
- `build.bat`: build script that creates the distributable app.
- `requirements.txt`: Python dependencies for developers/builds.
- `pinshot.ico`: app icon.
- `assets/`: README marketing screenshots and GIFs using mock data only.
- `README.md`: public-facing product page.
- `LICENSE`: personal-use-only license; no resale or redistribution without written permission.

## Build And Release

Development/build machine requirements:

- Windows 10/11.
- Python 3.11+.
- `pip install -r requirements.txt`.
- Run `build.bat`.

Expected output:

- `dist\PinShot\PinShot.exe`
- `dist\PinShot.zip`

Do not commit `dist/`, `build/`, `__pycache__/`, test scratch files, or local logs. Publish `PinShot.zip` as a GitHub Release asset.

## Product Direction

Keep PinShot lightweight, fast, and focused. Avoid features that turn it into a large screenshot suite, cloud service, account-based product, or heavy editor.

Prefer improvements that support the main promise:

- Faster capture.
- Better pinned-window behavior.
- Sharper screenshots.
- More reliable clipboard support.
- Simpler annotation.
- Better onboarding and marketing clarity.

## Important Constraints

- Preserve no-Python requirement for normal users by shipping a PyInstaller build.
- Avoid global keyboard hooks unless explicitly requested; previous keyboard shortcut work caused usability issues.
- Do not reintroduce paywall, license-key, keygen, watermark, or donation prompts inside the app.
- Do not describe PinShot as open source or MIT licensed; it is source-available for personal use only.
- Donation/support links can live in README/GitHub only, not as intrusive in-app monetization.
- Keep demo/marketing assets based on mock data only.
- Be careful with Windows clipboard formats; Outlook compatibility matters.

## Marketing Positioning

Lead with:

> Lightweight Windows screenshot pinning tool. Capture up to 10 screenshots and keep them floating above all other windows.

Secondary benefits:

- No installer.
- No Python required for users.
- No ads, telemetry, license key, or watermark.
- Great for Excel, dashboards, support, QA, operations, and documentation workflows.

## Coding Guidance

- Follow the existing Tkinter + Pillow + Windows API style.
- Keep changes scoped; avoid large rewrites unless there is a clear reliability or performance benefit.
- Prefer lazy imports for heavy optional modules to preserve startup speed.
- After UI/clipboard/rendering changes, test manually because automated coverage for Tkinter and Windows clipboard behavior is limited.
