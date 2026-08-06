#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Sparkle 原生桥接只能在 macOS 上构建。" >&2
  exit 1
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
native_root="$repo_root/desktop/native/sparkle"
source_path="$native_root/src/sparkle_bridge.mm"
framework_path="$native_root/vendor/Sparkle.framework"
output_dir="$native_root/build/Release"
output_path="$output_dir/sparkle_bridge.node"

if [[ ! -f "$framework_path/Versions/B/Sparkle" ]]; then
  echo "缺少 Sparkle.framework，请先运行 scripts/build/setup-sparkle.sh。" >&2
  exit 1
fi

node_prefix="$(node -p "require('node:path').resolve(require('node:path').dirname(process.execPath), '..')")"
node_include_dir="${NODE_INCLUDE_DIR:-$node_prefix/include/node}"
if [[ ! -f "$node_include_dir/node_api.h" ]]; then
  echo "找不到 Node.js N-API 头文件：${node_include_dir}/node_api.h" >&2
  echo "可通过 NODE_INCLUDE_DIR 指定 Node.js include/node 目录。" >&2
  exit 1
fi

mkdir -p "$output_dir"
xcrun --sdk macosx clang++ \
  -std=c++17 \
  -bundle \
  -undefined dynamic_lookup \
  -fobjc-arc \
  -arch arm64 \
  -mmacosx-version-min=11.0 \
  -I "$node_include_dir" \
  -F "$native_root/vendor" \
  "$source_path" \
  -framework Sparkle \
  -framework Cocoa \
  -Wl,-rpath,@loader_path/../../vendor \
  -Wl,-rpath,@loader_path/../../Frameworks \
  -o "$output_path"

codesign --force --sign - "$output_path"
echo "已构建 ${output_path}。"
