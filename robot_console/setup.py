import os
from glob import glob
from setuptools import setup

package_name = 'robot_console'


def files(pattern):
    return [f for f in glob(pattern) if os.path.isfile(f)]


def plugin_files():
    """Plugins shipped with the console (plugins/<id>/...) -> share/robot_console/plugins/<id>/..., without their
    frontend sources (web/: the built ui/ is installed) and caches."""
    out = {}
    for root, dirs, names in os.walk('plugins'):
        dirs[:] = [d for d in dirs if d not in ('web', '__pycache__', 'node_modules')]
        for n in names:
            out.setdefault(os.path.join('share', package_name, root), []).append(os.path.join(root, n))
    return sorted(out.items())


setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), files('launch/*.launch.py')),
        (os.path.join('share', package_name, 'config'), files('config/*.yaml')),
        (os.path.join('share', package_name, 'config', 'slam'), files('config/slam/*.yaml')),
        (os.path.join('share', package_name, 'config', 'glim_lidar_only'), files('config/glim_lidar_only/*.json')),
        *plugin_files(),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Leonhart999',
    maintainer_email='1425109609@qq.com',
    description='Web console for LiDAR SLAM mapping, evaluation and Nav2 navigation',
    license='MIT',
    entry_points={
        'console_scripts': [
            'console = robot_console.main:main',
            'sensor_adapter = robot_console.sensor_adapter:main',
            'fastlio_loop = robot_console.fastlio_loop_backend:main',
            'fastlio_loop_record = robot_console.fastlio_loop_record:main',
            'fastlio_loop_export = robot_console.fastlio_loop_export:main',
        ],
    },
)
