# nightloom-core

Nightloom's shared entity model, used by the API (and later the worker and Shuttles):

- `nightloom_core.entities`: `EntityType`, one Pydantic model per type with a canonical `value`, typed `properties` and a `sensitivity` (`infrastructure`, `organization` or `person`); `build_entity()` builds one from untrusted input.
- `nightloom_core.normalize`: one canonical key per entity, `<type>:<normalized>`, and `NormalizationError` with user-safe messages. Nothing here touches the network: the public suffix list is the snapshot bundled with `tldextract`.
- `nightloom_core.relationships`: `RelationType` and the allow-list of (source type, target type) pairs; `validate_relationship()`.
- `nightloom_core.provenance`: `Observation` (source, time, confidence, Admiralty rating, evidence reference, note) and the defaults for entries made by hand.

See the "Data model" section of [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md#data-model) for the rules. The tests (`packages/core/tests/`) need no services; run them with `make test-unit` or `make test` from the repository root.

```python
from nightloom_core.entities import EntityType, build_entity

domain = build_entity(EntityType.DOMAIN, "Example.ORG.")
domain.value  # "example.org"
domain.key  # "domain:example.org"
```

Licensed under the Apache License 2.0, like the rest of Nightloom.
