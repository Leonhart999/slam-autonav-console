import os
from glob import glob
from setuptools import setup

package_name = 'lidar_nav'


def files(pattern):
    return [f for f in glob(pattern) if os.path.isfile(f)]


setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), files('launch/*.launch.py')),
        (os.path.join('share', package_name, 'config'), files('config/*.yaml')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Leonhart999',
    maintainer_email='1425109609@qq.com',
    description='激光 SLAM 建图后的地图处理、定位与导航',
    license='MIT',
    entry_points={
        'console_scripts': [
            'pcd_to_grid = lidar_nav.pcd_to_grid:main',
            'nav_goals = lidar_nav.nav_goals:main',
        ],
    },
)
