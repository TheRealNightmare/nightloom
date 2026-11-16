/** Nightloom mark: two threads crossing beneath a crescent moon. */
export function LogoMark({ size = 28 }: { size?: number }) {
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
      {/* Crescent: a large arc closed by a smaller inner arc. */}
      <path d="M19.5 3.2a8 8 0 1 0 8.6 9.9A6.4 6.4 0 0 1 19.5 3.2Z" fill="var(--color-moon)" />
      {/* Two threads weaving across each other. */}
      <path
        d="M2.5 20.5C8 20.5 9.5 28.5 16 28.5S24 20.5 29.5 20.5"
        stroke="var(--color-accent)"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <path
        d="M2.5 28.5C8 28.5 9.5 20.5 16 20.5S24 28.5 29.5 28.5"
        stroke="var(--color-moon)"
        strokeWidth="2"
        strokeLinecap="round"
        opacity="0.75"
      />
    </svg>
  );
}
