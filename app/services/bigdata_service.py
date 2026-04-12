"""
大数据量处理服务 - 分页、流式传输、自动截断
"""
import hashlib
import json
import os
import tempfile
from typing import Generator


def paginate_query(query, page: int, page_size: int) -> dict:
    """分页查询辅助函数"""
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_next": page * page_size < total,
        "has_prev": page > 1,
    }


def truncate_large_result(items: list, max_items: int = 5000) -> dict:
    """大结果自动截断并生成本地下载链接"""
    if len(items) <= max_items:
        return {"items": items, "truncated": False, "total": len(items)}

    truncated = items[:max_items]

    data = json.dumps(items).encode()
    hash_val = hashlib.sha256(data).hexdigest()[:16]

    tmp_path = os.path.join(tempfile.gettempdir(), f"{hash_val}.json")
    with open(tmp_path, "w") as f:
        json.dump(items, f)

    return {
        "items": truncated,
        "truncated": True,
        "total": len(items),
        "displayed": max_items,
        "download_url": f"/api/v1/download/temp/{hash_val}.json",
    }


def stream_file_generator(file_path: str, chunk_size: int = 8192) -> Generator[bytes, None, None]:
    """流式文件生成器 - 避免大文件内存溢出"""
    with open(file_path, 'rb') as f:
        while chunk := f.read(chunk_size):
            yield chunk


__all__ = ["paginate_query", "truncate_large_result", "stream_file_generator"]