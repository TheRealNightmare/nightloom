# nightloom-worker

Nightloom's background worker: a [Celery](https://docs.celeryq.dev/) app that runs Shuttle jobs from the Valkey queue.

- `nightloom_worker.celery_app`: the Celery app (configured by `nightloom_api.jobs.queue.create_celery`: JSON only, late acknowledgement, one job per process at a time) and its one task, `nightloom.run_job(job_id)`.
- `nightloom_worker.runner`: takes a job from `queued` to its final state. It re-checks every guardrail at execution time, runs the Shuttle with its timeout, normalizes and checks each finding before writing it to the case's graph, and records `job.finish` in the audit log. Errors are stored only as fixed, user-safe messages.

It reuses the API's code (`nightloom-api`: settings, job service, graph repository, audit log) rather than copying it, and installs the built-in Shuttles. See [Jobs and the worker](../../docs/ARCHITECTURE.md#jobs-and-the-worker).

```sh
make worker     # run one locally against the stack's services (make deps-up or make up)
make shuttles   # list the installed Shuttles
```

In the Compose stack it runs as the `worker` service, from the same image as the API.

Licensed under the Apache License 2.0, like the rest of Nightloom.
