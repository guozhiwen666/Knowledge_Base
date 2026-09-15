"""请求/响应模型层（Pydantic）。

当前只放请求体模型（``requests``）。响应统一由 ``core.response.ok()`` 包装成
``{code, message, data}``，``data`` 的字段直接沿用服务层返回值 ——
再定义一套 Response 模型等于把同一批字段名写两遍，改一处漏一处。

本 ``__init__`` 不做导入，与其他包的处理保持一致。
"""
