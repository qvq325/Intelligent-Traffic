"""
白名单管理模块
支持加载、保存、匹配车牌白名单，提供精确匹配和模糊匹配规则
"""
import json
import os
import re
from dataclasses import dataclass, field
from typing import List, Set, Dict, Optional, Tuple


@dataclass
class WhitelistEntry:
    """白名单条目"""
    plate: str            # 车牌号码，如 "京A12345"
    note: str = ""        # 备注信息，如 "测试车辆A"
    added_at: str = ""    # 添加时间


@dataclass
class MatchResult:
    """白名单匹配结果"""
    matched: bool
    plate: str
    matched_entry: Optional[WhitelistEntry] = None
    match_rule: str = ""  # 匹配规则描述


class WhitelistManager:
    """
    车牌白名单管理器

    匹配规则:
    1. 精确匹配: 完全一致的号码匹配
    2. 部分匹配: 允许部分字符模糊（如忽略最后一位）
    3. 前缀匹配: 以指定前缀开头的车牌视为白名单

    使用方式:
        wm = WhitelistManager()
        wm.load("whitelist.json")
        wm.add("京A12345", note="测试车")
        result = wm.check("京A12345")  # MatchResult
    """

    def __init__(self):
        self._entries: Dict[str, WhitelistEntry] = {}  # plate -> entry（用于精确匹配）
        self._prefix_set: Set[str] = set()              # 前缀白名单
        self._enabled = True                            # 白名单功能开关

    # ---- 加载与保存 ----

    def load(self, filepath: str) -> int:
        """
        从 JSON 文件加载白名单

        文件格式:
        [
            {"plate": "京A12345", "note": "测试车辆", "added_at": "2025-01-01"},
            ...
        ]

        Args:
            filepath: JSON 文件路径

        Returns:
            加载的条目数量
        """
        if not os.path.exists(filepath):
            print(f"[Whitelist] 白名单文件不存在: {filepath}")
            return 0

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"[Whitelist] 加载失败: {e}")
            return 0

        if not isinstance(data, list):
            print("[Whitelist] 格式错误: 应为列表")
            return 0

        count = 0
        for item in data:
            plate = item.get("plate", "").strip().upper()
            if plate:
                entry = WhitelistEntry(
                    plate=plate,
                    note=item.get("note", ""),
                    added_at=item.get("added_at", ""),
                )
                self._entries[plate] = entry
                count += 1

        self._rebuild_prefix_set()
        print(f"[Whitelist] 已加载 {count} 条白名单记录")
        return count

    def save(self, filepath: str) -> bool:
        """
        保存白名单到 JSON 文件

        Args:
            filepath: 目标 JSON 文件路径

        Returns:
            是否保存成功
        """
        data = []
        for entry in self._entries.values():
            data.append({
                "plate": entry.plate,
                "note": entry.note,
                "added_at": entry.added_at,
            })

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"[Whitelist] 已保存 {len(data)} 条记录到 {filepath}")
            return True
        except IOError as e:
            print(f"[Whitelist] 保存失败: {e}")
            return False

    # ---- 增删改查 ----

    def add(self, plate: str, note: str = "") -> bool:
        """
        添加白名单条目

        Args:
            plate: 车牌号码
            note: 备注信息

        Returns:
            是否成功添加（车牌已存在时返回 False）
        """
        plate = plate.strip().upper()
        if not plate:
            return False

        if plate in self._entries:
            # 更新备注
            self._entries[plate].note = note
            return False

        from datetime import datetime
        entry = WhitelistEntry(
            plate=plate,
            note=note,
            added_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        self._entries[plate] = entry
        self._rebuild_prefix_set()
        return True

    def remove(self, plate: str) -> bool:
        """
        移除白名单条目

        Args:
            plate: 车牌号码

        Returns:
            是否成功移除
        """
        plate = plate.strip().upper()
        if plate in self._entries:
            del self._entries[plate]
            self._rebuild_prefix_set()
            return True
        return False

    def clear(self):
        """清空白名单"""
        self._entries.clear()
        self._prefix_set.clear()

    # ---- 查询 ----

    def get_all(self) -> List[WhitelistEntry]:
        """获取所有白名单条目"""
        return list(self._entries.values())

    def get(self, plate: str) -> Optional[WhitelistEntry]:
        """根据车牌号获取白名单条目"""
        return self._entries.get(plate.strip().upper())

    @property
    def count(self) -> int:
        return len(self._entries)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value

    # ---- 匹配规则 ----

    def check(self, plate: str) -> MatchResult:
        """
        检查车牌是否在白名单中

        匹配规则（优先级从高到低）:
        1. 精确匹配: 完全一致
        2. 前缀匹配: 车牌以白名单中任意前缀开头
        3. 归一化匹配: 去除空格、连字符后比较

        Args:
            plate: 待检查的车牌号码

        Returns:
            MatchResult 对象
        """
        if not self._enabled:
            return MatchResult(matched=False, plate=plate, match_rule="白名单功能未启用")

        plate = plate.strip().upper()
        if not plate:
            return MatchResult(matched=False, plate=plate, match_rule="无效车牌")

        # 规则1: 精确匹配
        if plate in self._entries:
            return MatchResult(
                matched=True,
                plate=plate,
                matched_entry=self._entries[plate],
                match_rule="精确匹配",
            )

        # 规则2: 前缀匹配
        for prefix in sorted(self._prefix_set, key=len, reverse=True):
            if plate.startswith(prefix):
                return MatchResult(
                    matched=True,
                    plate=plate,
                    match_rule=f"前缀匹配: {prefix}*",
                )

        # 规则3: 归一化匹配（去除空格、-、· 等分隔符）
        normalized = self._normalize(plate)
        for entry_plate, entry in self._entries.items():
            if self._normalize(entry_plate) == normalized:
                return MatchResult(
                    matched=True,
                    plate=plate,
                    matched_entry=entry,
                    match_rule="归一化匹配",
                )

        return MatchResult(matched=False, plate=plate, match_rule="未匹配")

    # ---- 内部方法 ----

    def _rebuild_prefix_set(self):
        """重建前缀匹配集合"""
        self._prefix_set = set()
        for plate in self._entries.keys():
            # 将每个车牌的前 N 个字符作为前缀（N >= 3）
            for n in range(3, len(plate) + 1):
                self._prefix_set.add(plate[:n])

    @staticmethod
    def _normalize(plate: str) -> str:
        """
        规范化车牌号码：去除空格、连字符、点等分隔符
        例如 "京A-12345" -> "京A12345"
        """
        return re.sub(r'[\s\-·\._]+', '', plate).upper()
