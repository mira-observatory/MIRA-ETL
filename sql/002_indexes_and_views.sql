-- Indexes used by ETL relationship lookups and citizen-facing query shapes.
-- Analytics log tables deliberately have no secondary indexes; quota_counters
-- already has the primary-key index required by its runtime read/update path.
create index if not exists idx_processes_country
    on mart.processes (country_code);

-- Fuzzy entity search (MIRA-API resolves a typed name to a supplier/buyer
-- candidate list). name_normalised keeps the published casing/accents, so
-- matching has to fold case and accents at query time -- it is never folded
-- when the row is written. unaccent() is STABLE, not IMMUTABLE, so it cannot
-- be used directly in an index expression; this wrapper pins the dictionary
-- by name to make it safely IMMUTABLE.
--
-- Lives in `query`, not `mart`: mira_query only has USAGE on `query` (see
-- docs/database_security.md), so MIRA-API's own runtime queries have to call
-- this same function to match the index expression below. A copy in `mart`
-- would build the index fine but be unreachable at query time -- mira_query
-- would get "permission denied for schema mart" the moment it tried to call
-- it directly (querying *through* a view doesn't need this, but constructing
-- a WHERE/ORDER BY with this function by name does).
create extension if not exists pg_trgm;
create extension if not exists unaccent;

create or replace function query.f_unaccent(text)
returns text
language sql
immutable
parallel safe
as $$
    select public.unaccent('public.unaccent', coalesce($1, ''))
$$;

create index if not exists idx_suppliers_name_trgm
    on mart.suppliers using gin (lower(query.f_unaccent(name_normalised)) gin_trgm_ops);

create index if not exists idx_buyers_name_trgm
    on mart.buyers using gin (lower(query.f_unaccent(name_normalised)) gin_trgm_ops);

create index if not exists idx_items_process
    on mart.items (process_id);

create index if not exists idx_awards_process
    on mart.awards (process_id);

create index if not exists idx_awards_award_date
    on mart.awards (award_date);

create index if not exists idx_award_suppliers_supplier
    on mart.award_suppliers (supplier_id);

create index if not exists idx_process_buyers_buyer_id
    on mart.process_buyers (buyer_id);

create index if not exists idx_buyers_country
    on mart.buyers (country_code);

create index if not exists idx_suppliers_country
    on mart.suppliers (country_code);

create index if not exists idx_audit_validation_results_run
    on audit.validation_results (run_id, severity, rule_code);

-- One row per procurement process. Items, awards, buyers, and suppliers are
-- exposed separately so joins never multiply monetary values accidentally.
create or replace view query.v_process as
select
    process_id, country_code, source_system, data_quality_status, source_url,
    extracted_at, source_last_modified_at, normalised_at, missing_fields,
    process_number, title, description, procurement_method, process_status,
    source_status, publication_date, closing_date, estimated_amount, currency_code
from mart.processes;

create or replace view query.v_buyers as
select
    buyer_id,
    country_code,
    source_system,
    name_normalised,
    buyer_tax_id
from mart.buyers;

create or replace view query.v_suppliers as
select
    supplier_id,
    country_code,
    source_system,
    name_normalised,
    supplier_tax_id,
    supplier_type
from mart.suppliers;

create or replace view query.v_process_buyers as
select process_id, buyer_id
from mart.process_buyers;

create or replace view query.v_items as
select
    item_id,
    process_id,
    source_item_id,
    line_number,
    item_description,
    category_source,
    category_normalised
from mart.items;

create or replace view query.v_awards as
select
    award_id,
    process_id,
    source_award_id,
    award_date,
    awarded_amount,
    currency_code
from mart.awards;

create or replace view query.v_award_items as
select award_id, item_id
from mart.award_items;

create or replace view query.v_award_suppliers as
select award_id, supplier_id
from mart.award_suppliers;

-- What source/period the ETL actually loaded, and how the run finished.
-- MIRA-API needs this to tell "zero rows because nothing was awarded" apart
-- from "zero rows because this period was never loaded" -- a bare count over
-- v_process cannot make that distinction on its own. audit.etl_runs.source
-- holds the source system identifier (e.g. "costa_rica_sicop"), the same
-- value as v_process.source_system -- not an ISO country code, so this joins
-- on source_system, not country_code.
create or replace view query.v_coverage as
select
    r.source as source_system,
    r.period,
    r.status,
    r.finished_at as loaded_at,
    rc.table_name,
    rc.row_count
from audit.etl_runs r
left join audit.etl_row_counts rc on rc.run_id = r.id
where r.status = 'SUCCESS';
