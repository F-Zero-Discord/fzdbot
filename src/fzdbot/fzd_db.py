from typing import Literal
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import aiomysql

from fzdbot.settings import get_settings
from fzdbot.utils.user_utils import default_display_name

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


async def get_user_id(db, discord_id: str):
    """Given a discord user id (discord_id), returns the database
    id of that user
    """
    sql_getuser = "SELECT id from users WHERE discord_user_id = %s"
    user = await execute_query(db, sql_getuser, params=(discord_id,), fetch="one")
    if user:
        return user["id"]
    else:
        return None


async def add_new_user(db, discord_username, display_name=None) -> None:
    """Adds new user to the database"""

    # Assuming "discord_display_name" isn't required
    sql_newuser = "INSERT INTO users (tag, discord_user_id) VALUES (%s, %s);"

    if display_name is None:  # Defaults to user's discord display name
        display_name = default_display_name(discord_username)
    await execute_query(db, sql_newuser, params=(display_name, discord_username.name), fetch=None)


async def modify_user_display_name(db, db_user_id, display_name) -> None:
    """Modifies an existing user's display name in the database"""
    sql_modifyuser = "UPDATE users SET tag = %s WHERE id = %s;"
    await execute_query(db, sql_modifyuser, params=(display_name, db_user_id), fetch=None)


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


async def submit_score(db, dataentry) -> int:
    """Executes sql query command to insert data to database
    db = database connection object
    dataentry = [ user_id, scheduled_event_id, score ] - all integers
    """
    sql_newrow = "sp_insert_score"
    dataentry.append("NULL")  # fifth argument is what will be returned...handled by "fetch" in python
    score = await execute_query(db, sql_newrow, params=dataentry, fetch="one", isProc=True)
    return int(score["@sp_output_score"])


async def edit_score(db, dataentry) -> None:
    """Executes sql query command to insert data to database
    db = database connection object
    dataentry = [ newscore, id ] for modifying score,
                all integer values
    """
    sql_updaterow = "UPDATE event_result_points SET score = %s WHERE id = %s;"
    await execute_query(db, sql_updaterow, params=dataentry, fetch=None)


async def delete_score(db, dataentry) -> None:
    """Executes sql query command to insert data to database
    db = database connection object
    dataentry = [ id ] for deleting score
                all integer values
    """
    sql_deleterow = "DELETE FROM event_result_points WHERE id = %s;"
    await execute_query(db, sql_deleterow, params=dataentry, fetch=None)


async def get_user_scores(db, user_name, check_for_score_method=False) -> list[dict[str, str]]:
    """Query the database for scores of active event of a given user
    Returns all values as strings for autocomplete bot feature
    """
    active_event = await check_for_active_event(db)
    if active_event["name"] == "NULL":
        return [{"score": "NO CURRENT EVENT", "id": "-999"}]
    elif check_for_score_method and active_event["scoring_method"] == "placement":
        return [{"score": "DISABLED FOR THIS EVENT", "id": "-888"}]
    db_user_id = await get_user_id(db, user_name)

    sql_getscores = """SELECT CAST(score AS CHAR) AS score, 
                              CAST(id AS CHAR) AS id
                       FROM event_result_points 
                       WHERE user_id = %s AND scheduled_event_id = %s 
                       ORDER BY id ASC;"""
    scoresdict = await execute_query(db, sql_getscores, params=(db_user_id, active_event["id"]), fetch="all")

    if not scoresdict:
        return [{"score": "NO USER SCORES FOUND", "id": "-999"}]

    return scoresdict


async def get_event_info_by_scheduled_event_id(db, scheduled_event_id: int, division_id: int = None, team_id: int = None):
    """Get all info pertaining to a specific event"""

    sql_getevent = """
        SELECT 
            es.id, 
            e.name, 
            es.utc_start_dt, 
            es.utc_end_dt, 
            es.event_id,
            es.scoring_method,
            es.num_mulligans,
            COALESCE(COUNT(el.id), 0) as num_lineups,
            GROUP_CONCAT(d.name ORDER BY d.name SEPARATOR ', ') AS all_divisions,
            GROUP_CONCAT(t.name ORDER BY t.name SEPARATOR ', ') AS all_teams
        FROM events_scheduled es
        JOIN events e ON e.id = es.event_id
        LEFT JOIN event_lineups el ON el.scheduled_event_id = es.id
        LEFT JOIN divisions d ON d.scheduled_event_id = es.id
        LEFT JOIN teams t ON t.scheduled_event_id = es.id
        WHERE es.id = %s
    """
    params = [scheduled_event_id]

    if division_id is not None:
        sql_getevent += " AND d.id = %s"
        params.append(division_id)

    if team_id is not None:
        sql_getevent += " AND t.id = %s"
        params.append(team_id)

    selectedEvent = await execute_query(db, sql_getevent, params=params, fetch="one")

    return selectedEvent


async def get_latest_event(db, event_id=None):
    """Get most recent event, return a dict containing the unique id,
    name of event, and start date of the event
    OPTIONAL: event_id to find latest of a specific event
              event)subid if an event_id has multiple sub0events (i.e. GGP)
              NOTE only ONE is allowed to be set when called!
    """

    sql_getevent = """SELECT es.id, e.name, es.utc_start_dt, es.utc_end_dt, es.event_id
                    FROM events_scheduled es
                    JOIN events e ON e.id = es.event_id
                    WHERE utc_start_dt =
                        (SELECT MAX(utc_start_dt) FROM events_scheduled 
                         WHERE utc_start_dt < UTC_TIMESTAMP())"""
    params = None
    if event_id is not None:
        sql_getevent = sql_getevent.replace("())", "() AND event_id = %s)")
        params = (event_id,)

    selectedevent = await execute_query(db, sql_getevent, params=params, fetch="one")

    return selectedevent

async def get_event_scoreboard(db, scheduled_event_id: int, division_id: int = None, team_id: int = None):
    """Query the FZD database for all scores of a given event,
    defined by scheduled_event_id. Can optionally filter results by division_id or team_id.

    Returns event results grouped by player: player	| team | division |	num_submissions	| score	| user_emote | team_emote | division_emote | qualifier_emote
    Emotes may be reworked and handled in a separate logic layer in the future.
    """
    sql_getscoreboard = "sp_show_scoreboard_NEW" ##@TODO: MOVE SQL INTO HERE DIRECTLY

    allscores = await execute_query(db, sql_getscoreboard, params=(scheduled_event_id, division_id, team_id), isProc=True)
    return allscores

async def get_event_scoreboard_old(db, db_user_id: int, event_type=None):
    """Query the FZD database for all scores of a given event,
    defined by scheduled_event_id.

    Returns an ordered list of dicts with 'player': str and 'score': Decimal
    as well as the eventinfo (from get_latest_event function)
    """
    sql_getscoreboard = "sp_show_scoreboard"

    if event_type is None:
        eventinfo = await get_latest_event(db)
    else:
        eventinfo = await get_latest_event(db, event_id=int(event_type))

    # Check there's an event to display
    if not eventinfo:
        return None, None

    allscores = await execute_query(db, sql_getscoreboard, params=(eventinfo["id"], db_user_id), isProc=True)

    # print(allscores)
    # strip off is_qualified results if we're not viewing a qualifier event
    valid_qual_events = [8, 9, 10, 11, 12, 13]  # MM, Thu FZD, Fri FZD, EAD, CC, APAC
    if eventinfo["event_id"] not in valid_qual_events:
        for d in allscores:
            d.pop("is_qualified", None)

    return eventinfo, allscores


async def get_event_schedule(db):
    """Executes sql process query to get scheduled events in future"""
    sql_events = "SELECT event, utc_start, utc_end FROM vw_list_scheduled_events"
    events = await execute_query(db, sql_events, params=None, fetch="all", isProc=False)
    # print(events)
    # print(type(events))
    return events


async def get_machines(db):
    sql_getmachines = "SELECT CAST(id AS CHAR) AS id, CAST(name AS CHAR) AS name FROM machines;"
    machines_dict = await execute_query(db, sql_getmachines)
    return machines_dict


###################################################################
# Database calls for event registration
###################################################################

async def get_registration_events(db) -> list[dict] | None:
    """ Gets event information from database.
    """
    sql_getregevents = ("""SELECT id AS scheduled_event_id,
                            event_id AS event_id,
                            CAST(display_name AS CHAR) AS event_name,
                            utc_start_dt AS start_time,
                            utc_end_dt AS end_time,
                            CAST(mode AS CHAR) AS mode,
                            CAST(scoring_method AS CHAR) AS scoring,
                            CAST(is_machine_input_required AS SIGNED) AS machine_required
                        FROM events_scheduled 
                        WHERE is_registration_event = 1 
                            AND utc_end_dt > CURRENT_TIMESTAMP;
                        """
                       )
    reg_event_dict = await execute_query(db, sql_getregevents, params=None, fetch="all", isProc=False)
    if reg_event_dict:
        return reg_event_dict
    else:
        return None


async def get_event_description(db, event_id: int) -> str | None:
    """ Gets event information from database.
    """
    sql_getevent = """
                    SELECT description
                    FROM events
                    WHERE id = %s
                    """
    params = (event_id,)
    event_desc = await execute_query(db, sql_getevent, params=params, fetch="one", isProc=False)
    if event_desc:
        return event_desc["description"]
    else:
        return None
    

async def get_registration_period(db, scheduled_event_id: int) -> dict | None:
    """
    """
    sql_get_regperiod = """
                    SELECT id AS reg_period_id,
                            registration_open AS reg_open,
                            registration_close AS reg_close
                    FROM registration_period
                    WHERE scheduled_event_id = %s
                    """
    params = (scheduled_event_id,)
    reginfo = await execute_query(db, sql_get_regperiod, params=params, fetch="one", isProc=False)
    return reginfo


async def get_user_registrations(db, discord_user_id):
    """ Gets event information where user is registered for either a team or division
        associated with an event. Note that this function, unlike 
        get_registration_events, does not get the event capacity.
        Output is:
        'scheduled_event_id': int
        'type': char (enum['division', 'team'])
        'div_team_id': int
    """
    sql_getuserevents = ("""SELECT divisions.scheduled_event_id AS scheduled_event_id,
                            CAST("division" AS CHAR) AS type,
                            user_divisions.division_id AS div_team_id
                            FROM user_divisions
                            INNER JOIN divisions
                            ON user_divisions.division_id = divisions.id AND user_divisions.user_id = %s

                            UNION

                            SELECT teams.scheduled_event_id AS scheduled_event_id,
                                    CAST("team" AS CHAR) AS type,
                                    user_teams.team_id AS div_team_id
                            FROM user_teams
                            INNER JOIN teams
                            ON user_teams.team_id = teams.id AND user_teams.user_id = %s
                         """
                        )
    user_event_dict = await execute_query(
                                        db, 
                                        sql_getuserevents, 
                                        params=(str(discord_user_id), str(discord_user_id),),
                                        fetch="all",
                                        isProc=False
                                        )
    if user_event_dict:
        return user_event_dict
    else:
        return None


async def get_event_divisions(db, event_id) -> list[dict] | None:
    sql_geteventdivisions = ("""SELECT A.id AS id, 
                                CAST(A.name AS CHAR) AS name,
                                CAST(A.alt_name AS CHAR) AS alt_name,
                                A.capacity AS capacity, 
                                (SELECT COUNT(*)
                                    FROM user_divisions
                                    WHERE user_divisions.division_id = A.id) AS num_registered,
                                CAST(A.emote AS CHAR) AS emote
                                FROM divisions A
                                INNER JOIN events_scheduled B
                                    ON B.id = A.scheduled_event_id
                                WHERE A.scheduled_event_id = %s"""
                             )
    divisions_dict = await execute_query(db, sql_geteventdivisions, params=(str(event_id),))
    # Recast numbers as numbers
    if divisions_dict:
        return divisions_dict
    else:
        return None


async def get_event_teams(db, event_id):
    sql_geteventteams = ("""SELECT A.id AS id, 
                         CAST(A.name AS CHAR) AS name,
                         CAST(A.alt_name AS CHAR) AS alt_name,
                         A.capacity AS capacity,
                         (SELECT COUNT(*)
                            FROM user_teams
                            WHERE user_teams.team_id = A.id) AS num_registered,
                         CAST(A.emote AS CHAR) AS emote
                         FROM teams A
                         INNER JOIN events_scheduled B
                         ON B.id = A.scheduled_event_id
                         WHERE A.scheduled_event_id = %s"""
                         )
    teams_dict = await execute_query(db, sql_geteventteams, params=(str(event_id),))
    # Recast numbers as numbers
    if teams_dict:
        return teams_dict
    else:
        return None


async def add_user_to_division(db, dataentry):
    """ Executes sql query command to insert data to database
        db = database connection object
        dataentry = [ user_id, division_id ] - all integers
    """
    sql_newrow="sp_assign_user_to_division"
    await execute_query(db, sql_newrow, params=dataentry, fetch=None, isProc=True)


async def add_user_to_team(db, dataentry):
    """ Executes sql query command to insert data to database
        db = database connection object
        dataentry = [ user_id, team_id ] - all integers
    """
    sql_newrow="sp_assign_user_to_team"
    await execute_query(db, sql_newrow, params=dataentry, fetch=None, isProc=True)


async def add_user_to_division_sql(db, db_user_id: int, division_id: int):
    """ Executes sql query command to insert division row to database, using query, 
        not stored procedure.
    """
    sql_newrow = """ INSERT INTO user_divisions (division_id, user_id)
                        VALUES (%s, %s)
                """
    params = (division_id, db_user_id,)
    await execute_query(db, sql_newrow, params=params, fetch=None, isProc=False)


async def add_user_to_team_sql(db, db_user_id: int, team_id: int):
    """ Executes sql query command to insert team row into database, using query, 
        not stored procedure.
    """
    sql_newrow = """ INSERT INTO user_teams (team_id, user_id)
                        VALUES (%s, %s)
                """
    params = (team_id, db_user_id,)
    await execute_query(db, sql_newrow, params=params, fetch=None, isProc=False)


async def remove_user_from_division(db, db_user_id, event_id):
    """ Executes sql query command to delete data in the user_divisions database table
    """
    sql_remove_from_division = ("""DELETE A
                                FROM user_divisions A
                                INNER JOIN divisions B
                                ON A.division_id = B.id
                                WHERE B.scheduled_event_id = %s AND user_id = %s"""
                                )
    await execute_query(db, sql_remove_from_division, params=(event_id, db_user_id,))


async def remove_user_from_team(db, db_user_id, event_id):
    """ Executes sql query command to delete data in the user_teams database table
        db = database connection object
        dataentry = [ user_id, event_id ] - all integers
    """
    sql_remove_from_team = ("""DELETE A
                            FROM user_teams A
                            INNER JOIN teams B
                            ON A.team_id = B.id
                            WHERE B.scheduled_event_id = %s AND user_id = %s"""
                            )
    await execute_query(db, sql_remove_from_team, params=(event_id, db_user_id,))


async def remove_user_from_division_sql(db, db_user_id: int, division_id: int):
    """ Executes sql query command to insert division row to database, using query, 
        not stored procedure.
    """
    sql_delete_row = """ DELETE FROM user_divisions
                        WHERE division_id = %s AND user_id = %s
                    """
    params = (division_id, db_user_id,)
    await execute_query(db, sql_delete_row, params=params, fetch=None, isProc=False)


async def remove_user_from_team_sql(db, db_user_id: int, team_id: int):
    """ Executes sql query command to insert division row to database, using query, 
        not stored procedure.
    """
    sql_delete_row = """ DELETE FROM user_teams
                        WHERE team_id = %s AND user_id = %s
                    """
    params = (team_id, db_user_id,)
    await execute_query(db, sql_delete_row, params=params, fetch=None, isProc=False)


async def create_update_event(db,
                              id: int | None,
                              name: str,
                              description: str | None,
                              duration: int | None,
                              mode: Literal["99","classic"],
                              scoring: Literal["points","placement"],
                              ) -> int | None:
    """ Adds/Updates entry to/in events table.
        Non-parameter arguments:
        - game_id = 1
    """
    if id:
        # Entry exists -- update
        sql_update_event = """UPDATE events
                                SET
                                    name = IFNULL(%s, name),
                                    description = IFNULL(%s, description),
                                    hour_duration = IFNULL(%s, hour_duration),
                                    mode = %s,
                                    scoring_method = %s
                                WHERE id = %s
                            """
        params = (name, description, duration, mode, scoring, id,)
        await execute_query(db, sql_update_event, params=params, fetch=None, isProc=False)
        return id
    else:
        # Entry does not exist -- create new
        sql_new_event = """INSERT INTO events
                            (game_id, name, description, hour_duration, mode, scoring_method)
                            VALUES 
                            (1, %s, %s, %s, %s, %s);
                            
                        """
        params = (name, description, duration, mode, scoring,)
        await execute_query(db, sql_new_event, params=params, fetch=None, isProc=False)
        sql_fetch_id = "SELECT LAST_INSERT_ID() AS id"
        event_id = await execute_query(db, sql_fetch_id, params=None, fetch="one", isProc=False)
        return event_id["id"]


async def create_update_scheduled_event(db, 
                                        id: int | None,
                                        event_id: int,
                                        name: str,
                                        start_time: datetime,
                                        end_time: datetime,
                                        mode: Literal["99","classic"],
                                        scoring: Literal["points","placement"],
                                        machine_required: bool
                                        ):
    """ Adds/Updates entry to/in events table.
        Non-parameter arguments:
        - is_registration_event = 1
    """
    match machine_required:
        case True:
            machine_required_int = 1
        case False:
            machine_required_int = 0
    if id:
        # Entry exists -- update
        sql_update_scheduled_event = """UPDATE events_scheduled
                                        SET
                                            event_id = CAST(IFNULL(%s, event_id) AS SIGNED),
                                            display_name = IFNULL(%s, display_name),
                                            utc_start_dt = IFNULL(%s, utc_start_dt),
                                            utc_end_dt = IFNULL(%s, utc_end_dt),
                                            mode = CAST(%s AS CHAR),
                                            scoring_method = %s,
                                            is_registration_event = 1,
                                            is_machine_input_required = IFNULL(%s, is_machine_input_required)
                                        WHERE id = %s
                                    """
        params = (event_id, name, 
                  start_time.strftime("%Y-%m-%d %H:%M:%S") if start_time is not None else None, 
                  end_time.strftime("%Y-%m-%d %H:%M:%S") if end_time is not None else None,
                  mode, scoring, 
                  machine_required,
                  id,)
        await execute_query(db, sql_update_scheduled_event, params=params, fetch=None, isProc=False)
        return id
    else:
        # Entry does not exist -- create new
        sql_new_scheduled_event = """INSERT INTO events_scheduled
                                    (event_id, display_name, utc_start_dt, 
                                        utc_end_dt, mode, scoring_method,
                                        is_registration_event, is_machine_input_required
                                        )
                                    VALUES 
                                    (%s, %s, %s, %s, %s, %s, 1, %s)
                                    ON DUPLICATE KEY UPDATE
                                        utc_start_dt = VALUES(utc_start_dt),
                                        utc_end_dt = VALUES(utc_end_dt),
                                        mode = VALUES(mode),
                                        scoring_method = VALUES(scoring_method),
                                        is_registration_event = VALUES(is_registration_event),
                                        is_machine_input_required = VALUES(is_machine_input_required);
                                """
        params = (int(event_id), name, 
                  start_time.strftime("%Y-%m-%d %H:%M:%S") if start_time is not None else None, 
                  end_time.strftime("%Y-%m-%d %H:%M:%S") if end_time is not None else None, 
                  #"2026-09-01 01:00", "2026-09-01 03:00",
                  mode, scoring, machine_required_int,
                  )
        #
        await execute_query(db, sql_new_scheduled_event, 
                            params=params, fetch=None, isProc=False)
        sql_fetch_id = "SELECT LAST_INSERT_ID() AS id"
        scheduled_event_id = await execute_query(db, sql_fetch_id, params=None, fetch="one", isProc=False)
        return scheduled_event_id["id"]


async def create_update_divteam(db, 
                                id: int | None,
                                scheduled_event_id: int, 
                                div_team: Literal["divisions","teams"],
                                name: str,
                                alt_name: str | None,
                                emote: str | None,
                                capacity: int | None,
                                ):
    """ Adds/Updates entry to/in either divisions or teams table,
        depending on input argument div_team.
    """
    if id:
        # Entry exists -- update
        sql_update_divteam = f"""UPDATE {div_team}
                                SET
                                    scheduled_event_id = IFNULL(%s, scheduled_event_id),
                                    name = IFNULL(%s, name),
                                    alt_name = IFNULL(%s, alt_name),
                                    emote = IFNULL(%s, emote),
                                    capacity = IFNULL(%s, capacity)
                                WHERE id = %s
                            """
        params = (scheduled_event_id, name, alt_name, emote, capacity, id,)
        await execute_query(db, sql_update_divteam, params=params, fetch=None, isProc=False)
        return id
    else:
        # Entry does not exist -- create new
        sql_new_divteam = f"""INSERT INTO {div_team}
                            (scheduled_event_id, name, alt_name, emote, capacity)
                            VALUES 
                            (%s, %s, %s, %s, %s)
                        """
        params = (scheduled_event_id, name, alt_name, emote, capacity,)
        await execute_query(db, sql_new_divteam, 
                            params=params, fetch=None, isProc=False)
        sql_fetch_id = "SELECT LAST_INSERT_ID() AS id"
        div_team_id = await execute_query(db, sql_fetch_id, params=None, fetch="one", isProc=False)
        return div_team_id["id"]


async def create_update_registration_period(db,
                                     id: int | None,
                                     scheduled_event_id: int,
                                     reg_open: datetime | None,
                                     reg_close: datetime | None
                                     ):
    """ Enters registration period information into the database.
    """
    if id:
        # Entry exists -- update
        sql_update_reg_period = """UPDATE registration_period
                                    SET
                                        scheduled_event_id = IFNULL(%s, scheduled_event_id),
                                        registration_open = IFNULL(%s, registration_open),
                                        registration_close = IFNULL(%s, registration_close)
                                    WHERE id = %s
                                """
        params = (scheduled_event_id, 
                  reg_open.strftime("%Y-%m-%d %H:%M:%S") if reg_open is not None else None, 
                  reg_close.strftime("%Y-%m-%d %H:%M:%S") if reg_close is not None else None, 
                  id,)
        await execute_query(db, sql_update_reg_period, params=params, fetch=None, isProc=False)
        return id
    else:
        # Entry does not exist -- create new
        sql_new_reg_period = """INSERT INTO registration_period
                                (scheduled_event_id, registration_open, registration_close)
                                VALUES 
                                (%s, %s, %s);
                            """
        params = (scheduled_event_id, 
                  reg_open.strftime("%Y-%m-%d %H:%M:%S") if reg_open is not None else None, 
                  reg_close.strftime("%Y-%m-%d %H:%M:%S") if reg_close is not None else None
                  ,)
        await execute_query(db, sql_new_reg_period, 
                                       params=params, fetch=None, isProc=False)
        sql_fetch_id = "SELECT LAST_INSERT_ID() AS id"
        reg_period_id = await execute_query(db, sql_fetch_id, params=None, fetch="one", isProc=False)
        return reg_period_id["id"]

    
async def get_div_team_number(db, div_team: Literal["division","team"], id: int):
    """ Get the number of participants assigned to a division/team.
    """
    sql_num_div_team = f"""
                        SELECT COUNT(*)
                        FROM user_{div_team}s
                        WHERE user_{div_team}s.{div_team}_id = %s
                        """
    params = (id,)
    count = await execute_query(db, sql_num_div_team, params=params, fetch="one", isProc=False)
    if not count:
        return None
    else:
        return count


async def reg_log_entry(db, db_user_id: int, scheduled_event_id: int, 
                        div_team: Literal["division","team"], 
                        div_team_id: int, action: Literal["join","withdraw"]) -> None:
    """ Creates log entry in database.
    """
    match div_team:
        case "division":
            d_id = div_team_id
            t_id = None
        case "team":
            d_id = None
            t_id = div_team_id
        case _:
            raise ValueError(f"'div_team' must be either 'division' or 'team', not {div_team}")        

    sql_add_log_entry = """ INSERT INTO event_registration_log 
                            (scheduled_event_id, user_id, action, division_id, team_id)
                            VALUES (%s, %s, %s, %s, %s)
                        """
    params = (scheduled_event_id, db_user_id, action, d_id, t_id,)
    await execute_query(db, sql_add_log_entry, params=params, fetch=None, isProc=False)


async def get_stats_99_options_db(db) -> tuple[dict, dict]:
    """ Gets two stats option sets from the database.
    """
    sql_get_ggp_result_options = """ SELECT * FROM stats_ggp_99;"""
    sql_get_recent_majors = """ SELECT * FROM stats_recent_99;"""
    sql_get_self_eval = """ SELECT * FROM stats_self_eval;"""
    ggp_result_options = await execute_query(db, sql_get_ggp_result_options, params=None, fetch="all", isProc=False)
    ggp_recent_options = await execute_query(db, sql_get_recent_majors, params=None, fetch="all", isProc=False)
    self_eval_options = await execute_query(db, sql_get_self_eval, params=None, fetch="all", isProc=False)

    return ggp_result_options, ggp_recent_options, self_eval_options


async def save_user_stats(db, user_stats) -> None:
    """
    """
    sql_save_stats = """ INSERT INTO user_stats (
                            user_id,
                            scheduled_event_id,
                            self_eval_id,
                            most_recent_id)
                        VALUES (%s, %s, %s, %s) AS new_row
                        ON DUPLICATE KEY UPDATE
                            self_eval_id = new_row.self_eval_id,
                            most_recent_id = new_row.most_recent_id
                        """
    params = (user_stats.user_id,
              user_stats.scheduled_event_id,
              user_stats.self_eval_id,
              user_stats.most_recent_id,
              )
    await execute_query(db, sql_save_stats, params=params, fetch=None, isProc=False)


async def get_users_most_recent_stats_by_mode(db, user_id, mode: str) -> dict | None:
    """ Gets the users most recent stats entry, given the mode ('99' or 'classic')
    """
    sql_recent_user_stats = """ WITH RankedStats AS (
                                    SELECT 
                                        us.*,
                                        es.mode,
                                        ROW_NUMBER() OVER (
                                            PARTITION BY us.user_id 
                                            ORDER BY us.created_dt DESC
                                        ) AS rn
                                    FROM user_stats us
                                    INNER JOIN events_scheduled es 
                                        ON us.scheduled_event_id = es.id
                                    WHERE us.user_id = %s 
                                    AND es.mode = %s
                                )
                                SELECT *
                                FROM RankedStats
                                WHERE rn = 1;
                            """
    params = (user_id, mode,)
    user_stats_dict = await execute_query(db, sql_recent_user_stats, params=params, fetch="one", isProc=False)

    return user_stats_dict


async def get_users_most_recent_stats(db, user_id: int) -> dict | None:
    """ Gets the users most recent stats entry.
        (Version that does not consider 99 or classic mode)
    """
    sql_recent_user_stats = """ SELECT *
                                FROM user_stats
                                WHERE user_id = %s 
                                ORDER BY created_dt DESC
                                LIMIT 1;
                            """
    params = (user_id,)
    user_stats_dict = await execute_query(db, sql_recent_user_stats, params=params, fetch="one", isProc=False)

    return user_stats_dict


async def get_user_stats(db, user_id, scheduled_event_id: int):
    """ Get user stats for a user_id and a scheduled_event_id if they exist;
        or if not, get the most recent user's stats; or if not, return None.
        Dictionary keys:
        - id (primary key, unused)
        - user_id (char)
        - scheduled_event_id (int)
        - self_eval_id (int NULL)
        - most_recent_id (int NULL)	
        - created_dt (datetime, unused)
    """
    # Grab user_stats by user_id and scheduled_event_id:
    sql_get_stats_1 =   """ SELECT * 
                            FROM user_stats
                            WHERE user_stats.user_id = %s
                                AND user_stats.scheduled_event_id = %s
                        """
    params = (user_id, scheduled_event_id,)
    user_stats_dict = await execute_query(db, sql_get_stats_1, params=params, fetch="one", isProc=False)

    # # Commented language used for getting user stats by mode
    # if not user_stats_dict:
    #     # Get the mode of scheduled_event_id
    #     sql_get_mode =  """ SELECT mode
    #                         FROM events_scheduled
    #                         WHERE id = %s 
    #                     """
    #     params = (scheduled_event_id,)
    #     mode_dict = await execute_query(db, sql_get_mode, params=params, fetch="one", isProc=False)

    #     # Grab most recent user stats:
    #     user_stats_dict = await get_users_most_recent_stats(db, user_id, mode_dict["mode"])
    
    # Grab most recent user stats:
    user_stats_dict = await get_users_most_recent_stats(db, user_id)

    if not user_stats_dict:
        return None
    else:
        return user_stats_dict


async def remove_user_stats_db(db, scheduled_event_id: int) -> None:
    """ Unclear if this is functionality that is desired, as it may be useful
        to keep the user stats as the last stats they entered (for use with
        `get_users_most_recent_stats` above) even if they withdraw their 
        registration.
    """
    ...

# # Shouldn't be needed. Superceded by get_private_prix_options.
# async def get_prix_options(db) -> list[dict]:
#     """ Gets the full list of prix options from the database.
#     """
#     sql_get_prix_list = """ SELECT id, name
#                             FROM vw_prix_options
#                         """
#     prix_dict = await execute_query(db, sql_get_prix_list, params=None, fetch="all", isProc=False)
#     return prix_dict


async def update_event_race_options(db, scheduled_event_id: int, race_json: str) -> int:
    """ On Duplicate SQL needed to preserve existing primary key and not auto-increment it
    """
    sql_update_race_options = """ INSERT INTO events_scheduled_config
                                        (scheduled_event_id, prix_options)
                                    VALUES (%s, %s)
                                    ON DUPLICATE KEY UPDATE 
                                        prix_options = %s;
                                """
    params = (scheduled_event_id, race_json, race_json,)
    await execute_query(db, sql_update_race_options, params=params, fetch=None, isProc=False)


async def get_private_prix_options(db) -> dict:
    """ Output dictionary format:
        - id (int)
        - name (str)
        - mode (str)
        - tickets (int)
    """
    sql_get_private_prix = """ SELECT
                                    g.id AS db_id,
                                    g.name AS name,
                                    g.mode AS mode,
                                    gp.tickets AS tickets
                                FROM grand_prix g
                                CROSS JOIN game_modes gp ON gp.short_name = 'GP'

                                UNION ALL

                                SELECT 
                                    m.id AS db_id,
                                    m.name AS name,
                                    m.mode AS mode,
                                    m.tickets AS tickets
                                FROM game_modes m
                                WHERE m.short_name IN ('99', 'Pro', 'TB', 'cMP', 'MP');
                            """
    prix_dict = await execute_query(db, sql_get_private_prix, params=None, fetch="all", isProc=False)

    return prix_dict


async def get_public_prix_options(db) -> dict:
    """ Output dictionary format:
        - id (int)
        - name (str)
        - mode (str)
        - tickets (int)
    """
    sql_get_private_prix = """ SELECT
                                    g.id AS db_id,
                                    g.name AS name,
                                    g.mode AS mode,
                                    gp.tickets AS tickets
                                FROM grand_prix g
                                CROSS JOIN game_modes gp ON gp.short_name = 'GP'

                                UNION ALL

                                SELECT 
                                    m.id AS db_id,
                                    m.name AS name,
                                    m.mode AS mode,
                                    m.tickets AS tickets
                                FROM game_modes m
                                WHERE m.short_name IN ('cMP', 'MP', 'WT', 'mWT');
                            """
    prix_dict = await execute_query(db, sql_get_private_prix, params=None, fetch="all", isProc=False)

    return prix_dict


async def get_race_config_db(db, scheduled_event_id):
    """
    """
    sql_get_race_config = """   SELECT DISTINCT
                                    jt.race_id AS db_id,
                                    COALESCE(gm.name, gp.name) AS name
                                FROM events_scheduled_config esc
                                -- 1. Unpack all db_id values from the JSON array
                                CROSS JOIN JSON_TABLE(
                                    esc.prix_options,
                                    '$[*]' COLUMNS (
                                        race_id INT PATH '$.db_id'
                                    )
                                ) AS jt
                                -- 2. Try to match against game_mode
                                LEFT JOIN game_modes gm 
                                    ON gm.id = jt.race_id
                                -- 3. Try to match against grand_prix
                                LEFT JOIN grand_prix gp 
                                    ON gp.id = jt.race_id
                                WHERE esc.scheduled_event_id = %s -- Replace with your event filter
                                -- 4. Filter out any IDs that didn't match either table
                                AND (gm.id IS NOT NULL OR gp.id IS NOT NULL);
                            """
    params = (scheduled_event_id,)
    race_dict = await execute_query(db, sql_get_race_config, params=params, fetch="all", isProc=False)
    return race_dict


async def update_event_machines(db, 
        config_id: int, scheduled_event_id: int, machine_json: str) -> int:
    """ On Duplicate SQL needed to preserve existing primary key and not auto-increment it
    """
    sql_update_machines = """INSERT INTO events_scheduled_config
                            (id, scheduled_event_id, machine_options)
                            VALUES (%s, %s, %s)
                            ON DUPLICATE KEY UPDATE 
                            machine_options = %s;
                        """
    params = (config_id, scheduled_event_id, machine_json, machine_json,)
    await execute_query(db, sql_update_machines, params=params, fetch=None, isProc=False)

    sql_fetch_id = "SELECT LAST_INSERT_ID() AS id"
    config_db_id = await execute_query(db, sql_fetch_id, params=None, fetch="one", isProc=False)

    return config_db_id


async def get_machine_config_db(db, scheduled_event_id: int) -> tuple[int, dict]:
    """ Get config_id and json string of machine data from database.
    """
    sql_get_config_id = """ SELECT id AS config_id
                            FROM events_scheduled_config
                            WHERE scheduled_event_id = %s
                        """
    params = (scheduled_event_id,)
    config_dict = await execute_query(db, sql_get_config_id, params=params, fetch="one", isProc=False)

    sql_grab_machines = """ SELECT DISTINCT
                                m.id,
                                m.name
                            FROM events_scheduled_config esc
                            -- Extract 'db_id' from each object in the array
                            CROSS JOIN JSON_TABLE(
                                esc.machine_options,
                                '$[*]' COLUMNS (
                                    machine_id INT PATH '$.db_id'
                                )
                            ) AS jt
                            JOIN machines m 
                                ON m.id = jt.machine_id
                            WHERE esc.scheduled_event_id = %s;
                        """
    params = (scheduled_event_id,)
    m_dict = await execute_query(db, sql_grab_machines, params=params, fetch="all", isProc=False)

    if config_dict:
        return config_dict["config_id"], m_dict
    else:
        return None, m_dict