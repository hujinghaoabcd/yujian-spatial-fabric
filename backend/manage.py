#!/usr/bin/env python
"""Django 管理命令入口。

本文件只负责选择默认本地设置并交给 Django；环境差异放到 config.settings.* 中。
"""

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
