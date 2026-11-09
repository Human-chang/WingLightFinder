# -*- coding: utf-8 -*-
"""在專案底下的 demo_project 資料夾產生範例工程資料夾，截圖、錄影用。

    python tools/make_demo_folder.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.demo import make_demo_project  # noqa: E402

if __name__ == "__main__":
    path = make_demo_project(os.path.join(ROOT, "demo_project"))
    print(path)
