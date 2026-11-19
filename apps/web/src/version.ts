/**
 * Nightloom's version, from apps/web/package.json at build time (Vite
 * `define` in vite.config.ts). Every package is released with the same
 * version (scripts/check-versions.py), so it is the API's version too.
 */
export const APP_VERSION: string = __NIGHTLOOM_VERSION__;
