"""隔离 worker 子包（ADR-0105 V2）。

- ``protocol``：行分帧 JSON RPC（协议版本化、帧上限）；
- ``context``：worker 进程内的激活门面（声明收集，不写权威 registry）；
- ``server``：子进程事件循环（``python -m ...worker.server --pack-dir``）；
- ``client``（Wave 3）：宿主侧 spawn / 握手 / 调用 / 崩溃检测。
"""
