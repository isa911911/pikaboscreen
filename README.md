# pikaboscreen
Live mini-viewer for Windows. Select an area of any window (like a game's minimap) and watch it in real time in a small always-on-top window, even when the original window is covered by other apps.

## Usage
1. Open the app you want to watch (don't minimize it).
2. Run `PikaboScreen.exe` (or `python pikaboscreen.py`).
3. Type the exact window title (by default: `Dota 2`), then drag to select an area.

## Install from source
```
pip install -r requirements.txt
```

## Requirements
Windows 10 ver 1903+ or Windows 11 (because using Windows Graphics Capture), Python 3.9+ if running from source.

## Notes
- For games, use Borderless Window mode.
- Captures window images only. No memory reading, code injection, or input sending.
- Built with help from Claude (Anthropic), directed and tested by isa911911.
