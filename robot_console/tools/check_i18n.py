#!/usr/bin/env python3
"""Static i18n check for robot_console (no dependencies, runs with the system python3).

Frontend: every literal key passed to t / $t / te / i18n.global.t / i18n.global.te (and to prefix helpers such as
`const k = (key: string) => t(`monitor.topicState.${key}`)`) must exist in both languages: in the global
locales (web/src/locales/zh-CN.ts, en.ts) or in the component's local messages (useI18n({ messages }) blocks in
the file itself or in a module it imports, e.g. evaluation/messages.ts). Local zh-CN and en blocks must have the
same keys. Template-literal keys with ${...} are dynamic and only listed.

Backend: error codes raised from robot_console/*.py (SomethingError('code'), error(status, 'code'),
detail={'code': 'code'}) must have error.<code> in both global locales, because web/src/messages.ts errorText()
translates error.<code>. RunFailed codes (batch.py, translated in BatchPanel.vue) are checked against
batch.fail.* instead.

Usage: python3 tools/check_i18n.py [--root DIR] [--part web|backend|all] [--unused]
Exit code 1 when a key or error code is missing; unused keys (--unused) are only a hint.
"""
import argparse
import re
import sys
from pathlib import Path

CALL_RE = re.compile(r'(?<![\w$.])(?:\$t|t|te|i18n\.global\.t|i18n\.global\.te)\(\s*([\'"`])((?:\\.|(?!\1).)*)\1')
HELPER_RE = re.compile(r'const\s+(\w+)\s*=\s*\(\s*\w+\s*(?::\s*string)?\s*\)\s*=>\s*t\(\s*`([^`$]*)\$\{\s*\w+\s*\}`\s*\)')
IMPORT_RE = re.compile(r'''import\s+[^'"]*?from\s+['"](@/[^'"]+|\.{1,2}/[^'"]+)['"]''')
LOCAL_ZH_RE = re.compile(r'''(?:['"]zh-CN['"]\s*:|const\s+zh\s*(?::\s*\w+)?\s*=)\s*\{''')
LOCAL_EN_RE = re.compile(r'''(?:(?<![\w.])en\s*:|const\s+en\s*(?::\s*\w+)?\s*=)\s*\{''')
BACKEND_RES = [
    re.compile(r'''\b(\w*Error)\(\s*['"]([a-z][a-z0-9_]*)['"]'''),                    # FooError('code', ...)
    re.compile(r'''(?<![\w.])error\(\s*[^,()]+,\s*['"]([a-z][a-z0-9_]*)['"]'''),     # error(409, 'code')
    re.compile(r'''detail\s*=\s*\{\s*['"]code['"]\s*:\s*['"]([a-z][a-z0-9_]*)['"]'''),  # HTTPException(detail=...)
]
IGNORED_ERRORS = {'ValueError', 'TypeError', 'KeyError', 'OSError', 'RuntimeError', 'FileNotFoundError',
                  'FileExistsError', 'PermissionError', 'TimeoutError', 'NotImplementedError', 'AssertionError',
                  'AttributeError', 'IndexError', 'Exception'}


# ---------------- TypeScript object literal -> flat keys ----------------

def _skip_string(s, i):
    q = s[i]
    i += 1
    while i < len(s):
        if s[i] == '\\':
            i += 2
            continue
        if s[i] == q:
            return i + 1
        i += 1
    return i


def _skip_ws(s, i):
    while i < len(s):
        if s[i].isspace():
            i += 1
        elif s.startswith('//', i):
            j = s.find('\n', i)
            i = len(s) if j < 0 else j + 1
        elif s.startswith('/*', i):
            j = s.find('*/', i)
            i = len(s) if j < 0 else j + 2
        else:
            break
    return i


def _skip_value(s, i):
    """Skip a non-object value up to the ',' or '}' that ends it."""
    depth = 0
    while i < len(s):
        c = s[i]
        if c in '\'"`':
            i = _skip_string(s, i)
            continue
        if s.startswith('//', i) or s.startswith('/*', i):
            i = _skip_ws(s, i)
            continue
        if c in '([{':
            depth += 1
        elif c in ')]}':
            if depth == 0:
                return i
            depth -= 1
        elif c == ',' and depth == 0:
            return i
        i += 1
    return i


def object_keys(s, i):
    """s[i] == '{'. Returns (set of dotted leaf keys, index after the closing brace)."""
    keys = set()
    i += 1
    while True:
        i = _skip_ws(s, i)
        if i >= len(s):
            return keys, i
        c = s[i]
        if c == '}':
            return keys, i + 1
        if c == ',':
            i += 1
            continue
        if s.startswith('...', i):
            i = _skip_value(s, i)
            continue
        if c in '\'"':
            j = _skip_string(s, i)
            key = s[i + 1:j - 1]
            i = j
        else:
            m = re.match(r'[A-Za-z_$][\w$]*', s[i:])
            if not m:
                i = _skip_value(s, i)
                continue
            key = m.group(0)
            i += len(key)
        i = _skip_ws(s, i)
        if i >= len(s) or s[i] != ':':          # shorthand property: not a message
            i = _skip_value(s, i)
            continue
        i = _skip_ws(s, i + 1)
        if i < len(s) and s[i] == '{':
            sub, i = object_keys(s, i)
            keys |= {f'{key}.{k}' for k in sub}
        else:
            keys.add(key)
            i = _skip_value(s, i)


def first_object(text, regex, start=0):
    m = regex.search(text, start)
    if not m:
        return None, -1
    return object_keys(text, m.end() - 1)


# ---------------- frontend ----------------

def resolve_import(src, here, spec):
    base = src / spec[2:] if spec.startswith('@/') else (here.parent / spec)
    for cand in (base, base.with_suffix('.ts'), base / 'index.ts'):
        if cand.is_file():
            return cand.resolve()
    return None


def local_blocks(text):
    """[(zh keys, en keys)] of the message blocks defined in one file (all zh-CN blocks and all en blocks of the
    file are merged; the order of 'zh-CN' and en inside a messages object does not matter)."""
    zh, en = set(), set()
    for rx, acc in ((LOCAL_ZH_RE, zh), (LOCAL_EN_RE, en)):
        for m in rx.finditer(text):
            keys, _ = object_keys(text, m.end() - 1)
            acc |= keys
    return [(zh, en)] if zh or en else []


def check_web(root, unused):
    src = root / 'web' / 'src'
    loc = src / 'locales'
    glob_zh, _ = first_object((loc / 'zh-CN.ts').read_text(encoding='utf-8'), re.compile(r'const\s+zhCN\s*=\s*\{'))
    glob_en, _ = first_object((loc / 'en.ts').read_text(encoding='utf-8'), re.compile(r'const\s+en\s*:\s*\w+\s*=\s*\{'))
    problems, dynamic, used = [], [], set()
    for k in sorted(glob_zh ^ glob_en):
        problems.append(f'locales: "{k}" only in {"zh-CN.ts" if k in glob_zh else "en.ts"}')

    files = sorted(p for p in src.rglob('*') if p.suffix in ('.ts', '.vue') and loc not in p.parents)
    blocks = {p.resolve(): local_blocks(p.read_text(encoding='utf-8')) for p in files}
    for p, bl in blocks.items():
        for zh, en in bl:
            for k in sorted(zh ^ en):
                problems.append(f'{p.relative_to(root)}: local message "{k}" only in {"zh-CN" if k in zh else "en"}')

    for p in files:
        text = p.read_text(encoding='utf-8')
        rel = p.relative_to(root)
        local_zh, local_en = set(), set()
        sources = [p.resolve()] + [r for r in (resolve_import(src, p, m.group(1)) for m in IMPORT_RE.finditer(text)) if r]
        for f in sources:
            for zh, en in blocks.get(f, []):
                local_zh |= zh
                local_en |= en
        keys = []
        for m in CALL_RE.finditer(text):
            q, key = m.group(1), m.group(2)
            line = text.count('\n', 0, m.start()) + 1
            if q == '`' and '${' in key:
                dynamic.append(f'{rel}:{line}: `{key}`')
                used.add(key.split('${')[0] + '*')
                continue
            keys.append((key, line))
        for h in HELPER_RE.finditer(text):
            name, prefix = h.group(1), h.group(2)
            for m in re.finditer(r'(?<![\w$.])' + re.escape(name) + r'''\(\s*(['"])([^'"]+)\1''', text):
                keys.append((prefix + m.group(2), text.count('\n', 0, m.start()) + 1))
        for key, line in keys:
            used.add(key)
            for lang, g, l in (('zh-CN', glob_zh, local_zh), ('en', glob_en, local_en)):
                if key not in g and key not in l:
                    problems.append(f'{rel}:{line}: "{key}" missing in {lang}')
        # For the unused-key hint only: keys kept in variables ('nav.mapping' in router.ts, `mapsPage.issue.${c}`)
        for m in re.finditer(r'''(['"])([a-z]\w*(?:\.\w+)+)\1''', text):
            used.add(m.group(2))
        for m in re.finditer(r'`([a-z]\w*(?:\.\w+)*\.)\$\{', text):
            used.add(m.group(1) + '*')

    hints = []
    if unused:
        prefixes = [u[:-1] for u in used if u.endswith('*')]
        for k in sorted(glob_zh):
            top = k.split('.')[0]
            if k in used or top == 'error' or top == 'result' or any(k.startswith(pf) for pf in prefixes):
                continue
            hints.append(k)
    return problems, dynamic, hints, glob_zh, glob_en


# ---------------- backend ----------------

def check_backend(root, glob_zh, glob_en, batch_fail):
    problems, codes, cls_of = [], {}, {}
    for p in sorted((root / 'robot_console').glob('*.py')):
        text = p.read_text(encoding='utf-8')
        for rx in BACKEND_RES:
            for m in rx.finditer(text):
                if m.lastindex == 2:
                    cls, code = m.group(1), m.group(2)
                    if cls in IGNORED_ERRORS:
                        continue
                else:
                    cls, code = 'error', m.group(1)
                line = text.count('\n', 0, m.start()) + 1
                codes.setdefault((cls == 'RunFailed', code), []).append(f'{p.relative_to(root)}:{line}')
                cls_of.setdefault(code, set()).add(cls)
    for (run_failed, code), where in sorted(codes.items()):
        if run_failed:
            for lang, keys in batch_fail.items():
                if f'batch.fail.{code}' not in keys:
                    problems.append(f'{where[0]}: RunFailed "{code}" missing batch.fail.{code} in {lang} (BatchPanel.vue)')
            continue
        for lang, keys in (('zh-CN', glob_zh), ('en', glob_en)):
            # SaveError raised while saving in the background is shown with resultText (result.<code>),
            # raised by the request itself with errorText (error.<code>); either translation is enough
            ok = f'error.{code}' in keys or (cls_of[code] == {'SaveError'} and f'result.{code}' in keys)
            if not ok:
                problems.append(f'{where[0]}: error code "{code}" missing error.{code} in {lang}')
    return problems, len(codes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument('--part', choices=('web', 'backend', 'all'), default='all')
    ap.add_argument('--unused', action='store_true')
    a = ap.parse_args()
    root = Path(a.root).resolve()
    problems, dynamic, hints, gzh, gen = check_web(root, a.unused)
    if a.part == 'backend':
        problems = []
    n_codes = 0
    if a.part in ('backend', 'all'):
        panel = root / 'web' / 'src' / 'components' / 'BatchPanel.vue'
        batch_fail = {'zh-CN': set(), 'en': set()}
        if panel.is_file():
            bl = local_blocks(panel.read_text(encoding='utf-8'))
            for zh, en in bl:
                batch_fail['zh-CN'] |= zh
                batch_fail['en'] |= en
        bp, n_codes = check_backend(root, gzh, gen, batch_fail)
        problems += bp
    for p in problems:
        print('MISSING', p)
    if a.part != 'backend':
        print(f'{len(dynamic)} dynamic keys skipped' + (':' if dynamic and a.unused else ''))
        if a.unused:
            for d in dynamic:
                print('  dynamic', d)
            print(f'{len(hints)} global keys look unused (hint only)')
            for h in hints:
                print('  unused', h)
    print(f'i18n check: {len(problems)} missing ({len(gzh)} global keys, {n_codes} backend codes)')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
