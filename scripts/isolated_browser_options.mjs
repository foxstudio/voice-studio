// Keep browser-owned updater processes out of isolated acceptance runs.
// Chromium's switch only applies to this launched process; it does not alter
// the user's installed browser or update preferences.
// https://chromium.googlesource.com/chromium/src/+/refs/heads/main/chrome/common/chrome_switches.h
export function isolatedBrowserOptions(options = {}) {
  return { ...options, args: [...new Set([...(options.args ?? []), '--disable-updater-scheduler'])] };
}
