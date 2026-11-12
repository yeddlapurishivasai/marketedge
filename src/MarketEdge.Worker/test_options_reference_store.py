"""Offline reference validation and transaction safety tests."""
import copy
import unittest
from datetime import date, datetime
from decimal import Decimal

from options_reference_store import MAX_SESSIONS, persist_sessions, persist_weights

DAY = date(2026, 11, 9)


class FakeConnection:
    """Small transactional database fake, including manual ownership predicates."""

    autocommit = False

    def __init__(self):
        self.catalogue = [(i, f"STOCK{i}.NS") for i in range(1, 51)]
        self.sessions = {}
        self.weights = []
        self.commands = []
        self.commits = self.rollbacks = 0
        self.fail_insert_at = None
        self.inserts = 0
        self._backup = None
        self.result = []
        self.description = []

    def cursor(self):
        if self._backup is None:
            self._backup = copy.deepcopy((self.sessions, self.weights))
        return self

    def execute(self, sql, *params):
        self.commands.append((sql, params))
        if sql.startswith("SELECT Id,Symbol"):
            self.description = [("Id",), ("Symbol",)]
            self.result = self.catalogue
        elif sql.startswith("SELECT Source"):
            row = self.sessions.get(params)
            self.result = [(row["source"],)] if row else []
        elif sql.startswith("SELECT StockId"):
            self.description = [("StockId",), ("Source",)]
            self.result = [(row["stock"], row["source"]) for row in self.weights
                           if (row["index"], row["day"]) == params]
        elif "UPDATE [options].[ExchangeSessions]" in sql:
            exchange, trading, opening, closing, source, index, day = params
            if self.sessions[index, day]["source"].startswith("auto:"):
                self.sessions[index, day] = dict(source=source, exchange=exchange, trading=trading,
                                                opening=opening, closing=closing, weekly=False, monthly=False)
        elif "INSERT INTO [options].[ExchangeSessions]" in sql:
            self._insert()
            index, day, exchange, trading, opening, closing, source = params
            self.sessions[index, day] = dict(source=source, exchange=exchange, trading=trading,
                                            opening=opening, closing=closing, weekly=False, monthly=False)
        elif "DELETE FROM" in sql:
            index, day, source = params
            self.weights = [row for row in self.weights
                            if not (row["index"] == index and row["day"] == day
                                    and row["source"] == source and row["source"].startswith("auto:"))]
        elif "INSERT INTO [options].[IndexConstituentHistory]" in sql:
            self._insert()
            index, stock, day, weight, source = params
            self.weights.append(dict(index=index, stock=stock, day=day, weight=weight, source=source))
        else:
            raise AssertionError(f"Unexpected SQL: {sql}")
        return self

    def _insert(self):
        self.inserts += 1
        if self.inserts == self.fail_insert_at:
            raise RuntimeError("Simulated database write failure")

    def fetchone(self):
        return self.result[0] if self.result else None

    def fetchall(self):
        return self.result

    def commit(self):
        self.commits += 1
        self._backup = None

    def rollback(self):
        self.rollbacks += 1
        self.sessions, self.weights = self._backup
        self._backup = None


def weights():
    return [{"symbol": f"stock{i}.NS", "weightPct": 2} for i in range(1, 51)]


def session(day="2026-11-09", **changes):
    return dict(sessionDate=day, isTradingDay=True, openTime="09:15", closeTime="15:30", **changes)


class SessionTests(unittest.TestCase):
    def test_explicit_hours_holidays_and_exchange_no_inferred_dates(self):
        conn = FakeConnection()
        result = persist_sessions(conn, "SENSEX", [
            session(), dict(sessionDate="2026-11-10", isTradingDay=False, openTime=None, closeTime=None),
        ], "BSE calendar")
        self.assertEqual(result, dict(supplied=2, written=2, preservedManual=0))
        self.assertEqual(len(conn.sessions), 2)
        self.assertEqual(conn.sessions["SENSEX", DAY]["exchange"], "BSE")
        self.assertEqual(conn.sessions["SENSEX", DAY]["source"], "auto:BSE calendar")
        self.assertFalse(conn.sessions["SENSEX", DAY]["weekly"])
        holiday = conn.sessions["SENSEX", date(2026, 11, 10)]
        self.assertFalse(holiday["trading"])
        self.assertIsNone(holiday["opening"])
        self.assertEqual(conn.commits, 1)

    def test_manual_preserved_auto_updated_and_update_has_ownership_predicate(self):
        conn = FakeConnection()
        conn.sessions["NIFTY50", DAY] = {"source": "manual-import", "weekly": True}
        conn.sessions["NIFTY50", date(2026, 11, 10)] = {"source": "auto:old"}
        result = persist_sessions(conn, "NIFTY50", [session(), session("2026-11-10")], "new")
        self.assertEqual(result, dict(supplied=2, written=1, preservedManual=1))
        self.assertTrue(conn.sessions["NIFTY50", DAY]["weekly"])
        update = next(sql for sql, _ in conn.commands if "UPDATE " in sql)
        self.assertIn("Source LIKE 'auto:%'", update)
        self.assertIn("IsWeeklyExpiry=0,IsMonthlyExpiry=0", update)
        self.assertIn("UPDLOCK,HOLDLOCK", conn.commands[0][0])

    def test_all_input_validated_before_any_sql(self):
        invalid = [
            [session(), session()],
            [session(), session("2026-02-30")],
            [dict(session(), isTradingDay=1)],
            [dict(session(), openTime="9:15")],
            [dict(session(), closeTime="24:00")],
            [dict(session(), closeTime="09:15")],
            [dict(session(), openTime=None)],
            [dict(session(), isTradingDay=False)],
            [dict(session(), sessionDate=datetime(2026, 11, 9))],
            [], [session()] * (MAX_SESSIONS + 1),
        ]
        for records in invalid:
            with self.subTest(records=records[:2]):
                conn = FakeConnection()
                with self.assertRaises(ValueError):
                    persist_sessions(conn, "NIFTY50", records, "source")
                self.assertEqual(conn.commands, [])

    def test_invalid_source_and_index(self):
        for source in (None, "", " ", "auto:", "x" * 496, "bad\nsource"):
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    persist_sessions(FakeConnection(), "NIFTY50", [session()], source)
        with self.assertRaises(ValueError):
            persist_sessions(FakeConnection(), "NASDAQ", [session()], "source")

    def test_session_write_failure_rolls_back_all_dates(self):
        conn = FakeConnection()
        conn.fail_insert_at = 2
        with self.assertLogs("options_reference_store", level="ERROR"), \
                self.assertRaisesRegex(RuntimeError, "write failure"):
            persist_sessions(conn, "NIFTY50", [session(), session("2026-11-10")], "source")
        self.assertEqual(conn.sessions, {})
        self.assertEqual((conn.commits, conn.rollbacks), (0, 1))


class WeightTests(unittest.TestCase):
    def persist(self, conn, records=None, **changes):
        arguments = dict(index_code="NIFTY50", effective_date=DAY, weights=weights() if records is None else records,
                         source="NSE weights", today=DAY)
        arguments.update(changes)
        return persist_weights(conn, **arguments)

    def test_full_snapshot_canonical_mapping_and_history_preserved(self):
        conn = FakeConnection()
        old = dict(index="NIFTY50", stock=999, day=date(2026, 10, 1), weight=100, source="auto:NSE weights")
        stale = dict(old, day=DAY)
        conn.weights = [old, stale]
        records = weights()
        records[0]["symbol"] = " STOCK1.BO "
        self.assertEqual(self.persist(conn, records), dict(supplied=50, written=50, preservedManual=0))
        self.assertEqual(len(conn.weights), 51)
        self.assertIn(old, conn.weights)
        self.assertNotIn(stale, conn.weights)
        self.assertEqual(conn.weights[1]["weight"], Decimal(2))
        delete, params = next((sql, p) for sql, p in conn.commands if "DELETE FROM" in sql)
        self.assertIn("EffectiveDate=?", delete)
        self.assertIn("Source=? AND Source LIKE 'auto:%'", delete)
        self.assertEqual(params, ("NIFTY50", DAY, "auto:NSE weights"))

    def test_manual_snapshot_preserved_wholesale(self):
        conn = FakeConnection()
        conn.weights = [dict(index="NIFTY50", stock=1, day=DAY, weight=100, source="manual")]
        before = copy.deepcopy(conn.weights)
        self.assertEqual(self.persist(conn), dict(supplied=50, written=0, preservedManual=1))
        self.assertEqual(conn.weights, before)
        self.assertFalse(any("DELETE" in sql or "INSERT" in sql for sql, _ in conn.commands))

    def test_different_automatic_owner_is_not_deleted(self):
        conn = FakeConnection()
        conn.weights = [dict(index="NIFTY50", stock=1, day=DAY, weight=100, source="auto:other")]
        with self.assertLogs("options_reference_store", level="ERROR"), \
                self.assertRaisesRegex(ValueError, "Another automatic source"):
            self.persist(conn)
        self.assertEqual(len(conn.weights), 1)
        self.assertFalse(any("DELETE" in sql for sql, _ in conn.commands))

    def test_missing_mismatched_and_ambiguous_catalogue_reject_before_writes(self):
        for catalogue in (
            [(i, f"STOCK{i}.NS") for i in range(1, 50)],
            [(i, f"WRONG{i}.NS") for i in range(1, 51)],
            [(i, f"STOCK{i}.NS") for i in range(1, 51)] + [(99, "stock1.BO")],
        ):
            conn = FakeConnection()
            conn.catalogue = catalogue
            with self.assertLogs("options_reference_store", level="ERROR"), \
                    self.assertRaisesRegex(ValueError, "existing IndianStocks|Ambiguous IndianStocks"):
                self.persist(conn)
            self.assertFalse(any("DELETE" in sql or "INSERT" in sql for sql, _ in conn.commands))
            self.assertEqual((conn.commits, conn.rollbacks), (0, 1))

    def test_invalid_weights_are_rejected_before_sql(self):
        variants = [[], weights()[:-1]]
        for value in (0, -1, 101, float("nan"), float("inf"), True, "2", 2.0000001, 1.9):
            records = weights()
            records[0]["weightPct"] = value
            variants.append(records)
        duplicate = weights()
        duplicate[-1]["symbol"] = "STOCK1.BO"
        variants.append(duplicate)
        for records in variants:
            with self.subTest(records=records[:1]):
                conn = FakeConnection()
                with self.assertRaises(ValueError):
                    self.persist(conn, records)
                self.assertEqual(conn.commands, [])

    def test_date_and_index_requirements(self):
        for changes in (dict(effective_date="2026-11-10"), dict(effective_date="20261109"),
                        dict(effective_date=None), dict(effective_date="2026-02-30"),
                        dict(effective_date=datetime(2026, 11, 9)), dict(index_code="SENSEX")):
            conn = FakeConnection()
            with self.assertRaises(ValueError):
                self.persist(conn, **changes)
            self.assertEqual(conn.commands, [])
        self.assertEqual(self.persist(FakeConnection(), effective_date="2026-11-09")["written"], 50)

    def test_explicit_authoritative_count_and_total_tolerance(self):
        records = [dict(symbol="STOCK1", weightPct=50), dict(symbol="STOCK2", weightPct=Decimal("49.99"))]
        self.assertEqual(self.persist(FakeConnection(), records, authoritative_count=2)["written"], 2)
        records[1]["weightPct"] = Decimal("49.989999")
        with self.assertRaisesRegex(ValueError, "total 100"):
            self.persist(FakeConnection(), records, authoritative_count=2)
        for count in (True, 0, 1001, 2.0):
            with self.assertRaises(ValueError):
                self.persist(FakeConnection(), records, authoritative_count=count)

    def test_six_decimal_precision_is_preserved(self):
        records = weights()
        records[0]["weightPct"] = Decimal("2.000001")
        records[1]["weightPct"] = Decimal("1.999999")
        conn = FakeConnection()
        self.persist(conn, records)
        self.assertEqual(conn.weights[0]["weight"], Decimal("2.000001"))
        self.assertEqual(conn.weights[1]["weight"], Decimal("1.999999"))

    def test_write_failure_restores_deleted_snapshot(self):
        conn = FakeConnection()
        conn.weights = [dict(index="NIFTY50", stock=999, day=DAY, weight=100, source="auto:NSE weights")]
        before = copy.deepcopy(conn.weights)
        conn.fail_insert_at = 3
        with self.assertLogs("options_reference_store", level="ERROR"), \
                self.assertRaisesRegex(RuntimeError, "write failure"):
            self.persist(conn)
        self.assertEqual(conn.weights, before)
        self.assertEqual((conn.commits, conn.rollbacks), (0, 1))

    def test_autocommit_connections_rejected(self):
        conn = FakeConnection()
        conn.autocommit = True
        with self.assertRaisesRegex(ValueError, "autocommit disabled"):
            self.persist(conn)
        with self.assertRaisesRegex(ValueError, "autocommit disabled"):
            persist_sessions(conn, "NIFTY50", [session()], "source")
        self.assertEqual(conn.commands, [])


if __name__ == "__main__":
    unittest.main()
