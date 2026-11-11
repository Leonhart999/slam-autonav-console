"""Open a data folder in the desktop file manager (Maps and Evaluation pages: "Open folder").

The console listens on 127.0.0.1 only, so the browser and the backend are on the same machine and the folder
opens on that machine's desktop: under WSL with Windows Explorer (explorer.exe <wslpath -w path>; folders in
the Linux file system open as \\\\wsl.localhost\\<distro>\\...), on a Linux desktop with xdg-open.

Only folders inside the console's own data folders can be opened (maps, run records, evaluation exports,
bag folders); the path must exist and be a directory. `~` is expanded, so the page can pass the paths it shows.
The recycle bin dialogs open <data folder>/.trash, which only exists after the first delete: that one folder is
created when it is missing.
"""
import os
import shutil
import subprocess

from fastapi import HTTPException


def is_wsl():
    if os.path.exists('/proc/sys/fs/binfmt_misc/WSLInterop'):
        return True
    try:
        with open('/proc/version', encoding='utf-8') as f:
            return 'microsoft' in f.read().lower()
    except OSError:
        return False


def windows_path(path):
    try:
        return subprocess.run(['wslpath', '-w', path], capture_output=True, text=True, timeout=5).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def opener(path):
    """Command that opens `path` in a file manager here, and the path as the user will see it; (None, None)
    when there is no desktop to open it on."""
    if is_wsl() and shutil.which('explorer.exe'):
        win = windows_path(path)
        if win:
            return ['explorer.exe', win], win
    if shutil.which('xdg-open') and (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        return ['xdg-open', path], path
    return None, None


def allowed_roots(cfg):
    m = cfg.get('mapping') or {}
    p = cfg.get('playback') or {}
    roots = [m.get('maps_dir'), m.get('runs_dir'), (cfg.get('evaluation') or {}).get('results_dir'),
             *(p.get('bag_dirs') or []), p.get('upload_dir')]
    return [os.path.realpath(os.path.expanduser(r)) for r in roots if r]


def register_open_folder_routes(app, cfg):
    roots = allowed_roots(cfg)

    def error(status, code, **params):
        return HTTPException(status, detail={'code': code, 'params': params})

    @app.post('/api/open-folder')
    def open_folder(path: str):
        real = os.path.realpath(os.path.expanduser(path or ''))
        if not any(real == r or real.startswith(r.rstrip('/') + '/') for r in roots):
            raise error(403, 'open_folder_outside', path=path)
        if (not os.path.lexists(real) and os.path.basename(real) == '.trash'
                and os.path.dirname(real) in roots and os.path.isdir(os.path.dirname(real))):
            os.makedirs(real, mode=0o700, exist_ok=True)
        if not os.path.isdir(real):
            raise error(404, 'open_folder_missing', path=path)
        cmd, shown = opener(real)
        if cmd is None:
            raise error(501, 'open_folder_unsupported')
        try:
            # explorer.exe returns exit code 1 even when it opened the window, so the result is not checked.
            # Wait for it (it hands the folder to the running Explorer and exits within a second) so it is reaped.
            p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        except OSError as e:
            raise error(500, 'open_folder_failed', reason=str(e))
        return {'path': real, 'opened': shown}
