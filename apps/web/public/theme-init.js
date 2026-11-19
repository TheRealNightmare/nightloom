// Sets the light or dark theme on <html> before the first paint: the choice
// stored by the theme toggle, else the system preference (dark when there is
// none). An external file, because the Content-Security-Policy allows no
// inline scripts. src/theme/theme.ts applies the same rule; keep them in step.
(() => {
  const prefersLight = () => {
    try {
      return window.matchMedia("(prefers-color-scheme: light)").matches;
    } catch {
      return false;
    }
  };
  let stored = null;
  try {
    stored = window.localStorage.getItem("nightloom.theme");
  } catch {
    // Storage is blocked (private mode, a policy): follow the system.
  }
  const theme =
    stored === "light" || stored === "dark" ? stored : prefersLight() ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", theme);
})();
