"""Shared GDAL environment — a RUNTIME property, not a reader detail.

``rasterio_env()`` is the canonical home of the GDAL knob set (moved here
from ``app.lib.geo_analysis.raster_math``, which now delegates). Every path
that opens a raster — RasterReader, tile streaming, temporal engines, COG
write, STAC assets — must HOLD this env for the lifetime of the open
dataset. Setting knobs only at ``open()`` (or only in one reader class)
leaves every other open path running in a default env: GDAL block cache
back to the ~5%-of-RAM default, no HTTP timeout, unbounded warp threads.
That is why this module exists standalone: the env is a property of the
runtime any raster open happens in, not an implementation detail of one
reader (Runtime V5 convergence, audit tension #2).

Knobs (ADR-0037 Win 2 + ADR-0089 resource governance):

* ``GDAL_DISABLE_READDIR_ON_OPEN=TRUE`` — no scanning of adjacent files.
* ``GDAL_HTTP_TIMEOUT=5`` / ``GDAL_HTTP_MAX_RETRY=0`` — a hanging remote
  source fails fast instead of blocking the worker.
* ``GDAL_CACHEMAX`` — block cache capped by RASTER_GDAL_CACHE_MAX_MB
  (default 64 MB).
* ``GDAL_NUM_THREADS=1`` — raster windows are processed sequentially by
  design (§42: no unbounded parallel windows); extra GDAL threads only
  amplify peak memory.
"""
from contextlib import contextmanager


def validate_remote_href(uri: str) -> str:
    """远端 http(s) href 的 SSRF 门禁（栅格打开前的唯一入口检查）。

    GDAL 收到 http(s) href 会在内部转成 ``/vsicurl`` 远端读——此前只有
    timeout/字节预算（本模块 env 旋钮），没有 SSRF 校验。这里复用
    data_fabric 的既有硬化层（``DataFabricSecurity.validate_url``：scheme
    白名单、私网 A/AAAA、IPv4-mapped IPv6、云元数据 IP），**绝不另写一套
    SSRF 判定**。

    - 本地路径与 ``/vsi*``、``s3`` 等非 http(s) 源原样放行（不触碰）；
    - ``/vsicurl/http(s)://…`` 里内嵌的目标同样校验；
    - 校验失败抛 ``DataFabricSecurityError``（``ValueError`` 子类）。

    模块级函数：测试经 ``monkeypatch.setattr`` 替换本模块或 reader 模块上
    的绑定名即可注入。
    """
    if not uri or not isinstance(uri, str):
        return uri
    target = uri[len("/vsicurl/"):] if uri.startswith("/vsicurl/") else uri
    scheme = target.split("://", 1)[0].lower() if "://" in target else ""
    if scheme not in ("http", "https"):
        return uri  # 本地路径 / /vsi* / s3 等原样放行
    from app.services.data_fabric.security import DataFabricSecurity

    DataFabricSecurity.validate_url(target)
    return uri  # 校验通过：输入原样返回（绝不改写调用方的 href）


@contextmanager
def rasterio_env():
    """Shared GDAL env for all raster reads/writes (see module docstring)."""
    try:
        from app.core.config import settings

        cache_max_mb = settings.RASTER_GDAL_CACHE_MAX_MB
    except Exception:  # noqa: BLE001 — 配置缺席按保守默认
        cache_max_mb = 64
    import rasterio

    with rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="TRUE",
        GDAL_HTTP_TIMEOUT=5,
        GDAL_HTTP_MAX_RETRY=0,
        GDAL_CACHEMAX=int(cache_max_mb),
        GDAL_NUM_THREADS=1,
    ):
        yield
