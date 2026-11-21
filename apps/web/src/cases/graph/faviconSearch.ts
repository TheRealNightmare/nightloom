/**
 * Searches for a favicon on internet-wide scan services, by the hash each one
 * uses: Shodan and FOFA by the MurmurHash3 (the Favicon's value), Censys by
 * the file's SHA-256. Nothing is sent until the analyst opens a link.
 */

export interface FaviconSearch {
  service: string;
  url: string;
}

export function faviconSearches(hash: string, sha256?: string): FaviconSearch[] {
  const searches: FaviconSearch[] = [
    {
      service: "Shodan",
      url: `https://www.shodan.io/search?query=${encodeURIComponent(`http.favicon.hash:${hash}`)}`,
    },
    {
      // FOFA takes its query as base64.
      service: "FOFA",
      url: `https://en.fofa.info/result?qbase64=${encodeURIComponent(btoa(`icon_hash="${hash}"`))}`,
    },
  ];
  if (sha256 && /^[0-9a-f]{64}$/.test(sha256)) {
    const query =
      `host.services.endpoints.http.favicons.hash_sha256: "${sha256}"` +
      ` or web.endpoints.http.favicons.hash_sha256: "${sha256}"`;
    searches.push({
      service: "Censys",
      url: `https://platform.censys.io/search?q=${encodeURIComponent(query)}`,
    });
  }
  return searches;
}
