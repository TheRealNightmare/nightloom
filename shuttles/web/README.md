# nightloom-shuttle-web

Built-in Nightloom Shuttles that **visit a site's public pages**, like a
browser or a search engine (`mode=visit`). They run where a case's owners
allow website visits (*Settings → Allow website visits*), or on domains the
case has verified.

| Name | Input | What it does |
|---|---|---|
| `site-files` | Domain | Reads `robots.txt`, `security.txt` and the sitemap (at most four requests). Adds the paths and pages they list as URLs, and records whether a `security.txt` exists, its expiry and which kinds of contact it gives (never the contacts). |
| `favicon` | Domain | Finds the icon the home page declares (or `/favicon.ico`), reads it (at most three requests) and adds it as a Favicon named by its MurmurHash3, the hash Shodan (`http.favicon.hash`) and FOFA (`icon_hash`) search for. Sites that serve the same icon meet on one node. |
| `web-tech` | Domain | Opens the home page once and recognizes its software (server, CMS, frameworks, analytics) from headers, cookie names, the generator tag and markers in the page. Records the title and embedded analytics IDs. |

All three follow redirects only on the same host, read at most a fixed number of
bytes, and are cached for a day. See
[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md#website-visits).
