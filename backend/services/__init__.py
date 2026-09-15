"""服务层（service layer）。

按《技术方案设计文档》5.1 ~ 5.8 划分服务模块，一个服务一个子包：

==========================================  ==================================================
子包 / 模块                                  对应 2.9.6 模块
==========================================  ==================================================
``authentication_and_authorization_module``  5.1 认证鉴权模块
  ├─ ``auth``                                认证、JWT 签发校验、RBAC 拦截、密码重置
  └─ ``passwords``                           口令哈希（单独成文件，供组织架构服务复用）
``organization_structure_service``           5.2 组织架构服务
  └─ ``org``                                 部门树、用户、角色权限分配
``knowledge_unit_management_service``        5.3 知识单元管理服务
  ├─ ``knowledge``                           主模块：导入、CRUD、状态流转、向量同步编排
  ├─ ``knowledge_importer``                  导入流水线（单文件与批量）
  ├─ ``knowledge_parsers``                   四类格式解析与文本切片
  └─ ``knowledge_storage``                   MinIO 对象存储、Milvus 向量读写
``data_permission_engine``                   5.4 数据权限引擎
  └─ ``permission``                          四维鉴权（唯一鉴权点，fail-closed）
``ai_authentication_and_retrieval_service``  5.5 AI 鉴权检索服务
  ├─ ``retrieval``                           向量 + 关键字混合召回
  └─ ``qa``                                  门面：权限过滤、Prompt 组装、流式、引用与提示
``data_dashboard_statistics_service``        5.6 数据看板统计服务
  ├─ ``dashboard``                           日志写入、实时计数、查询入口
  └─ ``aggregation``                         指标 / 榜单 / 趋势的 SQL 聚合
``knowledge_precipitation_and_mining_service``  5.7 知识沉淀挖掘服务
  ├─ ``mining``                              高频问题挖掘与知识缺口识别
  └─ ``settlement``                          门面：定时入口、推荐列表、审核流转
``FAQ_cache_ervice``                         5.8 FAQ 缓存服务
  └─ ``faq_cache``                           精确 / 语义匹配、发布与失效、命中计数
==========================================  ==================================================

**导入约定**：以 ``backend/`` 为工作目录（导入根），统一用包内绝对路径，例如::

    from services.knowledge_unit_management_service.knowledge import KnowledgeUnitService
    from models import KnowledgeUnit

**本 ``__init__`` 不做任何导入**：服务链路会牵出 minio / pymilvus / PyJWT 等第三方依赖，
在这里导入等于让"只想用某个无关服务"的环境被迫装齐它们。需要时显式导入即可。
"""
