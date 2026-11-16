# -*- coding: utf-8 -*-
"""把 WingLight 系列標誌畫成「關於」視窗用的 PNG（開發時執行，需要 Pillow；程式執行時只讀 PNG）。

    python tools\\make_logo_images.py

讀 assets/logos_src/winglight-mark.svg，用規範 kit 的 make_app_icon.render_svg() 畫成
assets/logos/winglight-mark_<尺寸>.png：64px 的位置依螢幕縮放挑 64、80、96、128。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_app_icon import LOGO_SOURCE_DIR, PROJECT_ROOT, render_svg  # noqa: E402

MARK_SVG = LOGO_SOURCE_DIR / "winglight-mark.svg"
OUTPUT_DIR = PROJECT_ROOT / "assets" / "logos"
SIZES = (64, 80, 96, 128)


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        path = OUTPUT_DIR / f"winglight-mark_{size}.png"
        render_svg(MARK_SVG, size).save(path, optimize=True)
        print(f"已產生 {path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
