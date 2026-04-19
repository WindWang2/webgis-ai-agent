import asyncio
import json
from unittest.mock import MagicMock
from app.services.chat_engine import ChatEngine, _construct_self_healing_message
from app.tools.registry import ToolRegistry

async def test_self_healing():
    print("Testing Self-Healing Prompt Construction...")
    
    # 1. Test validation error prompt
    p1 = _construct_self_healing_message("spatial_query", "参数 'lat' 校验失败: 必须是数字", "参数校验失败")
    print(f"\nValidation Error Prompt:\n{p1}")
    assert "参数校验失败" in p1
    assert "诊断与自愈指令" in p1
    
    # 2. Test reference resolution error
    p2 = _construct_self_healing_message("set_layer_status", "无法找到引用数据或别名: 'ref:missing'", "执行出错")
    print(f"\nReference Error Prompt:\n{p2}")
    assert "Session 重置" in p2

    print("\nTest Passed!")

if __name__ == "__main__":
    asyncio.run(test_self_healing())
