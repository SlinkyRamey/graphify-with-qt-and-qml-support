"""Source-backed Q_PROPERTY declarations and narrowly evidenced accessor links."""
from __future__ import annotations

import re

from graphify.extractors.cpp_constructors import _decoded
from graphify.extractors.cpp_member_identity import member_fact
from graphify.extractors.qt_cpp_api_shape import return_spelling
from graphify.extractors.qt_cpp_api_types import api_type_fields
from graphify.extractors.qt_cpp_mapping import normalize_type
from graphify.extractors.qt_cpp_registration import conditional_offset

_ATTRIBUTE = re.compile(r"\b(READ|WRITE|MEMBER|RESET|NOTIFY|REVISION|DESIGNABLE|SCRIPTABLE|STORED|USER|BINDABLE|CONSTANT|FINAL|VIRTUAL|OVERRIDE|REQUIRED)\b")
_HEAD = re.compile(r"\s*(.+?)(?:\s+|(?<=[*&]))([A-Za-z_]\w*)\s*")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_MODIFIERS = {"VIRTUAL", "OVERRIDE"}
_VALUES = {"READ", "WRITE", "MEMBER", "RESET", "NOTIFY", "BINDABLE",
           "REVISION", "DESIGNABLE", "SCRIPTABLE", "STORED", "USER"}


def _property_parts(text):
    """Attribute position cannot turn an ordinary property/accessor name into a flag."""
    matches = list(_ATTRIBUTE.finditer(text))
    # A keyword spelling may itself be the type or property name. Attribute
    # interpretation starts only after a complete declaration head exists.
    for index, token in enumerate(matches):
        if head := _HEAD.fullmatch(text[:token.start()]):
            matches = matches[index:]
            break
    else:
        return _HEAD.fullmatch(text), []
    tokens = []
    for token in matches:
        # READ VIRTUAL / MEMBER OVERRIDE consume the word as their value. A
        # subsequent modifier remains distinct, even with the same spelling.
        if token[1] in _MODIFIERS and tokens and tokens[-1][1] in _VALUES:
            if not text[tokens[-1].end():token.start()].strip():
                continue
        tokens.append(token)
    return head, tokens


def property_fields(macro):
    """Keep unsupported expressions literal; never expand a preprocessor wrapper."""
    text = macro["args"][0] if len(macro["args"]) == 1 else ""
    match, tokens = _property_parts(text)
    if not match:
        return {"status": "unsupported", "reason": "property_declaration_unsupported", "raw_name": ""}
    fields = {"raw_name": match[2], "raw_type": normalize_type(match[1]), "status": "resolved", "reason": ""}
    for index, token in enumerate(tokens):
        value = text[token.end():tokens[index + 1].start() if index + 1 < len(tokens) else len(text)].strip()
        key = token[1].lower()
        if key in fields:
            fields.update(status="unsupported", reason="duplicate_property_attribute")
        # These are source markers, not value clauses or proof of runtime/moc
        # override validity. Preserve combinations without simulating an engine.
        if token[1] in _MODIFIERS and value:
            fields.update(status="unsupported", reason="property_modifier_value_unsupported")
        fields[key] = value or True
    if not fields.get("read") and not fields.get("member"):
        fields.update(status="unsupported", reason="property_read_or_member_missing")
    # 'default' is a moc directive for READ and WRITE. It requires a literal
    # BINDABLE member name; an expression cannot substitute for that name.
    bindable = fields.get("bindable")
    if (fields["status"] == "resolved" and (fields.get("read") == "default" or fields.get("write") == "default")
            and not (isinstance(bindable, str) and _IDENTIFIER.fullmatch(bindable))):
        fields.update(status="unsupported", reason="generated_accessor_requires_literal_bindable")
    return fields


def _bindable_metaobject(mapping, class_record):
    """The class needs one direct, unconditional source marker for moc intent."""
    unit = mapping.unit
    markers = [macro for macro in class_record["macros"]
               if macro["name"] in {"Q_OBJECT", "Q_GADGET", "Q_GADGET_EXPORT"}
               and mapping.class_at(macro["start_byte"]) is class_record
               and mapping.owner_at(macro["start_byte"]) is None]
    if not markers:
        return "unavailable", "bindable_metaobject_declaration_unavailable"
    if len(markers) != 1:
        return "ambiguous", "bindable_metaobject_declaration_ambiguous"
    marker = markers[0]
    if conditional_offset(unit, marker["start_byte"]):
        return "dynamic", "conditional_bindable_metaobject_declaration"
    if marker["name"] == "Q_GADGET_EXPORT":
        valid = len(marker["args"]) == 1 and _IDENTIFIER.fullmatch(marker["args"][0])
    else:
        valid = unit.source[marker["start_byte"]:marker["end_byte"]] == marker["name"].encode()
    return ("resolved", "") if valid else ("unsupported", "bindable_metaobject_declaration_unsupported")


def _bindable_target(mapping, class_record, macro, fields, types):
    """Prove a direct source declaration for a generated READ/WRITE accessor."""
    if fields["status"] != "resolved":
        return fields["status"], fields["reason"], ""
    if not class_record["node_id"]:
        return "unavailable", "bindable_declaration_unavailable", ""
    if mapping.owner_at(macro["start_byte"]) is not None:
        return "unsupported", "bindable_property_scope_unsupported", ""
    status, reason = _bindable_metaobject(mapping, class_record)
    if status != "resolved":
        return status, reason, ""
    # Inspect all same-class candidates before filtering their signatures. An
    # overload can accept zero arguments via defaults, and macro conditions
    # cannot supply an unconditional target.
    body = class_record["syntax"].child_by_field_name("body")
    candidates = [record for record in mapping.functions if record["name"] == fields["bindable"]
                  and record["class_id"] == class_record["node_id"]
                  and mapping.class_at(record["span"]["start_byte"]) is class_record]
    if len(candidates) > 1:
        return "ambiguous", "bindable_declaration_ambiguous", ""
    if not candidates:
        return "unavailable", "bindable_declaration_unavailable", ""
    record = candidates[0]
    declaration = record["syntax"]
    unit = mapping.unit
    if conditional_offset(unit, declaration.start_byte):
        return "dynamic", "conditional_bindable_declaration", ""
    declarators = declaration.children_by_field_name("declarator")
    expected = f"QBindable<{fields['raw_type']}>"
    if (declaration.parent != body or len(declarators) != 1 or declarators[0].type != "function_declarator"
            or record["parameter_types"]):
        return "unsupported", "bindable_declaration_shape_unsupported", ""
    spelling = normalize_type(return_spelling(unit, declaration, declarators[0]))
    if spelling.removeprefix("::") != expected:
        return "unsupported", "bindable_declaration_shape_unsupported", ""
    if types is None or not types.sdk_type(spelling.partition("<")[0], declaration.start_byte, "QBindable"):
        return "unavailable", "bindable_type_identity_unavailable", ""
    # A name-based mapping alone cannot lend a C++ declaration identity.
    target = mapping.accepted.get(record["node_id"], {})
    proof = member_fact(target)
    if (not proof or proof["ambiguous"] or proof["role"] != "declaration" or proof["span"] != record["span"]
            or mapping.paths[id(target)] != unit.relative_file
            or _decoded(proof["owner_b64"]) != class_record["qualified_name"]):
        return "unavailable", "bindable_declaration_unavailable", ""
    return "resolved", "", target["id"]


def add_properties(unit, mapping, facts, class_record, *, types=None):
    """Properties are independent Qt facts; generic accessor IDs are borrowed."""
    for macro in class_record["macros"]:
        if macro["name"] != "Q_PROPERTY" or mapping.class_at(macro["start_byte"]) is not class_record:
            continue
        fields = property_fields(macro)
        if conditional_offset(unit, macro["start_byte"]):
            fields.update(status="dynamic", reason="conditional_property_declaration")
        name = fields.pop("raw_name")
        if types is not None:
            fields.update(api_type_fields(types, fields.get("raw_type", ""), macro["start_byte"],
                                          conditional=conditional_offset(unit, macro["start_byte"])))
        site = facts.add("property", name, macro["span"], owner=class_record["node_id"] or None,
                         class_id=class_record["node_id"], class_name=class_record["qualified_name"],
                         generic_target_id="", **fields)
        # Keep generated accessors as property-owned facts. Their evidence
        # points to the BINDABLE source member, never to a generated callable.
        bindable_proof = None
        for role in ("read", "write", "reset", "notify"):
            accessor = fields.get(role)
            if not isinstance(accessor, str):
                continue
            if role in {"read", "write"} and accessor == "default":
                if bindable_proof is None:
                    bindable_proof = _bindable_target(mapping, class_record, macro, fields, types)
                status, reason, target = bindable_proof
                bindable = fields.get("bindable")
                link = facts.add("property_accessor", accessor, macro["span"], owner=site["id"],
                                 semantic_key=[site["id"], role], property_id=site["id"], accessor_role=role,
                                 status=status, reason=reason, generic_target_id="", accessor_source="qt_bindable_default",
                                 bindable=bindable if isinstance(bindable, str) else "", bindable_target_id=target)
                if target:
                    facts.edge(link["id"], target, "references", macro["span"], context="qt_property_bindable_accessor",
                               property_id=site["id"], accessor_role=role)
                continue
            candidates = [item for item in mapping.functions if item["class_id"] == class_record["node_id"]
                          and item["name"] == accessor and item["node_id"]]
            if role == "notify":
                candidates = [item for item in candidates if "signal" in item["roles"]
                              and item["return_type"] == "void" and item["parameter_types"] in
                              ([], [fields.get("raw_type")])]
            elif role in {"read", "reset"}:
                candidates = [item for item in candidates if not item["parameter_types"]]
            elif role == "write":
                candidates = [item for item in candidates if len(item["parameter_types"]) == 1]
            signatures = {item["signature"] for item in candidates}
            ids = {item["node_id"] for item in candidates}
            status = "resolved" if len(ids) == len(signatures) == 1 else "ambiguous" if candidates else "unavailable"
            link = facts.add("property_accessor", accessor, macro["span"], owner=site["id"],
                             semantic_key=[site["id"], role], property_id=site["id"], accessor_role=role,
                             status=status, reason="" if status == "resolved" else "accessor_not_unique_or_unavailable",
                             generic_target_id=next(iter(ids)) if status == "resolved" else "")
            if status == "resolved":
                facts.edge(link["id"], next(iter(ids)), "references", macro["span"],
                           context="qt_property_accessor", property_id=site["id"], accessor_role=role)
