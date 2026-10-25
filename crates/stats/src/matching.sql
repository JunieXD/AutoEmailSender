WITH scores AS MATERIALIZED (
 SELECT CASE WHEN match_score IS NULL THEN 5 ELSE min(match_score/20,4) END AS bucket, count(*) AS n
 FROM mentor WHERE trashed_at IS NULL GROUP BY bucket
)
SELECT (SELECT coalesce(sum(n),0) FROM scores) AS "total!: i64",
 (SELECT json_array(coalesce(sum(CASE WHEN bucket=0 THEN n END),0), coalesce(sum(CASE WHEN bucket=1 THEN n END),0),
 coalesce(sum(CASE WHEN bucket=2 THEN n END),0),coalesce(sum(CASE WHEN bucket=3 THEN n END),0),
 coalesce(sum(CASE WHEN bucket=4 THEN n END),0),coalesce(sum(CASE WHEN bucket=5 THEN n END),0)) FROM scores) AS "matching!: String"
