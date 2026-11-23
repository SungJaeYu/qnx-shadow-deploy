#!/bin/bash
# Local, opt-in cross-build. Never downloads, accepts, or activates a QNX license.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: bash tools/qnx_container.sh image|build|shell

  image  Build an Ubuntu 24.04 linux/amd64 tool image (no QNX SDK included).
  build  Cross-compile ShadowDeploy using an installed and activated QNX SDK.
  shell  Open the same environment for diagnostics or image preparation.

Optional environment variables:
  SHADOW_QNX_SDP      SDK directory (default: $HOME/qnx800)
  SHADOW_QNX_LICENSE  License directory (default: $HOME/.qnx/license)
  SHADOW_QNX_IMAGE    Local image tag (default: shadowdeploy-qnx-host:24.04)
  SHADOW_DOCKER      Docker executable (auto-discovered when omitted)

Only the project, SDK and license directory are mounted. The SDK is read-only;
the project and license directory are writable. No privileged mode, host
networking, home-directory mount, or Docker socket mount is used.
EOF
}

action=${1:-help}
case "$action" in
    help|-h|--help) usage; exit 0 ;;
    image|build|shell) ;;
    *) usage >&2; exit 2 ;;
esac
if [ "$#" -ne 1 ]; then usage >&2; exit 2; fi

project_dir=$(cd "$(dirname "$0")/.." && pwd -P)
image_tag=${SHADOW_QNX_IMAGE:-shadowdeploy-qnx-host:24.04}
docker_cli=${SHADOW_DOCKER:-}
if [ -z "$docker_cli" ]; then
    if command -v docker >/dev/null 2>&1; then
        docker_cli=$(command -v docker)
    elif [ -x /Applications/Docker.app/Contents/Resources/bin/docker ]; then
        docker_cli=/Applications/Docker.app/Contents/Resources/bin/docker
    else
        printf 'Docker CLI not found. Install and start Docker Desktop.\n' >&2
        exit 1
    fi
fi
if ! "$docker_cli" info >/dev/null 2>&1; then
    printf 'Docker engine unavailable. Start Docker Desktop and check this terminal has access.\n' >&2
    printf 'If an execution sandbox blocks access, run this script in your own terminal.\n' >&2
    exit 1
fi

if [ "$action" = image ]; then
    exec "$docker_cli" build --platform linux/amd64 \
        --tag "$image_tag" "$project_dir/docker/qnx"
fi

sdk_dir=${SHADOW_QNX_SDP:-$HOME/qnx800}
license_dir=${SHADOW_QNX_LICENSE:-$HOME/.qnx/license}
if [ ! -f "$sdk_dir/qnxsdp-env.sh" ] || [ ! -x "$sdk_dir/host/linux/x86_64/usr/bin/q++" ]; then
    printf 'QNX SDK with Linux 64-bit Host Tools is missing. See docs/qnx-setup.md.\n' >&2
    exit 1
fi
if [ ! -d "$license_dir" ]; then
    printf 'QNX license directory missing. Activate the license in QNX Software Center first.\n' >&2
    exit 1
fi
sdk_dir=$(cd "$sdk_dir" && pwd -P)
license_dir=$(cd "$license_dir" && pwd -P)
# Docker --mount uses commas as separators; reject ambiguous paths.
case "$project_dir$sdk_dir$license_dir" in
    *,*) printf 'Mount paths must not contain commas.\n' >&2; exit 2 ;;
esac
if ! "$docker_cli" image inspect "$image_tag" >/dev/null 2>&1; then
    printf 'Build the tool image first: bash tools/qnx_container.sh image\n' >&2
    exit 1
fi

args=(run --rm --platform linux/amd64 --hostname shadowdeploy-qnx
    --user "$(id -u):$(id -g)" --cap-drop ALL
    --security-opt no-new-privileges
    --env HOME=/tmp/shadow-home
    --env QNX_CONFIGURATION=/tmp/shadow-home/.qnx
    --env QNX_CONFIGURATION_EXCLUSIVE=/tmp/shadow-home/.qnx
    --mount "type=bind,source=$project_dir,target=/work"
    --mount "type=bind,source=$sdk_dir,target=/opt/qnx,readonly"
    --mount "type=bind,source=$license_dir,target=/tmp/shadow-home/.qnx/license"
    --workdir /work)
if [ "$action" = shell ]; then args+=(-it); fi

exec "$docker_cli" "${args[@]}" "$image_tag" /bin/bash -c '
    set -eo pipefail
    . /opt/qnx/qnxsdp-env.sh
    if [ "$1" = shell ]; then exec /bin/bash --noprofile --norc; fi
    sh tools/check_qnx_env.sh
    q++ -V
    cmake -S . -B build-qnx-container -G Ninja \
        -DCMAKE_TOOLCHAIN_FILE=cmake/qnx8-x86_64.cmake \
        -DCMAKE_BUILD_TYPE=Debug
    cmake --build build-qnx-container --parallel 2
    file build-qnx-container/shadow-demo
    printf "Cross-build completed. QNX target execution is still required.\n"
' shadow-qnx "$action"
