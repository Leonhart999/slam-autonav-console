"""Bag converter plugin backend (plugin interface v1).

Routes under /api/ext/bag_convert/:
  GET    sources              ROS1 .bag files in the console's bag folders, its upload folder and the extra folders
  POST   inspect {path}       topics, counts and duration of one bag (bagtool.py in the plugin's venv)
  POST   convert {...}        start a conversion job (worker.py); the generic /jobs routes show its progress
  PUT    upload?name=&size=   upload a .bag into <upload folder>/ros1/ (raw request body)
  DELETE upload?path=         delete a .bag uploaded that way
The ROS2 bag is written into the console's upload folder (GET /api/playback/storage), where the Playback page
finds it.
"""
import asyncio
import json
import os
import re
import shutil
import uuid

from fastapi import Body, Request

NAME_RE = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
BAG_NAME_RE = re.compile(r'^[A-Za-z0-9_.()\- ]{1,120}\.bag$')
DEPTH = 4
MIN_FREE = 512 * 1024 * 1024        # bytes left over after an upload or at the start of a conversion
STAGE = '.bag_convert'              # hidden folder in the upload folder (the bag finder skips hidden folders)


def setup(ctx):
    async def folders():
        """(upload folder, [folders to search])"""
        storage = await ctx.api.get('/api/playback/storage')
        upload = os.path.realpath(storage['upload_dir'])
        roots = [upload, os.path.dirname(upload)]
        try:
            listing = await ctx.api.get('/api/playback/bags')        # {roots: [...], bags: [...]}
            roots += [r for r in listing.get('roots') or [] if isinstance(r, str)]
        except Exception as e:  # noqa: BLE001  the list still works with the upload folder
            ctx.log.warn(f'bag list unavailable: {e}')
        for line in (ctx.settings().get('search_dirs') or '').splitlines():
            if line.strip():
                roots.append(os.path.expanduser(line.strip()))
        out = []
        for r in roots:
            r = os.path.realpath(r)
            if os.path.isdir(r) and r not in out:
                out.append(r)
        return upload, out

    def find(roots):
        found = {}
        for root in roots:
            for d, subdirs, files in os.walk(root):
                depth = 0 if d == root else os.path.relpath(d, root).count(os.sep) + 1
                subdirs[:] = sorted(s for s in subdirs if not s.startswith('.')) if depth < DEPTH else []
                if 'metadata.yaml' in files:          # a ROS2 bag folder
                    subdirs[:] = []
                    continue
                for f in files:
                    if not f.endswith('.bag') or f.startswith('.'):
                        continue
                    path = os.path.realpath(os.path.join(d, f))
                    if path in found or not os.path.isfile(path):
                        continue
                    st = os.stat(path)
                    found[path] = {'path': path, 'name': f, 'folder': d, 'root': root, 'size': st.st_size,
                                   'mtime': st.st_mtime}
        return sorted(found.values(), key=lambda b: (b['folder'], b['name']))

    def inside(path, roots):
        real = os.path.realpath(path)
        for r in roots:
            try:
                if os.path.commonpath([real, r]) == r:
                    return True
            except ValueError:
                continue
        return False

    async def checked_bag(path, roots=None):
        if not isinstance(path, str) or not path.endswith('.bag'):
            raise ctx.error(400, 'not_a_bag')
        if roots is None:
            roots = (await folders())[1]
        if not inside(path, roots) or not os.path.isfile(path):
            raise ctx.error(404, 'bag_missing', path=path)
        return os.path.realpath(path)

    def disk_free(path):
        probe = os.path.realpath(path)
        while probe and not os.path.exists(probe):
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
        return shutil.disk_usage(probe or '/').free

    @ctx.router.get('/sources')
    async def sources():
        upload, roots = await folders()
        files = await asyncio.to_thread(find, roots)
        ros1 = os.path.join(upload, 'ros1')
        for f in files:
            f['uploaded'] = f['folder'] == ros1
        free = disk_free(upload)
        return {'upload_dir': upload, 'roots': roots, 'files': files, 'free': free,
                'storage': ctx.settings().get('storage') or 'sqlite3'}

    @ctx.router.post('/inspect')
    async def inspect(body: dict = Body(...)):
        path = await checked_bag(body.get('path'))
        code, out, err = await ctx.run_python([os.path.join(ctx.dir, 'bagtool.py'), 'inspect', path], timeout=180)
        try:
            data = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
        except ValueError:
            data = {}
        if code != 0 or 'topics' not in data:
            raise ctx.error(400, 'inspect_failed', reason=data.get('error') or (err.strip().splitlines() or ['?'])[-1])
        return {'path': path, **data}

    @ctx.router.post('/convert')
    async def convert(body: dict = Body(...)):
        upload, roots = await folders()
        src = await checked_bag(body.get('path'), roots)
        name = body.get('name')
        if not isinstance(name, str) or not NAME_RE.match(name) or name.startswith('.'):
            raise ctx.error(400, 'bad_name')
        topics = body.get('topics') or []
        if not isinstance(topics, list) or not all(isinstance(t, str) for t in topics):
            raise ctx.error(400, 'bad_topics')
        storage = body.get('storage') or ctx.settings().get('storage') or 'sqlite3'
        if storage not in ('sqlite3', 'mcap'):
            raise ctx.error(400, 'bad_storage')
        os.makedirs(upload, exist_ok=True)
        dst = os.path.join(upload, name)
        if os.path.exists(dst):
            raise ctx.error(409, 'output_exists', name=name)
        if any(j['state'] in ('queued', 'running') and j['params'].get('dst') == dst for j in ctx.jobs.list()):
            raise ctx.error(409, 'output_exists', name=name)
        free = disk_free(upload)
        if free < MIN_FREE:
            raise ctx.error(507, 'no_space', free=round(free / 2**30, 2))
        # stage = a private folder per job on the same disk as the output (the worker renames the bag into place);
        # folders of jobs that no longer run are leftovers of a stopped console
        stages = os.path.join(upload, STAGE)
        active = {j['params'].get('stage') for j in ctx.jobs.list() if j['state'] in ('queued', 'running')}
        if os.path.isdir(stages):
            for d in os.listdir(stages):
                if os.path.join(stages, d) not in active:
                    shutil.rmtree(os.path.join(stages, d), ignore_errors=True)
        params = {'src': src, 'dst': dst, 'topics': topics, 'livox': bool(body.get('livox')), 'storage': storage,
                  'stage': os.path.join(stages, uuid.uuid4().hex)}
        return ctx.jobs.submit('convert', params, title=f'{os.path.basename(src)} → {name}', target=src)

    @ctx.router.put('/upload')
    async def upload_bag(request: Request, name: str, size: int):
        upload, _ = await folders()
        if not BAG_NAME_RE.match(name or '') or name.startswith('.'):
            raise ctx.error(400, 'bad_upload_name')
        folder = os.path.join(upload, 'ros1')
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, name)
        if os.path.exists(target):
            raise ctx.error(409, 'upload_exists', name=name)
        free = disk_free(folder)
        if size <= 0 or size + MIN_FREE > free:
            raise ctx.error(507, 'no_space', free=round(free / 2**30, 2))
        part = os.path.join(folder, f'.{name}.{uuid.uuid4().hex[:8]}.part')
        written = 0
        buf = bytearray()
        flush_size = 2 * 1024 * 1024
        try:
            with open(part, 'wb') as f:
                async for chunk in request.stream():
                    written += len(chunk)
                    if written > size:
                        raise ctx.error(400, 'upload_size', expected=size, got=written)
                    buf.extend(chunk)
                    if len(buf) >= flush_size:
                        data = bytes(buf)
                        buf.clear()
                        await asyncio.to_thread(f.write, data)
                if buf:
                    await asyncio.to_thread(f.write, bytes(buf))
            if written != size:
                raise ctx.error(400, 'upload_size', expected=size, got=written)
            if os.path.exists(target):
                raise ctx.error(409, 'upload_exists', name=name)
            os.replace(part, target)
        finally:
            if os.path.exists(part):
                os.remove(part)
        return {'path': target, 'size': written}

    @ctx.router.delete('/upload')
    async def delete_upload(path: str):
        upload, _ = await folders()
        folder = os.path.join(upload, 'ros1')
        real = os.path.realpath(path)
        if os.path.dirname(real) != folder or not real.endswith('.bag') or not os.path.isfile(real):
            raise ctx.error(400, 'not_uploaded')
        if any(j['state'] in ('queued', 'running') and j['params'].get('src') == real for j in ctx.jobs.list()):
            raise ctx.error(409, 'bag_in_use')
        os.remove(real)
        return {'ok': True}
