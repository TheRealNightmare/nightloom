/**
 * Nightloom mark: a thin crescent above two crossing threads, with a knot
 * where they cross. Colors come from the design tokens, so it follows the
 * theme (public/favicon.svg is the same drawing for the browser tab).
 */
export function LogoMark({ size = 32 }: { size?: number }) {
  return (
    <svg
      className="logo-mark"
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      {/* Crescent: an outer arc closed by a smaller inner one. */}
      <path
        d="M19 3.5a6.5 6.5 0 1 0 6.9 8.2A5.2 5.2 0 0 1 19 3.5Z"
        stroke="var(--brass)"
        strokeWidth="1.25"
        strokeLinejoin="round"
      />
      {/* Two threads crossing. */}
      <path
        d="M3 19.5C10 19.5 22 28.5 29 28.5"
        stroke="var(--text)"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <path
        d="M3 28.5C10 28.5 22 19.5 29 19.5"
        stroke="var(--brass)"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      {/* The knot. */}
      <circle cx="16" cy="24" r="2.25" fill="var(--bg)" stroke="var(--text)" strokeWidth="1.25" />
    </svg>
  );
}
