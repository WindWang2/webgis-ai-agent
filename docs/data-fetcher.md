# 统一数据获取层 (Data Fetcher Layer) 接口文档

## 概述
数据获取层是webgis-ai-agent的核心组件，封装了所有GIS数据源的访问逻辑，向上层编排层提供统一的查询接口，屏蔽底层数据源差异。

## 功能特性
### 1. 多数据源支持
| 数据源类型 | 说明 | 支持格式 |
|-----------|------|----------|
| `postgis` | PostGIS空间数据库 | 矢量数据（GeoJSON） |
| `oss` | OSS对象存储 | GeoJSON、Shapefile(zip)、KML、GML |
| `third_party_api` | 第三方GIS服务API | 高德POI、天地图服务 |
| `local_file` | 本地上传GIS文件 | GeoJSON、Shapefile(zip)、KML、GML |

### 2. 统一数据模型
所有数据源返回数据统一转换为`StandardGISData`格式：
```json
{
  "success": true,
  "data_type": "vector", // vector/raster/attribute
  "features": [
    {
      "type": "Feature",
      "geometry": {"type": "Point", "coordinates": [116.4, 40.0]},
      "properties": {"name": "测试点", "address": "北京市"}
    }
  ],
  "metadata": {
    "source": "postgis",
    "feature_count": 1,
    "is_fallback": false
  },
  "error_message": null
}
```

### 3. 多级缓存机制
- **一级缓存**：内存缓存（TTL 5分钟，最多1000条）
- **二级缓存**：Redis缓存（可配置TTL，默认30分钟）
- 热点数据第二次查询响应时间降低80%以上
- 支持手动缓存失效

### 4. 权限控制
- 基于用户角色的数据过滤：
  - `admin`：完整访问所有数据
  - `editor`：访问非敏感数据，可编辑
  - `user`：仅访问公开数据，敏感字段自动过滤
  - `guest`：仅访问基础公共数据

### 5. 异常降级
- 数据源访问失败时自动返回最近缓存的数据
- 无缓存时返回友好错误提示，不影响上层服务可用性

## API 接口

### POST /api/data-fetcher/query
查询GIS数据

#### 请求参数
```json
{
  "data_source": "postgis", // 数据源类型，必填
  "query_params": { // 数据源查询参数，必填
    "table": "poi",
    "bbox": [116.3, 39.9, 116.5, 40.1],
    "filter": "type = 'restaurant'"
  },
  "data_type": "vector", // 可选，自动检测
  "skip_cache": false, // 可选，是否跳过缓存
  "cache_ttl": 3600 // 可选，缓存有效期（秒）
}
```

##### 各数据源query_params说明
1. **PostGIS**:
   - `table`: 数据库表名（必填）
   - `bbox`: 空间查询范围 [minx, miny, maxx, maxy]
   - `geometry_column`: 几何字段名，默认geom
   - `properties`: 返回字段列表，默认*
   - `filter`: SQL过滤条件

2. **OSS**:
   - `file_path`: OSS文件路径（必填）
   - `layer`: 图层名（多图层文件时）

3. **第三方API**:
   - `api_provider`: gaode/tianditu，默认gaode
   - `api_type`: poi/road/geocode，默认poi
   - `keywords`: 搜索关键词
   - `city`: 城市名
   - `limit`: 返回数量，默认20

4. **本地文件**:
   - `file_path`: 上传文件路径（必填）

#### 响应
返回`StandardGISData`格式数据，详见统一数据模型。

### POST /api/data-fetcher/invalidate-cache
失效指定查询的缓存（仅管理员可调用）

#### 请求参数
同query接口的请求参数，用于定位要失效的缓存。

## 使用示例
```python
from app.services.data_fetcher import DataFetcherService, DataQuery, DataSourceType

service = DataFetcherService()
query = DataQuery(
    data_source=DataSourceType.POSTGIS,
    query_params={"table": "poi", "bbox": [116.3, 39.9, 116.5, 40.1]}
)
result = service.query(query)
print(result.features)
```

## 配置项
在`app/core/config.py`中添加以下配置：
```python
# 缓存配置
DEFAULT_CACHE_TTL = 1800 # 30分钟
REDIS_URL = "redis://localhost:6379/0" # 可选，不配置则仅使用内存缓存

# OSS配置（可选）
OSS_ACCESS_KEY_ID = "your-access-key"
OSS_ACCESS_KEY_SECRET = "your-secret-key"
OSS_ENDPOINT = "oss-cn-beijing.aliyuncs.com"
OSS_BUCKET_NAME = "your-bucket"

# 第三方API配置（可选）
GAODE_API_KEY = "your-gaode-key"
TIANDITU_API_KEY = "your-tianditu-key"

# 上传文件配置
UPLOAD_DIR = "./uploads"
```
