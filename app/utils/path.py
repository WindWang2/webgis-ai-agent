import os

def validate_data_path(path: str, data_dir: str = "./data") -> str:
    """
    验证并规范化用户传入的文件路径，防止目录遍历 + 符号链接逃逸攻击。

    使用 os.path.realpath 解析所有符号链接后再比对基础目录 —— 即使攻击者
    在 data_dir 内放置了指向 /etc 的 symlink，校验也会失败。

    审计 S36：之前使用 os.path.abspath（不解析符号链接），威胁模型假设
    "data/tmp 由部署方控制，用户无法在其中创建 symlink"。但实际多入口
    （skill upload、shapefile zip 解压、NDVI 输出路径）都允许用户间接写入
    data_dir，symlink 逃逸成为可达路径。改用 realpath 关闭该路径。

    Args:
        path: 用户传入的路径（可为相对路径或绝对路径）
        data_dir: 允许的基础目录

    Returns:
        规范化的绝对路径（已解析所有符号链接）

    Raises:
        ValueError: 路径非法或超出允许范围
    """
    # 用 realpath 解析所有符号链接（abspath 仅做字符串规范化）
    data_dir_real = os.path.realpath(data_dir)

    if os.path.isabs(path):
        resolved = os.path.realpath(path)
    else:
        resolved = os.path.realpath(os.path.join(data_dir_real, path))

    # 安全检查：确保解析后的路径在 data_dir 之下
    if not resolved.startswith(data_dir_real + os.sep) and resolved != data_dir_real:
        # 允许 ./tmp 作为临时工作区（同样 realpath 化以避免符号链接绕过）
        tmp_dir = os.path.realpath("./tmp")
        if not (resolved.startswith(tmp_dir + os.sep) or resolved == tmp_dir):
            raise ValueError(f"非法路径: '{path}' 超出允许目录范围 ({data_dir_real})")

    return resolved
