# 地图管理 4b

## 使用

- 在地图详情点击“重新生成 2D”，调整分辨率、障碍物离地高度范围、地面模型等参数。
- 点击“生成预览”查看新 2D 栅格和叠加在点云上的结果，可切换“原栅格”对照。
- 修改参数后必须重新预览，才能保存。填写新名称，保存为独立版本；原地图不改写，同名拒绝覆盖。
- 新版本通过硬链接复用点云（不支持时复制）。删除原版本不会损坏新版本。
- “导入地图”支持 YAML + PGM/PNG、PCD，或两者。纯 PCD 导入成功后自动打开投影窗口。导航页仍要求 YAML + 图像。
- “删除地图”移到地图库的 `.trash/`；回收站可恢复、永久删除单项或清空。永久操作有确认提示，恢复不覆盖同名地图。
- 正在保存、正在运行或停止中的导航地图不能删除。旧平铺地图暂不提供删除，以免删除被其他 YAML 共用的图像。

## 参数与实验开关

常规参数：res（0.01–1 m）、obs_min / obs_max（前者必须更小）、ground_mode（plane/grid/none）、ground_tol、min_points、tile、voxel、inflate、closing_iters。

实验功能默认关闭，关闭时后端拒绝 heightmap、ground_cell、ground_smooth、min_obstacle_area、min_free_area。开启后可选连续高度图地面模型及小区域过滤。关闭开关会退出 heightmap 模式。

高度以地面模型为基准；none 使用点云绝对 z。本版保持点云坐标系，不开放调平或旋转，以保持 2D、3D 和导航坐标一致。轨迹近似射线清空等功能没有接入。

## 安装与复现

pcd2grid 以 wheel 形式随安装脚本提供（`install/wheels`），非 editable 安装到 `~/venvs/console`。它的版本号仍为 0.1.0，但包含未发布功能；每次投影记录核心源文件 SHA-256，区分相同版本号的快照。

新版本 `meta.json` 中的 `parent` 和 `regrid` 记录来源、参数、实验开关、软件版本与源码指纹、栅格统计和警告。计算在独立子进程进行，超时 120 秒，最多 400 万栅格。临时预览位于 `.regrid/`，关闭/替换/保存预览时清理，遗留预览在一小时后由后续生成请求清理。

重新部署依赖时，在控制台虚拟环境用非 editable 方式安装需要的 pcd2grid 快照，再运行 `bash ~/scripts/build_console_web.sh` 并重启控制台。

## 验证（2026-11-04）

- `~/venvs/console/bin/python -m unittest discover -s tests -v`：8 项通过，均使用临时地图库。
- `npm run build`：类型检查和构建通过，只有原有体积警告。
- 无头 Edge：实际办公地图预览；实验开关默认关且开启后显示控制项；参数变更后保存禁用；导入和回收站对话框。
- 页面实际操作合成 PCD：纯 3D 上传 → 自动进入投影 → 预览 → 保存新版本 → 自动选中。无未捕获 JS 异常。只清理本次唯一命名的测试地图，原有地图名称集合保持一致。

## 接口

- `POST /api/maps/{name}/regrid`：生成预览。
- `GET /api/maps/regrid/{token}/grid.png`：预览图。
- `DELETE /api/maps/regrid/{token}`：丢弃预览。
- `POST /api/maps/regrid/{token}/save`：JSON `{name}`，保存新版本。
- `DELETE /api/maps/{name}`：移到回收站。
- `GET /api/map-trash`：列出回收站。
- `POST /api/map-trash/{id}/restore`：恢复。
- `DELETE /api/map-trash/{id}` / `DELETE /api/map-trash`：永久删除单项 / 清空。
- `POST /api/navigation/import`：复用原 multipart 接口；YAML/图像成对，或仅 cloud_file。
