"""G38 数据关联与空间富集作者工作台。

架构对齐 G31/G33 workbench 范式：本包只拥有「关联计划/诊断/执行/派生成果」
域语义；执行活性/取消/fencing 走 G23 durable runner，内容走
DATA_DIR/data-join 终态包 + DataObject CAS（G18 纪律），源解析/授权复用
G18 source_resolver，选择集只认 G24 handle。不新建第二套 agent loop、
DAG、工具注册表、MapSpec store 或撤销栈。

单一编排面 = ``service``；REST 路由与 Pi 工具均为薄壳。
"""
