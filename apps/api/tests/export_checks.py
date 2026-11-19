"""Reading and checking export files in tests (importable as ``export_checks``)."""

import csv
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from lxml import etree

from nightloom_api.export.formats import GRAPHML_NAMESPACE

XSD_PATH = Path(__file__).parent / "fixtures" / "graphml-structure.xsd"
NS = {"g": GRAPHML_NAMESPACE}
KEY_TYPES = {"boolean", "int", "long", "float", "double", "string"}


def graphml_schema() -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(XSD_PATH)))


def validate_graphml(data: bytes) -> Any:
    """Parse GraphML, validate it against the bundled schema and check typed values.

    Returns the root element. Beyond the schema: every ``data`` element's key
    is declared for its element's kind (``for``), and every value parses as
    its key's ``attr.type``.
    """
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    root = etree.fromstring(data, parser)
    schema = graphml_schema()
    assert schema.validate(root), schema.error_log
    keys = {
        key.get("id"): (key.get("for"), key.get("attr.type")) for key in root.findall("g:key", NS)
    }
    assert all(type_ in KEY_TYPES for _scope, type_ in keys.values())
    for element in root.iter(f"{{{GRAPHML_NAMESPACE}}}data"):
        scope, type_ = keys[element.get("key")]
        parent = etree.QName(element.getparent()).localname
        assert scope in (parent, "all"), (element.get("key"), parent)
        text = element.text or ""
        if type_ == "double":
            float(text)
        elif type_ in ("int", "long"):
            int(text)
        elif type_ == "boolean":
            assert text in ("true", "false")
    return root


def graphml_items(root: Any) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Nodes and edges as ``{attr.name: text}`` (edges also get ``source``/``target`` keys)."""
    names = {key.get("id"): key.get("attr.name") for key in root.findall("g:key", NS)}

    def values(element: Any) -> dict[str, str]:
        return {names[data.get("key")]: data.text or "" for data in element.findall("g:data", NS)}

    nodes = [{"@id": node.get("id"), **values(node)} for node in root.findall("g:graph/g:node", NS)]
    by_id = {node["@id"]: node["key"] for node in nodes}
    edges = [
        {
            **values(edge),
            "source_key": by_id[edge.get("source")],
            "target_key": by_id[edge.get("target")],
        }
        for edge in root.findall("g:graph/g:edge", NS)
    ]
    return nodes, edges


def read_zip(data: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name).decode() for name in archive.namelist()}


def read_csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text, newline="")))


def read_json(data: bytes) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(data)
    return result
