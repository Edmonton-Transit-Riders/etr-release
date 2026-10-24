# Configurator

The web app at https://configurator.edmontontransitriders.org for setting up a
tracker: flashing, Wi-Fi, routes and display settings. Fork of TJ Horner's
transit-tracker-configurator.

Format: one `## YYYY-MM-DD — Title` per change set, newest first, then any of
`### Added`, `### Changed`, `### Fixed`, `### Known issues`. Write for someone
who runs a tracker, not for whoever wrote the code. ttdev is built from this.

## 2026-10-23 — Switched to the realfeeds data server

### Changed
- New setups now use `realfeeds.edmontontransitriders.org` for transit data, where
  Edmonton routes are coded `ets:`. A browser that already saved its settings
  keeps the server it had.
- **Use ETR defaults** treats `kcm:` and `ets:` as the same Edmonton route, so
  route colours and badges apply whichever server a tracker uses.

## 2026-10-22 — ETR branding, and setup installs the channel build

### Added
- ETR branding on the home page, with a support and credits footer.

### Fixed
- **Initial setup** could install an older ETR build (etr.2) that reports its
  version as `dev` and never auto-updates. It now installs the current channel
  build.

## 2026-10-20 — Firmware families and the ETR update page

### Added
- Firmware families: a tracker running ETR firmware is only ever offered ETR
  builds, and one running stock firmware only stock builds. Without this, a stock
  release could be offered to an ETR tracker as an "upgrade" and remove the badge
  layout.

### Changed
- The update prompt sends ETR trackers to the ETR update page instead of
  upstream's documentation.

### Fixed
- Switching from Badge back to Classic layout now always takes effect.
- A leftover layout setting from the first badge builds (May 2026) is reset.
- The real-time wave in badge mode is drawn at the same height as in Classic.

## 2026-08-21 — Firmware choice on setup, better defaults

### Added
- A firmware choice on **Initial setup**: ETR build or upstream.
- The preview shows the small "m" minutes mark the firmware draws.

### Changed
- **Use ETR defaults** now corrects a wrong route colour, not just missing ones.

### Fixed
- Letters with descenders (g, j, p, y) were clipped on the short band.
- The bar real-time indicator was reworked to read more clearly.

## 2026-08-20 — Badge layout in the configurator

### Added
- The simulator draws the badge layout, so you can preview it before pushing.
- Layout and real-time indicator pickers that show how many departures fit.
- **Use ETR defaults**: imports ETR's route category colours and badge codes.

### Fixed
- The warning before pushing settings never appeared.
- The simulator ignored language settings.
