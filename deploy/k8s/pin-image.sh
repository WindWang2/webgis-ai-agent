#!/usr/bin/env bash
# audit ISSUE-066（#1349）：k8s 清单默认 :master + imagePullPolicy:Always
# 是 raw-apply 开发回退——同 manifest 任意时刻 apply 拉到的都是最新
# master，部署不可复现。生产部署必须先经本脚本（或 kustomize edit set
# image）把 tag 钉到 CI 实际推送的 sha，再 apply。
#
# 用法：deploy/k8s/pin-image.sh <git-sha>
set -euo pipefail

TAG="${1:?usage: deploy/k8s/pin-image.sh <git-sha|image-tag>}"
IMAGE="ghcr.io/windwang2/webgis-ai-agent"
cd "$(dirname "$0")"

if [ "$TAG" = "master" ]; then
    echo "refusing to pin 'master': floating tag defeats reproducible deploys" >&2
    exit 1
fi

kustomize edit set image "${IMAGE}=${IMAGE}:${TAG}"
echo "pinned ${IMAGE}:${TAG}"
echo "verify with: kustomize build . | grep 'image:' — then kubectl apply -k ."
