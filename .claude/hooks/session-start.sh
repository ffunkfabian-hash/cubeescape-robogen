#!/bin/bash
# SessionStart hook for Claude Code cloud sessions (claude.ai/code) - does nothing on a local machine.
# Makes the test tiers runnable in the cloud (AGENTS.md › Cloud-Sitzungen):
# - Blender 5.2 LTS as a Python module from PyPI (download.blender.org is blocked) behind a `blender` command,
# - ImageMagick 6 from Ubuntu behind a small `magick` command (the generator calls ImageMagick 7's `magick`),
# - the macOS fonts the textures name (Arial, Hiragino) mapped to Liberation Sans and IPA Gothic.
# Idempotent: a resumed or cached container only re-checks.
set -euo pipefail
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

BPY_VERSION=5.2.2                     # Blender 5.2 LTS like the generator; needs exactly Python 3.13
BPY_DIR=/opt/bpy-$BPY_VERSION
TOOLS=/opt/robogen-cloud              # magick and fonts only for this repo (Cubegen detects ImageMagick 6 itself)
HOOKS=$(cd "$(dirname "$0")" && pwd)
ENV_FILE=${CLAUDE_ENV_FILE:-/dev/null}
log() { echo "[session-start] $*" >&2; }

apt_install() {  # packages whose dpkg status is missing
  local missing=()
  for p in "$@"; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || missing+=("$p")
  done
  [ ${#missing[@]} -eq 0 ] && return 0
  log "apt: ${missing[*]}"
  apt-get update -qq </dev/null >&2 || true
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
    -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold "${missing[@]}" </dev/null >&2 \
    || log "apt-get meldet einen Fehler - fehlende Pakete stehen unten in der Übersicht"
}

# ImageMagick, the fonts, and the libraries Blender needs headless (EGL via Mesa)
apt_install imagemagick fonts-liberation fonts-ipafont-gothic libegl1 libegl-mesa0 libgl1 libgl1-mesa-dri \
  libxrender1 libxxf86vm1 libxfixes3 libxi6 libxkbcommon0 libsm6

blender_note="Blender: fehlt (bpy $BPY_VERSION nicht installierbar) - keine Teststufe möglich"
if [ ! -x "$BPY_DIR/bin/blender" ]; then
  log "bpy $BPY_VERSION -> $BPY_DIR"
  if python3 -m venv "$BPY_DIR" >&2 && "$BPY_DIR/bin/pip" install -q "bpy==$BPY_VERSION" >&2; then
    { echo "#!$BPY_DIR/bin/python"; tail -n +2 "$HOOKS/blender_cli.py"; } > "$BPY_DIR/bin/blender"
    chmod +x "$BPY_DIR/bin/blender"
  else
    rm -rf "$BPY_DIR"
  fi
fi
if [ -x "$BPY_DIR/bin/blender" ]; then
  mkdir -p /opt/cloud-bin && ln -sf "$BPY_DIR/bin/blender" /opt/cloud-bin/blender
  echo "export BLENDER=\"$BPY_DIR/bin/blender\"" >> "$ENV_FILE"   # scripts/check_output.sh
  blender_note="Blender: $("$BPY_DIR/bin/blender" --version 2>/dev/null | head -1) als \`blender\` (headless, CPU)"
fi

mkdir -p "$TOOLS/bin" "$TOOLS/magick"
cat > "$TOOLS/bin/magick" <<'EOF'
#!/bin/sh
# ImageMagick 7's "magick" on top of ImageMagick 6: "magick <tool> ..." and "magick <in> ... <out>" (= convert)
case "${1:-}" in
  convert|montage|compare|identify|composite|mogrify|stream|import|display|animate|conjure)
    tool=$1; shift; exec "$tool" "$@" ;;
  *) exec convert "$@" ;;
esac
EOF
chmod +x "$TOOLS/bin/magick"
cat > "$TOOLS/magick/type.xml" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<typemap>
  <type name="Arial" family="Arial" glyphs="/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"/>
  <type name="Arial-Bold" family="Arial" weight="700" glyphs="/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"/>
  <type name="Arial-Black" family="Arial" weight="900" glyphs="/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"/>
  <type name="Hiragino-Kaku-Gothic-StdN-W8" family="Hiragino" glyphs="/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf"/>
</typemap>
EOF
echo "export PATH=\"$TOOLS/bin:/opt/cloud-bin:\$PATH\"" >> "$ENV_FILE"
echo "export MAGICK_CONFIGURE_PATH=\"$TOOLS/magick\"" >> "$ENV_FILE"

im=$(convert -version 2>/dev/null | head -1 | sed 's/^Version: //; s/ https.*//')
cat <<EOF
Cloud-Sitzung vorbereitet (.claude/hooks/session-start.sh, AGENTS.md › Cloud-Sitzungen):
- $blender_note
- ImageMagick: ${im:-fehlt} hinter \`magick\`; Arial/Hiragino als Liberation Sans/IPA Gothic
- Teststufe: \`blender -b --factory-startup --python-exit-code 1 --python scripts/generate_robot.py -- --tier quick\`
  (statt des macOS-Pfads; Zeiten und Texturen nicht mit dem Mac vergleichbar). Viewer (Swift) und Unity-Testpaket fehlen.
EOF
