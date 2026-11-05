import os
from glob import glob
from setuptools import setup

package_name = 'sim_robot'


def files(pattern):
    return [f for f in glob(pattern) if os.path.isfile(f)]


def tree(src_dir):
    """把一个目录（含子目录）原样装到 share/sim_robot/ 下，返回 data_files 条目"""
    entries = []
    for root, _, names in os.walk(src_dir):
        if names:
            entries.append((os.path.join('share', package_name, root), [os.path.join(root, n) for n in names]))
    return entries


setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        # 场景、模型、启动文件和配置都安装到 share/sim_robot 下
        (os.path.join('share', package_name, 'launch'), files('launch/*.launch.py')),
        (os.path.join('share', package_name, 'worlds'), files('worlds/*.sdf')),
        (os.path.join('share', package_name, 'config'), files('config/*.yaml')),
        (os.path.join('share', package_name, 'models', 'sim_robot'), files('models/sim_robot/*')),
    ] + tree('config/glim')              # GLIM configuration (the console's mapping_glim stack reads it from share/)
      + tree('models/fuel')              # Gazebo Fuel 上下载的仓库模型（带 meshes 子目录）
      + [e for d in sorted(glob('models/bim_*')) for e in tree(d)],   # SLABIM BIM-based buildings
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Leonhart999',
    maintainer_email='1425109609@qq.com',
    description='室内 SLAM / 导航仿真环境',
    license='MIT',
    entry_points={
        'console_scripts': [
            'cloud_adapter = sim_robot.cloud_adapter:main',
            'waypoint_driver = sim_robot.waypoint_driver:main',
        ],
    },
)
