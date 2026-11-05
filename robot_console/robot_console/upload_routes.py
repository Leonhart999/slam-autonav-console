"""有界 multipart 上传入口；解析数据与处理导入分离，避免阻塞状态推送。"""
import asyncio
import math
import tempfile
import threading
from pathlib import Path

from fastapi import HTTPException, Request
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from .map_import import MAX_REQUEST, MIB, MapImportError, import_navigation
from .scene_import import SceneImportError


def register_import_routes(app, cfg, library, scenes, presets, procs):
    lock = threading.Lock()
    temp_root = scenes.root / '.uploads'
    temp_root.mkdir(parents=True, exist_ok=True)

    def error(exc, status=None):
        code = exc.code
        status = status or (409 if code == 'import_exists' else 413 if code == 'import_too_large' else 400)
        return HTTPException(status, detail={'code': code, 'params': exc.params})

    async def parse_form(request):
        """同时限制声明长度和实际字节数，不信任 Content-Length。"""
        length = request.headers.get('content-length')
        if length:
            try:
                size = int(length)
            except ValueError:
                raise MapImportError('import_invalid', reason='Invalid Content-Length')
            if size < 0 or size > MAX_REQUEST:
                raise MapImportError('import_too_large', limit=MAX_REQUEST // MIB)
        receive = request._receive
        consumed = 0
        exceeded = False

        async def limited_receive():
            nonlocal consumed, exceeded
            message = await receive()
            consumed += len(message.get('body', b''))
            if consumed > MAX_REQUEST:
                exceeded = True
                # 使用解析器自己的异常，确保它关闭已落盘的临时文件。
                raise MultiPartException('Upload too large')
            return message

        request._receive = limited_receive
        try:
            return await request.form(max_files=3, max_fields=8, max_part_size=65536)
        except (StarletteHTTPException, MultiPartException) as exc:
            if exceeded:
                raise MapImportError('import_too_large', limit=MAX_REQUEST // MIB) from exc
            raise MapImportError('import_invalid', reason=str(getattr(exc, 'detail', exc))) from exc

    def text(form, name, default=''):
        value = form.get(name, default)
        if not isinstance(value, str):
            raise MapImportError('import_invalid', reason=f'{name} must be a text field')
        return value.strip()

    def fields(form, allowed):
        keys = [k for k, _ in form.multi_items()]
        if len(keys) != len(set(keys)) or set(keys) - allowed:
            raise MapImportError('import_invalid', reason='Duplicate or unexpected form fields')

    async def copy_file(form, key, dest, suffixes, limit, required=True):
        upload = form.get(key)
        if upload is None and not required:
            return None, None
        if not isinstance(upload, UploadFile) or not upload.filename:
            raise MapImportError('import_invalid', reason=f'{key} is required')
        filename = upload.filename.replace('\\', '/').rsplit('/', 1)[-1]
        if Path(filename).suffix.lower() not in suffixes:
            raise MapImportError('import_invalid', reason=f'Unsupported {key} file type: {filename}')
        if upload.size is not None and upload.size > limit:
            raise MapImportError('import_too_large', limit=round(limit / MIB, 4))
        count = 0
        with open(dest, 'xb') as target:
            while chunk := await upload.read(MIB):
                count += len(chunk)
                if count > limit:
                    raise MapImportError('import_too_large', limit=round(limit / MIB, 4))
                await asyncio.to_thread(target.write, chunk)
        if not count:
            raise MapImportError('import_invalid', reason=f'{key} is empty')
        return dest, filename

    async def run_import(request, kind):
        if not lock.acquire(blocking=False):
            raise error(MapImportError('import_failed', reason='Another import is in progress; try again shortly'), 409)
        form = None
        try:
            if kind == 'navigation':
                s = procs.get((cfg.get('navigation') or {}).get('stack', 'navigation'))
                if s is not None and s.state() in ('running', 'stopping'):
                    raise MapImportError('import_invalid', reason='Stop navigation before importing another map')
            form = await parse_form(request)
            name = text(form, 'name')
            with tempfile.TemporaryDirectory(prefix='request-', dir=temp_root) as tmp:
                d = Path(tmp)
                if kind == 'scene':
                    fields(form, {'name', 'file', 'x', 'y', 'z', 'yaw'})
                    try:
                        spawn = [float(text(form, k, default)) for k, default in
                                 [('x', '0'), ('y', '0'), ('z', '0.1'), ('yaw', '0')]]
                    except ValueError:
                        raise MapImportError('import_invalid', reason='Spawn coordinates must be numbers')
                    if not all(math.isfinite(v) and abs(v) <= 1000000 for v in spawn):
                        raise MapImportError('import_invalid', reason='Spawn coordinates are not finite or out of range')
                    path, filename = await copy_file(form, 'file', d / 'scene.upload', {'.sdf', '.zip'}, 256 * MIB)
                    return await asyncio.to_thread(scenes.import_scene, name, path, filename, spawn)
                fields(form, {'name', 'yaml_file', 'image_file', 'cloud_file', 'preset'})
                preset = text(form, 'preset') or None
                if preset is not None and preset not in presets:
                    raise MapImportError('import_invalid', reason='The selected simulation scene does not exist')
                has_yaml = 'yaml_file' in form
                has_image = 'image_file' in form
                if has_yaml != has_image:
                    raise MapImportError('import_invalid', reason='Map YAML and image must be supplied together')
                if not has_yaml and 'cloud_file' not in form:
                    raise MapImportError('import_invalid', reason='Supply a map YAML/image pair or a PCD cloud')
                y, _ = await copy_file(form, 'yaml_file', d / 'map.upload', {'.yaml', '.yml'}, 65536, required=False)
                im, filename = await copy_file(form, 'image_file', d / 'image.upload', {'.pgm', '.png'}, 64 * MIB, required=False)
                cloud, _ = await copy_file(form, 'cloud_file', d / 'cloud.upload', {'.pcd'}, 256 * MIB, required=False)
                return await asyncio.to_thread(import_navigation, library.maps_dir, name, y, im, filename, cloud, preset)
        except (MapImportError, SceneImportError) as exc:
            raise error(exc)
        except Exception as exc:
            raise error(MapImportError('import_failed', reason=str(exc)), 500) from exc
        finally:
            if form is not None:
                await form.close()
            lock.release()

    @app.post('/api/mapping/import')
    async def import_scene(request: Request):
        return await run_import(request, 'scene')

    @app.post('/api/navigation/import')
    async def import_map(request: Request):
        return await run_import(request, 'navigation')

    @app.delete('/api/mapping/presets/{preset}')
    def delete_scene(preset: str):
        """Delete an uploaded scene (moved to the scenes trash). Refused while its simulation runs."""
        sim = procs.get(((cfg.get('mapping') or {}).get('stacks') or {}).get('sim', 'sim'))
        if sim is not None and sim.preset == preset and sim.state() in ('running', 'stopping'):
            raise HTTPException(409, detail={'code': 'scene_in_use', 'params': {'name': preset}})
        if not lock.acquire(blocking=False):
            raise error(MapImportError('import_failed', reason='An import is in progress; try again shortly'), 409)
        try:
            return {'name': preset, 'trash': scenes.delete_scene(preset)}
        except SceneImportError as exc:
            raise error(exc, 404 if exc.code == 'scene_not_custom' else 500)
        finally:
            lock.release()
