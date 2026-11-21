import type { Verification, VerificationMethod, VerificationStatus } from "../api/ownership";
import { formatTimestamp } from "../domain/format";

export const VERIFIED_DOMAINS_HINT =
  "Active Shuttles touch the target itself, so they only run on domains this case has proven it controls, their subdomains and the addresses they resolve to. Verify a domain with a DNS record or a file on its website; Nightloom checks it again every day, and a verification lasts 30 days unless you check it again.";

export const METHOD_LABELS: Record<VerificationMethod, string> = {
  dns_txt: "DNS TXT record",
  http_file: "File on the website",
};

export const STATUS_LABELS: Record<VerificationStatus, string> = {
  pending: "Not verified yet",
  verified: "Verified",
  failed: "Not found",
  revoked: "Revoked",
  expired: "Expired",
};

/** One line under the status: until when, or why not. */
export function statusDetail(item: Verification): string {
  if (item.checking) return "Looking for the proof…";
  switch (item.status) {
    case "verified":
      return item.expires_at === null ? "" : `Until ${formatTimestamp(item.expires_at)}`;
    case "pending":
      return "Publish the proof, then check.";
    case "failed":
      return item.error_message ?? "The proof was not found.";
    case "revoked":
      return `The daily check no longer found the proof${item.error_message ? `: ${item.error_message}` : "."}`;
    case "expired":
      return "30 days passed. Check again to renew.";
  }
}

/** What to tell the owner to publish. */
export function proofInstructions(item: Verification): string {
  return item.method === "dns_txt"
    ? "Add a TXT record with this name and value at your DNS provider. Changes can take a few minutes to appear."
    : "Serve this line as a text file at this address, over HTTPS and without redirects.";
}
