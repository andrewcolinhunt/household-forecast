#!/usr/bin/env bash
# Scan the installed Pixi environment with a checksum-verified Grype release.
set -euo pipefail

cd "$(dirname "$0")/.."
if [[ ! -d .pixi/envs/default/conda-meta ]]; then
    echo "Run pixi install --locked before checking dependencies." >&2
    exit 1
fi

version=0.121.0
case "$(uname -s)-$(uname -m)" in
    Linux-x86_64)
        platform=linux_amd64
        checksum=64e07133d3486115de647a66581461dccf5c732748a0a3119077395565f15519
        ;;
    Darwin-arm64)
        platform=darwin_arm64
        checksum=7be39d3a0428c664ce0183e4aba31ed9accffce9eda7e7f6fa84e9c512285f27
        ;;
    *)
        echo "Dependency scanning supports Linux x86_64 and macOS arm64." >&2
        exit 1
        ;;
esac

scan_dir=$(mktemp -d)
trap 'rm -rf "$scan_dir"' EXIT
archive="grype_${version}_${platform}.tar.gz"
curl --fail --location --silent --show-error --retry 3 \
    "https://github.com/anchore/grype/releases/download/v${version}/${archive}" \
    --output "$scan_dir/$archive"
(cd "$scan_dir" && printf '%s  %s\n' "$checksum" "$archive" | shasum -a 256 --check)
tar -xzf "$scan_dir/$archive" -C "$scan_dir" grype
"$scan_dir/grype" dir:.pixi/envs/default --config .grype.yaml \
    --name household-forecast-dependencies
