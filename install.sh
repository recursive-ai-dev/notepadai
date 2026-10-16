#!/usr/bin/env bash
# Adds NotepadAI to the desktop app menu (per-user, no root needed).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
if ! python3 -c 'import tkinter' 2>/dev/null; then
    echo "Python 3 with Tkinter is required (python3-tkinter on Fedora, python3-tk on Debian/Ubuntu)." >&2
    exit 1
fi
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
mkdir -p "$APPS" "$ICONS"
cp "$DIR/notepadai.svg" "$ICONS/notepadai.svg"
# Quote both the desktop-entry string and its Exec argument, including literal %.
# https://specifications.freedesktop.org/desktop-entry/latest/exec-variables.html
python3 - "$DIR/notepadai.py" "$APPS/notepadai.desktop" <<'PY'
import pathlib
import sys

script, destination = sys.argv[1:]
if any(char in script for char in '\n\r\t'):
    raise SystemExit('Install path must not contain tabs or newlines.')
argument = ''.join('\\' + char if char in '\\"`$' else char for char in script)
argument = argument.replace('\\', '\\\\').replace('%', '%%')
entry = f'''\
[Desktop Entry]
Version=1.0
Type=Application
Name=NotepadAI
GenericName=Text Editor
Comment=Notepad with an AI assistant that can read and edit your tabs
Exec=python3 "{argument}" -- %F
TryExec=python3
Icon=notepadai
Terminal=false
Categories=Utility;TextEditor;
MimeType=text/plain;
StartupWMClass=Notepadai
Keywords=notes;notepad;AI;
'''
pathlib.Path(destination).write_text(entry, encoding='utf-8')
PY
chmod +x "$DIR/notepadai.py"
update-desktop-database "$APPS" 2>/dev/null || true
gtk-update-icon-cache -q -t "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" 2>/dev/null || true
echo "Installed: $APPS/notepadai.desktop"
