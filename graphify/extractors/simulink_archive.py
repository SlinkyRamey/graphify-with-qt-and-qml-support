"""Bounded, read-only access to the supported Simulink package parts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re
import stat
import struct
from typing import BinaryIO, NoReturn
from xml.etree import ElementTree as ET
from xml.parsers import expat
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_CATALOG_BYTES = 1024 * 1024
MAX_MEMBERS = 2048
MAX_PART_BYTES = 8 * 1024 * 1024
MAX_TOTAL_PART_BYTES = 64 * 1024 * 1024
MAX_XML_ELEMENTS = 120_000
MAX_DEPTH = 64
MAX_FIELD_BYTES = 512

_RELATIONSHIPS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_RELATIONSHIP_TYPES = {
    "blockDiagram": "http://schemas.mathworks.com/simulink/2010/relationships/blockDiagram",
    "system": "http://schemas.mathworks.com/simulink/2010/relationships/system",
    "stateflowMachine": "http://schemas.mathworks.com/simulinkModel/2014/relationships/stateflowMachine",
    "stateflowChart": "http://schemas.mathworks.com/simulinkModel/2018/relationships/stateflowMachine",
}
_SAFE_PATH = re.compile(r"[A-Za-z0-9_.\[\]/-]+\Z")
_END_DIRECTORY = struct.Struct("<4s4H2IH")


class SimulinkInputError(ValueError):
    """Carry a safe category and message without reporting archive contents."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def bounded_text(value: str) -> str:
    """Retain literal values within the metadata transport's string budget."""
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise SimulinkInputError("SIMULINK_STRUCTURE", "Model field is not valid Unicode.") from None
    if size > MAX_FIELD_BYTES:
        raise SimulinkInputError("SIMULINK_LIMIT", "A model field exceeds its text size limit.")
    return value


def parameter(element: ET.Element | None, name: str) -> str:
    """Read one direct saved parameter without consulting defaults or executing it."""
    if element is None:
        return ""
    entries = element.findall(f"./P[@Name='{name}']")
    if len(entries) > 1:
        raise SimulinkInputError("SIMULINK_STRUCTURE", "Model element contains duplicate saved parameters.")
    return (entries[0].text or "") if entries else ""


def _unsafe_xml(*_args: object) -> NoReturn:
    raise SimulinkInputError("SIMULINK_XML_UNSAFE", "XML document types and external entities are unsupported.")


def _member_name(name: str) -> str:
    """Accept literal internal names only. Archive data never becomes an OS path."""
    parts = name.split("/")
    if (len(name) > MAX_FIELD_BYTES or not _SAFE_PATH.fullmatch(name)
            or any(part in {"", ".", ".."} for part in parts)):
        raise SimulinkInputError("SIMULINK_PACKAGE_PATH", "Package member name is outside the supported literal form.")
    return name


def check_catalog(stream: BinaryIO, size: int) -> int:
    """Bound central directory processing before ZipFile allocates its index."""
    if size > MAX_ARCHIVE_BYTES:
        raise SimulinkInputError("SIMULINK_LIMIT", "Model archive exceeds the input size limit.")
    tail_size = min(size, _END_DIRECTORY.size + 65_535 + 20)
    stream.seek(size - tail_size)
    tail = stream.read(tail_size)
    end = tail.rfind(b"PK\x05\x06")
    if end < 0 or len(tail) - end < _END_DIRECTORY.size:
        raise SimulinkInputError("SIMULINK_PACKAGE_INVALID", "Model archive has no readable ZIP catalog.")
    _, disk, catalog_disk, disk_count, count, catalog_size, catalog_offset, comment_size = (
        _END_DIRECTORY.unpack_from(tail, end)
    )
    if len(tail) - end != _END_DIRECTORY.size + comment_size:
        raise SimulinkInputError("SIMULINK_PACKAGE_INVALID", "Model archive has an invalid ZIP catalog.")
    if disk or catalog_disk or disk_count != count or (end >= 20 and tail[end - 20:end - 16] == b"PK\x06\x07"):
        raise SimulinkInputError("SIMULINK_PACKAGE_UNSUPPORTED", "Only single-disk, non-ZIP64 model archives are supported.")
    if count > MAX_MEMBERS or catalog_size > MAX_CATALOG_BYTES:
        raise SimulinkInputError("SIMULINK_LIMIT", "Model archive catalog exceeds its member or size limit.")
    if catalog_offset + catalog_size != size - tail_size + end:
        raise SimulinkInputError("SIMULINK_PACKAGE_INVALID", "Model archive has inconsistent ZIP catalog offsets.")
    return count


@dataclass(frozen=True)
class XmlPart:
    """Each XML element keeps the opening line in its actual archive member."""

    member: str
    root: ET.Element
    lines: dict[ET.Element, int]

    def line(self, element: ET.Element) -> int:
        return self.lines[element]


def _parse_xml(member: str, raw: bytes) -> XmlPart:
    builder = ET.TreeBuilder()
    parser = expat.ParserCreate(namespace_separator="}")
    parser.buffer_text = True
    parser.StartDoctypeDeclHandler = _unsafe_xml
    parser.ExternalEntityRefHandler = _unsafe_xml
    positions: dict[ET.Element, int] = {}
    depth = 0

    def qualified_name(name: str) -> str:
        return "{" + name if "}" in name else name

    def start(name: str, attributes: dict[str, str]) -> None:
        nonlocal depth
        depth += 1
        if depth > MAX_DEPTH or len(positions) >= MAX_XML_ELEMENTS:
            raise SimulinkInputError("SIMULINK_LIMIT", "Package XML exceeds its nesting or element limit.")
        element = builder.start(qualified_name(name), {qualified_name(k): v for k, v in attributes.items()})
        positions[element] = parser.CurrentLineNumber

    def end(name: str) -> None:
        nonlocal depth
        depth -= 1
        builder.end(qualified_name(name))

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = builder.data
    try:
        parser.Parse(raw, True)
    except (expat.ExpatError, LookupError):
        raise SimulinkInputError("SIMULINK_XML_INVALID", "Model contains malformed package XML.") from None
    return XmlPart(member, builder.close(), positions)


class SimulinkArchive:
    """One invocation owns the catalog, read budget and XML/relationship indexes."""

    def __init__(self, archive: ZipFile, expected_members: int):
        self.archive = archive
        self.members: dict[str, ZipInfo] = {}
        self.xml_parts: dict[str, XmlPart] = {}
        self.relationships: dict[tuple[str, str], dict[str, str]] = {}
        self.bytes_read = 0
        self.xml_elements = 0
        aliases: set[str] = set()
        entries = archive.infolist()
        if len(entries) > MAX_MEMBERS:
            raise SimulinkInputError("SIMULINK_LIMIT", "Model archive exceeds the member limit.")
        if len(entries) != expected_members:
            raise SimulinkInputError("SIMULINK_PACKAGE_INVALID", "Package member count does not match its catalog.")
        for entry in entries:
            name = _member_name(entry.filename.rstrip("/") if entry.is_dir() else entry.filename)
            if entry.orig_filename != entry.filename or name.casefold() in aliases:
                raise SimulinkInputError("SIMULINK_PACKAGE_PATH", "Model archive has ambiguous package member names.")
            aliases.add(name.casefold())
            if not entry.is_dir():
                self.members[name] = entry

    def read(self, name: str) -> bytes:
        entry = self.members.get(name)
        if entry is None:
            raise SimulinkInputError("SIMULINK_PACKAGE_MEMBER", "A referenced model package member is missing.")
        if entry.flag_bits & 1:
            raise SimulinkInputError("SIMULINK_PACKAGE_UNSUPPORTED", "Encrypted package members are unsupported.")
        if entry.compress_type not in {ZIP_STORED, ZIP_DEFLATED} or (
                entry.create_system == 3 and stat.S_ISLNK(entry.external_attr >> 16)):
            raise SimulinkInputError("SIMULINK_PACKAGE_UNSUPPORTED", "Unsupported compression or linked package member.")
        limit = min(MAX_PART_BYTES, MAX_TOTAL_PART_BYTES - self.bytes_read)
        if entry.file_size > limit:
            raise SimulinkInputError("SIMULINK_LIMIT", "Package parts exceed their decompression size limit.")
        with self.archive.open(entry) as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise SimulinkInputError("SIMULINK_LIMIT", "Package parts exceed their decompression size limit.")
        if len(raw) != entry.file_size:
            raise SimulinkInputError("SIMULINK_PACKAGE_INVALID", "Package member length does not match its catalog.")
        self.bytes_read += len(raw)
        return raw

    def xml(self, name: str) -> XmlPart:
        if name not in self.xml_parts:
            part = _parse_xml(name, self.read(name))
            self.xml_elements += len(part.lines)
            if self.xml_elements > MAX_XML_ELEMENTS:
                raise SimulinkInputError("SIMULINK_LIMIT", "Package XML exceeds the total element limit.")
            self.xml_parts[name] = part
        return self.xml_parts[name]

    def _relationship_part(self, source: str) -> tuple[str, PurePosixPath]:
        if not source:
            return "_rels/.rels", PurePosixPath()
        path = PurePosixPath(source)
        return str(path.parent / "_rels" / (path.name + ".rels")), path.parent

    def _targets(self, source: str, kind: str) -> dict[str, str]:
        """Honor only known internal relationship types and exact package names."""
        rels, directory = self._relationship_part(source)
        doc = self.xml(rels)
        if doc.root.tag != _RELATIONSHIPS + "Relationships":
            raise SimulinkInputError("SIMULINK_PACKAGE_INVALID", "Invalid OPC relationship document.")
        targets = {}
        identities: set[str] = set()
        for relation in doc.root.findall(_RELATIONSHIPS + "Relationship"):
            key = relation.get("Id", "")
            if not key or key in identities:
                raise SimulinkInputError("SIMULINK_PACKAGE_PATH", "Package relationship identifiers are ambiguous.")
            identities.add(key)
            if relation.get("Type") != _RELATIONSHIP_TYPES[kind]:
                continue
            target = relation.get("Target", "")
            if not target or relation.get("TargetMode", "Internal") != "Internal":
                raise SimulinkInputError("SIMULINK_PACKAGE_PATH", "Model package relationship is ambiguous or external.")
            literal = _member_name(target.removeprefix("/"))
            targets[key] = literal if target.startswith("/") else _member_name(str(directory / literal))
        return targets

    def _cached_targets(self, source: str, kind: str) -> dict[str, str]:
        key = (source, kind)
        if key not in self.relationships:
            self.relationships[key] = self._targets(source, kind)
        return self.relationships[key]

    def optional_target(self, source: str, kind: str) -> str | None:
        rels, _ = self._relationship_part(source)
        if rels not in self.members:
            return None
        targets = self._cached_targets(source, kind)
        if len(targets) > 1:
            raise SimulinkInputError("SIMULINK_PACKAGE_MEMBER", "Optional model package relationship is ambiguous.")
        return next(iter(targets.values()), None)

    def target(self, source: str, kind: str, ref: str | None = None) -> str:
        targets = self._cached_targets(source, kind)
        if ref is None and len(targets) == 1:
            return next(iter(targets.values()))
        if ref is not None and ref in targets:
            return targets[ref]
        raise SimulinkInputError("SIMULINK_PACKAGE_MEMBER", "Required model package relationship is missing or ambiguous.")


    def system(self, source: str, ref: str) -> XmlPart:
        """Read a split system only through its declared package relationship."""
        part = self.xml(self.target(source, "system", ref))
        if part.root.tag != "System" or "Ref" in part.root.attrib:
            raise SimulinkInputError("SIMULINK_STRUCTURE", "Referenced system has an unsupported root.")
        return part
