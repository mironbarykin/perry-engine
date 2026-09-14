import json
from typing import Any

from pydantic import BaseModel, Field

from app.integrations.basketball_parser import (
    list_leagues as parser_list_leagues,
)
from app.integrations.basketball_parser import (
    query_games as parser_query_games,
)
from app.integrations.basketball_parser import (
    sync_leagues_to_db,
)
from app.tools.context import ToolContext


class QueryGamesParams(BaseModel):
    league_query: str = Field(
        description="League short name (e.g. '1RLH', '2RL1H'), number (e.g. '220'), ID ('54569'), or name ('Oberliga Herren')."
    )
    team_name: str | None = Field(
        default=None, description="Optional team name to filter games (home or away)."
    )
    date: str | None = Field(
        default=None, description="Optional exact date to filter games (YYYY-MM-DD)."
    )
    date_from: str | None = Field(
        default=None, description="Optional start of date range (YYYY-MM-DD)."
    )
    date_to: str | None = Field(
        default=None, description="Optional end of date range (YYYY-MM-DD)."
    )
    referee_name: str | None = Field(
        default=None, description="Optional referee or referee observer name to filter."
    )
    force_refresh: bool = Field(
        default=False, description="Set True to force refetching schedule from basketball-bund.net."
    )
    limit: int = Field(
        default=25, ge=1, le=100, description="Maximum number of games to return."
    )


class ListLeaguesParams(BaseModel):
    verband_id: int | None = Field(
        default=None, description="Optional basketball federation ID (11 = WBV Nordrhein-Westfalen, 100 = Bundesligen DBB). If omitted, lists leagues across all federations."
    )
    search: str | None = Field(
        default=None, description="Optional search term for league name, short name (e.g. BBL, ProA, ProB, NBBL, JBBL, 1RLH), or number."
    )
    altersklasse: str | None = Field(
        default=None, description="Optional age group filter (e.g. 'Senioren', 'U18', 'U16')."
    )
    geschlecht: str | None = Field(
        default=None, description="Optional gender filter ('männlich', 'weiblich', 'mix')."
    )
    limit: int = Field(
        default=50, ge=1, le=100, description="Maximum number of leagues to return."
    )


class SyncLeaguesParams(BaseModel):
    verband_id: int = Field(
        default=11, description="Basketball federation ID to refresh (11 = WBV Nordrhein-Westfalen, 100 = Bundesligen DBB)."
    )


def _schema(name: str, description: str, params: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": params.model_json_schema(),
        },
    }


def basketball_query_games(
    context: ToolContext,
    league_query: str,
    team_name: str | None = None,
    date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    referee_name: str | None = None,
    force_refresh: bool = False,
    limit: int = 25,
) -> str:
    res = parser_query_games(
        league_query=league_query,
        team_name=team_name,
        date=date,
        date_from=date_from,
        date_to=date_to,
        referee_name=referee_name,
        force_refresh=force_refresh,
        limit=limit,
    )
    return json.dumps(res, ensure_ascii=False)


def basketball_list_leagues(
    context: ToolContext,
    verband_id: int | None = None,
    search: str | None = None,
    altersklasse: str | None = None,
    geschlecht: str | None = None,
    limit: int = 50,
) -> str:
    res = parser_list_leagues(
        verband_id=verband_id,
        search=search,
        altersklasse=altersklasse,
        geschlecht=geschlecht,
        limit=limit,
    )
    return json.dumps({"verband_id": verband_id, "count": len(res), "leagues": res}, ensure_ascii=False)


def basketball_sync_leagues(
    context: ToolContext,
    verband_id: int = 11,
) -> str:
    count = sync_leagues_to_db(verband_id=verband_id)
    return json.dumps(
        {
            "status": "success",
            "message": f"Successfully synced {count} leagues for Verband {verband_id}.",
            "count": count,
        },
        ensure_ascii=False,
    )


QUERY_GAMES_SCHEMA = _schema(
    "basketball_query_games",
    "Query basketball games filtered by league, team name, date, date range, or referee from basketball-bund.net.",
    QueryGamesParams,
)
LIST_LEAGUES_SCHEMA = _schema(
    "basketball_list_leagues",
    "List or search available basketball leagues in the database (e.g. by name, age group, or short name like 1RLH).",
    ListLeaguesParams,
)
SYNC_LEAGUES_SCHEMA = _schema(
    "basketball_sync_leagues",
    "Force refresh the basketball league directory from basketball-bund.net into SQLite.",
    SyncLeaguesParams,
)

TOOLS = [
    (QUERY_GAMES_SCHEMA, QueryGamesParams, basketball_query_games),
    (LIST_LEAGUES_SCHEMA, ListLeaguesParams, basketball_list_leagues),
    (SYNC_LEAGUES_SCHEMA, SyncLeaguesParams, basketball_sync_leagues),
]