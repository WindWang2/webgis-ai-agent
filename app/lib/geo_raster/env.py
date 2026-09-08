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

    - 本地路径与 ``/vsizip/``、``s3`` 等不触网的源原样放行（不触碰）；
    - ``/vsicurl/http(s)://…`` 与扩展形 ``/vsicurl?…&url=<url>`` 里内嵌的
      目标同样校验（ Round-1 审计：扩展形与非 http scheme 曾绕过门禁）；
    - 内嵌目标是 ftp/ftps 或其他远端 scheme 一律拒绝（vsicurl 对这些
      scheme 同样会发起远程读取，不在白名单内）；
    - 校验失败抛 ``DataFabricSecurityError``（``ValueError`` 子类）。

    模块级函数：测试经 ``monkeypatch.setattr`` 替换本模块或 reader 模块上
    的绑定名即可注入。
    """
    if not uri or not isinstance(uri, str):
        return uri
    target = _extract_remote_target(uri)
    if target is None:
        return uri  # 本地路径 / 不含远程目标的 /vsi* / s3 等原样放行
    from app.services.data_fabric.security import DataFabricSecurity

    DataFabricSecurity.validate_url(target)
    return uri  # 校验通过：输入原样返回（绝不改写调用方的 href）


_REMOTE_SCHEME_RE = __import__("re").compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


def _extract_remote_target(uri: str) -> "str | None":
    """提取 URI 中交给 GDAL 网络栈的 http(s) 目标；无远程目标返回 None。

    覆盖形如（GDAL virtual file systems 官方语法）：
    - ``http(s)://…``                                → 本体
    - ``/vsicurl/http(s)://…``                       → 内嵌目标
    - ``/vsicurl?utf8=1&url=http%3A%2F%2F…``         → query ``url=`` 参数
    - ``/vsicurl?url=http://…``                      → 同上（未编码）
    - ``/vsizip//vsicurl/http(s)://…`` 等复合前缀     → 递归提取
    其余（本地路径、/vsis3、/vsizip 本地文件等）返回 None。
    """
    import re
    from urllib.parse import parse_qs, unquote

    rest = uri
    # 剥离 /vsi*/ 前缀（含 /vsizip//vsicurl/... 复合嵌套）；handler 后跟
    # "?"（/vsicurl?url=... 无斜杠形）时进入 query 解析分支。
    while True:
        m = re.match(r"^/vsi[a-z0-9_]+", rest)
        if not m:
            break
        handler_end = m.end()
        if rest[handler_end:handler_end + 1] == "/":
            rest = rest[handler_end + 1:]
            continue
        if rest[handler_end:handler_end + 1] == "?":
            rest = rest[handler_end:]
            break
        # /vsis3bucket 这类粘连形（无斜杠无 query）：交给后续 scheme 检查。
        rest = rest[handler_end:]
        break
    if rest.startswith("?"):
        params = parse_qs(rest[1:], keep_blank_values=True)
        candidates = params.get("url") or params.get("filename")
        # Round-2 审计 N-1：重复 url=/filename= 参数时 GDAL 按 last-wins
        # 取值，而门禁只能看到一个——存在即拒绝（fail closed）。
        for key in ("url", "filename"):
            if len(params.get(key, [])) > 1:
                raise ValueError(
                    f"multiple {key!r} parameters rejected by SSRF gate "
                    f"(ambiguous target): {uri!r}"
                )
        if not candidates or not candidates[0].strip():
            # Round-2 审计 N-7：空值同样拒绝。
            raise ValueError(
                f"vsicurl query form carries no resolvable url: {uri!r}"
            )
        rest = unquote(candidates[0])
    if rest.startswith("/vsi"):
        # 未识别的 /vsi 复合形式：保守视为含未知远程目标。
        raise ValueError(f"unrecognized /vsi composition rejected by SSRF gate: {uri!r}")
    if not _REMOTE_SCHEME_RE.match(rest):
        return None
    scheme = rest.split("://", 1)[0].lower()
    if scheme in ("http", "https"):
        return rest
    # ftp/ftps/sftp 等远端 scheme 会被 GDAL 网络栈执行，但不在白名单：
    # 显式拒绝而非放行（Round-1 审计 F2）。
    if scheme in ("ftp", "ftps", "sftp"):
        raise ValueError(
            f"remote scheme {scheme!r} is not allowed through the raster SSRF gate "
            f"(http/https only): {uri!r}"
        )
    return None


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
