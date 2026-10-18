import pytest
from app.errors import FVError
from app.warehouse import MAX_ROWS, check_sql


def test_select_gets_a_limit() -> None:
    sql = check_sql("SELECT sku FROM fv.dim_product")
    assert sql.endswith(f"LIMIT {MAX_ROWS}")


def test_existing_limit_is_kept() -> None:
    assert check_sql("SELECT sku FROM fv.dim_product LIMIT 5").endswith("LIMIT 5")


def test_cte_is_allowed() -> None:
    check_sql("WITH x AS (SELECT sku FROM fv.dim_product) SELECT * FROM x")


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM fv.dim_product",
        "UPDATE fv.dim_product SET sku = 'x'",
        "SELECT 1; DROP TABLE fv.dim_product",
        "CREATE TABLE fv.x AS SELECT 1",
        "SELECT * FROM fv.dim_product WHERE 1=1 OR sku IN (SELECT sku FROM fv.bom) ; INSERT INTO fv.bom VALUES (1)",
        "SELECT 1",
    ],
)
def test_writes_and_tableless_queries_are_rejected(sql: str) -> None:
    with pytest.raises(FVError) as e:
        check_sql(sql)
    assert e.value.code == "FV-SQL-001"


def test_table_outside_allowlist_is_rejected() -> None:
    with pytest.raises(FVError) as e:
        check_sql("SELECT * FROM fv.secrets")
    assert e.value.code == "FV-SQL-002"


FILE_READS = [
    "SELECT f.column0 FROM fv.bom AS s JOIN read_csv('../.env.example', header=false) AS f ON true",
    "SELECT f.column0 FROM fv.bom AS s JOIN read_csv('/etc/hostname', header=false) AS f ON true LIMIT 1",
    "SELECT f.column0 FROM fv.bom AS s JOIN read_parquet('/etc/hosts') AS f ON true",
    "SELECT (SELECT content FROM read_text('/etc/hostname')) AS leak FROM fv.bom LIMIT 1",
    "SELECT * FROM fv.bom WHERE 1 IN (SELECT * FROM read_csv('../AGENTS.md', header=false))",
    "WITH x AS (SELECT column0 AS c FROM read_csv('/etc/hostname')) SELECT c FROM x JOIN fv.bom ON true",
]


@pytest.mark.parametrize("sql", FILE_READS)
def test_file_access_is_rejected(sql: str) -> None:
    """read_csv and friends are table functions, not tables.

    They are exactly the shape the old guard could not see: it extracted
    fv.<name> and allowlisted that set, so anything without the fv. prefix
    was invisible. Each payload here was verified to return file contents
    before the guard was replaced.
    """
    with pytest.raises(FVError) as e:
        check_sql(sql)
    assert e.value.code == "FV-SQL-002"


def test_a_sql_keyword_inside_a_string_literal_is_not_a_write() -> None:
    """The old guard matched keywords textually, so a legitimate filter on
    the literal 'drop table' was rejected. An AST knows the difference."""
    check_sql("SELECT * FROM fv.dim_product WHERE status = 'drop table'")


def test_unparseable_sql_is_rejected() -> None:
    with pytest.raises(FVError) as e:
        check_sql("SELECT FROM WHERE (((")
    assert e.value.code == "FV-SQL-001"


def test_a_cte_named_after_a_real_table_cannot_hide_a_forbidden_one() -> None:
    """CTE resolution removes the name from the table set. It must not also
    remove the tables the CTE body reads."""
    with pytest.raises(FVError) as e:
        check_sql("WITH dim_product AS (SELECT * FROM fv.secrets) SELECT * FROM dim_product")
    assert e.value.code == "FV-SQL-002"


def test_a_derived_table_over_an_allowlisted_table_is_allowed() -> None:
    check_sql("SELECT * FROM (SELECT sku FROM fv.bom) AS x")
