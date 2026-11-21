# nightloom-shuttle-active

Built-in **active** Nightloom Shuttles: they connect to the target itself,
so they run only on targets a case has verified it controls (*Settings →
Verified domains*; the API and the worker check it before every run).

| Name | Input | What it does |
|---|---|---|
| `http-headers` | Domain | Opens `https://<domain>/` (same-host redirects only) and `http://<domain>/`, headers only. Records the status, the security headers sent and missing, `Server` / `X-Powered-By`, the TLS version, the certificate presented, and the address and service that answered. |
| `port-check` | IP address | A plain TCP connect to 20 common ports, four at a time, 3-second timeout, closed at once without sending anything. Each open port becomes a Service. |

Neither is cached (what they see is the target, now), and neither can be
part of a scan profile or an auto-pivot. See
[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md#the-active-shuttles).
