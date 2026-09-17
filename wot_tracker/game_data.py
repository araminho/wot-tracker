from __future__ import annotations

import gettext
import struct
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


PACKED_MAGIC = b"EN\xa1b"
DATA_POS_MASK = 0x0FFFFFFF
TYPE_MASK = 0xF0000000
TYPE_DATA_SECTION = 0x00000000
TYPE_STRING = 0x10000000
TYPE_INT = 0x20000000
TYPE_FLOAT = 0x30000000
TYPE_BOOL = 0x40000000

VEHICLE_CLASSES = {"lightTank", "mediumTank", "heavyTank", "AT-SPG", "SPG"}
LOCALIZATION_NATIONS = {"uk": "gb"}


@dataclass(frozen=True)
class VehicleInfo:
    name: str = ""
    tier: int | str = ""
    vehicle_class: str = ""


@dataclass
class Section:
    name: str
    value: Any
    children: list["Section"]

    def child(self, name: str) -> "Section | None":
        return next((item for item in self.children if item.name == name), None)


def _decode_scalar(data: bytes, kind: int) -> Any:
    if kind == TYPE_STRING:
        return data.rstrip(b"\0").decode("utf-8", errors="replace")
    if kind == TYPE_INT:
        if not data:
            return 0
        formats = {1: "<b", 2: "<h", 4: "<i", 8: "<q"}
        return struct.unpack(formats[len(data)], data)[0] if len(data) in formats else ""
    if kind == TYPE_FLOAT:
        if len(data) and len(data) % 4 == 0:
            values = struct.unpack("<" + "f" * (len(data) // 4), data)
            return values[0] if len(values) == 1 else values
        return ""
    if kind == TYPE_BOOL:
        return bool(data and data[0])
    return data


def _decode_section(data: bytes, strings: list[str], name: str, kind: int = TYPE_DATA_SECTION) -> Section:
    if kind != TYPE_DATA_SECTION:
        return Section(name, _decode_scalar(data, kind), [])
    if len(data) < 6:
        return Section(name, "", [])
    count = struct.unpack_from("<h", data, 0)[0]
    if count < 0 or 2 + count * 6 + 4 > len(data):
        raise ValueError("Invalid packed section child table")
    records: list[tuple[int, int]] = []
    offset = 2
    for _ in range(count):
        data_pos, key_pos = struct.unpack_from("<ih", data, offset)
        records.append((data_pos, key_pos))
        offset += 6
    final_pos = struct.unpack_from("<i", data, offset)[0]
    positions = [record[0] for record in records] + [final_pos]
    block_start = offset + 4
    own_end = positions[0] & DATA_POS_MASK if positions else 0
    own_kind = positions[0] & TYPE_MASK if positions else TYPE_STRING
    own = _decode_scalar(data[block_start : block_start + own_end], own_kind)
    children: list[Section] = []
    for index, (_, key_pos) in enumerate(records):
        start = positions[index] & DATA_POS_MASK
        end = positions[index + 1] & DATA_POS_MASK
        child_kind = positions[index + 1] & TYPE_MASK
        if start > end or block_start + end > len(data) or not 0 <= key_pos < len(strings):
            raise ValueError("Invalid packed section child record")
        children.append(_decode_section(data[block_start + start : block_start + end], strings, strings[key_pos], child_kind))
    return Section(name, own, children)


def decode_packed_xml(data: bytes) -> Section:
    if not data.startswith(PACKED_MAGIC) or len(data) < 6:
        raise ValueError("Not a BigWorld packed XML file")
    pos = 5  # four-byte magic and one-byte format version
    strings: list[str] = []
    while pos < len(data):
        end = data.find(b"\0", pos)
        if end < 0:
            raise ValueError("Unterminated packed XML string table")
        if end == pos:
            pos += 1
            break
        strings.append(data[pos:end].decode("utf-8", errors="replace"))
        pos = end + 1
    return _decode_section(data[pos:], strings, "root")


class VehicleCatalog:
    def __init__(self, game_root: str | Path):
        self.game_root = Path(game_root)
        self.scripts = self.game_root / "res" / "packages" / "scripts.pkg"
        self.messages = self.game_root / "res" / "text" / "lc_messages"

    @lru_cache(maxsize=512)
    def lookup(self, tank_id: str) -> VehicleInfo:
        nation, separator, code = tank_id.partition(":")
        if not separator or not code:
            return VehicleInfo()
        name = self._localized_name(nation, code)
        tier: int | str = ""
        vehicle_class = ""
        try:
            with zipfile.ZipFile(self.scripts) as package:
                packed = package.read(f"scripts/item_defs/vehicles/{nation}/list.xml")
            root = decode_packed_xml(packed)
            vehicle = root.child(code)
            level = vehicle.child("level") if vehicle else None
            tags = vehicle.child("tags") if vehicle else None
            if level and isinstance(level.value, int):
                tier = level.value
            if tags and isinstance(tags.value, str):
                vehicle_class = next((tag for tag in tags.value.split() if tag in VEHICLE_CLASSES), "")
        except (OSError, KeyError, zipfile.BadZipFile, ValueError, struct.error):
            pass
        return VehicleInfo(name=name, tier=tier, vehicle_class=vehicle_class)

    def _localized_name(self, nation: str, code: str) -> str:
        path = self.messages / f"{LOCALIZATION_NATIONS.get(nation, nation)}_vehicles.mo"
        try:
            with path.open("rb") as stream:
                translated = gettext.GNUTranslations(stream).gettext(code)
            return translated if translated != code else ""
        except OSError:
            return ""
