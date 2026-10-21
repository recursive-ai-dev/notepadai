# NotepadAI 1.0

A small tabbed text editor with an AI sidekick that can read and edit your notes.
Python's standard library only; no pip packages, account system, or background service.

## Run

Requires Python 3.10+ and Tkinter, with a graphical desktop. On Linux, Tkinter may
be a separate package: `python3-tkinter` on Fedora or `python3-tk` on Debian/Ubuntu.

```sh
python3 notepadai.py
python3 notepadai.py notes.txt another-note.md
python3 notepadai.py --version
```

To add the app to your Linux desktop menu:

```sh
bash install.sh
```

The installer creates a per-user launcher and icon under
`${XDG_DATA_HOME:-~/.local/share}`. It points to this source directory; rerun it
after moving the folder. It does not change your default text editor.
Remove `applications/notepadai.desktop` and
`icons/hicolor/scalable/apps/notepadai.svg` from that data directory to uninstall
the launcher. Your files and settings remain in place.

## Build a Debian package

Build a `.deb` for Debian/Ubuntu with Python 3.10+ and `dpkg-deb` installed:

```sh
bash build-deb.sh .
# Optional: set your package maintainer and output directory
bash build-deb.sh . --maintainer "Your Name <you@example.com>" --out ~/Downloads
```

The builder follows `npm-to-deb.sh`'s temporary staging and verification approach.
It packages the Python application, command launcher, desktop entry, and SVG icon;
Debian installs `python3` and `python3-tk` as dependencies. No pip packages,
downloads, root access, or source changes are needed to build. The package works
across CPU architectures and uses the version declared in `notepadai.py`.

Each build creates a unique directory under `desktop-packages/` in the current
directory, containing the `.deb`, `SHA256SUMS`, and `build-info.json`. Use
`--keep-work` to retain staging files. The builder validates the desktop entry
when `desktop-file-validate` is available and verifies archived files and modes.
The default maintainer uses a placeholder address; provide `--maintainer` when
distributing the package.

Install using the exact command printed by the builder:

```sh
sudo apt install /path/to/notepadai_1.0.0_all.deb
sudo apt remove notepadai
```

The installed command is `notepadai`, and the app appears in the desktop menu.
Removing the package leaves your notes and user settings in place. If you used
`install.sh` previously, remove its per-user launcher and icon as described above
so the menu uses the packaged launcher.

## Set up the AI sidekick

Open **AI → Settings** and enter the base URL, model name, and API key for a
provider that supports the OpenAI-compatible `/chat/completions` format.
The app adds `/chat/completions` to the base URL; do not include it yourself.
For a local server, use the URL and model name it provides; leave the key empty
if that server does not require one. An AI provider is optional for ordinary editing.

If chat reports a model timeout, an unavailable provider, or a rate limit, retry
later or choose another model in **AI → Settings**. A model can remain listed even
while its provider is offline. The chat shows provider error messages, including
failures reported inside otherwise successful HTTP responses.

Type a request in the chat box and press Enter. Shift+Enter adds a line break.
The assistant can read, write, append, replace text, and manage tabs using command
tags. Example requests: “Fix the spelling in this note” or “Turn this into a short checklist.”

- **Stop** immediately prevents further commands from that request. An HTTP request
  already in progress may still finish at the provider; stopping does not refund
  provider usage. Requests time out after 120 seconds of network inactivity.
- Edits target the tab where you started the request, even if you switch tabs.
  If you edit that tab while waiting, the assistant must receive its updated text
  before it can change it.
- Each AI edit is one Undo step. AI commands never save files or discard a dirty tab.
  Review the text and save it yourself.
- **Clear chat** also stops the current run. Settings changes apply to the next request.
- Malformed command batches and provider responses marked incomplete are rejected.
  A failed command stops the rest of that reply; earlier successful edits remain undoable.

Chat messages, tab titles, and requested tab text are sent to the configured
provider. By default, each request includes the active note if it is at most
20,000 characters. Turning this option off omits that automatic text; the assistant
can still request tab contents with a read command. Reads are limited to 200,000
characters. Chat history is kept in memory until you clear it or quit.

Settings live in `${XDG_CONFIG_HOME:-~/.config}/notepadai/config.json`.
The API key is stored as plain text in that file with owner-only permissions on Linux.
Use HTTPS for remote providers.

## Everyday editing

| Shortcut | Action |
| --- | --- |
| Ctrl+N / Ctrl+O | New tab / open files |
| Ctrl+S / Ctrl+Shift+S | Save / Save as |
| Ctrl+W / Ctrl+Q | Close tab / quit |
| Ctrl+Z / Ctrl+Shift+Z or Ctrl+Y | Undo / redo |
| Ctrl+F | Find in the current tab; Enter finds the next match |
| Ctrl+A / Ctrl+X / Ctrl+C / Ctrl+V | Select all / cut / copy / paste |

A dot on a tab means it has unsaved changes. Closing a dirty tab or the window
offers Save, Discard, or Cancel. A cancelled or failed save leaves your note open.
Save checks whether the file changed outside the app and asks before overwriting it.
Successful writes replace the destination atomically, so a failed write does not
truncate the original file. Opening the same file again selects its existing tab.

Files must be UTF-8 plain text, up to 10 MiB. UTF-8 BOMs and LF, CRLF, or CR line
endings are preserved. Unchanged files round-trip byte for byte; editing a file
with mixed line endings normalizes them to its most common ending. Unsupported
encodings and binary files are rejected instead of being silently converted.

Version 1 keeps the scope small: no autosave, crash recovery, or session restore.
Save important work regularly. The AI command protocol uses XML-like tags with raw
text bodies; literal closing command tags in generated text can be ambiguous.
Command examples in backticks or Markdown code fences are displayed without execution. Models must support
ordinary text chat completions and follow this protocol; native tool calls and
streaming are not used.

## Verify

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile notepadai.py
bash -n install.sh
```

The regression suite covers file integrity, settings, command parsing, undo,
external edits, close/cancel flows, and AI worker lifecycle. Editor tests use real
Tk widgets and need a display; they are explicitly skipped when no display is
available. On headless Linux with Xvfb installed, run the suite with `xvfb-run -a`.
Tests use temporary files and simulated providers; they do not use your API key
or contact a paid service.
