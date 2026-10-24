# ETR Transit Tracker — release channel

This repo publishes the Edmonton Transit Riders build of the Transit Tracker:
the web configurator **and** the firmware that devices auto-update from, as one
static site at **https://updates.edmontontransitriders.org**.

**Where it is hosted (since 2026-10-21):** piccadilly, not Cloudflare Pages.
`updates.edmontontransitriders.org` is a route on ETR's Cloudflare Tunnel
(`web_cloudflared_etr`) to `web_nginx`, which serves
`~/docker/web/etr-updates/current`. Publish with `./release.sh publish` wherever
this README says `deploy`. The Pages path (`deploy`, the one-time setup below)
still works unchanged if the site ever moves there: `SITE_URL` is the same
either way, which is what matters to devices.

Everything needed to take this over is in this repo plus the three source repos
below. Nothing depends on the machine it was built on except the paths in
`channel.conf`.

## Licensing

The release tooling in this repo (`release.sh`, `devsite/`, the changelogs) is
MIT licensed; see `LICENSE`. That licence does not cover `releases/`.

The firmware binaries under `releases/` are built from the three source repos
below and carry their licences: the firmware YAML is Eastside Urbanism's
(CC BY-NC-SA 4.0), the display component and configurator are TJ Horner's (MIT),
and ESPHome's C++ runtime compiled into every binary is GPLv3. Edmonton Transit
Riders uses them with the upstream authors' permission, including for other
Canadian transit advocacy organizations. Each release's `SOURCE.txt` names the
exact commits it was built from, which is how anyone can get the matching source.

## The pieces

| Repo | Branch | What it is |
|---|---|---|
| `transit-tracker` (fork of EastsideUrbanism/transit-tracker) | `badge-layout` | Firmware YAML, build script, USB tools (`firmware/tools/`) |
| `esphome-transit-tracker` (fork of tjhorner/esphome-transit-tracker) | `badge-layout` | The C++ renderer (badge layout), vendored `hub75` driver |
| `transit-tracker-configurator` (fork of tjhorner/transit-tracker-configurator) | `badge-layout` | The SvelteKit web app; on piccadilly at `~/docker/web/transit-tracker-badge` |
| **this repo** | `main` | `release.sh`, `channel.conf`, every published build under `releases/`, which build each channel serves under `channels/` |

`releases/` holds the actual `.bin` files, committed. That is deliberate: the
bytes a device installed must survive any one machine, and cloning this repo is
the whole handover.

## How updates reach a device

```
device  --(every few hours)-->  <SITE_URL>/firmware/manifest.json         (stable)
                                <SITE_URL>/firmware/beta/manifest.json    (beta)
```

- Each device has two settings, visible on its own web page
  (`http://<device-ip>/`) and over USB:
  - **Auto-Update** (on by default) — off means it reports updates but never
    installs them.
  - **Update Channel** — `Stable` (default) or `Beta`.
- A device installs whatever version the manifest names **if it differs from
  its own** — newer or older. So a rollback is just pointing the channel at an
  older release, and moving a device from Beta back to Stable reinstalls the
  stable build.
- Beta and stable serve **the same binary**. Promoting to stable re-points the
  manifest at the exact file beta ran; nothing is rebuilt.
- Local builds report version `dev` and never auto-update.
- The `SITE_URL` is **compiled into the firmware**. See "Changing the domain".

Versions are `v<upstream>-etr.<n>`, e.g. `v3.3.2-etr.3`. `n` only ever goes up,
including across upstream bumps (`v3.3.2-etr.7` → `v3.4.0-etr.8`). The
configurator relies on the `-etr.` suffix: an ETR device is only ever offered
ETR builds and a stock device only upstream builds. (Semver ranks
`v3.3.2-etr.3` *below* `v3.3.2`, so mixing them would offer stock firmware —
which drops the badge layout — as an "upgrade".)

## One-time setup (in ETR's Cloudflare account)

1. **Access** — Manage Account → Members: invite each maintainer as Administrator
   (or "Cloudflare Pages" admin). No shared logins.
2. **Pages project** — Workers & Pages → Create → Pages → *Upload assets* (direct
   upload, not Git). Name it to match `PAGES_PROJECT` in `channel.conf`
   (`etr-transit-tracker`). The first upload can be anything; `release.sh deploy`
   replaces it.
3. **Custom domain** — in the project, Custom domains → add the hostname (e.g.
   `tracker.edmontontransitriders.org`). The zone is in the same account, so
   Cloudflare creates the DNS record. Then set `SITE_URL` in `channel.conf` to
   `https://<that hostname>` and commit.
4. **API token** — My Profile → API Tokens → Create Token → Custom: permission
   *Account → Cloudflare Pages → Edit*, scoped to the ETR account only. Copy the
   token and the **Account ID** (shown on the account's overview page).

### Credentials

Create a file named `.env` in this directory (it is gitignored) containing
exactly:

```
CLOUDFLARE_API_TOKEN=<the token>
CLOUDFLARE_ACCOUNT_ID=<the account id>
```

Never commit it, paste it into an issue, or put it in a chat. If it leaks,
revoke it in the dashboard and make a new one; nothing else needs changing.

## Releasing

```bash
# 1. Commit everything in the firmware and component repos (build refuses a dirty tree).
# 2. Build. Notes are shown to users from the update prompt; plain text reads best.
./release.sh build v3.3.2-etr.4 notes.md

# 3. Flash it to a bench device over USB and look at it (see "USB" below).
#    This is the step that stops a bad build reaching devices nobody is watching.

# 4. Beta: devices set to the Beta channel pick it up.
./release.sh promote v3.3.2-etr.4 beta
./release.sh deploy
git add -A && git commit -m "v3.3.2-etr.4 to beta"

# 5. After it has run on beta devices for a while:
./release.sh promote v3.3.2-etr.4 stable
./release.sh deploy
git add -A && git commit -m "v3.3.2-etr.4 to stable"
```

`publish` (self-hosted) assembles `dist/`, copies it to a new
`PUBLISH_DIR/site-<timestamp>/`, re-points `current` at it with one rename (a
device never reads a half-written manifest), keeps the newest 5 trees, then runs
the same live checks as `deploy`.

`deploy` assembles `dist/` (configurator build + every release ever made + the
two manifests + `_headers` + `etr-defaults.json`), checks it, uploads it with
wrangler, then reads the live manifests and binary back and compares them to
what it uploaded. `./release.sh assemble` does everything except upload.
`./release.sh status` compares the channels here with what is live.

The configurator is rebuilt on **every** deploy from the configurator repo's
current commit, so a configurator-only change is just `./release.sh deploy`.

### Hardware models and bench builds

Panel geometry is compiled in, so each hardware model is its own binary:

| Model | Firmware YAML | Status |
|---|---|---|
| `p25-2x1` | `transit-tracker.yaml` | Released (the channel today). Also the double-sided sign: its faces are wired in parallel, so the controller sees one 2x1. |
| `p25-2x2` | `transit-tracker-p25-2x2.yaml` | Bench |
| `p2-192x64` | `transit-tracker-p2.yaml` | Bench. Its colour swizzle depends on chain order and panel supplier. |
| `p2-128x64` | `transit-tracker-p2-128x64.yaml` | Bench |

`./release.sh bench <model>...` builds any of them into `bench/<model>/`
(gitignored, never published), versioned `bench-<model>-<firmware sha>`. Bench
builds keep the firmware's unresolvable update address, so a bench device never
updates itself: flash it over USB or from its own web page. The build refuses
if two models come out byte-identical (a geometry override that silently did
not apply).

When a model joins the channel it gets its own manifest at
`/firmware/<model>/manifest.json` (never a bare `2x1`/`2x2`/`2x3` path: see
"Rules that keep devices safe"). `p25-2x1` keeps `/firmware/manifest.json`
forever, because etr.3 and later have it compiled in.

### The development site (ttdev)

https://ttdev.edmontontransitriders.org is the public release log: what stable
and beta serve, every release with its notes and SHA-256s, changelogs for the
configurator and feeds, and a live count of connected trackers. It is generated
by `devsite/build.py` from `releases/`, `channels/` and `changelog/`, so it
cannot disagree with what devices get.

- `publish` rebuilds it automatically at the end.
- After editing only `changelog/configurator.md` or `changelog/feeds.md`, run
  `./release.sh devsite`. Write those entries for people who run a tracker, never
  paste commit messages (they name internal hosts and paths).
- Withdrawing a release: put the reason in `releases/<v>/WITHDRAWN` (one line,
  shown on the page), then `./release.sh devsite`. Withdrawing does not change
  any channel; promote something else for that.
- `CONTACT_EMAIL` and `GITHUB_ORG` in `channel.conf` switch on the report
  address and source links.
- The live count is `live/counts.json` beside the published trees, written
  each minute by `~/docker/web/ttdash/collect.py`: totals per firmware version,
  nothing else.

### Rollback

```bash
./release.sh promote v3.3.2-etr.3 stable --skip-beta   # a build that already ran on stable
./release.sh deploy
```

Old binaries are never deleted, so every past release stays installable.

### USB

From `transit-tracker/firmware/tools/`:

- `serial_rpc.py dump before.json` — save the device's settings first.
- Flash the factory image:
  `docker run --rm --device /dev/ttyACM0 --entrypoint python3 -v <release dir>:/f:ro ghcr.io/esphome/esphome:2026.9.1 -m esptool --chip esp32s3 --port /dev/ttyACM0 --baud 460800 write-flash 0x0 /f/firmware-<version>.factory.bin`
  (Wi-Fi survives; the display settings must be restored.)
- `serial_rpc.py restore before.json` — puts the settings back and persists them.

The configurator's **Initial setup** page can also flash the current stable
build from a browser (Chrome/Edge), with no toolchain.

## Rules that keep devices safe

- **Never reuse a version or a filename.** Binaries are cached for a year at
  Cloudflare's edge; only the two manifests are uncached.
- **Never publish anything at `/firmware/2x1|2x2|2x3/manifest.json`.** An older
  generation of Mike Kunicki's devices polls that shape on transit.kunicki.app
  and a 404 is what stops them updating. `release.sh` refuses a `dist/`
  containing it.
- **Stable only gets what beta ran** (`promote` enforces it, except
  `--skip-beta` for rollbacks).
- **The first install of a channel build is by USB.** Devices on older ETR
  builds (`etr.1`, `etr.2`) report `dev` and have no update address; they join
  the channel only when flashed with a channel build.

## Changing the domain

Every device in the field has `SITE_URL` compiled in. To move:

1. Attach the new hostname to the same Pages project (both now serve the site).
2. Change `SITE_URL`, build and ship a release through beta → stable as usual.
   Devices fetch it from the OLD address and from then on poll the new one.
3. Keep the old hostname attached until no device is still on an older release
   — in practice, for good. It costs nothing.

## Handover checklist

- [x] Push the three source repos and this one to a GitHub organisation ETR
      controls: https://github.com/Edmonton-Transit-Riders (2026-10-23; the
      forks' default branch is `badge-layout`, remote name `etr` locally).
- [ ] Add a second owner to the GitHub organisation.
- [ ] In the `transit-tracker` fork, disable upstream's GitHub Actions
      (`release-firmware.yml`): it would publish to upstream's paths, not here.
- [ ] Update the three repo paths in `channel.conf` to wherever they are cloned.
- [ ] New maintainer creates their own API token (step 4) and `.env`; revoke the
      previous maintainer's token.
- [ ] `./release.sh assemble` succeeds on the new machine (needs Docker, git,
      python3, curl, md5sum, strings).
- [ ] Route Styles' `etr-defaults.json` currently comes from piccadilly
      (`ETR_DEFAULTS_FILE`). Copy it into this repo or point the setting at its
      new home.

## Known limits

- The configurator's built-in transit data (`PUBLIC_API_HOST`) is ETR's feed,
  which covers Edmonton only. Users elsewhere can change it under Advanced.
- Only the main 128×32 build is released. The P2 build
  (`transit-tracker-p2.yaml`) is not on the channel.
- The firmware still names itself "Eastside Urbanism.Transit Tracker" (ESPHome
  project name) and its `dashboard_import` points at upstream at the device's
  version tag, which does not exist for `-etr` versions. Neither affects
  updates.
