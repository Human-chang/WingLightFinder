# -*- coding: utf-8 -*-
"""WingLightFinder — 專案檔案速查。

用法：
    WingLightFinder.exe [資料夾]            啟動，並直接開啟指定的資料夾
    WingLightFinder.exe --self-test [結果.json]   不顯示視窗，自動檢查掃描、分類與介面
"""

import os
import sys
import json
import ctypes
import tempfile
import traceback
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config import Config, DEFAULT_GROUPS  # noqa: E402
from core.scanner import scan_folder, classify, IGNORED_EXTS  # noqa: E402
from core.demo import make_demo_project, FILES  # noqa: E402


def _prepare_windows():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HumanChang.WingLightFinder")
    except (AttributeError, OSError):
        pass


def run_self_test(result_path=None) -> int:
    checks = []

    def check(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)})

    try:
        with tempfile.TemporaryDirectory() as tmp:
            project = make_demo_project(tmp)
            expected = [n for _, n, _ in FILES if not n.startswith("~$")]

            res = scan_folder(project, 3)
            names = sorted(f.name for f in res.files)
            check("掃描到所有範例檔（略過 ~$ 鎖定檔）", names == sorted(expected), f"{len(names)}/{len(expected)}")
            check("沒有無法讀取的資料夾", not res.unreadable, res.unreadable)

            groups = [dict(g) for g in DEFAULT_GROUPS]
            by_group, others = classify(res.files, groups, [g["name"] for g in groups])
            pdfs = [f.name for f in by_group["PDF 報告"]]
            check("PDF 分欄數量正確", len(pdfs) == sum(n.endswith(".pdf") for n in expected), pdfs)
            check("由新到舊排序", [f.mtime for f in by_group["CAD 圖面"]] ==
                  sorted((f.mtime for f in by_group["CAD 圖面"]), reverse=True))
            check("CAD 鎖定檔與 Shapefile 附屬檔不會出現在「其他」",
                  not any(f.ext in IGNORED_EXTS for f in others), [f.name for f in others])

            hits, _ = classify(res.files, groups, ["PDF 報告"], query="審查 紀錄")
            check("關鍵字搜尋（多個關鍵字）", sorted(f.name for f in hits["PDF 報告"]) ==
                  ["第1次審查會議紀錄.pdf", "第2次審查會議紀錄.pdf"], [f.name for f in hits["PDF 報告"]])

            missing = scan_folder(os.path.join(tmp, "不存在的資料夾"), 2)
            check("讀不到的資料夾會被記錄，不會中斷", missing.unreadable and not missing.files, missing.unreadable)

            # 介面：不顯示視窗，跑一次完整的開啟資料夾流程
            from ui.app import FinderApp, OTHERS_KEY
            root = tk.Tk()
            root.withdraw()
            try:
                app = FinderApp(root, Config(base_dir=tmp))
                app.open_folder(project)
                for _ in range(400):
                    root.update()
                    if project in app.cache and app.column_keys:
                        break
                    root.after(25)
                on_groups = [g["name"] for g in DEFAULT_GROUPS if g["default_on"]]
                check("介面建立預設分欄", app.column_keys == on_groups + [OTHERS_KEY], app.column_keys)
                pdf_rows = [f.name for f in app.item_files.values() if f.ext == ".pdf"]
                check("介面列出 PDF 檔", len(pdf_rows) == len(pdfs), pdf_rows)

                app.search_var.set("審查")
                app._apply_search()
                shown = sorted(f.name for f in app.item_files.values())
                check("介面搜尋只留下符合的檔案", shown and all("審查" in f.rel_path for f in app.item_files.values()),
                      shown)

                app.config.favorites.append(project)
                app._save_config()
                check("設定檔存在程式旁邊", Config(base_dir=tmp).favorites == [project], app.config.path)
            finally:
                root.destroy()
    except Exception:
        check("自我測試沒有發生例外", False, traceback.format_exc())

    ok = all(c["ok"] for c in checks)
    report = {"ok": ok, "checks": checks}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if result_path:
        with open(result_path, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        try:
            print(text)
        except (OSError, AttributeError, UnicodeEncodeError):
            pass
    return 0 if ok else 1


def main():
    args = sys.argv[1:]
    if args and args[0] == "--self-test":
        sys.exit(run_self_test(args[1] if len(args) > 1 else None))

    _prepare_windows()
    from ui.app import FinderApp
    root = tk.Tk()
    app = FinderApp(root)
    if args and os.path.isdir(args[0]):
        root.after(100, lambda: app.open_folder(os.path.normpath(args[0])))
    root.mainloop()


if __name__ == "__main__":
    main()
