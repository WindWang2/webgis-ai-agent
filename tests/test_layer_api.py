"""
图层 API 单元测试
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from datetime import datetime

from app.main import app
from app.models.pydantic_models import LayerCreate, LayerResponse


@pytest.fixture
def client():
    """创建测试客户端"""
    return TestClient(app)


@pytest.fixture
def mock_layer():
    """模拟图层数据"""
    return {
        "id": 1,
        "name": "Test Layer",
        "description": "Test Description",
        "layer_type": "vector",
        "geometry_type": "Polygon",
        "source_url": "https://example.com/data.geojson",
        "source_format": "geojson",
        "crs": "EPSG:4326",
        "extent": {"xmin": 0, "ymin": 0, "xmax": 10, "ymax": 10},
        "attributes": [{"name": "id", "type": "int"}, {"name": "name", "type": "string"}],
        "owner_id": 1,
        "is_public": True,
        "permission": "read",
        "is_active": True,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat()
    }


class TestLayerCreate:
    """图层创建测试"""
    
    def test_create_layer_success(self, client, mock_layer):
        """测试成功创建图层"""
        with patch("app.services.layer_service.LayerService.create") as mock_create:
            mock_create.return_value = mock_layer
            
            response = client.post(
                "/api/v1/layers",
                json={
                    "name": "Test Layer",
                    "layer_type": "vector",
                    "description": "Test Description",
                    "is_public": True
                }
            )
            
            assert response.status_code == 200
            data = response.json()
            assert data["name"] == "Test Layer"
            assert data["layer_type"] == "vector"
    
    def test_create_layer_missing_name(self, client):
        """测试缺少必填字段"""
        response = client.post(
            "/api/v1/layers",
            json={"layer_type": "vector"}
        )
        
        assert response.status_code == 422
    
    def test_create_layer_invalid_type(self, client):
        """测试无效图层类型"""
        response = client.post(
            "/api/v1/layers",
            json={
                "name": "Test",
                "layer_type": "invalid_type"
            }
        )
        
        # 这里应该返回 422 验证错误
        assert response.status_code in [400, 422]


class TestLayerList:
    """图层列表测试"""
    
    def test_list_layers_default(self, client, mock_layer):
        """测试默认列表"""
        with patch("app.services.layer_service.LayerService.list_all") as mock_list:
            mock_list.return_value = ([mock_layer], 1)
            
            response = client.get("/api/v1/layers")
            
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 1
            assert len(data["layers"]) == 1
    
    def test_list_layers_with_filter(self, client, mock_layer):
        """测试带筛选的列表"""
        with patch("app.services.layer_service.LayerService.list_all") as mock_list:
            mock_list.return_value = ([mock_layer], 1)
            
            response = client.get("/api/v1/layers?layer_type=vector&is_public=true")
            
            assert response.status_code == 200
            mock_list.assert_called_once()
    
    def test_list_layers_pagination(self, client):
        """测试分页"""
        with patch("app.services.layer_service.LayerService.list_all") as mock_list:
            mock_list.return_value = ([], 0)
            
            response = client.get("/api/v1/layers?limit=10&offset=20")
            
            assert response.status_code == 200
            call_args = mock_list.call_args
            assert call_args[1]["limit"] == 10
            assert call_args[1]["offset"] == 20


class TestLayerGet:
    """单个图层获取测试"""
    
    def test_get_layer_success(self, client, mock_layer):
        """测试成功获取图层"""
        with patch("app.services.layer_service.LayerService.get") as mock_get:
            mock_get.return_value = mock_layer
            
            response = client.get("/api/v1/layers/1")
            
            assert response.status_code == 200
            data = response.json()
            assert data["id"] == 1
    
    def test_get_layer_not_found(self, client):
        """测试图层不存在"""
        with patch("app.services.layer_service.LayerService.get") as mock_get:
            mock_get.return_value = None
            
            response = client.get("/api/v1/layers/999")
            
            assert response.status_code == 404


class TestLayerUpdate:
    """图层更新测试"""
    
    def test_update_layer_success(self, client, mock_layer):
        """测试成功更新图层"""
        with patch("app.services.layer_service.LayerService.update") as mock_update:
            mock_update.return_value = mock_layer
            
            response = client.put(
                "/api/v1/layers/1",
                json={"name": "Updated Name", "description": "Updated"}
            )
            
            assert response.status_code == 200
    
    def test_update_layer_not_found(self, client):
        """测试更新不存在的图层"""
        with patch("app.services.layer_service.LayerService.update") as mock_update:
            mock_update.return_value = None
            
            response = client.put("/api/v1/layers/999", json={"name": "Test"})
            
            assert response.status_code == 404


class TestLayerDelete:
    """图层删除测试"""
    
    def test_delete_layer_success(self, client):
        """测试成功删除图层"""
        with patch("app.services.layer_service.LayerService.get") as mock_get:
            mock_get.return_value = {"id": 1, "owner_id": 1}
            
            with patch("app.services.layer_service.LayerService.delete") as mock_delete:
                mock_delete.return_value = True
                
                response = client.delete("/api/v1/layers/1")
                
                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
    
    def test_delete_layer_not_found(self, client):
        """测试删除不存在的图层"""
        with patch("app.services.layer_service.LayerService.get") as mock_get:
            mock_get.return_value = None
            
            response = client.delete("/api/v1/layers/999")
            
            assert response.status_code == 404


class TestTaskCreate:
    """任务创建测试"""
    
    def test_create_task_success(self, client):
        """测试成功创建任务"""
        with patch("app.services.layer_service.TaskService.create_task") as mock_create:
            mock_create.return_value = {
                "id": 1,
                "task_id": "abc-123",
                "task_type": "buffer",
                "status": "pending",
                "progress": 0
            }
            
            response = client.post(
                "/api/v1/layers/1/tasks",
                json={
                    "task_type": "buffer",
                    "parameters": {"distance": 100, "units": "meters"}
                }
            )
            
            assert response.status_code == 200
            data = response.json()
            assert data["task_type"] == "buffer"


class TestMetadataAPI:
    """元数据 API 测试"""
    
    def test_get_layer_types(self, client):
        """测试获取图层类型列表"""
        response = client.get("/api/v1/layer-types")
        
        assert response.status_code == 200
        data = response.json()
        assert "layer_types" in data
        assert "analysis_types" in data
    
    def test_get_layer_metadata(self, client, mock_layer):
        """测试获取图层元数据"""
        with patch("app.services.layer_service.LayerService.get") as mock_get:
            mock_get.return_value = mock_layer
            
            response = client.get("/api/v1/layers/1/metadata")
            
            assert response.status_code == 200
            data = response.json()
            assert data["name"] == "Test Layer"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
