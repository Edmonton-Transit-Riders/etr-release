# Feeds

ETR's transit data servers, which every tracker connects to for departures:
`feeds.edmontontransitriders.org` and `realfeeds.edmontontransitriders.org`. They
run TJ Horner's transit-tracker-api, unmodified, with ETR's feed list.

Format: one `## YYYY-MM-DD — Title` per change set, newest first, then any of
`### Added`, `### Changed`, `### Fixed`, `### Known issues`. ttdev is built from
this.

## 2026-10-23 — Second data server, shared real-time cache

### Added
- `realfeeds.edmontontransitriders.org`, a second data server where Edmonton
  routes and stops are coded `ets:`, the same as upstream's naming. New setups use
  it.

### Changed
- Both servers now share one cached copy of ETS's real-time data instead of each
  fetching it separately.

### Known issues
- `feeds.edmontontransitriders.org` (Edmonton coded `kcm:`) stays online for
  trackers set up before the switch. Moving a tracker means re-pushing its routes
  with `ets:` codes.

## 2026-10-22 — Seven more Alberta transit systems

### Added
- Calgary, Red Deer, Lethbridge, Medicine Hat, Grande Prairie, Roam (Bow Valley)
  and Wood Buffalo. Real-time predictions where the agency publishes them:
  Calgary, Medicine Hat, Roam and Wood Buffalo. The others show scheduled times.

### Fixed
- Calgary real-time predictions froze, because Calgary's server asks for its data
  to be cached for 14 days. They now refresh every 15 seconds.

## 2026-10-21 — ETR runs its own data server

### Added
- `feeds.edmontontransitriders.org` serves ETS schedules and real-time
  predictions. ETS's data also covers St. Albert, Strathcona County, Spruce
  Grove, Fort Saskatchewan, Beaumont and Leduc. Schedules refresh daily.

### Changed
- Edmonton is coded `kcm:` on this server, matching what trackers already in
  the field have stored.

### Fixed
- Looking up departures for a stop went from about 10 seconds to a few
  milliseconds.
