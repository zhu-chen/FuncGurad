"""把仓库根目录加入 sys.path，使测试在干净 clone 上直接 `pytest` 即可运行。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
