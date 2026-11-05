#!/usr/bin/env python3
"""生成室内办公场景 worlds/indoor_office.sdf。

场景布局用下面的 BOXES / CYLINDERS 列表描述，改布局只要改列表，然后重新运行：
    python3 make_world.py
坐标单位为米，原点在场景中心，x 向右（东），y 向上（北）。

布局（16 m × 10 m，墙高 2.5 m）：
    ┌───────────────────┬───────────────────┐  y = 5
    │ 凹室隔墙          │        桌子       │
    │═════════          │                   │
    │          立柱      门                 │
    │                   ┊          圆柱     │  y = 0
    │      箱子         │  箱子      箱子   │
    │ 起点 ●            │                   │
    └───────────────────┴───────────────────┘  y = -5
   x = -8              x = 0               x = 8
"""
import os

WALL_H = 2.5      # 墙高
WALL_T = 0.2      # 墙厚

# (名字, 中心 x, 中心 y, 长 x, 宽 y, 高 z, 颜色)
GRAY = "0.75 0.75 0.75 1"
BROWN = "0.55 0.35 0.2 1"
BOXES = [
    # 外墙
    ("wall_north", 0, 5, 16 + WALL_T, WALL_T, WALL_H, GRAY),
    ("wall_south", 0, -5, 16 + WALL_T, WALL_T, WALL_H, GRAY),
    ("wall_west", -8, 0, WALL_T, 10, WALL_H, GRAY),
    ("wall_east", 8, 0, WALL_T, 10, WALL_H, GRAY),
    # 中间隔墙，y 在 -0.8 到 0.8 之间留 1.6 m 宽的门
    ("wall_mid_south", 0, -2.9, WALL_T, 4.2, WALL_H, GRAY),
    ("wall_mid_north", 0, 2.9, WALL_T, 4.2, WALL_H, GRAY),
    # 西屋北侧的凹室隔墙
    ("wall_alcove", -6, 1.5, 4, WALL_T, WALL_H, GRAY),
    # 家具和障碍物
    ("box_west", -5, -2, 1.0, 1.0, 1.0, BROWN),
    ("box_east_1", 2.5, -2.5, 0.8, 0.8, 0.8, BROWN),
    ("box_east_2", 6, -3, 1.2, 0.8, 1.2, BROWN),
    ("table", 4, 2.5, 1.6, 0.8, 0.75, BROWN),
]
# (名字, 中心 x, 中心 y, 半径, 高)
CYLINDERS = [
    ("pillar_west", -2, 2.5, 0.3, WALL_H),
    ("pillar_east", 5.5, 0.5, 0.3, WALL_H),
]
ROBOT_START = (-6.5, -3.5, 0.1, 0.0)   # x, y, z（轮轴高度）, yaw


def box(name, x, y, sx, sy, sz, rgba):
    g = f"<box><size>{sx} {sy} {sz}</size></box>"
    return f"""    <model name="{name}"><static>true</static><pose>{x} {y} {sz / 2} 0 0 0</pose>
      <link name="link">
        <collision name="c"><geometry>{g}</geometry></collision>
        <visual name="v"><geometry>{g}</geometry><material><ambient>{rgba}</ambient><diffuse>{rgba}</diffuse></material></visual>
      </link></model>
"""


def cylinder(name, x, y, r, h):
    g = f"<cylinder><radius>{r}</radius><length>{h}</length></cylinder>"
    return f"""    <model name="{name}"><static>true</static><pose>{x} {y} {h / 2} 0 0 0</pose>
      <link name="link">
        <collision name="c"><geometry>{g}</geometry></collision>
        <visual name="v"><geometry>{g}</geometry><material><ambient>{GRAY}</ambient><diffuse>{GRAY}</diffuse></material></visual>
      </link></model>
"""


def world():
    rx, ry, rz, ryaw = ROBOT_START
    parts = [f"""<?xml version="1.0"?>
<!-- 由 tools/make_world.py 生成，不要手改，改布局请改脚本里的列表 -->
<sdf version="1.8">
  <world name="indoor_office">
    <physics name="1ms" type="ignored">
      <max_step_size>0.001</max_step_size>
      <real_time_factor>1.0</real_time_factor>
    </physics>
    <plugin filename="ignition-gazebo-physics-system" name="ignition::gazebo::systems::Physics"/>
    <plugin filename="ignition-gazebo-user-commands-system" name="ignition::gazebo::systems::UserCommands"/>
    <plugin filename="ignition-gazebo-scene-broadcaster-system" name="ignition::gazebo::systems::SceneBroadcaster"/>
    <plugin filename="ignition-gazebo-imu-system" name="ignition::gazebo::systems::Imu"/>
    <!-- 雷达用 ogre2 渲染，服务端用软件渲染运行（见 launch/sim.launch.py）。
         ogre1 的 gpu_lidar 测距误差太大（实测地面点 z 误差标准差约 0.1 m），不要用 -->
    <plugin filename="ignition-gazebo-sensors-system" name="ignition::gazebo::systems::Sensors">
      <render_engine>ogre2</render_engine>
    </plugin>

    <light type="directional" name="sun">
      <cast_shadows>false</cast_shadows><pose>0 0 10 0 0 0</pose>
      <diffuse>0.9 0.9 0.9 1</diffuse><direction>-0.3 0.2 -1</direction>
    </light>

    <model name="ground"><static>true</static>
      <link name="link">
        <collision name="c"><geometry><plane><normal>0 0 1</normal><size>40 40</size></plane></geometry></collision>
        <visual name="v"><geometry><plane><normal>0 0 1</normal><size>40 40</size></plane></geometry>
          <material><ambient>0.5 0.5 0.5 1</ambient><diffuse>0.5 0.5 0.5 1</diffuse></material></visual>
      </link></model>

"""]
    parts += [box(*b) for b in BOXES]
    parts += [cylinder(*c) for c in CYLINDERS]
    parts.append(f"""
    <include>
      <uri>model://sim_robot</uri>
      <pose>{rx} {ry} {rz} 0 0 {ryaw}</pose>
    </include>
  </world>
</sdf>
""")
    return "".join(parts)


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "worlds", "indoor_office.sdf")
    with open(out, "w") as f:
        f.write(world())
    print("已生成", os.path.normpath(out))
