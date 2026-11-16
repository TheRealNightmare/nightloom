"""Per-case investigation graphs in Neo4j.

Every entity is a node labelled ``:Entity`` plus its type's label
(``:Domain``, ``:IPAddress``, ...), identified by ``(case_id, key)``: the
same canonical key in two cases is two separate nodes, and every query is
scoped to one case. See the "Data model" section of docs/ARCHITECTURE.md.
"""
