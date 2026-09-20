import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import aiomysql

from fzdbot.settings import get_settings

logger = logging.getLogger(__name__)

_connection_pool = None


async def _safe_rollback(conn, source: str = "unknown") -> None:
    """Rollback only when possible; never mask the original exception."""
    if not conn or getattr(conn, "closed", True):
        return
    try:
        await conn.rollback()
    except aiomysql.Error as rollback_error:
        logger.warning(f"[DB] Rollback skipped ({source}): {rollback_error}")

async def init_db_pool():
    global _connection_pool
    settings = get_settings()
    POOL_SIZE = 16
    if _connection_pool is None:
        _connection_pool = await aiomysql.create_pool(minsize=1, maxsize=POOL_SIZE, **settings.db_config)
        logger.info("Database pool created")
    return _connection_pool

async def get_connection_from_pool():
    """
    Context manager that safely checks out a connection from the pool,
    and returns it afterward (even if errors happen).
    Automatically rebuilds the pool if it breaks.
    """
    global _connection_pool
    conn = None
    if _connection_pool is None:
        raise RuntimeError("Database pool is not initialized")

    try:
        conn = await _connection_pool.acquire()
        await conn.ping(reconnect=True)
        logger.debug("[DB] Got connection from pool: id=%s", id(conn))
    except Exception as e:
        logger.warning(f"[DB CONNECTION] Failed to get healthy pooled connection: {e}")
        if conn:
            conn.close()
            _connection_pool.release(conn)
        raise
    return conn

@asynccontextmanager
async def get_db_connection():
    """
    Context manager for safely acquiring and releasing a DB connection.
    Rolls back on error when possible and always releases the connection.
    """
    conn = None
    try:
        conn = await get_connection_from_pool()
        # Test connection quickly (cheap ping)
        # conn.ping(reconnect=True, attempts=1, delay=0)

        yield conn  # hand off to the calling code

    except aiomysql.Error as e:
        await _safe_rollback(conn, source="get_db_connection")
        logger.error("[DB ERROR] %s", e)
        raise  # propagate error up to cog

    finally:
        if conn:
            _connection_pool.release(conn)  # release_connection(conn)

async def execute_query(conn, query, params=None, fetch="all", isProc: bool = False):
    """
    Safely executes an SQL query with rollback on error.
    :conn: DB connection object
    :query: SQL query string
    :params: Optional tuple/list of parameters
    :fetch: "all", "one", or None (for INSERT/UPDATE/DELETE)
    :return: Query result or None if no result
    """
    async with conn.cursor(aiomysql.DictCursor) as cursor:
        try:
            if isProc:
                await cursor.callproc(query, params or ())
            else:
                await cursor.execute(query, params or ())

            if fetch == "all":
                result = await cursor.fetchall()
            elif fetch == "one":
                result = await cursor.fetchone()
            else:
                result = None

            await conn.commit()
            return result

        except Exception as e:
            await _safe_rollback(conn, source="execute_query")
            logger.error(f"[DB QUERY ERROR]: {e}\nQuery: {query}\nParams: {params}")
            raise

async def get_event_types(db):
    """Get event types and ids of recurring events from 'events' table"""
    sql_gettypes = "SELECT id, name FROM events"  # WHERE recurring = 1"
    eventtypes = await execute_query(db, sql_gettypes, fetch="all")

    return eventtypes  # [{'id': 7, 'name': 'Weekly Classic Mini'} . . .

async def create_event(db, event, duration: int = 2) -> None:
    """Inserts new event into the 'events_scheduled' database
    duration is optional variable to set how long the event window is (2 hours by default)
    """
    now = datetime.now(timezone.utc)  # now.strftime('%Y-%m-%d %H:%M:%S')
    endtime = now + timedelta(hours=duration)
    tformat = "%Y-%m-%d %H:%M:%S"
    sql_addevent = "INSERT INTO events_scheduled (event_id, utc_start_dt, utc_end_dt) VALUES (%s, %s, %s);"
    await execute_query(
        db, sql_addevent, params=(event["id"], now.strftime(tformat), endtime.strftime(tformat)), fetch=None
    )

async def check_for_active_event(db, hours_from_now: int = 0):
    """Checks database event times start and end times to see if
    event is active right now, returns dict with name and id
    """
    active_event = {"name": "NULL", "id": 0, "is_machine_input_required": False}  # Assume no match
    sql_getevent = """SELECT es.id, e.name, es.utc_start_dt, es.utc_end_dt, es.scoring_method, es.is_machine_input_required
                    FROM events_scheduled es
                    JOIN events e ON e.id = es.event_id
                    WHERE DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s HOUR) 
                          BETWEEN utc_start_dt AND utc_end_dt;"""
    eventmatch = await execute_query(db, sql_getevent, params=(hours_from_now,), fetch="one")
    if eventmatch:
        active_event["name"] = eventmatch["name"]
        active_event["id"] = eventmatch["id"]
        active_event["scoring_method"] = eventmatch["scoring_method"]
        raw_machine_flag = eventmatch.get("is_machine_input_required")
        if isinstance(raw_machine_flag, (bytes, bytearray)):
            active_event["is_machine_input_required"] = int.from_bytes(raw_machine_flag, byteorder="big") == 1
        else:
            active_event["is_machine_input_required"] = bool(raw_machine_flag)

    return active_event

async def get_event_schedule(db):
    """Executes sql process query to get scheduled events in future"""
    sql_events = "SELECT event, utc_start, utc_end FROM vw_list_scheduled_events"
    events = await execute_query(db, sql_events, params=None, fetch="all", isProc=False)
    # print(events)
    # print(type(events))
    return events
