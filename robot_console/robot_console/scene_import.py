"""安全导入静态 Gazebo 场景；不执行上传内容，不保留上传的运行逻辑。

支持 UTF-8 SDF、ZIP 包、基础几何、STL 和 PNG/JPEG PBR 贴图。
DAE/OBJ/MTL、材质脚本、外部模型、相对坐标系和动态行为不受支持。
包内必须恰好有一个 world；include 只可指向包内单个 model。
"""

import copy
import ctypes
import errno
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib


NAME_RE = re.compile(r'[A-Za-z0-9_-]{1,48}\Z')
MAX_FILES = 512
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_UPLOAD_BYTES = 256 * 1024 * 1024
MAX_XML_BYTES = 8 * 1024 * 1024
MAX_NODES = 50000
MAX_DEPTH = 32
MAX_TRIANGLES = 500000
SUPPORTED_FILES = {'.sdf', '.config', '.stl', '.png', '.jpg', '.jpeg'}
IMAGE_FILES = {'.png', '.jpg', '.jpeg'}
STRIPPED = {
    'plugin', 'sensor', 'actor', 'gui', 'script', 'audio', 'projector',
    'particle_emitter', 'animation', 'trajectory', 'executable',
}
# 仅保留静态几何；关节、动力学、接触参数不参与导入。
IGNORED = STRIPPED | {
    'static', 'joint', 'inertial', 'self_collide', 'allow_auto_disable',
    'enable_wind', 'gravity', 'kinematic', 'velocity_decay', 'surface',
    'cast_shadows', 'transparency', 'laser_retro', 'visibility_flags',
}


class SceneImportError(Exception):
    """code 和 params 交给接口翻译；reason 是可直接展示的英文说明。"""

    def __init__(self, code, **params):
        super().__init__(code)
        self.code, self.params = code, params


def _invalid(reason):
    raise SceneImportError('import_invalid', reason=str(reason))


def _spawn(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        _invalid('Spawn must be [x, y, z, yaw], with yaw in radians')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value):
        _invalid('Spawn values must be finite numbers')
    try:
        result = [float(v) for v in value]
    except (OverflowError, ValueError):
        _invalid('Spawn values must be finite numbers')
    if not all(math.isfinite(v) for v in result):
        _invalid('Spawn values must be finite numbers')
    return result


def _public(name, spawn):
    return {
        'name': 'custom_' + name,
        'label': {'zh': name, 'en': name},
        'description': {
            'zh': '自定义静态场景；动态行为和上传插件已移除；不支持内置航点',
            'en': 'Custom static scene; dynamics and uploaded plugins removed; no built-in waypoints',
        },
        'custom': True,
        'waypoints': False,
        'spawn': list(spawn),
    }


def _xml(path):
    if path.stat().st_size > MAX_XML_BYTES:
        _invalid('SDF/config exceeds the 8 MiB XML limit')
    text = path.read_bytes().decode('utf-8-sig')
    if re.search(r'<!\s*(DOCTYPE|ENTITY)\b', text, re.I):
        _invalid('XML DTDs and entities are not allowed')
    root = ET.fromstring(text)
    pending = [(root, 0)]
    count = 0
    while pending:
        element, depth = pending.pop()
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            _invalid('XML structure exceeds the node/depth limit')
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', element.tag):
            _invalid('XML namespaces and extension elements are not supported')
        pending.extend((child, depth + 1) for child in element)
    return root


def _sdf(path):
    root = _xml(path)
    if root.tag != 'sdf' or set(root.attrib) != {'version'}:
        _invalid('Expected an SDF document with a version attribute')
    if root.get('version') not in {'1.6', '1.7', '1.8', '1.9'}:
        _invalid('Only SDF versions 1.6 through 1.9 are supported')
    # 剥离整个子树，而不是只删除顶层 plugin。
    for parent in root.iter():
        for child in list(parent):
            if child.tag in STRIPPED:
                parent.remove(child)
    return root


def _path_text(value, *, traversal=False):
    value = (value or '').strip()
    if (not value or len(value) > 240 or value.startswith(('/', '~'))
            or any(c in value for c in '\\:%?#$')
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        _invalid('Resource paths must be local package-relative paths')
    parts = value.split('/')
    if (len(parts) > 16 or any(not p or len(p) > 100 for p in parts)
            or (not traversal and any(p in {'.', '..'} for p in parts))):
        _invalid('Invalid or excessive resource path')
    return value


def _inside(path, root):
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        _invalid('Resource path escapes the scene package')
    # 上传解压不会产生符号链接；重载时也不允许它们。
    cursor = path
    while cursor != root:
        if cursor.is_symlink():
            _invalid('Symbolic links are not allowed in scene resources')
        if cursor.parent == cursor:
            _invalid('Resource path escapes the scene package')
        cursor = cursor.parent
    return resolved


def _numbers(text, count, *, positive=False):
    try:
        values = [float(v) for v in (text or '').split()]
    except ValueError:
        _invalid('Geometry and poses require numeric values')
    if (len(values) != count or not all(math.isfinite(v) for v in values)
            or (positive and any(v <= 0 for v in values))):
        _invalid('Invalid geometry dimensions or pose values')
    return ' '.join(format(v, '.17g') for v in values)


def _one(parent, tag, *, required=False):
    values = parent.findall(tag)
    if len(values) > 1 or (required and not values):
        _invalid('Expected exactly one ' + tag + ' element')
    return values[0] if values else None


def _children(element, allowed):
    for child in element:
        if child.tag not in allowed and child.tag not in IGNORED:
            _invalid('Unsupported static scene element: ' + element.tag + '/' + child.tag)


def _name(element):
    name = element.get('name', '')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,127}', name):
        _invalid('Model/link/visual/collision names must be simple ASCII identifiers')
    if set(element.attrib) != {'name'}:
        _invalid('Unsupported attributes on ' + element.tag)
    return name


def _pose(source, target):
    pose = _one(source, 'pose')
    if pose is None:
        return
    if list(pose) or any(k != 'relative_to' or v for k, v in pose.attrib.items()):
        _invalid('Only six-value Euler poses in the default parent frame are supported')
    ET.SubElement(target, 'pose').text = _numbers(pose.text, 6)


def _numeric(parent, target, tag, count, *, required=False, positive=False):
    source = _one(parent, tag, required=required)
    if source is not None:
        if source.attrib or list(source):
            _invalid('Unsupported attributes or children on ' + tag)
        ET.SubElement(target, tag).text = _numbers(source.text, count, positive=positive)


def _stl_lines(stream):
    while True:
        line = stream.readline(512)
        if not line:
            return
        if len(line) == 512:
            _invalid('ASCII STL lines must be shorter than 512 characters')
        if line.strip():
            yield line.strip()


def _validate_stl(path):
    with path.open('rb') as stream:
        header = stream.read(84)
    if len(header) == 84:
        triangles = struct.unpack_from('<I', header, 80)[0]
        if path.stat().st_size == 84 + 50 * triangles:
            if not 0 < triangles <= MAX_TRIANGLES:
                _invalid('STL exceeds the triangle limit or is empty')
            # 二进制 STL 无任何文件引用；忽略不会被解释为路径的头部文本。
            return
    try:
        # 流式读取，避免恶意多行文件导致 splitlines 的内存放大。
        with path.open(encoding='ascii') as stream:
            lines = _stl_lines(stream)
            if not next(lines).startswith('solid'):
                raise ValueError()
            triangles = 0
            for line in lines:
                if line.startswith('endsolid'):
                    if next(lines, None) is not None or not triangles:
                        raise ValueError()
                    return
                if not line.startswith('facet normal '):
                    raise ValueError()
                _numbers(line[len('facet normal '):], 3)
                if next(lines) != 'outer loop':
                    raise ValueError()
                for _ in range(3):
                    vertex = next(lines)
                    if not vertex.startswith('vertex '):
                        raise ValueError()
                    _numbers(vertex[len('vertex '):], 3)
                if next(lines) != 'endloop' or next(lines) != 'endfacet':
                    raise ValueError()
                triangles += 1
                if triangles > MAX_TRIANGLES:
                    _invalid('STL exceeds the triangle limit')
    except (UnicodeError, ValueError, StopIteration):
        pass
    _invalid('Only valid binary or ASCII STL meshes are supported; DAE/OBJ/MTL are not supported')


def _validate_image(path):
    # 只允许真正的栅格图片，避免用扩展名伪装其他资源格式。
    with path.open('rb') as stream:
        header = stream.read(24)
        if path.suffix.lower() == '.png':
            if header[:16] != b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' or len(header) != 24:
                _invalid('Invalid PNG texture')
            width, height = struct.unpack('>II', header[16:24])
        else:
            if header[:2] != b'\xff\xd8':
                _invalid('Invalid JPEG texture')
            stream.seek(2)
            width = height = 0
            for _ in range(4096):
                if stream.read(1) != b'\xff':
                    break
                marker = stream.read(1)
                while marker == b'\xff':
                    marker = stream.read(1)
                if not marker or marker in {b'\xd9', b'\xda'}:
                    break
                if marker in {b'\x01', b'\xd8'} or 0xd0 <= marker[0] <= 0xd7:
                    continue
                raw = stream.read(2)
                if len(raw) != 2:
                    break
                size = struct.unpack('>H', raw)[0]
                if size < 2:
                    break
                if marker[0] in {0xc0, 0xc1, 0xc2}:
                    frame = stream.read(5)
                    if len(frame) == 5:
                        height, width = struct.unpack('>HH', frame[1:])
                    break
                stream.seek(size - 2, 1)
        if not (0 < width <= 8192 and 0 < height <= 8192 and width * height <= 16777216):
            _invalid('Textures must be PNG or baseline/progressive JPEG, at most 16 megapixels and 8192 pixels per side')


def _template():
    # 只从安装包（或源码树的兄弟包）读模板，不搜索上传包和当前目录。
    try:
        from ament_index_python.packages import get_package_share_directory
    except ImportError:
        package = Path(__file__).resolve().parents[2] / 'sim_robot'
    else:
        try:
            package = Path(get_package_share_directory('sim_robot'))
        except LookupError:
            _invalid('Trusted sim_robot package is not installed')
    root = _xml(package / 'worlds' / 'indoor_office.sdf')
    world = root.find('world')
    if world is None or not world.findall('plugin'):
        _invalid('Trusted sim_robot world template is unavailable')
    robot = [e for e in world.findall('include') if e.findtext('uri') == 'model://sim_robot']
    if len(robot) != 1:
        _invalid('Trusted sim_robot include is unavailable')
    return world, robot[0]


class _StaticWorld:
    """白名单重建，而不是把清洗不彻底的上传 XML 交给 Gazebo。"""

    def __init__(self, package, output):
        self.package = package
        self.output = output
        self.remaining = MAX_NODES
        self.assets = set()
        self.asset_bytes = 0
        self.documents = {}

    def _resolve(self, value, source):
        value = (value or '').strip()
        if value.startswith('model://'):
            tail = _path_text(value[len('model://'):])
            # 同时兼容包根目录、models/ 和 ZIP 的单层/多层外包装目录。
            candidates = set()
            directory = source.parent
            while True:
                for base in (directory, directory / 'models'):
                    candidate = base / tail
                    if candidate.exists():
                        candidates.add(_inside(candidate, self.package))
                if directory == self.package:
                    break
                directory = directory.parent
            if len(candidates) != 1:
                _invalid('model:// must identify exactly one model/resource inside the package: ' + value)
            return candidates.pop()
        value = _path_text(value, traversal=True)
        path = _inside(source.parent / value, self.package)
        if not path.exists():
            _invalid('Missing package resource: ' + value)
        return path

    def _asset(self, value, source, suffixes):
        path = self._resolve(value, source)
        if not path.is_file() or path.suffix.lower() not in suffixes:
            _invalid('Unsupported resource format; meshes must be STL and textures PNG/JPEG: ' + str(value))
        if path.stat().st_size > MAX_FILE_BYTES:
            _invalid('Resource exceeds the 64 MiB file limit')
        if path not in self.assets:
            self.asset_bytes += path.stat().st_size
            if len(self.assets) >= MAX_FILES or self.asset_bytes > MAX_TOTAL_BYTES:
                _invalid('Scene resources exceed the file-count or 256 MiB total limit')
            if path.suffix.lower() == '.stl':
                _validate_stl(path)
            else:
                _validate_image(path)
            self.assets.add(path)
        return path.relative_to(self.output).as_posix()

    def _geometry(self, source, target, path):
        geometry = _one(source, 'geometry', required=True)
        if geometry.attrib or len(geometry) != 1:
            _invalid('Geometry must contain one supported shape')
        shape = geometry[0]
        if shape.attrib:
            _invalid('Shape attributes are not supported')
        out = ET.SubElement(ET.SubElement(target, 'geometry'), shape.tag)
        dimensions = {
            'box': [('size', 3)], 'sphere': [('radius', 1)],
            'cylinder': [('radius', 1), ('length', 1)],
            'capsule': [('radius', 1), ('length', 1)],
            'ellipsoid': [('radii', 3)], 'plane': [('normal', 3), ('size', 2)],
        }
        if shape.tag in dimensions:
            fields = dimensions[shape.tag]
            _children(shape, {tag for tag, _ in fields})
            for tag, count in fields:
                _numeric(shape, out, tag, count, required=True, positive=(tag != 'normal'))
            if shape.tag == 'plane' and not any(float(v) for v in out.findtext('normal').split()):
                _invalid('Plane normal must be nonzero')
        elif shape.tag == 'mesh':
            _children(shape, {'uri', 'scale'})
            uri = _one(shape, 'uri', required=True)
            if uri.attrib or list(uri):
                _invalid('Unsupported mesh URI attributes or children')
            ET.SubElement(out, 'uri').text = self._asset(uri.text, path, {'.stl'})
            _numeric(shape, out, 'scale', 3, positive=True)
        else:
            _invalid('Unsupported geometry: ' + shape.tag + '; use primitives or STL meshes')

    def _material(self, source, target, path):
        material = _one(source, 'material')
        if material is None:
            return
        if material.attrib:
            _invalid('Material attributes are not supported')
        _children(material, {'ambient', 'diffuse', 'specular', 'emissive', 'pbr', 'lighting'})
        out = ET.SubElement(target, 'material')
        for tag in ('ambient', 'diffuse', 'specular', 'emissive'):
            _numeric(material, out, tag, 4)
        pbr = _one(material, 'pbr')
        if pbr is None:
            return
        if pbr.attrib or len(pbr) != 1 or pbr[0].tag != 'metal':
            _invalid('Only the metal PBR workflow is supported')
        metal = pbr[0]
        if metal.attrib:
            _invalid('PBR attributes are not supported')
        maps = {'albedo_map', 'normal_map', 'roughness_map', 'metalness_map', 'emissive_map'}
        _children(metal, maps | {'roughness', 'metalness'})
        workflow = ET.SubElement(ET.SubElement(out, 'pbr'), 'metal')
        for tag in ('roughness', 'metalness'):
            _numeric(metal, workflow, tag, 1)
        for tag in sorted(maps):
            image = _one(metal, tag)
            if image is not None:
                if image.attrib or list(image):
                    _invalid('Texture attributes and children are not supported')
                ET.SubElement(workflow, tag).text = self._asset(image.text, path, IMAGE_FILES)

    def _include(self, element, source, stack, depth):
        uri = _one(element, 'uri', required=True)
        value = (uri.text or '').strip()
        override = _one(element, 'name')
        if ((override is not None and (override.text or '').strip() == 'sim_robot')
                or value.rstrip('/') in {'model://sim_robot', 'model://sim_robot/model.sdf'}):
            return None
        if element.attrib:
            _invalid('Include attributes, including merge, are not supported')
        _children(element, {'uri', 'name', 'pose', 'static'})
        if uri.attrib or list(uri):
            _invalid('Unsupported include URI attributes or children')
        path = self._resolve(value, source)
        if path.is_dir():
            config = path / 'model.config'
            if config.exists():
                config_root = _xml(_inside(config, self.package))
                entries = config_root.findall('sdf')
                if config_root.tag != 'model' or len(entries) != 1:
                    _invalid('model.config must select exactly one SDF file')
                relative = _path_text(entries[0].text, traversal=True)
                selected = _inside(path / relative, self.package)
                if not selected.is_relative_to(path):
                    _invalid('model.config SDF must stay inside its model directory')
                path = selected
            else:
                path = _inside(path / 'model.sdf', self.package)
        if path.suffix.lower() != '.sdf' or not path.is_file():
            _invalid('Include must resolve to a package-local SDF model')
        if path in stack or len(stack) >= 16:
            _invalid('Cyclic or excessive model include dependency')
        if path not in self.documents:
            self.documents[path] = _sdf(path)
        root = self.documents[path]
        if len(root) != 1 or root[0].tag != 'model':
            _invalid('Included SDF must contain exactly one model')
        model = self._entity(root[0], path, stack + (path,), depth + 1)
        if model is None:
            return None
        if override is not None:
            if override.attrib or list(override):
                _invalid('Unsupported include name attributes or children')
            model.set('name', (override.text or '').strip())
            _name(model)
        if _one(element, 'pose') is not None:
            previous = model.find('pose')
            if previous is not None:
                model.remove(previous)
            _pose(element, model)
            pose = model.find('pose')
            model.remove(pose)
            model.insert(0, pose)
        return model

    def _entity(self, element, path, stack, depth=0):
        self.remaining -= 1
        if self.remaining < 0 or depth > MAX_DEPTH:
            _invalid('Expanded static scene exceeds the node/depth limit')
        if element.tag == 'include':
            return self._include(element, path, stack, depth)
        name = _name(element)
        if element.tag == 'model' and name == 'sim_robot':
            return None
        allowed = {
            'model': {'pose', 'link', 'model', 'include', 'static'},
            'link': {'pose', 'visual', 'collision'},
            'visual': {'pose', 'geometry', 'material'},
            'collision': {'pose', 'geometry'},
        }[element.tag]
        _children(element, allowed)
        out = ET.Element(element.tag, {'name': name})
        _pose(element, out)
        if element.tag == 'model':
            ET.SubElement(out, 'static').text = 'true'
        if element.tag in {'visual', 'collision'}:
            self._geometry(element, out, path)
            if element.tag == 'visual':
                self._material(element, out, path)
            self.remaining -= sum(1 for _ in out.iter()) - 1
            if self.remaining < 0:
                _invalid('Expanded static scene exceeds the node limit')
        else:
            names = set()
            for child in element:
                if child.tag in {'model', 'include', 'link', 'visual', 'collision'}:
                    built = self._entity(child, path, stack, depth + 1)
                    if built is not None:
                        key = (built.tag if built.tag in {'visual', 'collision'} else 'entity', built.get('name'))
                        if key in names:
                            _invalid('Duplicate entity name: ' + built.get('name'))
                        names.add(key)
                        out.append(built)
        return out

    def build(self, source, spawn):
        root = _sdf(source)
        if len(root) != 1 or root[0].tag != 'world':
            _invalid('Scene SDF must contain exactly one world')
        trusted, robot = _template()
        result = ET.Element('sdf', {'version': '1.8'})
        world = ET.SubElement(result, 'world', {'name': 'imported_scene'})
        for child in trusted:
            if child.tag in {'physics', 'plugin', 'light'}:
                world.append(copy.deepcopy(child))
        names = set()
        for element in root[0]:
            # 世界级配置全部丢弃；物理、光源和系统插件只来自可信模板。
            if element.tag in {'model', 'include'}:
                built = self._entity(element, source, (source,))
                if built is not None:
                    if built.get('name') in names:
                        _invalid('Duplicate world model name: ' + built.get('name'))
                    names.add(built.get('name'))
                    world.append(built)
        if not any(e.tag == 'geometry' for e in world.iter()):
            _invalid('The scene must contain supported static geometry')
        robot = copy.deepcopy(robot)
        for pose in list(robot.findall('pose')):
            robot.remove(pose)
        x, y, z, yaw = spawn
        ET.SubElement(robot, 'pose').text = _numbers(f'{x} {y} {z} 0 0 {yaw}', 6)
        world.append(robot)
        # 限制包括几何及材质字段在内的最终结构，保证重载也接受此输出。
        pending = [(result, 0)]
        count = 0
        while pending:
            element, depth = pending.pop()
            count += 1
            if count > MAX_NODES or depth > MAX_DEPTH:
                _invalid('Expanded static scene exceeds the node/depth limit')
            pending.extend((child, depth + 1) for child in element)
        return result


def _extract(source, destination):
    # 在 ZipFile 分配整个中央目录之前限制条目数量与目录大小；不支持 ZIP64/分卷。
    with source.open('rb') as stream:
        if stream.read(4) != b'PK\x03\x04':
            _invalid('Expected a standard ZIP resource package, not an executable archive')
        size = stream.seek(0, os.SEEK_END)
        stream.seek(max(0, size - 65557))
        tail = stream.read(65557)
    offset = tail.rfind(b'PK\x05\x06')
    if offset < 0 or len(tail) - offset < 22:
        _invalid('Invalid ZIP end record')
    _, disk, start_disk, entries, total, directory_size, directory_offset, comment = struct.unpack_from('<4s4H2IH', tail, offset)
    if (disk or start_disk or entries != total or not 1 <= total <= MAX_FILES
            or directory_size > 2 * 1024 * 1024 or offset + 22 + comment != len(tail)
            or directory_offset + directory_size != size - len(tail) + offset):
        _invalid('ZIP must be a single-volume, non-ZIP64 archive with at most 512 entries and a 2 MiB central directory')
    with zipfile.ZipFile(source) as archive:
        members = archive.infolist()
        if not members or len(members) > MAX_FILES:
            _invalid('ZIP must contain between 1 and 512 entries')
        total = 0
        seen = set()
        for member in members:
            original = member.orig_filename
            if original != member.filename or '\x00' in original:
                _invalid('Invalid ZIP member name')
            relative = _path_text(original.rstrip('/'))
            key = relative.casefold()
            if key in seen:
                _invalid('Duplicate or case-colliding ZIP paths')
            seen.add(key)
            mode = member.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
                _invalid('ZIP links and special files are not allowed')
            if member.flag_bits & 1 or member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                _invalid('Encrypted ZIPs and nonstandard ZIP compression are not supported')
            target = destination.joinpath(*PurePosixPath(relative).parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if kind == stat.S_IFDIR:
                _invalid('Inconsistent ZIP directory entry')
            if target.suffix.lower() not in SUPPORTED_FILES:
                _invalid('Unsupported ZIP resource; allowed: SDF/config, STL, PNG/JPG/JPEG (not DAE/OBJ/MTL)')
            total += member.file_size
            if member.file_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                _invalid('ZIP exceeds the 64 MiB per-file or 256 MiB unpacked limit')
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as incoming, target.open('xb') as outgoing:
                copied = 0
                while True:
                    chunk = incoming.read(1024 * 1024)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > member.file_size or copied > MAX_FILE_BYTES:
                        _invalid('ZIP member exceeds its declared size')
                    outgoing.write(chunk)
                if copied != member.file_size:
                    _invalid('Truncated ZIP member')


def _publish(staging, destination):
    # Linux 的普通 rename 会覆盖空目录；RENAME_NOREPLACE 才能保证并发不覆盖。
    if os.name == 'nt':
        os.rename(staging, destination)
        return
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, 'renameat2', None)
    if rename is None:
        _invalid('Atomic no-replace directory rename is not available on this platform')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(staging), -100, os.fsencode(destination), 1):
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), str(destination))


class SceneLibrary:
    def __init__(self, root, builtin_presets):
        self.root = Path(root).expanduser().resolve()
        self.presets = builtin_presets
        self._lock = threading.RLock()

    def _register(self, public, directory):
        internal = copy.deepcopy(public)
        internal['args'] = {'sim': ['world:=' + str(directory / 'world.sdf')]}
        self.presets[public['name']] = internal

    def load(self):
        """向原来的 mutable dict 添加合法记录；不信任磁盘上的命令参数或 XML。"""
        with self._lock:
            if not self.root.is_dir():
                return
            for directory in sorted(self.root.iterdir()):
                name = directory.name
                if (not NAME_RE.fullmatch(name) or directory.is_symlink() or not directory.is_dir()
                        or 'custom_' + name in self.presets):
                    continue
                try:
                    metadata = _inside(directory / 'preset.json', directory)
                    if metadata.stat().st_size > 16384:
                        continue
                    saved = json.loads(metadata.read_text(encoding='utf-8'))
                    if not isinstance(saved, dict):
                        continue
                    public = _public(name, _spawn(saved.get('spawn')))
                    if saved != public or saved.get('custom') is not True or saved.get('waypoints') is not False:
                        continue
                    world = _inside(directory / 'world.sdf', directory)
                    original = _xml(world)
                    rebuilt = _StaticWorld(directory, directory).build(world, public['spawn'])
                    # 重新构建后必须完全相同，否则可能是被篡改的插件/include/资源引用。
                    if ET.tostring(original) != ET.tostring(rebuilt):
                        continue
                    self._register(public, directory)
                except (SceneImportError, OSError, ValueError, TypeError, OverflowError,
                        ET.ParseError, RuntimeError):
                    continue

    def delete_scene(self, preset):
        """Remove an uploaded scene: its folder moves to <root>/.trash/<name>_<time> (restorable by hand) and the
        preset disappears from the list. Built-in scenes cannot be deleted. Returns the trash folder."""
        name = preset[len('custom_'):] if isinstance(preset, str) and preset.startswith('custom_') else None
        if name is None or not NAME_RE.fullmatch(name):
            raise SceneImportError('scene_not_custom', name=str(preset))
        with self._lock:
            entry = self.presets.get(preset)
            if entry is None or entry.get('custom') is not True:
                raise SceneImportError('scene_not_custom', name=preset)
            source = self.root / name
            trash = self.root / '.trash'
            trash.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime('%Y%m%d_%H%M%S')
            target, k = trash / f'{name}_{stamp}', 2
            while target.exists():
                target, k = trash / f'{name}_{stamp}_{k}', k + 1
            try:
                if source.is_dir() and not source.is_symlink():
                    os.rename(source, target)
            except OSError as exc:
                raise SceneImportError('scene_delete_failed', reason=str(exc)) from exc
            del self.presets[preset]
            return str(target)

    def import_scene(self, name, source_path, filename, spawn):
        """同步完成验证、静态化和原子发布；只返回不含 args 的 public preset。"""
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise SceneImportError('import_name_invalid', name=name)
        staging = None
        with self._lock:
            try:
                destination = self.root / name
                if os.path.lexists(destination) or 'custom_' + name in self.presets:
                    raise SceneImportError('import_exists', name=name)
                spawn = _spawn(spawn)
                if (not isinstance(filename, str) or not filename or len(filename) > 240
                        or any(c in filename for c in '/\\\x00')
                        or Path(filename).suffix.lower() not in {'.sdf', '.zip'}):
                    _invalid('Upload a .sdf file or .zip scene package')
                source = Path(source_path)
                if source.is_symlink() or not stat.S_ISREG(source.stat().st_mode):
                    _invalid('Upload source must be a regular file, not a symbolic link')
                limit = MAX_XML_BYTES if filename.lower().endswith('.sdf') else MAX_UPLOAD_BYTES
                if source.stat().st_size > limit:
                    _invalid('Upload exceeds the 8 MiB SDF or 256 MiB ZIP limit')
                self.root.mkdir(parents=True, exist_ok=True)
                staging = Path(tempfile.mkdtemp(prefix='.import-', dir=self.root))
                package = staging / 'assets'
                package.mkdir()
                if filename.lower().endswith('.zip'):
                    _extract(source, package)
                    worlds = []
                    for path in sorted(package.rglob('*')):
                        if path.is_file() and path.suffix.lower() == '.sdf':
                            document = _sdf(path)
                            if document.find('world') is not None:
                                worlds.append(path)
                    if len(worlds) != 1:
                        _invalid('ZIP must contain exactly one world SDF')
                    entry = worlds[0]
                else:
                    entry = package / 'source.sdf'
                    # 不读上传文件旁边的目录；单文件 SDF 必须自包含。
                    with source.open('rb') as incoming, entry.open('xb') as outgoing:
                        data = incoming.read(MAX_XML_BYTES + 1)
                        if len(data) > MAX_XML_BYTES:
                            _invalid('SDF exceeds the 8 MiB limit')
                        outgoing.write(data)
                builder = _StaticWorld(package, staging)
                world = builder.build(entry, spawn)
                serialized = ET.tostring(world, encoding='utf-8', xml_declaration=True)
                if len(serialized) > MAX_XML_BYTES:
                    _invalid('Generated world exceeds the 8 MiB SDF limit')
                (staging / 'world.sdf').write_bytes(serialized)
                # 不持久化原始 SDF/config 等不再使用的上传内容，库内只留下已验证资源。
                for path in sorted(package.rglob('*'), reverse=True):
                    if path.is_file() and path not in builder.assets:
                        path.unlink()
                    elif path.is_dir() and not any(path.iterdir()):
                        path.rmdir()
                public = _public(name, spawn)
                (staging / 'preset.json').write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding='utf-8')
                try:
                    _publish(staging, destination)
                except OSError as exc:
                    if exc.errno in {errno.EEXIST, errno.ENOTEMPTY}:
                        raise SceneImportError('import_exists', name=name) from exc
                    raise
                staging = None
                self._register(public, destination)
                return public
            except SceneImportError:
                raise
            except (OSError, ValueError, TypeError, OverflowError, ET.ParseError,
                    zipfile.BadZipFile, zlib.error, EOFError, RuntimeError) as exc:
                raise SceneImportError('import_invalid', reason=str(exc) or type(exc).__name__) from exc
            finally:
                # 绝不删除 destination，也不扫描/清理其他请求的 staging。
                if staging is not None:
                    shutil.rmtree(staging, ignore_errors=True)
