import os

def validate_data_path(path: str, data_dir: str = "./data") -> str:
    """
    验证并规范化用户传入的文件路径，防止目录遍历攻击。

    Args:
        path: 用户传入的路径（可为相对路径或绝对路径）
        data_dir: 允许的基础目录

    Returns:
        规范化的绝对路径

    Raises:
        ValueError: 路径非法或超出允许范围
    """
    # 转换为绝对路径
    data_dir_abs = os.path.abspath(data_dir)
    
    # 如果 path 是绝对路径，检查它是否在 data_dir 之下
    # 如果 path 是相对路径，先 join 再检查
    if os.path.isabs(path):
        resolved = os.path.abspath(path)
    else:
        resolved = os.path.abspath(os.path.join(data_dir_abs, path))

    # 安全检查：确保解析后的路径在 data_dir 之下
    # 增加对一些常见系统目录的例外（如果需要，但在本项目中应严格限制在 data_dir）
    if not resolved.startswith(data_dir_abs + os.sep) and resolved != data_dir_abs:
        # 允许 /tmp 作为临时工作区
        tmp_dir = os.path.abspath("./tmp")
        if not (resolved.startswith(tmp_dir + os.sep) or resolved == tmp_dir):
            raise ValueError(f"非法路径: '{path}' 超出允许目录范围 ({data_dir_abs})")

    return resolved
