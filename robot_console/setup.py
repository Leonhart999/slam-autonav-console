import os
from glob import glob
from setuptools import setup

package_name = 'robot_console'


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
    description='Web console for LiDAR SLAM mapping, evaluation and Nav2 navigation',
    license='MIT',
    entry_points={
        'console_scripts': [
            'console = robot_console.main:main',
        ],
    },
)
