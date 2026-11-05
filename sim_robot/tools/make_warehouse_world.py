#!/usr/bin/env python3
"""生成仓库场景 worlds/warehouse.sdf。

场景来自 Gazebo Fuel 上的 "Tugbot in Warehouse"（OpenRobotics 发布）：
  - 仓库建筑 OpenRobotics/Warehouse，货架、货箱托盘、推车来自 MovAi；
  - 这些模型的许可证是 CC BY-NC(-ND) 4.0：可以用于非商业的学术研究，不能修改模型文件本身，
    论文里要注明出处。所以这里只"引用"它们（model://），不改动模型目录里的任何文件。
  - 模型文件已下载到 models/fuel/ 下（通过 Windows 的代理下载，见学习日志）。

和原场景的区别：
  - 去掉原场景里的 Tugbot 机器人和充电桩，换成我们自己的 sim_robot；
  - 插件和渲染设置与 indoor_office 一致（雷达用 ogre2，服务端软件渲染）。

用法：python3 make_warehouse_world.py   →  ../worlds/warehouse.sdf
"""
import os
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ORIG = os.path.join(HERE, '..', 'models', 'fuel', 'tugbot_warehouse_orig.sdf')
OUT = os.path.join(HERE, '..', 'worlds', 'warehouse.sdf')
SKIP = {'tugbot', 'charging_station'}            # 原场景自带的机器人和充电桩，不要
ROBOT_START = (2.9, -6.0, 0.1, 1.5708)           # 小车起点 x, y, z, yaw：南区两排长货架之间的通道，车头朝 +y
# 注意 shelf_big 的碰撞盒是 2.1 m × 18 m（沿 y 方向的长货架），三排分别占 x ≈ [-6.9,-4.8]、[-1.0,1.1]、[4.7,6.8]，
# y ≈ [-22,-4]。起点最初放在 (0.5, -6)，正好在中间那排货架的碰撞盒里面，小车开不动。
# 仓库建筑的碰撞网格里地面在模型坐标 z = 0.1，原场景把模型放在 z = -0.09，地面就在 z = 0.01。
# 小车放上去会陷进网格地面、开不动（实测下沉到 z = 0.092，给 0.4 m/s 指令 3 秒只动了 1 mm）。
# 所以把建筑整体再往下放，让网格地面比我们自己的平面地面（z = 0）低 2 cm，小车只和平面接触。
POSE_OVERRIDE = {'warehouse': '0 0 -0.12 0 0 0'}


def main():
    world = ET.parse(ORIG).getroot().find('world')
    includes = []
    for inc in world.findall('include'):
        name = inc.findtext('name').strip()
        if name in SKIP:
            continue
        uri = inc.findtext('uri').strip()
        model_dir = uri.rstrip('/').split('/')[-1]          # .../models/shelf -> shelf
        pose = POSE_OVERRIDE.get(name, inc.findtext('pose', '0 0 0 0 0 0').strip())
        static = inc.findtext('static')
        includes.append((name, model_dir, pose, static))

    rx, ry, rz, ryaw = ROBOT_START
    parts = [f"""<?xml version="1.0"?>
<!-- 由 tools/make_warehouse_world.py 生成，不要手改 -->
<!-- 模型出处：Gazebo Fuel，OpenRobotics/Warehouse 与 MovAi/shelf、shelf_big、pallet_box_mobile、cart_model_2（CC BY-NC 4.0 / CC BY-NC-ND 4.0） -->
<sdf version="1.8">
  <world name="warehouse">
    <physics name="1ms" type="ignored">
      <max_step_size>0.001</max_step_size>
      <real_time_factor>1.0</real_time_factor>
    </physics>
    <plugin filename="ignition-gazebo-physics-system" name="ignition::gazebo::systems::Physics"/>
    <plugin filename="ignition-gazebo-user-commands-system" name="ignition::gazebo::systems::UserCommands"/>
    <plugin filename="ignition-gazebo-scene-broadcaster-system" name="ignition::gazebo::systems::SceneBroadcaster"/>
    <plugin filename="ignition-gazebo-imu-system" name="ignition::gazebo::systems::Imu"/>
    <!-- 雷达用 ogre2，服务端用软件渲染（见 launch/sim.launch.py） -->
    <plugin filename="ignition-gazebo-sensors-system" name="ignition::gazebo::systems::Sensors">
      <render_engine>ogre2</render_engine>
    </plugin>

    <light type="directional" name="sun">
      <cast_shadows>false</cast_shadows><pose>-5 -3 10 0 0 0</pose>
      <diffuse>0.9 0.9 0.9 1</diffuse><direction>-0.3 0.2 -1</direction>
    </light>

    <model name="ground_plane"><static>true</static>
      <link name="link">
        <collision name="c"><geometry><plane><normal>0 0 1</normal><size>100 100</size></plane></geometry></collision>
        <visual name="v"><geometry><plane><normal>0 0 1</normal><size>100 100</size></plane></geometry>
          <material><ambient>0.5 0.5 0.5 1</ambient><diffuse>0.5 0.5 0.5 1</diffuse></material></visual>
      </link></model>

"""]
    for name, model_dir, pose, static in includes:
        s = f'      <static>{static.strip()}</static>\n' if static is not None else ''
        parts.append(f'    <include>\n      <uri>model://{model_dir}</uri>\n      <name>{name}</name>\n'
                     f'      <pose>{pose}</pose>\n{s}    </include>\n')
    parts.append(f"""
    <include>
      <uri>model://sim_robot</uri>
      <pose>{rx} {ry} {rz} 0 0 {ryaw}</pose>
    </include>
  </world>
</sdf>
""")
    with open(OUT, 'w') as f:
        f.write(''.join(parts))
    print(f'已生成 {os.path.normpath(OUT)}：{len(includes)} 个场景模型 + sim_robot')
    for name, model_dir, pose, _ in includes:
        print(f'   {name:22s} {model_dir:20s} {pose}')


if __name__ == '__main__':
    main()
