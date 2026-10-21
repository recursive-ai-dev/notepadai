#!/usr/bin/env bash
# Adapted from npm-to-deb's private staging, verification and output approach.
set -euo pipefail
exec python3 - "$@" <<'PY'
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description='Build a NotepadAI Debian desktop package; no installation or downloads.')
    parser.add_argument('project', type=Path, nargs='?', default=Path.cwd())
    parser.add_argument('--out', type=Path, default=Path.cwd() / 'desktop-packages')
    parser.add_argument('--maintainer', default='NotepadAI contributors <notepadai@example.invalid>')
    parser.add_argument('--keep-work', action='store_true')
    args = parser.parse_args()
    if not shutil.which('dpkg-deb'):
        parser.error('dpkg-deb is required.')
    if not re.fullmatch(r'[^<>\x00-\x1f]+ <[^<>\s]+@[^<>\s]+>', args.maintainer):
        parser.error('--maintainer must be a single-line Name <email@example.com>.')
    project = args.project.resolve()
    tree = ast.parse((project / 'notepadai.py').read_text(encoding='utf-8'))
    version = next(ast.literal_eval(node.value) for node in tree.body
                   if isinstance(node, ast.Assign) and any(
                       isinstance(target, ast.Name) and target.id == 'VERSION' for target in node.targets))
    if not isinstance(version, str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)*', version):
        parser.error('Application VERSION must contain numeric dot-separated components.')
    work = Path(tempfile.mkdtemp(prefix='notepadai-deb-'))
    success = False
    try:
        root = work / 'package'

        def write(relative, content, mode=0o644):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
            path.chmod(mode)
            return path

        # Explicit payload excludes credentials, caches, tests and Git data.
        for name, destination in [
            ('notepadai.py', 'usr/share/notepadai/notepadai.py'),
            ('notepadai.svg', 'usr/share/icons/hicolor/scalable/apps/notepadai.svg'),
            ('README.md', 'usr/share/doc/notepadai/README.md'),
        ]:
            path = root / destination
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(project / name, path)
            path.chmod(0o644)
        write('usr/bin/notepadai', '#!/bin/sh\nexec /usr/bin/python3 /usr/share/notepadai/notepadai.py "$@"\n', 0o755)
        desktop = write('usr/share/applications/notepadai.desktop', '''[Desktop Entry]
Version=1.0
Type=Application
Name=NotepadAI
GenericName=Text Editor
Comment=Notepad with an AI assistant that can read and edit your tabs
Exec=notepadai -- %F
TryExec=notepadai
Icon=notepadai
Terminal=false
Categories=Utility;TextEditor;
MimeType=text/plain;
StartupWMClass=Notepadai
Keywords=notes;notepad;AI;
''')
        if shutil.which('desktop-file-validate'):
            subprocess.run(['desktop-file-validate', str(desktop)], check=True)
        payload = sorted(path for path in root.rglob('*') if path.is_file())
        size = sum(path.stat().st_size for path in payload)
        write('DEBIAN/control', f'''Package: notepadai
Version: {version}
Section: editors
Priority: optional
Architecture: all
Maintainer: {args.maintainer}
Depends: python3 (>= 3.10), python3-tk
Installed-Size: {(size + 1023) // 1024}
Description: Tabbed text editor with an optional AI assistant
 A UTF-8 text editor built with Python and Tkinter, with an optional
 assistant using an OpenAI-compatible chat provider.
''')
        write('DEBIAN/md5sums', ''.join(
            f'{hashlib.md5(path.read_bytes()).hexdigest()}  {path.relative_to(root).as_posix()}\n'
            for path in payload))
        for path in [root, *(path for path in root.rglob('*') if path.is_dir())]:
            path.chmod(0o755)
        filename = f'notepadai_{version}_all.deb'
        archive = work / filename
        subprocess.run(['dpkg-deb', '--root-owner-group', '--build', str(root), str(archive)], check=True)
        for field, expected in [('Package', 'notepadai'), ('Version', version), ('Architecture', 'all')]:
            actual = subprocess.check_output(['dpkg-deb', '--field', str(archive), field], text=True).strip()
            if actual != expected:
                raise RuntimeError(f'Package verification failed: {field}')
        extracted = work / 'verified'
        subprocess.run(['dpkg-deb', '--extract', str(archive), str(extracted)], check=True)
        for path in payload:
            installed = extracted / path.relative_to(root)
            if (installed.read_bytes() != path.read_bytes()
                    or installed.stat().st_mode & 0o777 != path.stat().st_mode & 0o777):
                raise RuntimeError(f'Payload verification failed: {path.relative_to(root)}')
        args.out.mkdir(parents=True, exist_ok=True)
        output = Path(tempfile.mkdtemp(prefix=f'notepadai-{version}-all-', dir=args.out.resolve()))
        result = output / filename
        shutil.copyfile(archive, result)
        digest = hashlib.sha256(result.read_bytes()).hexdigest()
        (output / 'SHA256SUMS').write_text(f'{digest}  {filename}\n', encoding='utf-8')
        (output / 'build-info.json').write_text(json.dumps({
            'generator': 'notepadai-to-deb.sh', 'package': 'notepadai', 'version': version,
            'architecture': 'all', 'maintainer': args.maintainer, 'python': sys.version.split()[0],
            'file': filename, 'sha256': digest,
        }, indent=2) + '\n', encoding='utf-8')
        success = True
        print(f'\nCreated: {result}\nInstall: sudo apt install {shlex.quote(str(result))}\nRemove: sudo apt remove notepadai')
    finally:
        if success and not args.keep_work:
            shutil.rmtree(work)
        else:
            print(f'Staging files retained: {work}', file=sys.stderr)


try:
    main()
except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
    sys.exit(f'notepadai-to-deb: {error}')
PY
