#!/bin/zsh
set -euo pipefail

repo=${0:A:h:h:h}
destination="/Applications/Library.app"
staging=$(mktemp -d /tmp/libraryos-app.XXXXXX)
iconset="$staging/LibraryOS.iconset"
bundle="$staging/Library.app"
source_icon="$repo/src/libraryos/ui/assets/libraryos-icon.png"

trap 'rm -rf "$staging"' EXIT

mkdir -p "$bundle/Contents/MacOS" "$bundle/Contents/Resources" "$iconset"
cp "$repo/packaging/macos/Info.plist" "$bundle/Contents/Info.plist"
cp "$repo/packaging/macos/LibraryLauncher" "$bundle/Contents/MacOS/LibraryLauncher"
chmod 755 "$bundle/Contents/MacOS/LibraryLauncher"

for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$source_icon" --out "$iconset/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z "$double" "$double" "$source_icon" --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$iconset" -o "$bundle/Contents/Resources/LibraryOS.icns"

rm -rf "$destination"
cp -R "$bundle" "$destination"
xattr -dr com.apple.quarantine "$destination" 2>/dev/null || true
touch "$destination"

echo "Installed $destination"
