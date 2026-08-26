"""#701 三处行为一致性修复的测试：Pi secret fail-loud、store 副本语义统一、prompt 对齐"""
import copy
import os
import tempfile
import pytest
from unittest.mock import patch

from app.services.session_data import MemorySessionStore


# ============================================================================
# 测试 1: Pi secret fail-loud (#701-1)
# ============================================================================

class TestPiSecretFailLoud:
    """Pi secret 写失败时服务启动失败，不回退随机值（ADR-0066 精神）"""

    def test_secret_write_failure_raises(self):
        """模拟文件写失败，断言 RuntimeError"""
        from app.api.routes.pi_tools import get_bridge_secret

        # 清除环境变量强制走文件路径
        old_secret = os.environ.pop("WEBGIS_BRIDGE_SECRET", None)
        
        try:
            # Mock Path.read_text 抛 FileNotFoundError（secret 不存在），
            # 然后 mock tempfile.mkstemp 失败（无法写入新 secret）
            with patch("pathlib.Path.read_text") as mock_read:
                mock_read.side_effect = FileNotFoundError()
                with patch("tempfile.mkstemp") as mock_mkstemp:
                    mock_mkstemp.side_effect = OSError("Permission denied")
                    
                    with pytest.raises(RuntimeError, match="Cannot initialize Pi bridge secret"):
                        get_bridge_secret()
        finally:
            # 恢复环境变量
            if old_secret:
                os.environ["WEBGIS_BRIDGE_SECRET"] = old_secret

    def test_secret_normal_path_succeeds(self):
        """正常路径：能写文件时成功返回 secret"""
        from app.api.routes.pi_tools import get_bridge_secret

        # 清除环境变量
        os.environ.pop("WEBGIS_BRIDGE_SECRET", None)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.core.config.settings") as mock_settings:
                mock_settings.DATA_DIR = tmpdir
                
                secret = get_bridge_secret()
                assert len(secret) > 20  # token_urlsafe(32) 生成 ~43 字符
                assert os.environ["WEBGIS_BRIDGE_SECRET"] == secret

    def test_secret_idempotent_reads_from_file(self):
        """二次调用读取已存在的 secret，不重新生成"""
        from app.api.routes.pi_tools import get_bridge_secret

        os.environ.pop("WEBGIS_BRIDGE_SECRET", None)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.core.config.settings") as mock_settings:
                mock_settings.DATA_DIR = tmpdir
                
                secret1 = get_bridge_secret()
                # 清除 os.environ 强制第二次走文件读取
                os.environ.pop("WEBGIS_BRIDGE_SECRET", None)
                secret2 = get_bridge_secret()
                assert secret1 == secret2


# ============================================================================
# 测试 2: Store 副本语义统一 (#701-2)
# ============================================================================

class TestStoreDeepCopySemantic:
    """内存 store 的 get 返回深拷贝副本，与 Redis 侧语义对齐"""

    @pytest.fixture
    def store(self):
        return MemorySessionStore(capacity=200)

    @pytest.mark.asyncio
    async def test_get_returns_deepcopy_not_alias(self, store):
        """get 后就地改 payload 不影响存储（证明返回副本）"""
        session_id = "test-session"
        original_data = {
            "type": "FeatureCollection",
            "features": [{"id": 1, "properties": {"value": 100}}]
        }
        
        ref_id = await store.store(session_id, copy.deepcopy(original_data), "geojson")
        
        # 第一次 get
        data1 = await store.get(session_id, ref_id)
        assert data1 == original_data
        
        # 就地改 payload
        data1["features"][0]["properties"]["value"] = 999
        data1["features"].append({"id": 2, "properties": {"value": 200}})
        
        # 第二次 get，断言未变（证明返回的是副本）
        data2 = await store.get(session_id, ref_id)
        assert data2 == original_data
        assert data2["features"][0]["properties"]["value"] == 100
        assert len(data2["features"]) == 1

    @pytest.mark.asyncio
    async def test_explicit_store_updates_data(self, store):
        """显式 overwrite 方法更新存储（正路径健康）"""
        session_id = "test-session"
        original = {"value": 100}
        
        ref_id = await store.store(session_id, copy.deepcopy(original), "test")
        
        # 更新存储（使用 overwrite 而非 store）
        updated = {"value": 200}
        success = await store.overwrite(session_id, ref_id, updated)
        assert success
        
        # 验证更新生效
        data = await store.get(session_id, ref_id)
        assert data["value"] == 200

    @pytest.mark.asyncio
    async def test_get_returns_independent_copies(self, store):
        """多次 get 返回的副本相互独立"""
        session_id = "test-session"
        original = {"mutable": [1, 2, 3]}
        
        ref_id = await store.store(session_id, copy.deepcopy(original), "test")
        
        data1 = await store.get(session_id, ref_id)
        data2 = await store.get(session_id, ref_id)
        
        # 改 data1 不影响 data2
        data1["mutable"].append(4)
        assert len(data2["mutable"]) == 3


# ============================================================================
# 测试 3: Prompt display_layer 对齐 (#701-3)
# ============================================================================

class TestPromptDisplayLayerConsistency:
    """SYSTEM_PROMPT 与 heatmap skill 对自动挂载工具的指导一致"""

    def test_prompt_exempts_auto_mount_tools(self):
        """SYSTEM_PROMPT 明确豁免自动挂载工具（heatmap_data 等）"""
        # prompt.py 是静态常量模块，直接读文件内容
        with open("app/services/chat/prompt.py", "r", encoding="utf-8") as f:
            prompt_content = f.read()

        # #1011：24da614 finalize_display 重写后措辞从「自动挂载/自动显示」
        # 漂移为「自动加载（默认隐藏）+ finalize_display 收口」——断言对齐
        # 现行契约（意图不变：数据自动挂载、轮末统一收口、有例外说明）。
        assert "自动加载" in prompt_content
        assert "finalize_display" in prompt_content
        assert "heatmap_data" in prompt_content or "热力图" in prompt_content
        # 断言不再是无条件"必须 display_layer"（有例外说明）
        assert "例外" in prompt_content or "除外" in prompt_content or "无需显式调用" in prompt_content

    def test_heatmap_skill_says_no_display_layer(self):
        """heatmap skill 文档明确说"不要调 display_layer"（保持现有说明）"""
        with open("app/skills/heatmap.md", "r", encoding="utf-8") as f:
            skill_content = f.read()
        
        # 断言 skill 保留"不要调"说明（与 prompt 现在一致）
        assert "不要" in skill_content
        assert "display_layer" in skill_content
        assert "自动" in skill_content or "挂载" in skill_content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
