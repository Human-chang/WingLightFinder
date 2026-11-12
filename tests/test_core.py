# -*- coding: utf-8 -*-
import os
import sys
import json
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config, DEFAULT_GROUPS, CONFIG_NAME, LEGACY_CONFIG_NAME  # noqa: E402
from core.scanner import scan_folder, scan_depth_for, classify, FileEntry  # noqa: E402
from core.demo import make_demo_project  # noqa: E402
from core.tabs import TabList  # noqa: E402


def entry(name, mtime, rel_dir=""):
    return FileEntry(name, os.path.join("X:\\p", rel_dir, name), rel_dir, os.path.splitext(name)[1].lower(), mtime, 1)


class ScanDepthTest(unittest.TestCase):
    def test_depth_rule(self):
        self.assertEqual(scan_depth_for("D:\\"), 0)
        self.assertEqual(scan_depth_for("D:\\計畫案"), 2)
        self.assertEqual(scan_depth_for("D:\\計畫案\\南40線"), 3)


class ScanFolderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def touch(self, *parts):
        path = os.path.join(self.root, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "wb").close()

    def test_respects_depth(self):
        self.touch("a.pdf")
        self.touch("L1", "b.pdf")
        self.touch("L1", "L2", "c.pdf")
        self.touch("L1", "L2", "L3", "d.pdf")
        names = {f.name for f in scan_folder(self.root, 2).files}
        self.assertEqual(names, {"a.pdf", "b.pdf", "c.pdf"})

    def test_rel_dir(self):
        self.touch("設計", "審查", "紀錄.pdf")
        (f,) = scan_folder(self.root, 3).files
        self.assertEqual(f.rel_dir, os.path.join("設計", "審查"))
        self.assertEqual(f.rel_path, os.path.join("設計", "審查", "紀錄.pdf"))

    def test_skips_lock_files_and_hidden_dirs(self):
        self.touch("~$報告.docx")
        self.touch("Thumbs.db")
        self.touch(".git", "x.pdf")
        self.touch("$RECYCLE.BIN", "y.pdf")
        self.touch("Windows備份", "z.pdf")   # 只略過名稱完全相同的系統資料夾
        self.touch("報告.docx")
        names = {f.name for f in scan_folder(self.root, 3).files}
        self.assertEqual(names, {"報告.docx", "z.pdf"})

    def test_unreadable_folder_is_reported(self):
        res = scan_folder(os.path.join(self.root, "missing"), 2)
        self.assertEqual(res.files, [])
        self.assertEqual(len(res.unreadable), 1)

    def test_cancel(self):
        self.touch("a.pdf")
        res = scan_folder(self.root, 2, cancelled=lambda: True)
        self.assertTrue(res.cancelled)
        self.assertEqual(res.files, [])


class ClassifyTest(unittest.TestCase):
    groups = [dict(g) for g in DEFAULT_GROUPS]

    def test_groups_and_order(self):
        files = [entry("舊.pdf", 1), entry("新.pdf", 3), entry("圖.dwg", 2), entry("說明.txt", 4)]
        by_group, others = classify(files, self.groups, ["PDF 報告", "CAD 圖面"])
        self.assertEqual([f.name for f in by_group["PDF 報告"]], ["新.pdf", "舊.pdf"])
        self.assertEqual([f.name for f in by_group["CAD 圖面"]], ["圖.dwg"])
        self.assertEqual([f.name for f in others], ["說明.txt"])

    def test_unchecked_known_ext_not_in_others(self):
        _, others = classify([entry("a.docx", 1)], self.groups, ["PDF 報告"])
        self.assertEqual(others, [])

    def test_ignored_ext_not_in_others(self):
        _, others = classify([entry("a.dwl", 1), entry("b.shx", 1), entry("c.dbf", 1)], self.groups, [])
        self.assertEqual([f.name for f in others], ["c.dbf"])

    def test_query_matches_folder_and_name(self):
        files = [entry("平面圖.dwg", 1, "02_設計"), entry("平面圖.dwg", 2, "舊版"), entry("縱斷面.dwg", 3, "02_設計")]
        by_group, _ = classify(files, self.groups, ["CAD 圖面"], query="設計 平面")
        self.assertEqual([(f.rel_dir, f.name) for f in by_group["CAD 圖面"]], [("02_設計", "平面圖.dwg")])

    def test_query_is_case_insensitive(self):
        by_group, _ = classify([entry("C3D_Report.PDF", 1)], self.groups, ["PDF 報告"], query="c3d report")
        self.assertEqual(len(by_group["PDF 報告"]), 1)

    def test_since(self):
        by_group, _ = classify([entry("a.pdf", 10), entry("b.pdf", 20)], self.groups, ["PDF 報告"], since=15)
        self.assertEqual([f.name for f in by_group["PDF 報告"]], ["b.pdf"])


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip(self):
        c = Config(base_dir=self.dir)
        c.favorites.append("D:\\計畫案")
        group = c.add_extension("kmz")
        self.assertIsNone(group)  # .kmz 已經在 GIS 分類裡
        group = c.add_extension(".ifc")
        self.assertEqual(group["exts"], [".ifc"])
        c.group_states["PDF 報告"] = False
        c.tabs = ["D:\\計畫案", "E:\\南40線"]
        c.active_tab = 1
        c.save()

        c2 = Config(base_dir=self.dir)
        self.assertEqual(c2.favorites, ["D:\\計畫案"])
        self.assertEqual(c2.tabs, ["D:\\計畫案", "E:\\南40線"])
        self.assertEqual(c2.active_tab, 1)
        names = {g["name"]: g["on"] for g in c2.groups()}
        self.assertFalse(names["PDF 報告"])
        self.assertTrue(names[".IFC 檔"])

    def test_imports_legacy_favorites(self):
        with open(os.path.join(self.dir, LEGACY_CONFIG_NAME), "w", encoding="utf-8") as f:
            json.dump({"favorites": ["E:\\舊專案"]}, f, ensure_ascii=False)
        c = Config(base_dir=self.dir)
        self.assertEqual(c.favorites, ["E:\\舊專案"])
        c.save()
        self.assertTrue(os.path.exists(os.path.join(self.dir, CONFIG_NAME)))

    def test_pinned_tabs_roundtrip(self):
        c = Config(base_dir=self.dir)
        self.assertTrue(c.remember_session)
        c.pinned_tabs = ["D:\\常用"]
        c.tabs = ["E:\\南40線"]
        c.save()
        c2 = Config(base_dir=self.dir)
        self.assertEqual(c2.pinned_tabs, ["D:\\常用"])
        self.assertEqual(c2.tabs, ["E:\\南40線"])

    def test_not_remembering_keeps_only_pinned_tabs(self):
        c = Config(base_dir=self.dir)
        c.add_extension(".ifc")
        c.pinned_tabs = ["D:\\常用"]
        c.tabs = ["E:\\南40線"]
        c.active_tab = 1
        c.favorites.append("D:\\計畫案")
        c.remember_session = False
        c.save()
        self.assertEqual(len(c.custom_groups), 1)   # 這次開著的不受影響
        c2 = Config(base_dir=self.dir)
        self.assertFalse(c2.remember_session)
        self.assertEqual(c2.pinned_tabs, ["D:\\常用"])
        self.assertEqual(c2.tabs, [])
        self.assertEqual(c2.active_tab, 0)
        self.assertEqual(c2.custom_groups, [])
        self.assertEqual(c2.favorites, ["D:\\計畫案"])  # 我的最愛一律保留

    def test_broken_config_falls_back_to_defaults(self):
        with open(os.path.join(self.dir, CONFIG_NAME), "w", encoding="utf-8") as f:
            f.write("{ 壞掉的 json")
        c = Config(base_dir=self.dir)
        self.assertEqual(c.favorites, [])


class TabListTest(unittest.TestCase):
    def test_starts_with_one_blank_tab(self):
        tabs = TabList()
        self.assertEqual(len(tabs.tabs), 1)
        self.assertEqual(tabs.current.title, "新分頁")

    def test_restore_clamps_active(self):
        tabs = TabList(["D:\\A", "D:\\B"], active=9)
        self.assertEqual(tabs.active, 1)
        self.assertEqual(tabs.current.title, "B")

    def test_add_inserts_right_of_current(self):
        tabs = TabList(["D:\\A", "D:\\B"], active=0)
        index = tabs.add("D:\\C")
        self.assertEqual(index, 1)
        self.assertEqual([t.folder for t in tabs.tabs], ["D:\\A", "D:\\C", "D:\\B"])
        self.assertEqual(tabs.active, 0)  # 新增不會自動切換

    def test_close_keeps_sensible_active(self):
        tabs = TabList(["D:\\A", "D:\\B", "D:\\C"], active=2)
        tabs.close(2)                      # 關掉目前（最右邊）的分頁 → 左邊那個
        self.assertEqual(tabs.current.folder, "D:\\B")
        tabs.close(0)                      # 關掉左邊的分頁，目前分頁不變
        self.assertEqual(tabs.current.folder, "D:\\B")
        tabs.close(0)                      # 全部關掉時留一個空白分頁
        self.assertEqual(len(tabs.tabs), 1)
        self.assertEqual(tabs.current.folder, "")

    def test_step_wraps(self):
        tabs = TabList(["D:\\A", "D:\\B"], active=1)
        tabs.step(1)
        self.assertEqual(tabs.active, 0)
        tabs.step(-1)
        self.assertEqual(tabs.active, 1)

    def test_tabs_keep_their_own_state(self):
        tabs = TabList(["D:\\A", "D:\\B"])
        tabs.tabs[0].query = "平面圖"
        tabs.tabs[0].expanded.add("PDF 報告")
        self.assertEqual(tabs.tabs[1].query, "")
        self.assertEqual(tabs.tabs[1].expanded, set())

    def test_saved_skips_blank_tabs(self):
        tabs = TabList(["D:\\A"])
        tabs.active = tabs.add("")         # 空白分頁
        tabs.active = tabs.add("D:\\B")
        self.assertEqual(tabs.saved_folders(), ["D:\\A", "D:\\B"])
        self.assertEqual(tabs.saved_active(), 1)

    def test_title_of_drive_root(self):
        self.assertEqual(TabList(["D:\\"]).current.title, "D:\\")

    def folders(self, tabs):
        return [t.folder for t in tabs.tabs]

    def test_pinned_come_first(self):
        tabs = TabList(["D:\\A"], active=1, pinned=["D:\\P"])
        self.assertEqual(self.folders(tabs), ["D:\\P", "D:\\A"])
        self.assertTrue(tabs.tabs[0].pinned)
        self.assertEqual(tabs.current.folder, "D:\\A")
        self.assertEqual(tabs.saved_pinned(), ["D:\\P"])
        self.assertEqual(tabs.saved_folders(), ["D:\\A"])
        self.assertEqual(tabs.saved_active(), 1)

    def test_only_pinned_when_not_remembering(self):
        tabs = TabList([], active=0, pinned=["D:\\P", "D:\\Q"])
        self.assertEqual(self.folders(tabs), ["D:\\P", "D:\\Q"])
        self.assertEqual(tabs.current.folder, "D:\\P")

    def test_pinned_cannot_close(self):
        tabs = TabList(["D:\\A"], pinned=["D:\\P"])
        self.assertFalse(tabs.close(0))
        self.assertEqual(self.folders(tabs), ["D:\\P", "D:\\A"])

    def test_add_never_goes_into_pinned_area(self):
        tabs = TabList(["D:\\A"], active=0, pinned=["D:\\P", "D:\\Q"])
        index = tabs.add("D:\\B")
        self.assertEqual(index, 2)
        self.assertEqual(self.folders(tabs), ["D:\\P", "D:\\Q", "D:\\B", "D:\\A"])

    def test_move(self):
        tabs = TabList(["D:\\A", "D:\\B", "D:\\C"], active=0)
        self.assertEqual(tabs.move(0, 3), 2)       # 拖到最右邊
        self.assertEqual(self.folders(tabs), ["D:\\B", "D:\\C", "D:\\A"])
        self.assertEqual(tabs.current.folder, "D:\\A")  # 目前分頁跟著移動
        tabs.move(1, 0)
        self.assertEqual(self.folders(tabs), ["D:\\C", "D:\\B", "D:\\A"])
        tabs.move(1, 1)                            # 放回原位
        tabs.move(1, 2)
        self.assertEqual(self.folders(tabs), ["D:\\C", "D:\\B", "D:\\A"])

    def test_move_stays_in_own_area(self):
        tabs = TabList(["D:\\A", "D:\\B"], pinned=["D:\\P", "D:\\Q"])
        tabs.move(3, 0)                            # 一般分頁拖到最左邊 → 停在釘選區右邊
        self.assertEqual(self.folders(tabs), ["D:\\P", "D:\\Q", "D:\\B", "D:\\A"])
        tabs.move(0, 4)                            # 釘選分頁拖到最右邊 → 停在釘選區最右邊
        self.assertEqual(self.folders(tabs), ["D:\\Q", "D:\\P", "D:\\B", "D:\\A"])

    def test_pin_and_unpin_move_to_the_boundary(self):
        tabs = TabList(["D:\\A", "D:\\B", "D:\\C"], active=1, pinned=["D:\\P"])
        self.assertTrue(tabs.set_pinned(3, True))
        self.assertEqual(self.folders(tabs), ["D:\\P", "D:\\C", "D:\\A", "D:\\B"])
        self.assertEqual(tabs.current.folder, "D:\\A")
        self.assertTrue(tabs.set_pinned(0, False))
        self.assertEqual(self.folders(tabs), ["D:\\C", "D:\\P", "D:\\A", "D:\\B"])
        self.assertEqual(tabs.saved_pinned(), ["D:\\C"])

    def test_blank_tab_cannot_be_pinned(self):
        tabs = TabList()
        self.assertFalse(tabs.set_pinned(0, True))

    def test_same_folder_twice_are_different_tabs(self):
        tabs = TabList(["D:\\A", "D:\\A"], active=1)
        tabs.move(1, 0)
        self.assertEqual(tabs.active, 0)


class DemoProjectTest(unittest.TestCase):
    def test_mtimes_are_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_demo_project(tmp)
            res = scan_folder(root, 3)
            years = {__import__("datetime").datetime.fromtimestamp(f.mtime).year for f in res.files}
            self.assertEqual(years, {2024, 2025, 2026})


if __name__ == "__main__":
    unittest.main()
