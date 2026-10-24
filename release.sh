#!/usr/bin/env bash
# ETR Transit Tracker release pipeline: build firmware, publish it to a channel,
# and deploy the configurator + firmware to Cloudflare Pages as one site.
#
#   ./release.sh build   <version> <notes.md>   compile + archive a release
#   ./release.sh promote <version> beta|stable  point a channel at a release
#   ./release.sh assemble                       build dist/ (no network writes)
#   ./release.sh deploy                         assemble, upload, verify live
#   ./release.sh publish                        assemble, swap into PUBLISH_DIR, verify live
#   ./release.sh devsite                        rebuild + publish the ttdev site only
#   ./release.sh bench   <model>...             unpublished test builds per hardware model
#   ./release.sh status                         channels here vs. live site
#
# Versions look like v3.3.2-etr.3: upstream's release it is based on, then
# -etr.N, counting up forever across upstream bumps. The configurator relies on
# that suffix to keep ETR and stock devices on their own manifests.
#
# Read README.md before the first release. The short version of why it is
# shaped like this:
#   * releases/<version>/ is committed to git, binaries included. The bytes a
#     device installed must outlive any build machine, and git is what gets
#     backed up and what moves when this is handed over.
#   * Filenames carry the version and are never reused, so a cached .bin can
#     never be a different build than the manifest names. Only the two
#     manifests are uncached.
#   * One binary serves beta AND stable. Promoting to stable re-points the
#     manifest at the exact file beta ran; it never rebuilds.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
# shellcheck source=channel.conf
. ./channel.conf

abspath() { case "$1" in /*) echo "$1" ;; *) (cd "$HERE/$1" && pwd) ;; esac; }
FIRMWARE_REPO="$(abspath "$FIRMWARE_REPO")"
COMPONENT_REPO="$(abspath "$COMPONENT_REPO")"
CONFIGURATOR_REPO="$(abspath "$CONFIGURATOR_REPO")"

RELEASES="$HERE/releases"
CHANNELS="$HERE/channels"
DIST="$HERE/dist"
VERSION_RE='^v[0-9]+\.[0-9]+\.[0-9]+-etr\.[0-9]+$'

die() { echo "release: $*" >&2; exit 1; }

# Hardware models -> firmware YAML. Panel geometry is fixed at compile time, so
# each model is its own binary. Every YAML sets its own `name:` substitution,
# which is also its build dir, so building one never overwrites another.
# p25-2x1 is the released channel build (also the double-sided sign, whose two
# faces are wired in parallel and look like one 2x1 to the controller).
declare -A MODEL_YAML=(
  [p25-2x1]=transit-tracker.yaml
  [p25-2x2]=transit-tracker-p25-2x2.yaml
  [p2-192x64]=transit-tracker-p2.yaml
  [p2-128x64]=transit-tracker-p2-128x64.yaml
)
yaml_name() {  # first `name:` in the file = the substitutions entry
  sed -nE '0,/^[[:space:]]+name:/s/^[[:space:]]+name:[[:space:]]*"?([^"]*)"?.*/\1/p' "$FIRMWARE_REPO/firmware/$1"
}

need_site_url() {
  [ -n "$SITE_URL" ] || die "SITE_URL is empty in channel.conf. Attach the custom domain to the Pages project first (README step 3)."
  case "$SITE_URL" in
    https://*) ;;
    *) die "SITE_URL must start with https:// (devices verify TLS)" ;;
  esac
  case "$SITE_URL" in */) die "SITE_URL must not end with /" ;; esac
}

repo_clean() {
  [ -z "$(git -C "$1" status --porcelain)" ] || die "$1 has uncommitted changes; a release must be reproducible from commits alone"
}

md5() { md5sum "$1" | cut -d' ' -f1; }

channel_version() { [ -f "$CHANNELS/$1" ] && cat "$CHANNELS/$1" || true; }

cmd_build() {
  local version="${1:-}" notes="${2:-}"
  [[ "$version" =~ $VERSION_RE ]] || die "version must look like v3.3.2-etr.3"
  [ -f "$notes" ] || die "release notes file required (markdown, shown to users)"
  [ ! -e "$RELEASES/$version" ] || die "$version already exists; versions are never reused"
  need_site_url
  repo_clean "$FIRMWARE_REPO"
  repo_clean "$COMPONENT_REPO"

  echo "== compiling $version against $SITE_URL"
  "$FIRMWARE_REPO/firmware/tools/build.sh" transit-tracker.yaml \
    -s project_version "$version" \
    -s update_base_url "$SITE_URL/firmware"

  local out="$FIRMWARE_REPO/firmware/.esphome/build/transit-tracker/build"
  local dest="$RELEASES/$version"
  mkdir -p "$dest"
  cp "$out/firmware.factory.bin" "$dest/firmware-$version.factory.bin"
  cp "$out/firmware.ota.bin" "$dest/firmware-$version.ota.bin"
  cp "$notes" "$dest/NOTES.md"

  # The build must contain what we asked for. A build that kept upstream's
  # manifest URL would turn devices into stock firmware on their next check.
  # strings goes to a file first: `strings | grep -q` under pipefail fails on a
  # MATCH, because grep exits early and strings dies of SIGPIPE.
  local bin="$dest/firmware-$version.ota.bin" str
  str="$(mktemp)"; strings "$bin" > "$str"
  grep -qxF "$version" "$str" || die "built image does not carry version $version"
  grep -qF "$SITE_URL/firmware/manifest.json" "$str" || die "built image does not poll $SITE_URL/firmware/manifest.json"
  if grep -qF "eastsideurbanism.org/firmware/manifest.json" "$str"; then
    die "built image still polls upstream's manifest"
  fi
  rm -f "$str"

  cat > "$dest/SOURCE.txt" <<EOF
version:        $version
built:          $(date -u +%Y-%m-%dT%H:%M:%SZ) on $(hostname)
site_url:       $SITE_URL
firmware repo:  $(git -C "$FIRMWARE_REPO" rev-parse HEAD) ($(git -C "$FIRMWARE_REPO" rev-parse --abbrev-ref HEAD))
component repo: $(git -C "$COMPONENT_REPO" rev-parse HEAD) ($(git -C "$COMPONENT_REPO" rev-parse --abbrev-ref HEAD))
esphome image:  $(grep -o 'ghcr.io/esphome/esphome:[^}"]*' "$FIRMWARE_REPO/firmware/tools/build.sh" | head -1)
factory md5:    $(md5 "$dest/firmware-$version.factory.bin")
ota md5:        $(md5 "$dest/firmware-$version.ota.bin")
EOF
  cat "$dest/SOURCE.txt"
  echo
  echo "Built. Next: flash it over USB and check it (README 'Releasing'), then"
  echo "  ./release.sh promote $version beta"
}

cmd_promote() {
  local version="${1:-}" channel="${2:-}"
  [ -d "$RELEASES/$version" ] || die "no such release: $version"
  case "$channel" in
    beta) ;;
    stable)
      # Stable only ever receives what beta has run. --skip-beta exists for a
      # rollback, where the target already ran on stable before.
      if [ "$(channel_version beta)" != "$version" ] && [ "${3:-}" != "--skip-beta" ]; then
        local b; b="$(channel_version beta)"
        die "$version is not the current beta (${b:-none}). Promote to beta first, or pass --skip-beta for a rollback to a release that already ran."
      fi
      ;;
    *) die "channel must be beta or stable" ;;
  esac
  mkdir -p "$CHANNELS"
  local was; was="$(channel_version "$channel")"
  echo "$version" > "$CHANNELS/$channel"
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $channel ${was:-none} -> $version" >> "$CHANNELS/history.log"
  echo "$channel: ${was:-none} -> $version. Run ./release.sh publish (or deploy, for Pages), then commit."
}

write_manifest() {
  local version="$1" file="$2"
  local base="$SITE_URL/firmware"
  python3 - "$version" "$base" "$(md5 "$DIST/firmware/firmware-$version.ota.bin")" > "$file" <<'PY'
import json, sys
version, base, ota_md5 = sys.argv[1:4]
# Same shape as upstream's firmware/manifest-template.json, which is what
# ESPHome's http_request update platform and ESP Web Tools both parse.
print(json.dumps({
    "name": "ETR Transit Tracker Firmware",
    "version": version,
    "home_assistant_domain": "esphome",
    "new_install_prompt_erase": True,
    "builds": [{
        "chipFamily": "ESP32-S3",
        "parts": [{"path": f"{base}/firmware-{version}.factory.bin", "offset": 0}],
        "ota": {
            "md5": ota_md5,
            "path": f"{base}/firmware-{version}.ota.bin",
            "release_url": f"{base}/notes/{version}.txt",
        },
    }],
}, indent=2))
PY
}

cmd_assemble() {
  need_site_url
  local stable beta
  stable="$(channel_version stable)"; beta="$(channel_version beta)"
  [ -n "$stable$beta" ] || die "no channel points at a release yet (./release.sh promote ...)"

  rm -rf "$DIST" && mkdir -p "$DIST/firmware/notes" "$DIST/firmware/beta"

  echo "== configurator ($CONFIGURATOR_REPO @ $(git -C "$CONFIGURATOR_REPO" rev-parse --short HEAD))"
  repo_clean "$CONFIGURATOR_REPO"
  local tag="etr-release-configurator:build"
  docker build -q --target builder \
    --build-arg PUBLIC_API_HOST="$PUBLIC_API_HOST" \
    --build-arg PUBLIC_ETR_FIRMWARE_BASE= \
    -t "$tag" "$CONFIGURATOR_REPO" >/dev/null
  local cid; cid="$(docker create "$tag")"
  docker cp -q "$cid:/app/build/." "$DIST/" && docker rm -f "$cid" >/dev/null

  echo "== firmware"
  # EVERY release ever made stays downloadable: a device mid-update, a rollback,
  # and the configurator's fallback all depend on old URLs staying valid.
  local d v
  for d in "$RELEASES"/*/; do
    v="$(basename "$d")"
    cp "$d/firmware-$v.factory.bin" "$d/firmware-$v.ota.bin" "$DIST/firmware/"
    cp "$d/NOTES.md" "$DIST/firmware/notes/$v.txt"
  done
  [ -n "$stable" ] && write_manifest "$stable" "$DIST/firmware/manifest.json"
  # A device on beta with no beta release yet follows stable.
  write_manifest "${beta:-$stable}" "$DIST/firmware/beta/manifest.json"
  cp "$HERE/site/_headers" "$DIST/_headers"
  [ -f "$ETR_DEFAULTS_FILE" ] || die "ETR_DEFAULTS_FILE not found: $ETR_DEFAULTS_FILE"
  python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d.get("categoryColors"), "no categoryColors"' "$ETR_DEFAULTS_FILE" \
    || die "$ETR_DEFAULTS_FILE is not a usable etr-defaults.json"
  cp "$ETR_DEFAULTS_FILE" "$DIST/firmware/etr-defaults.json"

  verify_dist
  echo "== dist ready: stable=${stable:-none} beta=${beta:-$stable}"
}

verify_dist() {
  python3 - "$DIST" "$SITE_URL" <<'PY'
import hashlib, json, os, sys
dist, site = sys.argv[1:3]
errors = []
for rel in ("firmware/manifest.json", "firmware/beta/manifest.json"):
    p = os.path.join(dist, rel)
    if not os.path.exists(p):
        continue
    m = json.load(open(p))
    b = m["builds"][0]
    for url in (b["parts"][0]["path"], b["ota"]["path"], b["ota"]["release_url"]):
        if not url.startswith(site + "/firmware/"):
            errors.append(f"{rel}: {url} is not under {site}/firmware/")
            continue
        local = os.path.join(dist, url[len(site) + 1:])
        if not os.path.exists(local):
            errors.append(f"{rel}: {url} is not in dist")
    ota = os.path.join(dist, b["ota"]["path"][len(site) + 1:])
    if os.path.exists(ota) and hashlib.md5(open(ota, "rb").read()).hexdigest() != b["ota"]["md5"]:
        errors.append(f"{rel}: md5 does not match {ota}")
# The old fleet polls /firmware/2x1|2x2|2x3/manifest.json on transit.kunicki.app,
# where a 404 is what stops them updating. Nothing here should ever recreate
# that shape, on any host.
for layout in ("2x1", "2x2", "2x3"):
    if os.path.exists(os.path.join(dist, "firmware", layout)):
        errors.append(f"firmware/{layout}/ must not exist (fleet interlock)")
for page in ("index.html", "setup.html", "configure/routes.html", "404.html"):
    if not os.path.exists(os.path.join(dist, page)):
        errors.append(f"configurator page missing: {page}")
if errors:
    print("\n".join("  FAIL " + e for e in errors)); sys.exit(1)
print("  dist verified: manifests resolve, md5s match, fleet paths absent")
PY
}

load_credentials() {
  local f="$HERE/.env"
  [ -f "$f" ] || die "no Cloudflare credentials file (README 'Credentials')"
  set -a; . "$f"; set +a
  [ -n "${CLOUDFLARE_API_TOKEN:-}" ] && [ -n "${CLOUDFLARE_ACCOUNT_ID:-}" ] \
    || die "credentials file must set CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID"
}

cmd_deploy() {
  cmd_assemble
  load_credentials
  echo "== uploading to Pages project $PAGES_PROJECT ($PAGES_BRANCH)"
  # Token passed by NAME (-e VAR), so its value never appears in a process list.
  docker run --rm -e CLOUDFLARE_API_TOKEN -e CLOUDFLARE_ACCOUNT_ID \
    -v "$DIST":/dist:ro "$NODE_IMAGE" \
    npx --yes "wrangler@$WRANGLER_VERSION" pages deploy /dist \
      --project-name "$PAGES_PROJECT" --branch "$PAGES_BRANCH" --commit-dirty=true
  verify_live
}

verify_live() {
  echo "== verifying $SITE_URL"
  local ok=1 rel want got
  for rel in manifest.json beta/manifest.json; do
    # Beta-only (nothing promoted to stable yet) has no stable manifest.
    [ -f "$DIST/firmware/$rel" ] || { echo "  skip $rel (channel empty)"; continue; }
    want="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["version"])' "$DIST/firmware/$rel")"
    # The edge can take a few seconds to see a new deployment.
    for _ in 1 2 3 4 5 6; do
      got="$(curl -fsS "$SITE_URL/firmware/$rel" 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin)["version"])' 2>/dev/null || true)"
      [ "$got" = "$want" ] && break; sleep 5
    done
    if [ "$got" = "$want" ]; then echo "  ok   $rel = $got"; else echo "  FAIL $rel: live '$got', expected '$want'"; ok=0; fi
  done
  local v url
  v="$(channel_version stable)"; v="${v:-$(channel_version beta)}"
  url="$SITE_URL/firmware/firmware-$v.ota.bin"
  if [ "$(curl -fsS "$url" | md5sum | cut -d' ' -f1)" = "$(md5 "$RELEASES/$v/firmware-$v.ota.bin")" ]; then
    echo "  ok   $url matches the archived build"
  else
    echo "  FAIL $url does not match releases/$v"; ok=0
  fi
  [ "$ok" = 1 ] || die "live site does not match what was deployed"
  echo "Deployed. Commit channels/ and releases/ now."
}

# Self-hosted alternative to `deploy` (2026-10-21): updates.edmontontransitriders.org
# is a hostname on ETR's tunnel into piccadilly, and nginx serves PUBLISH_DIR/current.
# Each publish lands in a fresh dated directory and `current` is re-pointed with
# one rename, so a device never reads a half-copied manifest. Older trees are kept
# (pruned to the newest 5); every .bin is also in releases/, so nothing is lost.
cmd_publish() {
  [ -n "${PUBLISH_DIR:-}" ] || die "PUBLISH_DIR is not set in channel.conf"
  cmd_assemble
  local stamp tree
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"; tree="$PUBLISH_DIR/site-$stamp"
  mkdir -p "$PUBLISH_DIR"
  cp -a "$DIST" "$tree"
  rm -f "$tree/_headers"   # Pages-only; nginx sets the same headers itself
  chmod -R a+rX "$tree"
  ln -sfn "site-$stamp" "$PUBLISH_DIR/current.tmp"
  mv -T "$PUBLISH_DIR/current.tmp" "$PUBLISH_DIR/current"
  echo "== published $tree"
  # shellcheck disable=SC2012
  ls -1d "$PUBLISH_DIR"/site-* | sort | head -n -5 | xargs -r rm -rf
  verify_live
  cmd_devsite
}

# The public development site (ttdev): release log, beta status, changelogs,
# generated from releases/, channels/ and changelog/ so it can never disagree
# with what devices are served. Same atomic swap as `publish`; live/ (the
# connected count from ttdash/collect.py) sits beside the trees and is kept.
cmd_devsite() {
  [ -n "${DEV_PUBLISH_DIR:-}" ] || { echo "== devsite skipped (DEV_PUBLISH_DIR unset)"; return 0; }
  local out="$HERE/dist-dev" stamp tree
  SITE_URL="$SITE_URL" DEV_SITE_URL="${DEV_SITE_URL:-}" CONTACT_EMAIL="${CONTACT_EMAIL:-}" \
    GITHUB_ORG="${GITHUB_ORG:-}" python3 "$HERE/devsite/build.py" "$out" \
    || die "devsite build failed; ttdev left as it was (the firmware channel, if just published, is live and fine)"
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"; tree="$DEV_PUBLISH_DIR/site-$stamp"
  mkdir -p "$DEV_PUBLISH_DIR/live"
  cp -a "$out" "$tree"
  chmod -R a+rX "$tree"
  ln -sfn "site-$stamp" "$DEV_PUBLISH_DIR/current.tmp"
  mv -T "$DEV_PUBLISH_DIR/current.tmp" "$DEV_PUBLISH_DIR/current"
  # shellcheck disable=SC2012
  ls -1d "$DEV_PUBLISH_DIR"/site-* | sort | head -n -5 | xargs -r rm -rf
  echo "== devsite published $tree"
  if [ -n "${DEV_SITE_URL:-}" ]; then
    local want got
    want="$(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print(d["channels"].get("stable",{}).get("version",""))' "$out/releases.json")"
    got="$(curl -fsS -4 "$DEV_SITE_URL/releases.json" 2>/dev/null | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["channels"].get("stable",{}).get("version",""))' 2>/dev/null || true)"
    if [ "$got" = "$want" ]; then echo "  ok   $DEV_SITE_URL shows stable=$got"
    else echo "  WARN $DEV_SITE_URL shows stable='$got', expected '$want' (firmware channel is unaffected)"; fi
  fi
}

cmd_status() {
  echo "here:  stable=$(channel_version stable || true)  beta=$(channel_version beta || true)"
  if [ -n "$SITE_URL" ]; then
    local rel
    for rel in manifest.json beta/manifest.json; do
      echo "live:  $rel = $(curl -fsS "$SITE_URL/firmware/$rel" 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin)["version"])' 2>/dev/null || echo unreachable)"
    done
  else
    echo "live:  SITE_URL not set"
  fi
  echo "releases: $(ls "$RELEASES" 2>/dev/null | tr '\n' ' ')"
}

# Test builds for the bench, never published. They report `bench-<model>-<sha>`
# and keep the firmware's unresolvable update address, so a bench device never
# installs anything by itself: flash it over USB or the device's web page.
cmd_bench() {
  [ $# -gt 0 ] || die "usage: release.sh bench <model>...  (models: ${!MODEL_YAML[*]})"
  local model
  for model in "$@"; do [ -n "${MODEL_YAML[$model]:-}" ] || die "unknown model '$model' (models: ${!MODEL_YAML[*]})"; done
  repo_clean "$FIRMWARE_REPO"
  repo_clean "$COMPONENT_REPO"
  local fw yaml name ver src dest str sums=""
  fw="$(git -C "$FIRMWARE_REPO" rev-parse --short=7 HEAD)"
  for model in "$@"; do
    yaml="${MODEL_YAML[$model]}"; name="$(yaml_name "$yaml")"; ver="bench-$model-$fw"
    [ -n "$name" ] || die "$yaml has no name: substitution"
    echo "== bench $model ($yaml, build dir $name) as $ver"
    "$FIRMWARE_REPO/firmware/tools/build.sh" "$yaml" -s project_version "$ver"
    src="$FIRMWARE_REPO/firmware/.esphome/build/$name/build"
    dest="$HERE/bench/$model"
    rm -rf "$dest" && mkdir -p "$dest"
    cp "$src/firmware.factory.bin" "$dest/firmware-$ver.factory.bin"
    cp "$src/firmware.ota.bin" "$dest/firmware-$ver.ota.bin"
    str="$(mktemp)"; strings "$dest/firmware-$ver.ota.bin" > "$str"
    grep -qxF "$ver" "$str" || die "$model: image does not carry version $ver (wrong build dir collected?)"
    rm -f "$str"
    cat > "$dest/SOURCE.txt" <<SRC
version:        $ver (bench, unpublished, no update address)
model:          $model ($yaml)
built:          $(date -u +%Y-%m-%dT%H:%M:%SZ)
firmware repo:  $(git -C "$FIRMWARE_REPO" rev-parse HEAD) ($(git -C "$FIRMWARE_REPO" rev-parse --abbrev-ref HEAD))
component repo: $(git -C "$COMPONENT_REPO" rev-parse HEAD) ($(git -C "$COMPONENT_REPO" rev-parse --abbrev-ref HEAD))
esphome image:  $(grep -o 'ghcr.io/esphome/esphome:[^}"]*' "$FIRMWARE_REPO/firmware/tools/build.sh" | head -1)
factory md5:    $(md5 "$dest/firmware-$ver.factory.bin")
ota md5:        $(md5 "$dest/firmware-$ver.ota.bin")
SRC
    sums+="$(md5 "$dest/firmware-$ver.ota.bin") $model"$'\n'
  done
  # Identical images across models mean a geometry override silently did not apply.
  if [ -n "$(printf '%s' "$sums" | cut -d' ' -f1 | sort | uniq -d)" ]; then
    die "two models built byte-identical images: geometry override not applied"$'\n'"$sums"
  fi
  echo "== bench builds in $HERE/bench/:"
  printf '%s' "$sums" | sed 's/^/   /'
}

case "${1:-}" in
  build)    shift; cmd_build "$@" ;;
  promote)  shift; cmd_promote "$@" ;;
  assemble) cmd_assemble ;;
  deploy)   cmd_deploy ;;
  publish)  cmd_publish ;;
  devsite)  cmd_devsite ;;
  bench)    shift; cmd_bench "$@" ;;
  status)   cmd_status ;;
  *) sed -n '2,10p' "$0"; exit 1 ;;
esac
