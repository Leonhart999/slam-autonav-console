# Plugin Development Rules & UI Design Guidelines

This document specifies the engineering rules, security boundaries, UI design standards and verification checks for plugins targeting **Plugin Interface v1** (`api: 1`). It complements the API reference in [`plugins.md`](plugins.md) and applies to both shipped plugins (`plugins/<id>/`) and external market plugins (`robot_console_plugin/<id>/`).

Rules are marked **MUST** / **MUST NOT** (enforced by code, tests or review) and **SHOULD** (recommended practice).

---

## 1. Layout & Manifest (`plugin.yaml`)

### 1.1 Folder layout

Each plugin lives in a directory whose name equals `plugin.yaml` `id`:

```text
<id>/
  plugin.yaml          # required manifest
  backend.py           # optional backend (or backend/__init__.py); must expose setup(ctx)
  worker.py            # recommended child-process worker for heavy computation / venv packages
  ui/
    plugin.js          # optional pre-built IIFE frontend bundle (committed/packaged; no Node.js needed at runtime)
    plugin.css         # optional pre-built stylesheet
  web/                 # optional frontend source (excluded from market zips: node_modules, .vite, dist)
```

Folders whose names begin with `_` (such as `plugins/_shared`) are ignored by the scanner.

### 1.2 Manifest rules

`robot_console/plugins.py` validates every field strictly; invalid manifests are marked `invalid` and never loaded:

| Field | Rule |
|---|---|
| `id` | **MUST** match `^[a-z][a-z0-9_]{1,39}$` (2–40 chars) and equal the folder name |
| `api` | **MUST** be integer `1` (`SUPPORTED_APIS = (1,)`) |
| `version` | **MUST** be a non-empty string (<= 40 chars); SemVer (e.g. `0.1.0`) is **SHOULD** |
| `name`, `description` | **MUST** provide both Chinese and English (`{zh: ..., en: ...}`) |
| `author`, `license` | **SHOULD** be set; `license` is **MUST** for market plugins |
| `homepage`, `upstream` | `http://` or `https://` only; `upstream` is **MUST** when wrapping an upstream open-source project |
| `backend.module` | **MUST** match `^[A-Za-z_][A-Za-z0-9_]*$` and define `setup(ctx)` |
| `frontend` | `dir` (default `ui`), `script` (`plugin.js`), optional `style` (`plugin.css`); **MUST NOT** contain absolute paths, `..` or `\` |
| `python.requirements` | Optional, 1–40 entries; **MUST** be plain `package[extras]` + version specifiers (no URLs, paths, markers or pip options) |
| `settings` | Optional; `key` matches `^[a-z][a-z0-9_]{0,39}$`; `type` in `string`, `text`, `number`, `integer`, `boolean`, `select`, `secret` |

Notes:
- YAML 1.1 treats unquoted `on`, `off`, `yes`, `no` as booleans; quote them or avoid them as setting keys and select option values.
- `secret` settings **MUST NOT** specify `default`. Maximum string lengths are `string: 1000`, `text: 20000`, `secret: 4000`.

---

## 2. Security, Isolation & Architecture Boundaries

1. **Zero-trust discovery**: Scanning reads `plugin.yaml` only and executes no plugin code. Newly discovered or installed plugins start disabled and require user opt-in plus console restart. Plugins **MUST NOT** attempt to bypass this lifecycle.
2. **Public REST API only**: Backend code **MUST** read and modify console state exclusively through `ctx.api` (which blocks `/api/plugins*`). Plugins **MUST NOT** import internal `robot_console.*` modules.
3. **Process isolation for third-party Python packages**:
   - Third-party libraries **MUST** be declared in `python.requirements` so the console installs them into `${data_root}/plugin_envs/<id>`.
   - Backend code running inside the console process **MUST NOT** import packages from the plugin venv. Execute them in child processes via `ctx.jobs` or `ctx.run_python` to avoid ROS/NumPy dependency conflicts and keep copyleft packages out of the console process.
4. **No hardcoded secrets or machine paths**:
   - Credentials **MUST** use `type: secret` settings, read via `ctx.settings()` on the backend and **MUST NOT** be returned in HTTP responses or logged.
   - Plugins **MUST NOT** hardcode machine-specific paths; persist plugin state and files under `ctx.data_dir` (`${data_root}/plugin_data/<id>`).
5. **Non-destructive outputs**:
   - Tools producing a modified map **MUST** call `POST /api/maps/{name}/derive` to create a new derived version and **MUST NOT** overwrite source map files.
   - Destructive actions (deleting reference files, records, etc.) **MUST** require explicit confirmation (`ElMessageBox.confirm` or `ElPopconfirm`).
6. **Fault isolation & cleanup**: Exceptions in `setup()` or routes must stay local to the plugin. Background threads and handles **MUST** be released in `ctx.on_shutdown(fn)`.

---

## 3. Backend Rules (`setup(ctx)` & Worker Protocol)

### 3.1 Routes, settings, errors and logging

- Register HTTP endpoints on `ctx.router` (mounted at `/api/ext/<id>/...`).
- Call `ctx.settings()` per request so setting updates apply without restarting the console.
- **Structured errors**: Raise `ctx.error(status, code, **params)` (produces `plugin.<id>.<code>`, translated via `errors.<code>`) or `ctx.console_error(status, code, **params)` when forwarding a failed `ctx.api` call. Do not return hardcoded user-facing error prose.
- **Logging**: Use `ctx.log.info/warn/error(msg)` (prefixed with `[plugin <id>]`). Keep each severity level on its own call site (rclpy loggers reject changing severity dynamically at a single call site).

### 3.2 Async & background jobs (`ctx.jobs`)

- **Do not block the event loop**: Any operation exceeding ~200 ms (point cloud I/O, registration, bag conversion, disk scans) **MUST** run via `ctx.jobs.submit(...)` (long jobs) or `await ctx.run_python(...)` (short commands).
- `ctx.jobs` runs jobs serially per plugin, retains the newest 50 finished records across restarts, and automatically mounts `/api/ext/<id>/jobs`, `/jobs/{job}`, `/jobs/{job}/cancel`, `/jobs/{job}/log` and `/jobs/{job}/files/{name}` (results up to 64 KiB are embedded in `GET /jobs`).
- **Worker contract** (`ctx.python -E -s <plugin_dir>/<script> <job_dir>`, cwd = `<job_dir>`):
  1. Read `<job_dir>/input.json` (`id`, `kind`, `target`, `params`, `plugin_dir`, `data_dir`, `job_dir`).
  2. Flush single-line JSON progress to stdout: `{"progress": 0.42, "phase": "segmenting"}` (`progress` in `0.0..1.0`; `phase` is a short key translated on the frontend). Other output is recorded in `log.txt`.
  3. On success, write a compact `result.json` (store large images/clouds as separate files served via `/files/{name}`) and exit `0`.
  4. On failure, write `error.json` (`{"code": "plugin.<id>.<code>", "params": {...}}`) and exit non-zero.
  5. Write large artifacts atomically (temp file + rename) so cancelled jobs leave no partial files.

---

## 4. Frontend Engineering & SDK Rules (`window.RobotConsole`)

### 4.1 Shared runtime & bundling

- Build as a pre-compiled **IIFE** bundle writing `ui/plugin.js` and `ui/plugin.css` (`cssCodeSplit: false`).
- Externalize `vue` and `element-plus` to the console globals:
  ```ts
  rollupOptions: {
    external: ['vue', 'element-plus'],
    output: {
      globals: { vue: 'RobotConsole.vue', 'element-plus': 'RobotConsole.ui' },
      assetFileNames: 'plugin.[ext]',
    },
  }
  ```
- `RobotConsole.vue` is the **runtime-only** build: use pre-compiled `.vue` SFCs or `h()` render functions; runtime template strings are not supported.
- Import **only** components present in `window.RobotConsole.ui` (others are `undefined` at runtime):
  - Console components (from `window.RobotConsole.ui`): `GlassPanel`, `KpiTile`, `InfoTip`.
  - Element Plus whitelist: `ElAlert`, `ElButton`, `ElCheckbox`, `ElDialog`, `ElDivider`, `ElDrawer`, `ElEmpty`, `ElForm`, `ElFormItem`, `ElIcon`, `ElInput`, `ElInputNumber`, `ElLink`, `ElOption`, `ElPopconfirm`, `ElProgress`, `ElRadio`, `ElRadioButton`, `ElRadioGroup`, `ElScrollbar`, `ElSelect`, `ElSlider`, `ElSwitch`, `ElTable`, `ElTableColumn`, `ElTag`, `ElTooltip`, `ElMessage`, `ElMessageBox`.
- **MUST NOT** bundle full Element Plus CSS or override global Element Plus / console theme styles.

### 4.2 Registration & shared patterns

- Call `window.RobotConsole.register({ id, messages, pages?, header?, themes? })` synchronously once before the 15 s load timeout.
- `pages[i].path` **MUST** match `^[a-z0-9][a-z0-9_-]{0,39}$` and be unique within the plugin (mounted at `/#/ext/<id>/<path>`).
- `pages[i].title` **MUST** be concise bilingual `{zh, en}` (recommended <= 6 Chinese characters / <= 20 English characters) so the side menu never clips.
- UI theme plugins registering `themes` **MUST** provide `id` (not `light`/`dark`), bilingual `label`, and `base: 'light' | 'dark'`; CSS overrides **MUST** be `--*` tokens in `tokens` (applied cleanly to `:root`) rather than unscoped global CSS selectors.
- Use `window.RobotConsole.request(...)` for JSON calls (use `XMLHttpRequest` only when upload progress events are required) and format errors with `errorText(e)` / `codedText(m)`.
- Tool plugins **SHOULD** follow the `plugins/_shared/web` pattern (`kit.ts`, `useJobs.ts`, `PythonGate.vue`, `JobList.vue`, `commonMessages`) for consistent job and environment UX.

### 4.3 CSS scoping

- Prefix all custom classes with a short plugin-specific prefix (e.g. `.me-*`, `.lc-*`, `.aia-*`) and prefer `<style scoped>`.
- **MUST NOT** write unscoped rules targeting `html`, `body`, `.glass`, `.chip`, `.panel`, or `.el-*`.

---

## 5. UI Visual & Interaction Design Requirements

The console uses a **clean frosted-glass HUD aesthetic** supporting both **Light** and **Dark (`html.dark`)** appearances. Plugin pages must visually match native console pages in both themes.

### 5.1 Design tokens (no hardcoded theme colors)

**MUST** style against the CSS variables in `web/src/style.css`:

| Role | Tokens | Notes |
|---|---|---|
| Typography | `var(--font-ui)`, `var(--font-mono)` | Add `.mono` (`tabular-nums`) to all numbers, coordinates, timestamps, metrics, matrices, paths and versions |
| Text hierarchy | `var(--ink)`, `var(--ink-2)`, `var(--ink-3)` | Never hardcode `#333`, `#666`, `#000`, `#fff` on theme surfaces |
| Accent & status | `var(--accent)`, `var(--accent-2)`, `var(--accent-grad)`, `var(--ok)`, `var(--warn)`, `var(--bad)`, `var(--idle)` | Mix tinted text with `var(--ink)` or `var(--deepen)` via `color-mix(...)` to preserve contrast |
| Surfaces & borders | `var(--glass)`, `var(--glass-strong)`, `var(--paper)`, `var(--hairline)`, `var(--glass-edge)` | Use `1px solid var(--hairline)` for dividers and nested cards |
| Canvas & charts | `var(--canvas-bg)`, `var(--grid-line)`, `var(--line-est)` | Prevents glaring light canvases in dark mode |
| Notices | `var(--note-bg)`, `var(--note-edge)`, `var(--note-ink)`, `var(--note-ink-2)` | Non-fatal prerequisites and warnings |

Scientific colormaps (e.g. error heatmaps) may use fixed perceptually uniform ramps, provided they show a legend with physical units whose text uses `var(--ink)` / `var(--ink-2)`.

### 5.2 Standard components & visual conventions

1. **Sections**: Wrap top-level blocks in `GlassPanel` (use `:flush="true"` for tables, `tip="..."` for header help). Stack panels vertically with `gap: 14px`.
2. **KPIs & status**: Use `KpiTile` for top-level counts/ratios or a responsive card grid (`repeat(auto-fill, minmax(220px, 1fr))`) for multi-metric job results. Use `.chip` (`success`, `warning`, `danger`, `primary`, `info`, optional `.live`) for status pills. Avoid decorative emojis in buttons, headings or badges.
3. **Parameter help**: Place `<InfoTip :content="t('...')" />` beside non-obvious algorithm parameters instead of cluttering forms with long inline paragraphs.
4. **Python environment gate**: Plugins declaring `python.requirements` **MUST** render a prominent notice (`PythonGate`) with an **Install Python packages** action while `python.state !== 'ready'` and disable job submission until ready while keeping job history readable.
5. **Action hierarchy & control density**: Keep at most one `type="primary"` button per action group; use `size="small"` for form controls; use `type="danger"` for destructive actions.

### 5.3 Layout, responsiveness & safe zones

- **Shell breakpoints**:
  - Wide (`> 1180px`): `220px` sidebar rail (`1707x985`, `1440x1000`, `1280x760`).
  - Compact (`<= 1180px`): `68px` icon-only rail (`1100x700`, leaving ~`980px` content width).
- **Responsive grids & wrapping**:
  - Parameter grids **SHOULD** use `grid-template-columns: repeat(auto-fill, minmax(260px, 1fr))` with full-width rows (`grid-column: 1 / -1`) for selectors and section dividers.
  - Multi-column result blocks **MUST** stack cleanly on narrow viewports; never hardcode fixed widths > `900px`.
  - Apply `min-width: 0` and `overflow-wrap: anywhere` to containers showing map names, filenames or paths.
  - Tables **MUST** fit at `1100x700` without horizontal scrollbars; use `show-overflow-tooltip` on long text columns.
- **Header components & floating panels**:
  - `header` triggers sit before *Background tasks* and must remain compact on a single line.
  - Non-modal floating panels **MUST** stay within `top >= 64px` and `bottom >= 84px` so they **never** cover the top header clock or the bottom status / E-STOP bar.
  - `ElDialog` **SHOULD** use `width="min(860px, 94vw)"` and `append-to-body`.

### 5.4 Data visualization & empty states

- Prefer lightweight inline `<svg viewBox="...">` styled with CSS variables and `<title>` tooltips for histograms or simple curves rather than bundling heavy charting libraries.
- Label all metrics and images with explicit physical units (`m`, `deg`, `%`, `s`), provide download links for `result.json` and artifacts, render `<ElEmpty />` for empty lists, and expand only the selected (or newest succeeded) job by default.

### 5.5 Automated UI smoke checks (`tools/ui_smoke.sh`)

Every plugin page **MUST** pass `tools/ui_smoke.sh` (which runs `tools/ui_smoke.mjs`) across **`1440x1000` & `1100x700`**, **`light` & `dark`**, and **`zh-CN` & `en`**:

1. **Zero runtime faults**: 0 uncaught exceptions, 0 `console.error`, 0 Vue / vue-i18n warnings, 0 unexpected 4xx/5xx responses.
2. **Zero raw i18n keys**: No untranslated keys in visible text, `title`, `aria-label` or `placeholder`.
3. **Zero layout overflows**: No clipped controls/text, no unexpected horizontal scrollbars (including `el-table`), no content past the right viewport edge.
4. **Zero broken single-line controls**: Table headers (`th .cell`), buttons (`.el-button > span`), `.chip` pills, radio buttons (`.el-radio-button__inner`), and `.num`/`.name` table cells **MUST** stay on one line without overflowing their boxes.
5. **Readable contrast**: Every visible text node **MUST** achieve WCAG contrast **>= 2.2:1** against its composited background in both themes (`>= 4.5:1` recommended for body copy).
6. **Reduced motion**: Respect `prefers-reduced-motion: reduce`.

---

## 6. Internationalization (i18n)

1. **Full bilingual coverage**: Provide complete `zh-CN` and `en` texts in both `plugin.yaml` and `register({ messages })`, including tooltips, job `phases.*`, `warnings.*` and backend `errors.*`.
2. **vue-i18n syntax**: Avoid unescaped `{`, `}`, `@`, `|`, `$` in plain copy (use `{param}` for interpolation only).
3. **Formatting**: Use concise, plain engineering copy and display `–` (en dash) for missing values instead of `null`/`NaN`/`undefined`.

---

## 7. Testing, Packaging & Market Publishing

### 7.1 Testing

- **Backend (`pytest`)**: Cover manifest validation, core processing/parsing logic on offline synthetic data, error codes and edge cases.
- **Frontend**: `vue-tsc --noEmit` + `vite build` with zero errors.
- **UI smoke**: Run `tools/ui_smoke.sh` against an isolated test console (dedicated port and unused `ROS_DOMAIN_ID`) covering `PAGES=plugins,ext/<id>/<path>` in both `1440x1000` and `1100x700`, `light` and `dark`.

### 7.2 Market archives & release

`robot_console/plugin_market.py` enforces the following archive constraints on install:

- Exactly **one** top-level directory `<id>/` with a valid `plugin.yaml` whose `id`, `version`, and `api` match the index entry.
- **No** symlinks, absolute paths, `..`, `node_modules`, `__pycache__`, `.pytest_cache`, `.vite`, `.pyc`/`.log`, or hidden dotfiles.
- At most **5,000** files, **512 MB** unpacked, and **64 MB** zip download size.
- Reproducible zip packaging (fixed timestamps, sorted entries) via `package.py <id>`, attached to a GitHub Release tagged `console-<id>-v<version>` and registered in `slam-console-plugins` via `python3 tools/add_entry.py <entry.json> --check-url`.
- Upgrades **MUST** stay backward-compatible with existing user settings in `plugin_state.json` (new settings provide sensible defaults).

---

## 8. Pre-Release Checklist

- [ ] `plugin.yaml` passes validation (`id`, `api: 1`, bilingual `name`/`description`, `license`, `upstream`, clean `python.requirements`)
- [ ] Backend uses `ctx.api` only, runs third-party packages strictly in child processes, and contains no hardcoded secrets or machine paths
- [ ] Frontend is pre-built to `ui/plugin.js` + `ui/plugin.css`, externalizes `vue` & `element-plus`, and uses only whitelisted SDK components
- [ ] Styling uses console CSS tokens + plugin class prefix, handles light & dark themes, includes `PythonGate` / `InfoTip` / `ElEmpty`, and never obscures the header clock or bottom E-STOP bar
- [ ] `tools/ui_smoke.sh` passes at `1440x1000` & `1100x700` x `light` & `dark` x `zh-CN` & `en`
- [ ] Offline unit tests pass; market zip & entry pass `package.py` and `add_entry.py --check-url`
