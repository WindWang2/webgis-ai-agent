"""H14: Layer data fetches must be aborted on session switch.

契约不变，实现变了：F-FE-3 之后 geojson_ref 的数据获取走共享 transport
（apiFetch），不再裸 `fetch(API_BASE + ...)`。本测试改为守卫新实现的三个要素：
  1. 获取必须传 `layerFetchAbortRef.current?.signal`（可被取消）；
  2. 必须在 session 变化时 abort 旧 controller 并新建（useEffect 依赖 sessionId）；
  3. 组件卸载时 abort（cleanup）。
"""
import re


def _read_source() -> str:
    with open("frontend/lib/hooks/use-sse-stream.ts") as f:
        return f.read()


class TestLayerFetchAbort:
    def test_geojson_fetch_uses_abort_signal(self):
        """geojson_ref 获取必须通过 apiFetch 传入 AbortSignal。"""
        source = _read_source()

        fetch_match = re.search(r"/api/v1/layers/data/\$\{encodeURIComponent\(fetchRef\)\}", source)
        assert fetch_match, "Could not find geojson_ref fetch call"

        context_start = fetch_match.start()
        context = source[context_start - 200:context_start + 400]

        assert "signal: layerFetchAbortRef.current?.signal" in context, (
            "geojson_ref fetch does not pass layerFetchAbortRef's signal. "
            "Add an AbortController ref that resets on session change and pass its signal to apiFetch."
        )

    def test_has_abort_controller_ref(self):
        """Hook must maintain an AbortController ref for in-flight layer fetches."""
        source = _read_source()
        assert "layerFetchAbortRef" in source, (
            "use-sse-stream.ts does not use an AbortController ref for layer data fetches. "
            "Add a layerFetchAbortRef that is reset on session change."
        )

    def test_abort_ref_resets_on_session_change(self):
        """session 切换时必须 abort 在飞请求并换新 controller。"""
        source = _read_source()

        # useEffect 依赖 [sessionId]：切换 session → abort 旧 controller → 新建
        reset_block = re.search(
            r"useEffect\([\s\S]{0,600}?layerFetchAbortRef\.current\.abort\(\)[\s\S]{0,300}?layerFetchAbortRef\.current = new AbortController\(\)[\s\S]{0,100}?\}, \[sessionId\]\)",
            source,
        )
        assert reset_block, (
            "layerFetchAbortRef must be aborted and recreated in a useEffect keyed on sessionId "
            "(otherwise in-flight fetches from the old session keep writing into the new session)."
        )

    def test_abort_ref_cleaned_up_on_unmount(self):
        """组件卸载时 abort，避免卸载后响应写 state。"""
        source = _read_source()
        cleanup = re.search(
            r"return \(\) => \{\s*layerFetchAbortRef\.current\?\.abort\(\);\s*\};",
            source,
        )
        assert cleanup, (
            "layerFetchAbortRef must be aborted in the effect cleanup (unmount path)."
        )
