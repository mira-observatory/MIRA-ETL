"""Exact per-currency supplier totals, refreshed atomically after each load."""

SUPPLIER_TOTALS_SQL = """
    insert into mart.supplier_award_totals
        (country_code, supplier_id, currency_code, total_awarded_amount,
         award_count, shared_award_count, refreshed_at)
    select p.country_code, links.supplier_id, a.currency_code,
           sum(a.awarded_amount), count(*), count(shared.award_id), now()
    from query.v_awards a
    join mart.processes p on p.process_id = a.process_id
    join mart.award_suppliers links on links.award_id = a.award_id
    left join (
        select award_id from mart.award_suppliers
        group by award_id having count(*) > 1
    ) shared on shared.award_id = a.award_id
    where p.country_code = %s
      and a.awarded_amount is not null
      and a.currency_code is not null and btrim(a.currency_code) <> ''
    group by p.country_code, links.supplier_id, a.currency_code
"""
