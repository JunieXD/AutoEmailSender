SELECT u.purpose AS "purpose!: model::usage::Purpose",
 coalesce(sum(u.input_tokens),0) AS "input!: i64", coalesce(sum(u.cached_tokens),0) AS "cached!: i64",
 coalesce(sum(u.output_tokens),0) AS "output!: i64",
 sum(CASE WHEN c.input_price IS NOT NULL AND c.output_price IS NOT NULL
   THEN (u.input_tokens*c.input_price+u.output_tokens*c.output_price)/1000000.0 END) AS "estimated_cost?: f64",
 count(CASE WHEN c.input_price IS NULL OR c.output_price IS NULL THEN 1 END) AS "unpriced_calls!: i64"
FROM usage u LEFT JOIN model_config c ON c.id=u.model_config_id
WHERE (?1 IS NULL OR u.at>=?1) AND u.at<?2 GROUP BY u.purpose ORDER BY u.purpose
