CREATE TABLE IF NOT EXISTS business_domain (
  id CHAR(36) CHARACTER SET ascii PRIMARY KEY,
  code VARCHAR(100) NOT NULL UNIQUE,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  management_status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
  revision BIGINT NOT NULL DEFAULT 1,
  created_at DATETIME(6) NOT NULL,
  updated_at DATETIME(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS business_system (
  id CHAR(36) CHARACTER SET ascii PRIMARY KEY,
  domain_id CHAR(36) CHARACTER SET ascii NOT NULL,
  code VARCHAR(100) NOT NULL UNIQUE,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  management_status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
  revision BIGINT NOT NULL DEFAULT 1,
  created_at DATETIME(6) NOT NULL,
  updated_at DATETIME(6) NOT NULL,
  FOREIGN KEY (domain_id) REFERENCES business_domain(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS catalog_import_run (
  sequence BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  id CHAR(36) CHARACTER SET ascii NOT NULL UNIQUE,
  source_instance_key VARCHAR(191) NOT NULL,
  source_version VARCHAR(100) NOT NULL,
  source_relative_path VARCHAR(512) NOT NULL,
  snapshot_sha256 CHAR(64) CHARACTER SET ascii NOT NULL,
  mapper_version VARCHAR(50) NOT NULL,
  scope_json JSON NOT NULL,
  completeness_json JSON NOT NULL,
  observed_at DATETIME(6),
  imported_at DATETIME(6) NOT NULL,
  status VARCHAR(20) NOT NULL,
  is_current BOOLEAN NOT NULL DEFAULT FALSE,
  current_source VARCHAR(191) GENERATED ALWAYS AS
    (CASE WHEN is_current THEN source_instance_key ELSE NULL END) STORED,
  counts_json JSON,
  UNIQUE KEY uq_catalog_input (source_instance_key, snapshot_sha256, mapper_version),
  UNIQUE KEY uq_catalog_current (current_source)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS deployment_unit (
  id CHAR(36) CHARACTER SET ascii PRIMARY KEY,
  code VARCHAR(50) NOT NULL UNIQUE,
  name VARCHAR(255) NOT NULL,
  unit_type VARCHAR(30) NOT NULL,
  technology_code VARCHAR(100),
  system_id CHAR(36) CHARACTER SET ascii,
  description TEXT,
  creation_origin VARCHAR(30) NOT NULL,
  name_policy VARCHAR(20) NOT NULL DEFAULT 'FOLLOW_SOURCE',
  management_status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
  revision BIGINT NOT NULL DEFAULT 1,
  created_at DATETIME(6) NOT NULL,
  updated_at DATETIME(6) NOT NULL,
  FOREIGN KEY (system_id) REFERENCES business_system(id),
  CHECK (unit_type IN ('APPLICATION','DATABASE','MIDDLEWARE','OTHER')),
  CHECK (name_policy IN ('FOLLOW_SOURCE','MANUAL'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS deployment_unit_environment (
  id CHAR(36) CHARACTER SET ascii PRIMARY KEY,
  unit_id CHAR(36) CHARACTER SET ascii NOT NULL,
  environment VARCHAR(20) NOT NULL,
  configuration_state VARCHAR(30) NOT NULL,
  coverage_state VARCHAR(20) NOT NULL,
  current_observation_id CHAR(36) CHARACTER SET ascii,
  observed_at DATETIME(6),
  updated_at DATETIME(6) NOT NULL,
  UNIQUE KEY uq_unit_environment (unit_id, environment),
  FOREIGN KEY (unit_id) REFERENCES deployment_unit(id),
  CHECK (environment IN ('TEST','SIMULATION','PRODUCT'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS deployment_instance (
  id CHAR(36) CHARACTER SET ascii PRIMARY KEY,
  unit_environment_id CHAR(36) CHARACTER SET ascii NOT NULL,
  name VARCHAR(255),
  instance_kind VARCHAR(30) NOT NULL,
  primary_ip VARCHAR(45),
  service_address VARCHAR(512),
  network_scope_key VARCHAR(191),
  declared_port_raw VARCHAR(100),
  transport_protocol VARCHAR(20),
  host_reference_json JSON,
  identity_quality VARCHAR(20) NOT NULL,
  management_status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
  presence_status VARCHAR(20) NOT NULL,
  current_observation_id CHAR(36) CHARACTER SET ascii,
  first_seen_at DATETIME(6),
  last_seen_at DATETIME(6),
  revision BIGINT NOT NULL DEFAULT 1,
  created_at DATETIME(6) NOT NULL,
  updated_at DATETIME(6) NOT NULL,
  UNIQUE KEY uq_instance_context (id, unit_environment_id),
  KEY idx_instance_environment (unit_environment_id, presence_status),
  KEY idx_instance_ip (primary_ip),
  FOREIGN KEY (unit_environment_id) REFERENCES deployment_unit_environment(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS deployment_unit_source (
  id CHAR(36) CHARACTER SET ascii PRIMARY KEY,
  unit_id CHAR(36) CHARACTER SET ascii NOT NULL,
  source_instance_key VARCHAR(191) NOT NULL,
  object_kind VARCHAR(40) NOT NULL,
  native_key_json JSON NOT NULL,
  native_key_hash CHAR(64) CHARACTER SET ascii NOT NULL,
  primary_app_unit CHAR(36) CHARACTER SET ascii GENERATED ALWAYS AS
    (CASE WHEN object_kind='DEVOPS_APPLICATION' THEN unit_id ELSE NULL END) STORED,
  presence_status VARCHAR(20) NOT NULL,
  first_seen_at DATETIME(6),
  last_seen_at DATETIME(6),
  latest_observation_id CHAR(36) CHARACTER SET ascii,
  confidence VARCHAR(20) NOT NULL,
  evidence_json JSON NOT NULL,
  UNIQUE KEY uq_unit_native (source_instance_key, object_kind, native_key_hash),
  UNIQUE KEY uq_primary_app_unit (primary_app_unit),
  FOREIGN KEY (unit_id) REFERENCES deployment_unit(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS deployment_instance_source (
  id CHAR(36) CHARACTER SET ascii PRIMARY KEY,
  instance_id CHAR(36) CHARACTER SET ascii NOT NULL,
  unit_environment_id CHAR(36) CHARACTER SET ascii NOT NULL,
  source_instance_key VARCHAR(191) NOT NULL,
  object_kind VARCHAR(40) NOT NULL,
  native_key_json JSON NOT NULL,
  native_key_hash CHAR(64) CHARACTER SET ascii NOT NULL,
  identity_strength VARCHAR(20) NOT NULL,
  confidence VARCHAR(20) NOT NULL,
  matching_basis VARCHAR(100) NOT NULL,
  first_seen_at DATETIME(6),
  last_seen_at DATETIME(6),
  evidence_json JSON NOT NULL,
  UNIQUE KEY uq_instance_native (source_instance_key, object_kind, native_key_hash, unit_environment_id),
  FOREIGN KEY (instance_id, unit_environment_id)
    REFERENCES deployment_instance(id, unit_environment_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS catalog_observation (
  id CHAR(36) CHARACTER SET ascii PRIMARY KEY,
  import_run_id CHAR(36) CHARACTER SET ascii NOT NULL,
  record_kind VARCHAR(30) NOT NULL,
  unit_id CHAR(36) CHARACTER SET ascii,
  unit_environment_id CHAR(36) CHARACTER SET ascii,
  instance_id CHAR(36) CHARACTER SET ascii,
  source_locator_json JSON NOT NULL,
  source_locator_hash CHAR(64) CHARACTER SET ascii NOT NULL,
  observed_at DATETIME(6),
  time_basis VARCHAR(30) NOT NULL,
  source_presence VARCHAR(20) NOT NULL,
  confidence VARCHAR(20) NOT NULL,
  matching_basis VARCHAR(100) NOT NULL,
  source_attributes_json JSON NOT NULL,
  evidence_json JSON NOT NULL,
  semantic_hash CHAR(64) CHARACTER SET ascii NOT NULL,
  UNIQUE KEY uq_catalog_observation (import_run_id, record_kind, source_locator_hash),
  KEY idx_observation_instance (instance_id, observed_at),
  FOREIGN KEY (import_run_id) REFERENCES catalog_import_run(id),
  FOREIGN KEY (unit_id) REFERENCES deployment_unit(id),
  FOREIGN KEY (unit_environment_id) REFERENCES deployment_unit_environment(id),
  FOREIGN KEY (instance_id) REFERENCES deployment_instance(id),
  CHECK (
    (record_kind='APPLICATION' AND unit_id IS NOT NULL AND unit_environment_id IS NULL AND instance_id IS NULL)
    OR (record_kind IN ('ENVIRONMENT','UNRESOLVED_DEPLOYMENT') AND unit_id IS NULL AND unit_environment_id IS NOT NULL AND instance_id IS NULL)
    OR (record_kind='DEPLOYMENT' AND unit_id IS NULL AND unit_environment_id IS NULL AND instance_id IS NOT NULL)
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS catalog_change (
  id CHAR(36) CHARACTER SET ascii PRIMARY KEY,
  change_batch_id CHAR(36) CHARACTER SET ascii NOT NULL,
  object_type VARCHAR(40) NOT NULL,
  object_id CHAR(36) CHARACTER SET ascii NOT NULL,
  action VARCHAR(40) NOT NULL,
  before_json JSON,
  after_json JSON,
  actor_type VARCHAR(20) NOT NULL,
  actor_id VARCHAR(191),
  import_run_id CHAR(36) CHARACTER SET ascii,
  changed_at DATETIME(6) NOT NULL,
  reason VARCHAR(255),
  KEY idx_catalog_change_object (object_type, object_id, changed_at),
  FOREIGN KEY (import_run_id) REFERENCES catalog_import_run(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
