"""
File: setup.py
Description: Packaging metadata and installation rules for the deepstream ROS2 package.
Author: Yuhao Ding
"""

from glob import glob
import os

from setuptools import find_packages, setup


package_name = "deepstream"
model_assets = [
    path for path in glob("models/*") if not path.endswith(".download")
]


setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        (
            "share/ament_index/resource_index/packages",
            [os.path.join("resource", package_name)],
        ),
        (os.path.join("share", package_name), ["package.xml"]),
        (
            os.path.join("share", package_name, "launch"),
            glob("launch/*.launch.py"),
        ),
        (os.path.join("share", package_name, "config"), glob("config/*")),
        (os.path.join("share", package_name, "models"), model_assets),
        (
            os.path.join("share", package_name, "lib"),
            glob(".local/lib/libnvdsinfer_yolov11s.so"),
        ),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Yuhao Ding",
    maintainer_email="d346863528@163.com",
    author="Yuhao Ding",
    author_email="d346863528@163.com",
    description="ROS2 DeepStream package with base and SAHI modes.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "deepstream_node = deepstream.node:main",
        ],
    },
)
