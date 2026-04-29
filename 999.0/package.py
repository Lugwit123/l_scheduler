# -*- coding: utf-8 -*-

name = "l_scheduler"
version = "999.0"
description = "Lugwit scheduled task runner - cron-style job scheduler"
authors = ["Lugwit Team"]

requires = ["python-3.12+<3.13", "Lugwit_Module", "watchdog"]

build_command = False
cachable = True
relocatable = True

def commands():
    env.PYTHONPATH.prepend("{root}/src")
    env.L_SCHEDULER_ROOT = "{root}"

    alias("l_scheduler", "python {root}/src/l_scheduler/main.py")
