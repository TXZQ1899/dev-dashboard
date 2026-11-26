CREATE TABLE IF NOT EXISTS jvm_adjustment (
  id CHAR(36) CHARACTER SET ascii PRIMARY KEY,
  deployment_instance_id CHAR(36) CHARACTER SET ascii,
  instance_ip VARCHAR(45),
  unit_id CHAR(36) CHARACTER SET ascii,
  environment VARCHAR(20),
  current_jvm_config VARCHAR(512),
  xms_new VARCHAR(32),
  xmx_new VARCHAR(32),
  operator VARCHAR(191),
  note TEXT,
  source VARCHAR(30) DEFAULT 'MANUAL',
  created_at DATETIME(6) NOT NULL,
  INDEX idx_jvm_adj_instance (deployment_instance_id, created_at),
  INDEX idx_jvm_adj_ip_env (instance_ip, environment, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
