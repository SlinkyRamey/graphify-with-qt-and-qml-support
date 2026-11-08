"""Extract saved Simulink model structure without loading or executing a model."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any, NoReturn
from xml.etree import ElementTree as ET
import zipfile
import zlib

from graphify.extractors.simulink_archive import (
    SimulinkArchive,
    SimulinkInputError,
    XmlPart,
    bounded_text,
    check_catalog,
    parameter,
)
from graphify.extractors.simulink_facts import SimulinkFacts
from graphify.extractors.simulink_stateflow import add_saved_stateflow
from graphify.source_identity import normalize_input_source, walked_relative_source


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SimulinkInputError("SIMULINK_JSON_INVALID", "Model diagram JSON has duplicate keys.")
        result[key] = value
    return result


def _json_constant(_value: str) -> NoReturn:
    raise SimulinkInputError("SIMULINK_JSON_INVALID", "Model diagram JSON contains a nonstandard constant.")


_WORKSPACE_ENTRY = re.compile(
    r"[A-Za-z][A-Za-z0-9_]{0,127}[ \t]*=[ \t]*([A-Za-z][A-Za-z0-9_]{0,127})"
    r"[ \t]*\([ \t]*\)[ \t]*(?:;|%|\Z)"
)
_WORKSPACE_SCAN_LIMIT = 4096


def _workspace_entry(code: str) -> str | None:
    """Admit only a direct zero-argument call on the first assignment statement."""
    for line in code[:_WORKSPACE_SCAN_LIMIT].splitlines(keepends=True):
        if len(code) > _WORKSPACE_SCAN_LIMIT and not line.endswith(("\r", "\n")):
            return None
        statement = line.strip()
        if statement.startswith(("%{", "%}")):
            return None
        if not statement or statement.startswith("%"):
            continue
        match = _WORKSPACE_ENTRY.match(statement)
        return match.group(1) if match else None
    return None


def _relative_reference(value: Any) -> str:
    if isinstance(value, str) and not value.startswith(("/", "\\")) and ":" not in value:
        return bounded_text(value)
    return ""


def _model_fields(dictionary: Any, source_kind: Any, source_file: Any, code: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if (literal := _relative_reference(dictionary)):
        fields["data_dictionary"] = literal
    # The data source selects the active workspace declaration. MATLAB File
    # and MAT-File are distinct mechanisms despite sharing the saved filename.
    if source_kind in ("Model File", "MAT-File", "MATLAB File", "MATLAB Code"):
        fields["workspace_source_kind"] = source_kind
    if source_kind in ("MAT-File", "MATLAB File") and (literal := _relative_reference(source_file)):
        fields["workspace_source_file"] = literal
    if source_kind == "MATLAB Code" and isinstance(code, str) and code.strip():
        fields["workspace_has_matlab_code"] = True
        if (entry := _workspace_entry(code)):
            fields["workspace_entry_call"] = entry
    return fields

def _json_diagram(archive: SimulinkArchive, member: str):
    try:
        doc = json.loads(archive.read(member).decode("utf-8-sig"), object_pairs_hook=_json_object,
                         parse_constant=_json_constant)
    except SimulinkInputError:
        raise
    except (ValueError, RecursionError):
        raise SimulinkInputError("SIMULINK_JSON_INVALID", "Model contains malformed diagram JSON.") from None
    diagram = doc.get("BlockDiagram") if isinstance(doc, dict) else None
    system = diagram.get("System") if isinstance(diagram, dict) else None
    ref = system.get("Ref") if isinstance(system, dict) else None
    if not isinstance(system, dict) or set(system) != {"Ref"} or not isinstance(ref, str) or not ref:
        raise SimulinkInputError("SIMULINK_STRUCTURE", "Model diagram JSON has no supported root system reference.")
    assert isinstance(diagram, dict)
    workspace = diagram.get("ModelWorkspace")
    workspace = workspace if isinstance(workspace, dict) else {}
    details = _model_fields(diagram.get("DataDictionary"), workspace.get("WSDataSource"),
                            workspace.get("WSSourceFileName"),
                            workspace.get("WSMATLABCode"))
    kind = "library" if diagram.get("LibraryType") == "BlockLibrary" else "model"
    part = archive.system(member, ref)
    return part, part.root, kind, details, 1


def _xml_diagram(archive: SimulinkArchive, member: str):
    part = archive.xml(member)
    root = part.root
    if root.tag in {"Model", "Library"}:
        diagrams = [root]
    elif root.tag == "ModelInformation":
        diagrams = [child for child in root if child.tag in {"Model", "Library"}]
    else:
        diagrams = []
    if len(diagrams) != 1:
        raise SimulinkInputError("SIMULINK_STRUCTURE", "Model diagram XML must declare one model or library.")
    diagram = diagrams[0]
    systems = diagram.findall("System")
    if len(systems) != 1:
        raise SimulinkInputError("SIMULINK_STRUCTURE", "Model diagram XML must declare one root system.")
    system = systems[0]
    workspace = diagram.find("ModelWorkspace")
    details = _model_fields(parameter(diagram, "DataDictionary"), parameter(workspace, "WSDataSource"),
                            parameter(workspace, "WSSourceFileName"),
                            parameter(workspace, "WSMATLABCode"))
    line = part.line(diagram)
    if "Ref" in system.attrib:
        if len(system) or not system.attrib["Ref"]:
            raise SimulinkInputError("SIMULINK_STRUCTURE", "Model root system reference has conflicting saved content.")
        part = archive.system(member, system.attrib["Ref"])
        system = part.root
    return part, system, diagram.tag.lower(), details, line


def _source_owner(path: Path, root: Path | None) -> tuple[Path, str]:
    """Keep the admitted walked owner while constraining the actual read target."""
    try:
        admitted = normalize_input_source(path, root)
    except ValueError:
        raise SimulinkInputError("SIMULINK_SOURCE_IDENTITY", "Native model filename could not be admitted.") from None
    physical = admitted.resolve(strict=True)
    if not physical.is_file():
        raise SimulinkInputError("SIMULINK_READ", "Model path must be a regular file.")
    anchor = root.resolve(strict=True) if root is not None else physical.parent
    written = physical if ".." in admitted.parts else Path(os.path.abspath(admitted))
    try:
        relative = walked_relative_source(
            written, anchor, physical, realpath=lambda value, _: Path(value).resolve(), cwd=os.getcwd(),
        )
    except ValueError:
        raise SimulinkInputError("SIMULINK_ROOT", "Model path is outside the scan root.") from None
    return physical, relative.as_posix()


def _failed(relative: str, error: SimulinkInputError) -> dict:
    return {
        "nodes": [], "edges": [], "error": error.code,
        "diagnostics": [{
            "code": error.code, "severity": "error", "owner": "simulink",
            "source_file": relative, "message": str(error),
            "recovery": "Check the saved model and use a supported SLX package.",
        }],
    }


def extract_simulink(path: Path, *, root: Path | None = None) -> dict:
    """Return source-owned blocks, ports, connectivity and literal references."""
    relative = ""
    try:
        physical, relative = _source_owner(Path(path), root)
        with physical.open("rb") as stream:
            count = check_catalog(stream, os.fstat(stream.fileno()).st_size)
            with zipfile.ZipFile(stream) as raw_archive:
                archive = SimulinkArchive(raw_archive, count)
                member = archive.target("", "blockDiagram")
                if member.endswith(".json"):
                    diagram = _json_diagram(archive, member)
                elif member.endswith(".xml"):
                    diagram = _xml_diagram(archive, member)
                else:
                    raise SimulinkInputError("SIMULINK_PACKAGE_UNSUPPORTED", "Unsupported model diagram format.")
                part, system, kind, details, line = diagram
                facts = SimulinkFacts(archive, relative, member, line, kind, details)
                facts.add_system(part, system)
                add_saved_stateflow(facts, member)
                return facts.result()
    except SimulinkInputError as error:
        return _failed(relative, error)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, UnicodeError, zlib.error):
        return _failed(relative, SimulinkInputError("SIMULINK_PACKAGE_INVALID", "Model archive or member is corrupt."))
    except (NotImplementedError, RuntimeError):
        return _failed(relative, SimulinkInputError("SIMULINK_PACKAGE_UNSUPPORTED", "Unsupported model package encoding."))
    except OSError:
        return _failed(relative, SimulinkInputError("SIMULINK_READ", "Model file could not be read."))
