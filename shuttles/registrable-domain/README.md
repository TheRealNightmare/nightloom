# nightloom-shuttle-registrable-domain

A built-in Nightloom Shuttle that needs no network. For a domain it finds the **registrable domain** (the part a registrant controls, one label below the public suffix) using the Public Suffix List snapshot bundled with `tldextract`, via `nightloom_core.normalize.registrable_domain`:

| Input | Findings |
| --- | --- |
| `www.shop.example.co.uk` | Domain `example.co.uk`, and `example.co.uk -HAS_SUBDOMAIN-> www.shop.example.co.uk` |
| `example.co.uk` (already registrable) | nothing |
| `co.uk` (a public suffix) | nothing |

Only the ICANN section of the list is used, as everywhere in `nightloom_core`: privately registered suffixes such as `github.io` are not treated as public suffixes, so `docs.project.github.io` gives `github.io`. Wildcard and exception rules apply (`a.www.example.kawasaki.jp` → `www.example.kawasaki.jp`, `www.city.kawasaki.jp` → `city.kawasaki.jp`). Internationalized names work in their stored A-label form (`shop.пример.рф` → `xn--e1afmkfd.xn--p1ai`).

Passive, infrastructure-only, `network=False`. Registered under the `nightloom.shuttles` entry point as `registrable-domain`.

Licensed under the Apache License 2.0, like the rest of Nightloom.
