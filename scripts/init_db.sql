-- =============================================================================
-- 知识库管理平台 · 数据库初始化脚本
--
-- 依据：《技术方案设计文档》7.3 DDL 脚本（逐字对齐，不增删任何列与索引）
-- 用途：建库 + 建表。表结构以此脚本为准 ——
--       SQLAlchemy 的 create_all() 建不出 ON UPDATE CURRENT_TIMESTAMP
--       （server_onupdate 只用于 ORM 提示，不生成 DDL），
--       两套建表方式会产生行为不同的表，因此统一走本脚本。
--
-- 执行：mysql -h127.0.0.1 -uroot -p < scripts/init_db.sql
-- =============================================================================

CREATE DATABASE IF NOT EXISTS knowledge_base
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
USE knowledge_base;

-- 1. 部门
CREATE TABLE IF NOT EXISTS departments (
  id          BIGINT       NOT NULL AUTO_INCREMENT,
  parent_id   BIGINT       NULL COMMENT '上级部门，顶级为 NULL',
  name        VARCHAR(128) NOT NULL COMMENT '部门名称',
  leader_id   BIGINT       NULL COMMENT '部门负责人 users.id',
  sort_order  INT          NOT NULL DEFAULT 0 COMMENT '同级排序',
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_departments_parent (parent_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='部门表';

-- 2. 用户
CREATE TABLE IF NOT EXISTS users (
  id            BIGINT       NOT NULL AUTO_INCREMENT,
  username      VARCHAR(64)  NOT NULL COMMENT '登录名',
  password_hash VARCHAR(255) NOT NULL COMMENT '密码哈希',
  display_name  VARCHAR(64)  NOT NULL COMMENT '显示名',
  department_id BIGINT       NULL COMMENT '所属部门',
  status        TINYINT      NOT NULL DEFAULT 1 COMMENT '1启用 0停用',
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_users_username (username),
  KEY idx_users_department (department_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户表';

-- 3. 角色
CREATE TABLE IF NOT EXISTS roles (
  id          BIGINT       NOT NULL AUTO_INCREMENT,
  role_name   VARCHAR(64)  NOT NULL COMMENT '角色名称',
  role_code   VARCHAR(64)  NOT NULL COMMENT '角色编码',
  description VARCHAR(255) NULL     COMMENT '角色描述',
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_roles_code (role_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色表';

-- 4. 用户-角色
CREATE TABLE IF NOT EXISTS user_roles (
  id         BIGINT   NOT NULL AUTO_INCREMENT,
  user_id    BIGINT   NOT NULL,
  role_id    BIGINT   NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_user_roles (user_id, role_id),
  KEY idx_user_roles_role (role_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户角色关联表';

-- 5. 角色-权限
CREATE TABLE IF NOT EXISTS role_permissions (
  id              BIGINT      NOT NULL AUTO_INCREMENT,
  role_id         BIGINT      NOT NULL,
  permission_code VARCHAR(128) NOT NULL COMMENT '权限编码',
  permission_type VARCHAR(32)  NOT NULL COMMENT 'menu/operation/ai',
  created_at      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_role_perm (role_id, permission_code),
  KEY idx_role_perm_role (role_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色权限表';

-- 6. 知识单元
CREATE TABLE IF NOT EXISTS knowledge_units (
  id               BIGINT       NOT NULL AUTO_INCREMENT,
  unit_code        VARCHAR(64)  NOT NULL COMMENT '知识单元唯一编号',
  title            VARCHAR(255) NOT NULL COMMENT '标题',
  content          LONGTEXT     NULL     COMMENT '正文内容',
  summary          VARCHAR(1000) NULL    COMMENT '摘要',
  category         VARCHAR(64)  NULL     COMMENT '分类',
  source_file_name VARCHAR(255) NULL     COMMENT '来源文件名',
  file_type        VARCHAR(16)  NULL     COMMENT 'pdf/md/docx/txt',
  file_size        BIGINT       NULL     COMMENT '文件字节数',
  status           VARCHAR(32)  NOT NULL DEFAULT 'active' COMMENT '状态',
  creator_id       BIGINT       NULL     COMMENT '创建人',
  created_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_ku_code (unit_code),
  KEY idx_ku_category (category),
  KEY idx_ku_status (status),
  KEY idx_ku_creator (creator_id),
  KEY idx_ku_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='知识单元表';

-- 7. 知识单元数据权限实体
CREATE TABLE IF NOT EXISTS unit_permissions (
  id          BIGINT      NOT NULL AUTO_INCREMENT,
  unit_id     BIGINT      NOT NULL,
  target_type VARCHAR(16) NOT NULL COMMENT 'global/department/role/user',
  target_id   BIGINT      NOT NULL DEFAULT 0 COMMENT '目标实体 ID，global 固定为 0',
  created_at  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_unit_perm (unit_id, target_type, target_id),
  KEY idx_unit_perm_unit (unit_id),
  KEY idx_unit_perm_target (target_type, target_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='知识单元数据权限表';

-- 8. 问答访问日志
CREATE TABLE IF NOT EXISTS qa_access_logs (
  id                       BIGINT       NOT NULL AUTO_INCREMENT,
  session_id               VARCHAR(64)  NOT NULL COMMENT '会话标识',
  user_id                  BIGINT       NULL     COMMENT '提问用户',
  question                 TEXT         NULL     COMMENT '提问内容',
  answer                   MEDIUMTEXT   NULL     COMMENT '回答内容',
  recalled_unit_ids_json   JSON         NULL     COMMENT '召回单元 ID 列表',
  authorized_unit_ids_json JSON         NULL     COMMENT '授权单元 ID 列表',
  unauthorized_unit_ids_json JSON       NULL     COMMENT '未授权单元 ID 列表',
  prompt_tokens            INT          NOT NULL DEFAULT 0,
  completion_tokens        INT          NOT NULL DEFAULT 0,
  total_tokens             INT          NOT NULL DEFAULT 0,
  response_time_ms         INT          NOT NULL DEFAULT 0,
  created_at               DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_log_session (session_id),
  KEY idx_log_user (user_id),
  KEY idx_log_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='问答访问日志表';

-- 9. FAQ
CREATE TABLE IF NOT EXISTS faqs (
  id              BIGINT       NOT NULL AUTO_INCREMENT,
  question        VARCHAR(512) NOT NULL COMMENT '标准问题',
  answer          MEDIUMTEXT   NULL     COMMENT '标准答案',
  category        VARCHAR(64)  NULL     COMMENT '分类',
  related_unit_id BIGINT       NULL     COMMENT '关联知识单元',
  source_type     VARCHAR(16)  NOT NULL COMMENT 'manual/auto_mined',
  status          VARCHAR(24)  NOT NULL DEFAULT 'pending_review'
                  COMMENT 'pending_review/published/rejected',
  hit_count       BIGINT       NOT NULL DEFAULT 0 COMMENT '缓存命中次数',
  reviewer_id     BIGINT       NULL     COMMENT '审核人',
  reviewed_at     DATETIME     NULL     COMMENT '审核时间',
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_faq_status (status),
  KEY idx_faq_source (source_type),
  KEY idx_faq_unit (related_unit_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='FAQ 表';

-- 10. 知识缺口
CREATE TABLE IF NOT EXISTS knowledge_gaps (
  id                    BIGINT       NOT NULL AUTO_INCREMENT,
  question_pattern      VARCHAR(512) NOT NULL COMMENT '问题模式/聚类代表问题',
  sample_questions_json JSON         NULL     COMMENT '样本提问列表',
  ask_count             INT          NOT NULL DEFAULT 1 COMMENT '提问频次',
  last_asked_at         DATETIME     NULL     COMMENT '最近提问时间',
  status                VARCHAR(16)  NOT NULL DEFAULT 'unresolved'
                        COMMENT 'unresolved/resolved/ignored',
  resolved_unit_id      BIGINT       NULL     COMMENT '补全后的知识单元',
  created_at            DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at            DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_gap_status (status),
  KEY idx_gap_count (ask_count)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='知识缺口表';
