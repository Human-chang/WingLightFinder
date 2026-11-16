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
                def wait(done):
                    for _ in range(400):
                        root.update()
                        if done():
                            return
                        root.after(25)

                app = FinderApp(root, Config(base_dir=tmp))
                app.open_folder(project)
                wait(lambda: project in app.cache and app.column_keys)
                on_groups = [g["name"] for g in DEFAULT_GROUPS if g["default_on"]]
                check("介面建立預設分欄（「其他」預設不顯示）", app.column_keys == on_groups, app.column_keys)
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
                app._remove_favorite_path(project)
                check("移除最愛", Config(base_dir=tmp).favorites == [] and not app.tree.get_children(app.fav_node),
                      Config(base_dir=tmp).favorites)

                # 資料夾樹的箭頭畫在圖示裡：沒有子項目時不畫，展開後朝下
                def tree_image(node):
                    return str(app.tree.item(node, "image")[0])
                check("資料夾樹：空的分組不畫箭頭",
                      tree_image(app.fav_node) == str(app.chevrons.image("star", "leaf")), tree_image(app.fav_node))
                app._add_favorite_path(project)
                node = app.tree.get_children(app.fav_node)[0]
                app.tree.focus(node)
                app.tree.event_generate("<<TreeviewOpen>>")
                app.tree.item(node, open=True)
                check("資料夾樹：展開後箭頭朝下、子資料夾收合",
                      tree_image(node) == str(app.chevrons.image("folder", "open"))
                      and app.tree.get_children(node)
                      and tree_image(app.tree.get_children(node)[0]) == str(app.chevrons.image("folder", "closed")),
                      tree_image(node))
                app._remove_favorite_path(project)

                # 分頁：新分頁開子資料夾，各分頁的搜尋字互不影響
                sub = os.path.join(project, "02_設計")
                app.new_tab(sub)
                wait(lambda: sub in app.cache and app.column_keys)
                check("新分頁開啟子資料夾", app.tabset.active == 1 and app.current_folder == sub, app.current_folder)
                check("新分頁的搜尋字是空的", app.search_var.get() == "", app.search_var.get())
                check("新分頁只列子資料夾裡的檔案",
                      app.item_files and all(f.path.startswith(sub) for f in app.item_files.values()),
                      sorted(f.rel_path for f in app.item_files.values()))
                app.switch_tab(0)
                root.update()
                check("切回第一個分頁，搜尋字和結果都還在",
                      app.search_var.get() == "審查" and app.item_files
                      and all("審查" in f.rel_path for f in app.item_files.values()),
                      sorted(f.rel_path for f in app.item_files.values()))
                check("分頁存進設定檔", Config(base_dir=tmp).tabs == [project, sub], Config(base_dir=tmp).tabs)
                app.close_tab(1)
                check("關閉分頁", len(app.tabset.tabs) == 1 and app.current_folder == project)
                check("關閉後設定檔跟著更新", Config(base_dir=tmp).tabs == [project], Config(base_dir=tmp).tabs)

                app.others_var.set(True)
                app._on_others_toggled()
                check("勾選「其他」後才出現其他欄", app.column_keys == on_groups + [OTHERS_KEY], app.column_keys)

                # 自動更新：資料夾裡多了檔案，背景重掃後畫面跟著更新
                app.search_var.set("")
                app._apply_search()
                with open(os.path.join(sub, "新增的平面圖.pdf"), "wb"):
                    pass
                app.refresh_quietly(project, min_age=0)
                wait(lambda: any(f.name == "新增的平面圖.pdf" for f in app.item_files.values()))
                status = app.status.text()
                check("背景重掃後出現新增的檔案",
                      any(f.name == "新增的平面圖.pdf" for f in app.item_files.values()), status)
                check("狀態列說明自動更新了什麼", "已自動更新：新增 1 個" in status, status)

                # 深淺色主題：即時切換，記在設定檔（暫存資料夾裡的，不碰使用者的設定）
                from tkinter import ttk
                from ui.winglight_tk import T
                first = app.theme.name
                app.toggle_theme()
                second = app.theme.name
                dark = second == "dark"
                colors = (root.cget("bg"), app.status.cget("bg"), ttk.Style(root).lookup("Treeview", "background"))
                check("切換深淺色主題，畫面跟著換色",
                      second != first and colors == (T["bg"][dark], T["chrome"][dark], T["panel"][dark]),
                      (first, second, colors))
                check("主題記在設定檔", Config(base_dir=tmp).theme == second, Config(base_dir=tmp).theme)

                # 線條圖示：都載入了，切換主題時就地重塗成新主題的 muted
                names = ("folder", "star", "refresh", "search", "check", "warning", "chevron-down", "sun", "moon",
                         "calendar", "info", "copy")
                missing = [n for n in names if app.icons.get(n) is None]
                check("線條圖示都載入了", not missing, f"缺 {missing}（找的位置：{app.icons.dir}）")
                mask = app.icons.mask("folder")
                xy = next(((x, y) for y, row in enumerate(mask.rows) for x, a in enumerate(row) if a == 255), (0, 0))
                rgb = "#%02X%02X%02X" % tuple(app.icons.get("folder").get(*xy))
                check("切換主題時圖示跟著重塗", rgb == T["muted"][dark], (rgb, T["muted"][dark]))
                app.set_status("掃描中…", kind="busy")
                check("處理中的狀態列有 glow 圓點",
                      app.status._dot is not None and str(app.status.icon.cget("image")) == str(app.status._dot))

                from core.config import assets_dir
                from core.version import AUTHOR_URL, VERSION
                from ui.about import AboutDialog
                about = AboutDialog(root, app.theme, assets_dir())
                check("關於視窗有版本、作者網址與系列標誌",
                      VERSION in about.version_label.cget("text") and about.link.cget("text") == AUTHOR_URL
                      and about.mark is not None, (about.version_label.cget("text"), assets_dir()))
                about.win.destroy()

                # 釘選：存進設定檔、不能關、從左邊開資料夾會開在新分頁
                app.set_tab_pinned(0, True)
                saved = Config(base_dir=tmp)
                check("釘選的分頁另外存", saved.pinned_tabs == [project] and saved.tabs == [],
                      (saved.pinned_tabs, saved.tabs))
                app.close_tab(0)
                check("釘選的分頁不能關", len(app.tabset.tabs) == 1 and app.tab.pinned)
                app.open_from_tree(sub)
                wait(lambda: sub in app.cache and app.column_keys)
                folders = [t.folder for t in app.tabset.tabs]
                check("釘選分頁裡從左邊開資料夾會開在新分頁",
                      folders == [project, sub] and app.tabset.active == 1, folders)

                # 不記住上次畫面：下次開啟只剩釘選的分頁
                app.entry_ext.insert(0, ".ifc")
                app.add_extension()
                app.remember_var.set(False)
                app._on_remember_toggled()
                root2 = tk.Tk()
                root2.withdraw()
                try:
                    app2 = FinderApp(root2, Config(base_dir=tmp))
                    folders = [t.folder for t in app2.tabset.tabs]
                    names = [g["name"] for g in app2.groups]
                    check("不記住上次畫面時只打開釘選的分頁", folders == [project], folders)
                    check("不記住上次畫面時不保留加入的副檔名", ".IFC 檔" not in names, names)
                    check("下次開啟沿用選過的主題", app2.theme.name == second, app2.theme.name)
                finally:
                    root2.destroy()
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
        # 指定的資料夾開在新分頁，不蓋掉上次留下來的分頁
        folder = os.path.normpath(args[0])
        root.after(100, lambda: app.new_tab(folder) if app.tab.folder else app.open_folder(folder))
    root.mainloop()


if __name__ == "__main__":
    main()
