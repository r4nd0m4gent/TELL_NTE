-- =============================================================================
-- TELL – Textile Ecosystem Living Lab  |  PostgreSQL schema
-- =============================================================================
DROP TABLE IF EXISTS contributions CASCADE;
DROP TABLE IF EXISTS keywords      CASCADE;
DROP TABLE IF EXISTS organizations CASCADE;
DROP TABLE IF EXISTS geographies   CASCADE;

-- 1. geographies  ─  reference table keyed by Dutch 4-digit postcode
CREATE TABLE geographies (
    postcode    CHAR(4)      PRIMARY KEY,
    country     VARCHAR(4)   NOT NULL DEFAULT 'NL',
    region      VARCHAR(128),
    province    VARCHAR(128),
    city        VARCHAR(128),
    latitude    NUMERIC(9,6),
    longitude   NUMERIC(9,6)
);

-- 2. organizations
CREATE TABLE organizations (
    organization_id  SERIAL        PRIMARY KEY,
    trade_name       VARCHAR(255)  NOT NULL,
    legal_name       VARCHAR(255),
    website          VARCHAR(512),
    postcode         CHAR(4)       REFERENCES geographies(postcode),
    status           VARCHAR(64),
    number_employees INTEGER,
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_org_postcode   ON organizations(postcode);
CREATE INDEX idx_org_trade_name ON organizations(trade_name);

-- 3. keywords  ─  stable tags + dynamic NLP results via JSONB
--
--   To store a new NLP run, add a key to nlp_analyses – no ALTER TABLE needed:
--   UPDATE keywords
--   SET nlp_analyses = nlp_analyses || '{"my_model_v2":{"value":"Tier 1"}}'::jsonb
--   WHERE organization_id = <id>;
CREATE TABLE keywords (
    keyword_id            SERIAL       PRIMARY KEY,
    organization_id       INTEGER      NOT NULL
                              REFERENCES organizations(organization_id)
                              ON DELETE CASCADE,
    main_activity         VARCHAR(255),
    secondary_activity_1  VARCHAR(255),
    secondary_activity_2  VARCHAR(255),
    tag_category          VARCHAR(255),
    tags                  TEXT,
    nlp_analyses          JSONB        NOT NULL DEFAULT '{}'::JSONB,
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_kw_org ON keywords(organization_id);
CREATE INDEX idx_kw_nlp ON keywords USING GIN (nlp_analyses);

-- 4. contributions  ─  form submissions awaiting review
CREATE TABLE contributions (
    contribution_id  SERIAL        PRIMARY KEY,
    type             VARCHAR(16)   NOT NULL CHECK (type IN ('add','edit')),
    organization_id  INTEGER       REFERENCES organizations(organization_id),
    payload          JSONB         NOT NULL,
    status           VARCHAR(16)   NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending','accepted','rejected')),
    submitted_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    reviewed_at      TIMESTAMPTZ
);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$;

CREATE TRIGGER trg_org_updated BEFORE UPDATE ON organizations
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_kw_updated  BEFORE UPDATE ON keywords
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

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
    k.nlp_analyses -> 'predicted_category_v1' ->> 'value'  AS "Predicted_Category",
    k.nlp_analyses -> 'predicted_tier_v1'     ->> 'value'  AS "Predicted_Tier"
FROM organizations o
LEFT JOIN geographies g ON g.postcode = o.postcode
LEFT JOIN keywords    k ON k.organization_id = o.organization_id;
