import pytest

from app.errors import FVError
from app.warehouse import MAX_ROWS, check_sql


def test_select_gets_a_limit():
    sql = check_sql("SELECT sku FROM fv.dim_product")
    assert sql.endswith(f"LIMIT {MAX_ROWS}")


def test_existing_limit_is_kept():
    assert check_sql("SELECT sku FROM fv.dim_product LIMIT 5").endswith("LIMIT 5")


def test_cte_is_allowed():
    check_sql("WITH x AS (SELECT sku FROM fv.dim_product) SELECT * FROM x")


@pytest.mark.parametrize("sql", [
    "DELETE FROM fv.dim_product",
    "UPDATE fv.dim_product SET sku = 'x'",
    "SELECT 1; DROP TABLE fv.dim_product",
    "CREATE TABLE fv.x AS SELECT 1",
    "SELECT * FROM fv.dim_product WHERE 1=1 OR sku IN (SELECT sku FROM fv.bom) ; INSERT INTO fv.bom VALUES (1)",
    "SELECT 1",
])
def test_writes_and_tableless_queries_are_rejected(sql):
    with pytest.raises(FVError) as e:
        check_sql(sql)
    assert e.value.code == "FV-SQL-001"


def test_table_outside_allowlist_is_rejected():
    with pytest.raises(FVError) as e:
        check_sql("SELECT * FROM fv.secrets")
    assert e.value.code == "FV-SQL-002"
