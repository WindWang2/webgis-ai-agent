import unittest
import json
from unittest.mock import MagicMock, patch

class TestAdvancedGISFeatures(unittest.TestCase):
    def setUp(self):
        # 模拟数据
        self.mock_geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"pop": 1000, "name": "Zone A"},
                "geometry": {"type": "Polygon", "coordinates": [[[0,0],[0,1],[1,1],[1,0],[0,0]]]}
            }]
        }

    def test_cartography_choropleth(self):
        """测试专题地图分类和样式注入"""
        from app.services.cartography_service import CartographyService
        
        result = CartographyService.apply_choropleth(
            self.mock_geojson, "pop", palette="Reds"
        )
        
        self.assertEqual(result["type"], "FeatureCollection")
        self.assertTrue("fill_color" in result["features"][0]["properties"])
        self.assertTrue(result["features"][0]["properties"]["fill_color"].startswith("#"))
        self.assertEqual(result["metadata"]["field"], "pop")

    def test_path_analysis_task_registration(self):
        """测试路径分析任务逻辑"""
        from app.services.spatial_tasks import run_path_analysis
        from app.services.spatial_analyzer import SpatialAnalyzer
        
        with patch.object(SpatialAnalyzer, "path_analysis") as mock_path:
            mock_path.return_value = MagicMock(success=True, data={"type": "LineString"}, stats={"dist": 10})
            
            # 手动模拟 self (Celery task bind=True)
            mock_self = MagicMock()
            result = run_path_analysis(mock_self, [], [0,0], [1,1])
            
            self.assertTrue(result["success"])
            self.assertEqual(result["data"]["type"], "LineString")

    def test_cartography_tool_registry(self):
        """测试工具注册和参数验证"""
        from app.tools.registry import ToolRegistry
        from app.tools.cartography import register_cartography_tools
        
        registry = ToolRegistry()
        register_cartography_tools(registry)
        
        schemas = registry.get_schemas()
        tool_names = [s["function"]["name"] for s in schemas]
        
        self.assertIn("create_thematic_map", tool_names)
        self.assertIn("apply_layer_style", tool_names)

if __name__ == "__main__":
    unittest.main()
