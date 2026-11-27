"""只读地图发布（04 线）——领域服务包。

唯一权威链：作者端 REST 与 Pi 工具共用本包的领域服务；读者端只经
``reader`` 的授权投影读取。不新建平行任务中心/状态库。

模块：
- ``contract``   发布快照契约（publication_snapshot.v1）+ typed 阻断词表
- ``projection`` 有界 release projection（字段白名单/几何精度/bbox/预算）
- ``store``      DB 行 + data/publications/{pid}/ 产物目录
- ``access``     share_code 签发/校验/撤销/过期（fail-closed）
- ``service``    prepare/preview/publish/revoke/link 生命周期（幂等）
- ``tasks``      物化 durable job（atlas_batch 范式）
"""
