# nightloom-sdk

The Shuttle SDK (import `nightloom_sdk`): everything needed to write a Nightloom Shuttle.

- `ShuttleMeta`: what a Shuttle declares (name, version, input and output types, mode, sensitivity, network use, rate limit, timeout, Admiralty defaults). Validated against `nightloom_core`'s relationship allow-list and sensitivities.
- `Shuttle`: the base class; `run(ctx, entity)` is an async generator of findings.
- `EntityFinding`, `RelationshipFinding`, `EntityRef`: what a Shuttle yields. Values are raw; the worker normalizes and checks them.
- `ShuttleContext`: the logger, the guarded HTTP client (`ctx.http`: SSRF guard after DNS resolution, http/https only, per-host rate limit, 5 MB response cap), API keys, cancellation and progress.
- `InputRefusedError` / `RefusalReason`: refuse an input before doing any work; the job fails with the reason's fixed message.
- `Shuttle.check_configuration()` / `ShuttleConfigurationError`: check a Shuttle's own settings; the worker refuses to start if one fails.
- `nightloom_sdk.registry`: discovers Shuttles from the `nightloom.shuttles` entry-point group. `python -m nightloom_sdk.registry list` lists the installed ones, `check` runs their configuration checks.
- `nightloom_sdk.testing`: `entity_input()`, `fake_context()`, `collect()` and `assert_declared()` for unit tests without services or network.

See "Writing a Shuttle" in [CONTRIBUTING.md](../../CONTRIBUTING.md#writing-a-shuttle) and the [Shuttle contract](../../docs/ARCHITECTURE.md#shuttle-contract).

Licensed under the Apache License 2.0, like the rest of Nightloom.
