# Plugins

Plugins add backend routes, pages and settings to the console without changing its code. This guide describes
**plugin interface v1** (`api: 1`). A complete, minimal plugin lives in
[`examples/plugins/hello_console`](../examples/plugins/hello_console). For engineering rules, UI design standards,
and release checks, see [`plugin-guidelines.md`](plugin-guidelines.md).

## Trust model

A plugin's Python code runs inside the console process with the console's rights (files, processes, ROS).
There is no sandbox, so installing a plugin means trusting its author. To make that a deliberate step:

- The console only **reads `plugin.yaml`** when it scans the plugin folders; it never imports plugin code then.
- **New plugins start disabled.** Enable them on the *Plugins* page; enabling and disabling apply at the next
  console start (there is no hot loading).
- A plugin that fails to import, or whose `setup()` raises, is shown as *failed* with the error. The console and
  the other plugins keep running. A page component that throws shows an error card instead of the page.
- Start the console with `ROBOT_CONSOLE_NO_PLUGINS=1` to load no plugin at all (for example when a plugin hangs
  the start-up).
- Settings of type `secret` are kept only in the backend state file (mode 0600); the API tells the page only
  whether a secret is set.

## Installing a plugin

Plugins shipped with the console live in `plugins/` of this package (installed to
`share/robot_console/plugins` and listed after `plugins.dirs` unless `plugins.shipped: false`); they also start
disabled. At the moment these are the **AI assistant** (`plugins/ai_assistant`) and three tools built on open-source
packages: **Bag converter** (`bag_convert`, rosbags), **Ground segmentation** (`ground_seg`, Patchwork++) and
**Dynamic object removal** (`dynamic_removal`, DUFOMap); see [Shipped plugins](#shipped-plugins).

Install plugins from the **Plugin market** tab (see [Plugin market](#plugin-market)), or copy other plugins into a plugin folder (default `~/robot_console_data/plugins`, the first entry of `plugins.dirs` in
`config/console.yaml`; a plugin there overrides a shipped one with the same id), press *Rescan* on the *Plugins*
page, enable it, and restart the console:

```bash
cp -r examples/plugins/hello_console ~/robot_console_data/plugins/
```

| Path (defaults) | Content |
|---|---|
| `${data_root}/plugins/<id>/` | the plugin itself; can be replaced or deleted to upgrade/remove it |
| `${data_root}/plugin_state.json` | enabled flags and settings of every plugin (survive upgrades) |
| `${data_root}/plugin_data/<id>/` | the plugin's own files (`ctx.data_dir`); jobs in `jobs/<job id>/` |
| `${data_root}/plugin_envs/<id>/` | the plugin's Python venv (`python.requirements`), with `<id>.json` (last install) and `<id>.log` (pip output) |

## Layout and manifest

```
hello_console/            # folder name = plugin id
  plugin.yaml
  backend.py              # optional; or a package backend/__init__.py
  ui/plugin.js            # optional, pre-built
  ui/plugin.css           # optional
```

```yaml
id: hello_console          # 2-40 chars: a-z first, then a-z 0-9 _; must equal the folder name
api: 1                     # plugin interface version the plugin is written for
version: 0.1.0
name: {zh: 示例插件, en: Hello plugin}   # or one string for both languages
description: {zh: ..., en: ...}
author: you                # optional
license: MIT               # optional
homepage: https://...      # optional, http(s) only
upstream: https://...      # optional: the open-source project the plugin is built on (shown in the market)
backend:                   # optional
  module: backend          # backend.py or backend/__init__.py, must define setup(ctx)
frontend:                  # optional
  dir: ui                  # only files in this folder are served (at /ext/<id>/...)
  script: plugin.js
  style: plugin.css        # optional
python:                    # optional: packages for the plugin's own venv (see "Python packages" below)
  requirements: ['rosbags==0.11.9', 'numpy>=1.23,<3']
settings:                  # optional; the Plugins page builds a form from it
  - key: greeting          # a-z first, then a-z 0-9 _
    type: string           # string | text | number | integer | boolean | select | secret
    label: {zh: 问候语, en: Greeting}
    help: {zh: ..., en: ...}   # optional
    default: Hello         # not allowed for secret
    max_length: 80         # string / text / secret
  - {key: repeat, type: integer, label: Repeat, min: 1, max: 5, step: 1, default: 1}
  - key: tone
    type: select
    label: Tone
    options: [{value: plain, label: {zh: 平静, en: Plain}}, {value: excited, label: Excited}]
  - {key: api_key, type: secret, label: API key}
```

YAML 1.1 reads `on`, `off`, `yes`, `no` as booleans: do not use them as setting keys or select values without
quotes.

## Backend: `setup(ctx)`

The backend module is imported as `robot_console_ext.<id>` (relative imports inside a package backend work) and
`setup(ctx)` is called once at console start. `ctx` is the whole backend interface:

| Member | Use |
|---|---|
| `ctx.id`, `ctx.dir` | plugin id and folder |
| `ctx.data_dir` | `${data_root}/plugin_data/<id>`, created on first use |
| `ctx.router` | a FastAPI `APIRouter`, mounted at `/api/ext/<id>` |
| `ctx.settings()` | current setting values with defaults, secrets included; read on every call (changes apply without a restart) |
| `ctx.log.info/warn/error(msg)` | console log with a `[plugin <id>]` prefix |
| `ctx.error(status, code, **params)` | an `HTTPException` with code `plugin.<id>.<code>`; the page translates it with the plugin's `errors.<code>` message |
| `ctx.api.get/post/put/delete(path, ...)` | async call of the console's own REST API inside the process; raises `ApiCallError(status, detail)` |
| `ctx.api.post/put(path, files=..., data=...)` | multipart form: `files={name: (filename, bytes or path)}`, `data={name: text}`; `content=bytes, content_type=...` sends a raw body |
| `ctx.ApiCallError` | the exception class `ctx.api` raises (`.status`, `.detail`) |
| `ctx.console_error(status, code, **params)` | an `HTTPException` with one of the console's own codes, e.g. to pass on a failed `ctx.api` call |
| `ctx.on_shutdown(fn)` | called when the console stops (stop your threads here) |
| `ctx.python` | interpreter of the plugin's venv when it is installed and up to date, else `None` (the console's interpreter for a plugin without `python:`) |
| `ctx.python_status()` | `{state, requirements, packages, error, ...}` of the venv, `None` without `python:` |
| `await ctx.run_python(args, input_data=None, timeout=60)` | run `ctx.python -E -s *args` without blocking the console; returns `(exit code, stdout, stderr)` |
| `ctx.jobs` | background jobs, see below |
| `ctx.api_version` | `1` |

```python
def setup(ctx):
    @ctx.router.get('/summary')
    async def summary():
        if not ctx.settings()['greeting']:
            raise ctx.error(409, 'empty_greeting')
        stacks = await ctx.api.get('/api/stacks')
        return {'running': [s['name'] for s in stacks if s['state'] == 'running']}
```

Read and change console data **only through `ctx.api`**, that is, through the REST routes the web page uses.
Internal Python modules of the console change between versions; the REST routes are the stable surface.
`ctx.api` refuses the plugin management routes (`/api/plugins...`).

### Python packages

A plugin that needs packages lists them in `python.requirements` (pip requirement strings: a name, optional
extras and version specifiers; no URLs, paths, markers or pip options). The console never installs them into its
own environment. The *Plugins* page shows the list with an **Install packages** button: the console then creates
`${data_root}/plugin_envs/<id>` with `python -m venv` (from the interpreter behind the console venv, without the
console's `PYTHONPATH`) and runs `pip install` there, one plugin at a time, in the background; no plugin code
runs during the install. A failed install leaves no venv behind and shows pip's last error; the log is on the
page. When the plugin is updated and lists other packages, the venv is *outdated* and not used until it is
reinstalled. pip options for all installs (`index_url`, `extra_index_url`, `proxy`, `timeout`, `retries`) go in
`plugins.pip` of `console.yaml`; `plugins.python` picks another interpreter.

Routes: `POST /api/plugins/{id}/python/install`, `DELETE /api/plugins/{id}/python`,
`GET /api/plugins/{id}/python/log`; `GET /api/plugins` has a `python` entry per plugin.

The packages are used in **separate processes** (`ctx.jobs`, `ctx.run_python`), never imported into the
console: the console keeps its own NumPy and ROS packages, and copyleft packages stay out of the console process.

### Background jobs (`ctx.jobs`)

`ctx.jobs.submit(kind, params, title='', target='', script='worker.py')` queues a job and returns its record;
the jobs of one plugin run one at a time. Each job runs `ctx.python -E -s <plugin dir>/<script> <job folder>` in
its own process group (cancel kills it), with the job folder `${data_root}/plugin_data/<id>/jobs/<job id>/` as
the working directory. The worker

- reads `input.json` (`{id, kind, target, params, plugin_dir, data_dir, job_dir}`),
- prints progress as JSON lines on stdout: `{"progress": 0.42, "phase": "segmenting"}` (every other line goes to
  `log.txt`),
- writes `result.json` and exits 0 when it succeeded, or writes `error.json` (`{"code": "plugin.<id>.<code>",
  "params": {...}}`) and exits non-zero; without `error.json` the last output line becomes the error,
- writes whatever files it makes into the job folder.

Records survive restarts (a job that was running when the console stopped becomes *interrupted*); the newest 50
finished jobs are kept. Every plugin with a backend gets these routes under `/api/ext/<id>/`:
`GET jobs` (`{python, jobs}`; results up to 64 KiB included), `GET jobs/{job}`, `POST jobs/{job}/cancel`,
`DELETE jobs/{job}`, `GET jobs/{job}/log`, `GET jobs/{job}/files/{name}` (files of the job folder). In the
backend, `ctx.jobs.get(job)`, `ctx.jobs.list(kind)` and `ctx.jobs.dir_of(job)` read them.

### New map versions from a tool

`POST /api/maps/{name}/derive` (multipart) makes a new version of a folder map with a replacement grid and/or
cloud, keeping `data/`, `goals.yaml`, the scene link and the scan recording; `meta.json` gets `source: derived`,
`parent` and an entry in `derived` (tool, note, what changed). Fields: `new_name`, `tool` (shown in meta.json),
`note` (JSON object), `grid_mode` = `keep` | `replace` (file `grid`: raw cells in map.pgm row order, 0/205/254,
same size as the source grid) | `regrid` (pcd2grid on the new cloud), and the file `cloud` (PCD). Call it with
`ctx.api.post(..., files=..., data=...)`. `GET /api/maps/{name}` names the scan recording of a map in `scans`
(`{recording, trajectory}`) when it has one.

## Frontend: `window.RobotConsole`

When at least one active plugin has a frontend, the console defines `window.RobotConsole` and loads the plugin
scripts in order (the style sheet first). A script registers its messages, pages, header items, and optional UI
themes:

```js
RobotConsole.register({
  id: 'hello_console',
  messages: { 'zh-CN': { title: '示例', errors: { empty_greeting: '…' } }, en: { title: 'Hello', errors: { … } } },
  pages: [{ path: 'main', title: { zh: '示例插件', en: 'Hello plugin' }, component: HelloPage, icon: undefined }],
  themes: [{ id: 'eye_care', label: { zh: '护眼防蓝光', en: 'Eye Care' }, base: 'light', tokens: { '--accent': '#a16207' } }],
})
```

- Pages are shown at `/#/ext/<id>/<path>` and listed in the side menu in their own group under the console's workflow pages (Monitor and Plugins sit in the bottom tool group).
- `header: [Component, ...]` puts components into the page header, before the *Background tasks* button (the AI
  assistant uses it for its button and floating panel). A header component that throws is hidden and reported on
  the *Plugins* page.
- `themes: [{ id, label, base, pageColor?, tokens?, scene?, charts? }]` registers UI appearance extensions that appear in the sidebar appearance switcher alongside Light / Dark; `tokens` maps CSS custom properties (`--*`) on `:root`, while `scene` and `charts` override 2D/3D WebGL/canvas and ECharts evaluation palettes.
- Messages are merged under `plugins.<id>`; avoid `{ } @ | $` in texts (vue-i18n syntax).

| Member | Content |
|---|---|
| `apiVersion` | `1` |
| `vue` | the console's Vue module (`h`, `ref`, `computed`, `defineComponent`, ...). It is the runtime-only build: use render functions or pre-compiled SFCs, not template strings |
| `ui` | `GlassPanel`, `KpiTile`, `InfoTip` (console components) and Element Plus `ElAlert, ElButton, ElCheckbox, ElDialog, ElDivider, ElDrawer, ElEmpty, ElForm, ElFormItem, ElIcon, ElInput, ElInputNumber, ElLink, ElOption, ElPopconfirm, ElProgress, ElRadio, ElRadioButton, ElRadioGroup, ElScrollbar, ElSelect, ElSlider, ElSwitch, ElTable, ElTableColumn, ElTag, ElTooltip, ElMessage, ElMessageBox` |
| `request(method, path, body?, signal?, options?)` | JSON request with the console's timeouts and error codes; `options.timeout` (ms) for slow calls |
| `errorText(error)` | the error as text in the current language (plugin codes use your `errors.*` messages) |
| `codedText({code, params})` | the same for a coded error object, e.g. the `error` of a failed job |
| `i18n.useT(id)` | `t(key, params)` for your own messages; `i18n.pick({zh, en})`; `i18n.locale` (computed) |
| `theme` | `{ current, base, isDark, choices, set(id, origin?) }` to inspect or switch the active appearance (including custom plugin themes) |
| `register(def)` | see above; the whole definition is checked before anything is applied |

Use the console's CSS variables (`--ink`, `--ink-2`, `--ink-3`, `--accent`, `--accent-grad`, `--hairline`,
`--ok`, `--warn`, `--bad`, ...) and its `.chip` / `.mono` classes so light and dark themes both work, and prefix
your own CSS classes with the plugin id.

### Building a frontend from .vue files

Plugins shipped in `plugins/<id>/web` are built with the console's own toolchain:
`cd web && npm run build:plugins` (all of them; `PLUGIN=<id>` for one; `web/scripts/build-plugins.mjs` with
`web/vite.plugins.config.ts`). It type-checks `plugins/<id>/web/tsconfig.json` and writes
`plugins/<id>/ui/plugin.js` + `plugin.css`, which are committed so users need no Node.js. Code shared by the
shipped tool plugins (job list, Python-environment notice, request helpers, common texts) lives in
`plugins/_shared/web` and is bundled into each of them; folders starting with `_` are not plugins. For a plugin of your own, the equivalent standalone setup is:

```ts
// vite.config.ts of the plugin's own frontend project
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: '../ui', emptyOutDir: true, cssCodeSplit: false,
    lib: { entry: 'src/main.ts', formats: ['iife'], name: 'HelloConsolePlugin', fileName: () => 'plugin.js' },
    rollupOptions: {
      external: ['vue', 'element-plus'],
      output: { globals: { vue: 'RobotConsole.vue', 'element-plus': 'RobotConsole.ui' }, assetFileNames: 'plugin.[ext]' },
    },
  },
})
```

```ts
// src/main.ts
import HelloPage from './HelloPage.vue'
window.RobotConsole!.register({ id: 'hello_console', pages: [{ path: 'main', title: 'Hello', component: HelloPage }] })
```

Import Element Plus components explicitly (`import { ElButton } from 'element-plus'`) and only those in the
`ui` list; anything else is `undefined` at run time.

## The AI assistant plugin

`plugins/ai_assistant` adds an **AI assistant** button to the header. It opens a panel on the right (not modal; it
stays open across pages and never covers the E-STOP button) where you ask questions about the console's data in
plain language; the model answers by calling tools and the panel shows each tool call.

- **Under the input** (like Claude Code / Codex): a model chip (service + model + reasoning effort; the menu lists
  the models of every service, an effort track for the model in use, *Test connection* and *Manage model
  services*; choices are saved on the backend) and an **access** chip, kept per browser and sent with every
  question:
  - *Chat only*: tools are still declared (the history may hold tool calls) but `tool_choice` is `none`;
  - *Read only* (default): read tools; run tools are refused and the model is told how to allow them;
  - *Ask before running*: every run tool call shows a card with its arguments and **Allow / Decline**; the turn
    waits (up to 15 min, or until the user stops) for `POST chat/confirm`;
  - *Run automatically*: read and run tools without asking.
  The access level is added to the question for the model and enforced in `agent.py`; the full tool list is always
  sent so the prompt prefix stays stable.
- **Run tools** (`backend/actions.py`, one POST each to a route the web page uses, so the console's own checks
  apply): `evaluate_run` (evo ATE/RPE), `start_mapping_batch`, `start_nav_batch` (the map's saved goals unless
  goals are given), `cancel_batch`. The read tool `get_experiment_options` lists what can be started (presets, SLAM
  stacks, speeds, Nav2 variants, maps with scenes and saved goals) and what is running.

- **Bring your own model services.** The plugin's page *AI model services* (`/#/ext/ai_assistant/providers`, also
  the gear in the panel) keeps several services and one active, like cc-switch's provider list: name, note, colour,
  website, API format *OpenAI-compatible* (OpenAI, DeepSeek, Qwen/DashScope compatible mode, Kimi, Zhipu,
  SiliconFlow, OpenRouter, Ollama, relays: anything with `/chat/completions` and tool calling) or *Anthropic* (the
  Claude Messages API through the official `anthropic` SDK, default model `claude-opus-5-5`, server-side refusal
  fallback on Anthropic's own API), API key (never sent back to the browser), address with alternative addresses
  and a latency check, a model list fetched from the service (`GET /models`) or typed, each model with a display
  name, checked reasoning effort levels and a default effort (OpenAI `reasoning_effort`, Anthropic `output_config.effort`;
  effort values are shown in English and the panel's effort track uses the model's checked levels when set), and
  advanced options: proxy, *full URL* (the address is the complete endpoint), User-Agent, header and body overrides.
  Presets only fill in addresses. Shared settings on the *Plugins* page: timeout, tool
  rounds per question, extra instructions. Nothing is preset: the console ships without any key or service.
  Profiles live in `${data_root}/plugin_data/ai_assistant/providers.json` (mode 0600); the single service of plugin
  version 0.1 is turned into a profile on the first start.
- **Read tools** (all GET requests through `ctx.api`): `list_maps`, `get_map`, `list_runs`, `get_run` (run
  info, the SLAM parameters the run used, evo ATE/RPE, deviation from reference odometry, and a trajectory summary
  with height at distance checkpoints, z drift per 100 m and the steepest z change over 2 m), `get_trajectory`,
  `compare_runs` (metrics side by side + differing SLAM parameters), `list_batches`, `get_batch`, `get_slam_config`,
  `get_stack_log`, `get_console_status`, `list_sensor_profiles`, `list_bags`, `get_experiment_options`. Large data (trajectories, evo series,
  help texts) is summarized before it goes to the model; each result is capped at 14,000 characters.
- **Privacy:** the question, the open page and everything the tools read are sent to the configured service.
- Conversations are kept in `${data_root}/plugin_data/ai_assistant/sessions` (newest 50) and only appended to, so
  provider reasoning (Claude thinking blocks, `reasoning_content`) is passed back unchanged. Ctrl+/ toggles the panel.
- Backend: `plugins/ai_assistant/backend` (`providers.py`, `profiles.py`, `tools.py`, `actions.py`, `agent.py`,
  `sessions.py`);
  routes under `/api/ext/ai_assistant/`: `GET status`, `GET/POST providers`, `PUT/DELETE providers/{id}`,
  `POST providers/{id}/activate` (`{model?, effort?}`), `POST providers/order`, `POST probe/models`, `POST probe/latency`
  (both take the editor's unsaved values), `POST test`, `POST chat` (`{message, session?, page?, access?}`, NDJSON
  events incl. `confirm`), `POST chat/confirm` (`{session, id, approve}`), `GET session/{id}`. Tests:
  `tests/test_ai_assistant.py` (fake model servers, no network), `web/tests/ai-markdown.test.mjs`.

## Shipped plugins

All start disabled: enable them, press **Install packages** (for tools with Python dependencies), restart the console. Forks of the upstream projects
are kept at github.com/Leonhart999 (rosbags, patchwork-plusplus, dufomap).

| Plugin | Package (licence) | What it does |
|---|---|---|
| `eye_care_ui` Eye-care anti-blue-light UI | — (MIT) | Registers an Eye Care (`eye_care`) warm-amber anti-blue-light theme in the sidebar appearance switcher, adds a header quick-toggle button, and provides an interactive control page to tune warmth level (1–5), blue-light optical filter strength (0–80%), and soft contrast across all console pages, 2D/3D viewers, and charts. |
| `bag_convert` Bag converter | rosbags 0.11.9 (Apache-2.0) | ROS1 `.bag` to a ROS2 bag (sqlite3 or MCAP) in the playback upload folder; pick topics; Livox `CustomMsg` (livox_ros_driver / driver2) to `PointCloud2` with x y z intensity time (s after the stamp) ring tag. Lists `.bag` files in the bag folders, the upload folder and the folders of its `search_dirs` setting; uploads go to `<upload folder>/ros1/`. |
| `ground_seg` Ground segmentation | pypatchworkpp 1.4.1 (BSD-2) | Ground / non-ground labels by Patchwork++, from every raw scan (maps with a scan recording; labels are voted per 5 cm voxel and given to the map cloud's points) or from discs of the map cloud along the mapping trajectory. Rebuilds the 2D grid in the same geometry from each point's height above the local ground (ramps, tilted maps); preview images (grid, changes, labels), ground / non-ground PCDs, save as a new map version. |
| `dynamic_removal` Dynamic object removal | dufomap 1.1.1 (BSD-3) | DUFOMap over the raw scans of a map (needs *Save raw scans* during playback) labels the map points that lie in space seen as free: points of people and vehicles that moved. Writes the cleaned cloud and saves it as a new map version with the grid projected again (or the old grid kept). |

Tests: `tests/test_shipped_plugins.py` (manifests, Livox parsing, map I/O, grid building),
`tests/test_plugin_ext.py` (venvs, jobs, multipart), `tests/test_map_derive.py`.

## Plugin market

The *Plugins* page has two tabs: **Installed** and **Plugin market**. The market lists the plugins of an index file,
by default [Leonhart999/slam-console-plugins](https://github.com/Leonhart999/slam-console-plugins) `index.json`;
its plugins live in forks of the open-source projects they are built on (the plugin folder is
`robot_console_plugin/<id>/` in the fork, the zip is a release asset of the fork).

```yaml
# config/console.yaml
plugins:
  market:
    index_urls:              # tried in order; https URLs, file:// URLs or local paths
      - https://raw.githubusercontent.com/Leonhart999/slam-console-plugins/main/index.json
      - https://api.github.com/repos/Leonhart999/slam-console-plugins/contents/index.json?ref=main
    proxy: ''                # HTTP proxy for the index and the downloads (empty = plugins.pip.proxy, then the environment)
    timeout: 30
    max_size_mb: 64
```

The second URL is there because `raw.githubusercontent.com` is blocked on some networks where the GitHub API works.
The last index read is kept in `${data_root}/plugin_market.json`, so the list still shows (marked as old) when
GitHub cannot be reached.

**Installing never runs plugin code** and keeps the trust model above:

1. The index is read again; the plugin's zip is downloaded to `${data_root}/plugin_downloads/`. Its size and
   SHA-256 must equal the index entry, else nothing is installed.
2. The archive must hold exactly one folder named like the plugin id, without absolute paths, `..`, links or more
   than 5000 files / 512 MB; its `plugin.yaml` must be valid, with the id and version of the entry and a supported
   `api`.
3. It is unpacked next to the first plugin folder (`plugins.dirs[0]`) and moved into place with renames. A
   `.market.json` in the plugin folder records where it came from (URL, SHA-256, time).
4. A folder of the same id that the market did not install (no `.market.json`: a plugin you put there yourself)
   is never replaced. A shipped plugin of the same id is overridden, as with any plugin in `plugins.dirs[0]`.
5. A new plugin is disabled like every new plugin; enable it, install its Python packages, restart. An update keeps
   the enabled flag and the settings and applies at the next start (the card says so).

**Install from file** takes a plugin zip from the browser and runs the same checks; it counts as *verified* when its
SHA-256 equals an entry of the last index read. **Uninstall** (market or file installs only) deletes the folder and
clears the enabled flag; *also delete settings, data and Python environment* removes those too. Plugins cannot call
these routes through `ctx.api`.

| Route | Use |
|---|---|
| `GET /api/plugins/market?refresh=true` | index entries with `status` (`available`, `installed`, `update`, `newer`, `incompatible`) and what is installed under the id (`installed.version`, `origin`: `market` / `file` / `manual` / `bundled`) |
| `POST /api/plugins/market/{id}/install` | install or update from the index |
| `POST /api/plugins/market/upload?name=<file name>` | install a zip sent as the raw request body |
| `DELETE /api/plugins/{id}?purge=true` | uninstall a plugin the market (or a file) installed |

### Publishing a plugin

1. Put the plugin folder into `robot_console_plugin/<id>/` of a repository (for plugins built on another project:
   a fork of it). Give `plugin.yaml` an `upstream:` URL when it is built on another project; the market shows it.
2. Build the page (a standalone Vite project in `<id>/web` like the one in the forks below, with a copy of
   `plugins/_shared/web` in `src/shared/`), then run `python3 package.py <id>`: it writes a reproducible
   `dist/<id>-<version>.zip` and `dist/<id>-<version>.entry.json` (id, version, api, texts, requirements, URL,
   SHA-256, size).
3. Attach the zip to a release of that repository (tag `console-<id>-v<version>`).
4. In slam-console-plugins run `python3 tools/add_entry.py <entry.json> --check-url` and push `index.json`.

Plugins in the market now:

| Plugin | Fork (upstream) | What it does |
|---|---|---|
| `map_eval` Map quality evaluation | [Leonhart999/small_gicp](https://github.com/Leonhart999/small_gicp) (koide3/small_gicp, MIT) | registers a map onto a reference cloud (another map or an uploaded PCD/PLY) and computes accuracy, completeness, precision, F-score and Chamfer distance |
| `loop_closures` Loop closure check | [Leonhart999/MapClosures](https://github.com/Leonhart999/MapClosures) (PRBonn/MapClosures, MIT) | finds the places a mapping run revisited (needs a map with raw scans) and measures the odometry drift at each loop |

## REST routes of the plugin system

| Route | Use |
|---|---|
| `GET /api/plugins` | plugins with manifest, state, issue, settings specification, `restart_needed` |
| `POST /api/plugins/rescan` | read the plugin folders again (manifests only) |
| `PUT /api/plugins/{id}/enabled` `{"enabled": true}` | enable/disable (applies at the next start) |
| `GET /api/plugins/{id}/settings` | `{values, secrets}`; secrets only as set/not set |
| `PUT /api/plugins/{id}/settings` `{"values": {...}, "clear": [...]}` | checked against the manifest; a secret given as `""` stays unchanged, `clear` removes secrets |
| `POST /api/plugins/{id}/python/install`, `DELETE /api/plugins/{id}/python`, `GET /api/plugins/{id}/python/log` | the plugin's venv |
| `GET /api/plugins/market`, `POST /api/plugins/market/{id}/install`, `POST /api/plugins/market/upload`, `DELETE /api/plugins/{id}` | the plugin market, see above |
| `/api/ext/{id}/...` | the plugin's routes, plus the job routes (`jobs`, `jobs/{job}`, ...) |
| `/ext/{id}/...` | the plugin's frontend files (active plugins only) |

States: `active` (loaded at this start), `disabled`, `failed` (enabled but loading failed), `invalid` (manifest
problem), `duplicate` (same id in another folder), `incompatible` (unsupported `api`).

## Versioning

Plugin interface v1 only grows: members are added, never changed or removed. A breaking change becomes `api: 2`,
and the console will then support 1 and 2 side by side for a while.
