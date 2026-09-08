"""SDK 标识符规则（与 manifest 同源的 ASCII 正则；Round-1 审计 F6）。

此前 SDK 层用 ``str.isalnum()`` 判定——它接受 unicode 字母（如
"café"），与 manifest 的 ASCII 正则不一致。统一从本模块取正则。
"""

import re

# 单字符合法；小写 ASCII 开头；只允许小写字母/数字/下划线。
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
