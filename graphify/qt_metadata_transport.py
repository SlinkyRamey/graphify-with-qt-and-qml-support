"""Decode persisted Qt/QML metadata without loading extractor integrations."""
from __future__ import annotations

import base64
import json
from collections.abc import Mapping


def qml_metadata(node: dict) -> dict:
    """Read literal lookup values after graph sanitation/JSON round trips.

    Display values retain Graphify's HTML escaping. Their bounded base64 copies
    preserve semantic punctuation without trusting HTML as a lookup encoding.
    """
    meta = dict(node.get("metadata", {}).get("qml", {}))
    raw = meta.get("raw_values", {})
    if not isinstance(raw, Mapping) or len(raw) > 50:
        raise ValueError("QML_METADATA: invalid literal transport mapping")
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str) or not isinstance(meta.get(key), str):
            raise ValueError("QML_METADATA: invalid literal transport field")
        if len(value) > 512:
            raise ValueError("QML_LIMIT: literal transport exceeds its encoded bound")
        try:
            meta[key] = base64.b64decode(value, validate=True).decode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise ValueError("QML_METADATA: invalid literal transport encoding") from exc
    return meta


def qt_metadata(node: dict) -> dict:
    """Reject corrupted transport instead of interpreting escaped display strings."""
    value = node.get("metadata", {}).get("qt", {})
    if not isinstance(value, dict) or value.get("contract_version") != 1:
        return {}
    result = json.loads(json.dumps(value))
    literals = result.get("raw_values", {})
    if not isinstance(literals, dict) or len(literals) > 100:
        raise ValueError("QT_METADATA: malformed literal transport")
    for key, encoded in literals.items():
        if not isinstance(key, str) or not isinstance(encoded, str) or len(encoded) > 512:
            raise ValueError("QT_METADATA: malformed literal field")
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
            parts, current = key.split("/"), result
            for part in parts[:-1]:
                current = current[int(part)] if isinstance(current, list) else current[part]
            if isinstance(current, list):
                current[int(parts[-1])] = decoded
            else:
                current[parts[-1]] = decoded
        except (ValueError, UnicodeError, KeyError, IndexError, TypeError) as exc:
            raise ValueError("QT_METADATA: malformed literal field") from exc
    return result
