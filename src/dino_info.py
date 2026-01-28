from datetime import datetime, timedelta
from typing import Dict, Optional
from dataclasses import dataclass, field
import asyncio
import os

from dotenv import load_dotenv
from discord import Interaction, app_commands
from supabase import Client as SupabaseClient

load_dotenv("../.env")


@dataclass
class CacheItem:
    dinos: list[dict]
    created_at: datetime = field(default_factory=datetime.now)

    def is_fresh(self) -> bool:
        return datetime.now() <= self.created_at + timedelta(seconds=max_cache_age)


class DatabaseClient(SupabaseClient):
    def __init__(self, url: str, key: str):
        super().__init__(url, key)


user_cache: Dict[int, CacheItem] = {}
db: DatabaseClient

# How long till the item expires, IN SECONDS!!!
max_cache_age = 60


async def initialize(url: str, key: str) -> None:
    """
    Run this before using the module!!!
    :param url: The database url
    :param key: The key to the database
    :return: Nothing
    """
    global db

    db = DatabaseClient(url, key)
    asyncio.create_task(check_fridge())


async def check_fridge() -> None:
    """
    Checks if any items in the cache are spoiled

    :return: Nothing
    """
    while True:
        spoiled_items: list = []
        for key in user_cache:
            if user_cache[key].is_fresh():
                continue

            # If spoiled, add it to the list for disposal
            spoiled_items.append(key)

        # Cleaning up
        for spoiled_item in spoiled_items:
            del user_cache[spoiled_item]

        await asyncio.sleep(10)


def get_users_dinos(discord_id: int) -> Optional[list[dict]]:
    """
    Gets a list of all dinos logged by the user
    :param discord_id: The id of the user
    :return: The list of dinos, if possible
    """
    # If we have the user cached, make sure that it's not expired and use the cached data
    if discord_id in user_cache:
        cache_item: CacheItem = user_cache[discord_id]
        if cache_item.is_fresh():
            return cache_item.dinos

    try:
        # If we don't, then we need to get the data from the DB
        response = db.table('logged_dinos').select("*").eq("discord_id", discord_id).execute()
        dinos = response.data

        # Make sure to save the data we snagged lol
        user_cache[discord_id] = CacheItem(dinos=dinos)
        return dinos

    except Exception as e:
        print(e)
        return None


def get_dino(user_id, dino_id) -> Optional[dict]:
    """
    Returns a specific dino logged by the user
    :param user_id: The Discord ID of the user
    :param dino_id: The id assigned to the dinosaur from the database
    :return: The dino, if possible.
    """
    dinos = get_users_dinos(user_id)

    # If the dino exists, return it
    for dino in dinos:
        if dino["id"] == dino_id:
            return dino

    return None


async def dino_autocomplete(interaction: Interaction, current: str) -> list[app_commands.Choice]:
    """
    An autocomplete for a discord function that shows the dinos logged by the user
    :param interaction: The interaction from the command
    :param current: The current typed in text
    :return: A list of dinos, filtered to match the current search.
    """
    dinos = get_users_dinos(interaction.user.id)
    filtered = [
        d for d in dinos
        if d["name"].startswith(current)
    ]
    return [
        app_commands.Choice(
            name=d["name"],
            value=str(d["id"])
        )
        for d in filtered[:25]
    ]
