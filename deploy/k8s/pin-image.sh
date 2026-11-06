#!/usr/bin/env bash
# Pin the CI-published content digest before applying Kubernetes resources.
set -euo pipefail

DIGEST="${1:?usage: deploy/k8s/pin-image.sh sha256:<published-manifest-digest>}"
IMAGE="ghcr.io/windwang2/webgis-ai-agent"
cd "$(dirname "$0")"

if [[ ! "$DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || [[ "$DIGEST" =~ ^sha256:0{64}$ ]]; then
    echo "expected a published sha256 manifest digest; mutable tags are not accepted" >&2
    exit 1
fi

# Registry/authentication failure must not mutate the local deployment.
docker manifest inspect "${IMAGE}@${DIGEST}" >/dev/null
kustomize edit set image "${IMAGE}=${IMAGE}@${DIGEST}"
echo "pinned ${IMAGE}@${DIGEST}"
echo "review kustomize build . before kubectl apply -k ."
