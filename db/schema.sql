-- =============================================================================
-- TELL – Textile Ecosystem Living Lab  |  MySQL 8.0+ schema
-- =============================================================================
SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS contributions;
DROP TABLE IF EXISTS keywords;
DROP TABLE IF EXISTS organizations;
DROP TABLE IF EXISTS geographies;
SET FOREIGN_KEY_CHECKS = 1;

-- 1. geographies  ─  reference table keyed by Dutch 4-digit postcode
CREATE TABLE geographies (
    postcode    CHAR(4)       NOT NULL,
    country     VARCHAR(4)    NOT NULL DEFAULT 'NL',
    region      VARCHAR(128),
    province    VARCHAR(128),
    city        VARCHAR(128),
    latitude    DECIMAL(9,6),
    longitude   DECIMAL(9,6),
    PRIMARY KEY (postcode)
);

-- 2. organizations
CREATE TABLE organizations (
    organization_id  INT           NOT NULL AUTO_INCREMENT,
    trade_name       VARCHAR(255)  NOT NULL,
    legal_name       VARCHAR(255),
    website          VARCHAR(512),
    postcode         CHAR(4),
    status           VARCHAR(64),
    number_employees INT,
    created_at       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP
                                            ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (organization_id),
    FOREIGN KEY (postcode) REFERENCES geographies(postcode)
);

CREATE INDEX idx_org_postcode   ON organizations(postcode);
CREATE INDEX idx_org_trade_name ON organizations(trade_name);

-- 3. keywords  ─  stable tags + dynamic NLP results via JSON
--
--   To store a new NLP run, merge a key into nlp_analyses – no ALTER TABLE needed:
--   UPDATE keywords
--   SET nlp_analyses = JSON_MERGE_PATCH(nlp_analyses, '{"my_model_v2":{"value":"Tier 1"}}')
--   WHERE organization_id = <id>;
CREATE TABLE keywords (
    keyword_id            INT          NOT NULL AUTO_INCREMENT,
    organization_id       INT          NOT NULL,
    main_activity         VARCHAR(255),
    secondary_activity_1  VARCHAR(255),
    secondary_activity_2  VARCHAR(255),
    tag_category          VARCHAR(255),
    tags                  TEXT,
    nlp_analyses          JSON         NOT NULL DEFAULT (JSON_OBJECT()),
    updated_at            DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                                                ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (keyword_id),
    UNIQUE KEY idx_kw_org (organization_id),
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id)
        ON DELETE CASCADE
);

-- 4. contributions  ─  form submissions awaiting review
CREATE TABLE contributions (
    contribution_id  INT           NOT NULL AUTO_INCREMENT,
    type             VARCHAR(16)   NOT NULL,
    organization_id  INT,
    payload          JSON          NOT NULL,
    status           VARCHAR(16)   NOT NULL DEFAULT 'pending',
    submitted_at     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at      DATETIME,
    PRIMARY KEY (contribution_id),
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id),
    CONSTRAINT chk_type   CHECK (type   IN ('add', 'edit')),
    CONSTRAINT chk_status CHECK (status IN ('pending', 'accepted', 'rejected'))
);

-- Flat view used by the dashboard
CREATE OR REPLACE VIEW v_companies AS
SELECT
    o.organization_id,
    o.trade_name,
    o.legal_name,
    o.website,
    o.status,
    o.number_employees,
    g.postcode,
    g.region,
    g.province,
    g.city,
    g.latitude,
    g.longitude,
    k.main_activity,
    k.secondary_activity_1,
    k.secondary_activity_2,
    k.tag_category,
    k.tags,
    k.nlp_analyses,
    k.nlp_analyses->>'$.predicted_category_v1.value'  AS Predicted_Category,
    k.nlp_analyses->>'$.predicted_tier_v1.value'      AS Predicted_Tier
FROM organizations o
LEFT JOIN geographies g ON g.postcode = o.postcode
LEFT JOIN keywords    k ON k.organization_id = o.organization_id;
