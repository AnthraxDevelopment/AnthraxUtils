import datetime
import json
import os

from typing import Literal

import discord
from discord import Client, Intents, app_commands, Interaction, Embed, Message, Member
from discord.app_commands import Command
from dotenv import load_dotenv
import asyncio
from rich.console import Console
import requests

from db_stuff import DBClient
from ui_stuff import StickyModal, AddShutdownView
import rcon_stuff

load_dotenv()
console = Console()

CACHE_REFRESH_INTERVAL = 300  # seconds


class AnthraxUtilsClient(Client):
    def __init__(self):
        intents = Intents.default()
        intents.message_content = True
        intents.members = True  # This is the key line
        super().__init__(intents=intents)

        self.lifespans = {}
        self.load_configs()

        self.sticky_locks = {}

        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        await self.tree.sync(guild=discord.Object(id=1374722200053088306))
        # await self.tree.sync()
        console.print("Commands synced globally", style="green")

    def load_configs(self):
        with open("config/lifespans.json", "r") as f:
            self.lifespans = json.load(f)
        console.print(f"Loaded [yellow i]{len(self.lifespans)}[/] lifespan entries.", style="green")

    async def refresh_cache(self):
        pass


client = AnthraxUtilsClient()
db_client = DBClient(console, CACHE_REFRESH_INTERVAL)


@client.event
async def on_ready():
    console.print(f"Logged in as [green]{client.user.name}[/green]", justify="center")
    console.print("Starting cache refresh thread.")
    await db_client.start_cache_refresh()

    # Validate sticky messages on startup
    console.print("Validating sticky messages...")
    stale_stickies = []

    for sticky in db_client.stickied_messages:
        channel = client.get_channel(sticky["channel_id"])

        if channel is None:
            console.print(
                f"[yellow]Channel {sticky['channel_id']} not found. Marking sticky {sticky['message_id']} for removal.[/yellow]"
            )
            stale_stickies.append(sticky["message_id"])
            continue

        try:
            await channel.fetch_message(sticky["message_id"])
            console.print(f"[green]✓[/green] Sticky message {sticky['message_id']} in channel {channel.name} is valid")
        except discord.errors.NotFound:
            console.print(
                f"[yellow]Sticky message {sticky['message_id']} not found in channel {channel.name}. Marking for removal.[/yellow]"
            )
            stale_stickies.append(sticky["message_id"])
        except Exception as e:
            console.print(
                f"[red]Error validating sticky message {sticky['message_id']}: {e}[/red]"
            )

    # Clean up stale stickies from database
    if stale_stickies:
        console.print(f"[yellow]Removing {len(stale_stickies)} stale sticky messages from database...[/yellow]")
        for message_id in stale_stickies:
            db_client.delete_sticky_message(message_id)
        db_client.refresh_cache()
        console.print(f"[green]✓[/green] Cleaned up stale sticky messages")
    else:
        console.print("[green]✓ All sticky messages are valid![/green]")


@client.event
async def on_message(message: Message):
    if message.author.id == client.user.id:
        return

    if message.channel.id not in db_client.listened_channels:
        return

    if message.channel.id not in client.sticky_locks:
        client.sticky_locks[message.channel.id] = asyncio.Lock()

    async with client.sticky_locks[message.channel.id]:
        for sticky in db_client.stickied_messages:
            if sticky["channel_id"] == message.channel.id:
                try:
                    old_message = await message.channel.fetch_message(sticky["message_id"])
                    old_id = old_message.id
                    await old_message.delete()

                    new_message = await message.channel.send(sticky["content"] + "\n-# This is a sticky message.")

                    db_client.refresh_sticky_message(old_id, new_message.id)
                    db_client.refresh_cache()
                except discord.errors.NotFound:
                    console.print(
                        f"[yellow]Sticky message {sticky['message_id']} not found in channel {message.channel.id}. Creating new one.[/yellow]"
                    )
                    new_message = await message.channel.send(sticky["content"] + "\n-# This is a sticky message.")
                    db_client.refresh_sticky_message(sticky["message_id"], new_message.id)
                    db_client.refresh_cache()
                except Exception as e:
                    console.print(
                        f"[red]Error handling sticky message {sticky['message_id']} in channel {message.channel.id}: {e}[/red]"
                    )
                break


@client.tree.command(name="refresh-cache", description="Refreshes cache of DB")
async def refresh_cache_command(interaction: Interaction):
    if not (interaction.user.guild_permissions.administrator or interaction.user.id == 767047725333086209):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return

    db_client.refresh_cache()

    embed = Embed(title="Cache Refreshed", color=discord.Color.green())
    embed.add_field(name="Sticky Messages", value=len(db_client.stickied_messages), inline=False)
    embed.add_field(name="Channels", value=len(db_client.listened_channels), inline=False)
    embed.add_field(name="Shutdowns", value=len(db_client.shutdowns), inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------
# --- Age Calculator + Shutdown Stuff ---
# ---------------------------------------
# TODO: Add back species to the description and function declaration once elder stuff is done
@client.tree.command(name="calculate-age", description="Calculate the age of your dino, using their birthdate.")
@app_commands.describe(day="The day the dinosaur was born", month="The month the dinosaur was born",
                       year="The year the dinosaur was born")
async def calculate_age(interaction: Interaction, day: int, month: int, year: int):
    try:
        print(f"Calculating age for {interaction.user.display_name}...")

        birth_date = datetime.datetime.fromisoformat(f"{year:02d}-{month:02d}-{day:02d}")
        raw_difference = (datetime.date.today() - birth_date.date()).days

        # Check if birthdate is in the future
        if raw_difference < 0:
            await interaction.response.send_message("Birth date cannot be in the future!", ephemeral=True)
            return

        shutdown_offset = db_client.calculate_shutdown_offset(birth_date.date())
        adjusted_difference = raw_difference - shutdown_offset
        age_in_weeks = adjusted_difference // 7

        # Section for checking what season the dino was born in.
        birth_season_key = None
        seasons = {
            "spring": ":cherry_blossom:",
            "summer": ":sun:",
            "autumn": ":maple_leaf:",
            "fall": ":maple_leaf:",
            "winter": ":snowflake:",
        }

        # Snagging the 20 messages
        try:
            closest_distance = None
            search_date = birth_date = datetime.datetime.fromisoformat(f"{year:02d}-{month:02d}-{day + 1:02d}")
            async for message in client.get_guild(1374722200053088306).get_channel(1383845771232678071).history(
                    limit=20, around=search_date):
                # Only look at messages before the birthdate
                if message.created_at.date() > search_date.date():
                    continue

                for key in seasons.keys():
                    # They put "hot springs" in the gowanda activity lol, now we just check the first importaint part
                    if key in message.content.lower().split("gondwa")[0]:
                        distance = (birth_date.date() - message.created_at.date()).days
                        print(f"{message.created_at.date()}: {key} | dist: {distance}")

                        if closest_distance is None or distance < closest_distance:
                            closest_distance = distance
                            birth_season_key = key

                        break


        # To catch discord errors (Things like impossible dates, etc.) Will just default to "Unknown" if error occurs.
        except Exception as e:
            print(f"Error fetching birth season messages: {e}")

        embed = Embed(
            title=f"Dinosaur's Age",
            description=f"""\
Age in Weeks: `{age_in_weeks}` week(s)
Age in in-game years: `{age_in_weeks // 4}` year(s)
Birth Season: `{birth_season_key.title() if birth_season_key else "Unknown"}` {seasons.get(birth_season_key, "")}
Shutdown Offset Applied `{shutdown_offset}` days(s)
""",
            color=discord.Color.greyple(),
        )
        embed.add_field(
            name="Today's Date",
            value=datetime.date.today().strftime("%d-%m-%Y"),
            inline=True,
        )
        embed.add_field(
            name="Birthdate",
            value=f"{birth_date.strftime("%d-%m-%Y")}",
            inline=True
        )

        embed.set_footer(text="Each in-game year is 4 weeks long.")

        await interaction.response.send_message(embed=embed, ephemeral=True)


    except ValueError as e:
        await interaction.response.send_message("Invalid date format. Please check that your inputs are actual dates!.",
                                                ephemeral=True)
        return


@client.tree.command(name="add-shutdown", description="Add a shutdown command to your dinosaur.")
async def add_shutdown_command(interaction: Interaction):
    if not (interaction.user.guild_permissions.administrator or interaction.user.id == 767047725333086209):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return

    await interaction.response.send_message(
        "Fill in the following information:\n**Start Date**:\n**End Date**:\n**Description**:",
        view=AddShutdownView(interaction, db_client), ephemeral=True)


@client.tree.command(name="remove-shutdown", description="Add a shutdown command to your dinosaur.")
@app_commands.describe(shutdown_id="The ID of the shutdown you want to remove")
async def remove_shutdown_command(interaction: Interaction, shutdown_id: str):
    if not (interaction.user.guild_permissions.administrator or interaction.user.id == 767047725333086209):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return

    shutdown_id = int(shutdown_id)
    await interaction.response.send_message("Removing shutdown from DB")
    db_client.delete_shutdown(shutdown_id)
    db_client.refresh_cache()

    await interaction.edit_original_response(content="Shutdown removed!!!")


@remove_shutdown_command.autocomplete("shutdown_id")
async def remove_shutdown_autocomplete(interaction: Interaction, current: str):
    filtered = [
        s for s in db_client.shutdowns
        if s["description"].startswith(current)
    ]
    return [
        app_commands.Choice(
            name=f"{s["description"]} | {s["start_date"].strftime('%d-%m-%Y')} -> {s['end_date'].strftime('%d-%m-%Y')}",
            value=str(s["id"])
        )
        for s in filtered[:25]
    ]


# --------------------------
# --- Help Command Stuff ---
# --------------------------
@client.tree.command(name="help", description="Lists all available commands")
async def help_command(interaction: Interaction):
    await interaction.response.send_message(embed=help_embed(), ephemeral=True)
    # bot_member = interaction.guild.get_member(client.user.id)
    # perms = bot_member.guild_permissions
    #
    # perm_list = [perm for perm, value in perms if value]
    #
    # await interaction.response.send_message(
    #     f"Bot has these permissions:\n{', '.join(perm_list)}",
    #     ephemeral=True
    # )


def help_embed():
    embed = Embed(
        title="AnthraxUtils Commands",
        description="Here are all the commands available in AnthraxUtils!",
        color=discord.Color.greyple(),
    )
    commands = {
        "help": "... You are using it rn lol",
        "calculate_age": "Calculates how old the dinosaur is from the given date.",
    }

    for name in commands:
        embed.add_field(name=name, value=commands[name], inline=True)

    embed.set_footer(
        text="If you have any ideas for more quality of life commands, DM OccultParrot!"
    )
    return embed


# -----------------------------
# --- Sticky Messages Stuff ---
# -----------------------------

@client.tree.command(name="make-sticky", description="Creates a message that stays on the bottom of the discord chat.")
async def make_sticky(interaction: Interaction):
    if not (interaction.user.guild_permissions.administrator or interaction.user.id == 767047725333086209):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return

    await interaction.response.send_modal(StickyModal(create_sticky_message))


async def create_sticky_message(content: str, interaction: Interaction):
    guild_id = interaction.guild.id
    channel_id = interaction.channel.id
    sticky_msg = await interaction.channel.send(content + "\n-# This is a sticky message.")
    db_client.post_sticky_message(sticky_msg.id, channel_id, guild_id, content)
    db_client.refresh_cache()

    await interaction.response.send_message("Sticky message created!", ephemeral=True)


@client.tree.command(name="remove-sticky", description="Removes selected sticky message from the channel.")
@app_commands.describe(message_id="The ID of the sticky message to remove")
async def remove_sticky(interaction: Interaction, message_id: str):
    if not (interaction.user.guild_permissions.administrator or interaction.user.id == 767047725333086209):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return

    message_id = int(message_id)
    await interaction.response.send_message("Removing sticky message...", ephemeral=True)
    message = await interaction.channel.fetch_message(message_id)
    await message.delete()
    db_client.delete_sticky_message(message_id)
    db_client.refresh_cache()

    await interaction.edit_original_response(content="Sticky message removed!")


@remove_sticky.autocomplete("message_id")
async def remove_sticky_autocomplete(interaction: Interaction, current: str) -> list[app_commands.Choice[str]]:
    filtered = [
        s for s in db_client.stickied_messages
        if str(s["message_id"]).startswith(current) and s["channel_id"] == interaction.channel.id
    ]
    return [
        app_commands.Choice(
            name=f"ID: {s['message_id']} | Content: {s['content'][:30]}{"..." if len(s['content']) > 30 else ""}",
            value=str(s["message_id"]))
        for s in filtered[:25]
    ]


# -----------------------
# --- Dino Fact Stuff ---
# -----------------------
def get_dino_image_from_wikipedia(dino_name):
    # Search for the page
    search_url = f"https://en.wikipedia.org/w/api.php"
    search_params = {
        "action": "query",
        "format": "json",
        "titles": dino_name,
        "prop": "pageimages",
        "pithumbsize": 500
    }

    headers = {
        "User-Agent": "AnthraxUtilsBot/1.0 (Discord Bot; stemlertho@gmail.com)"
    }

    response = requests.get(search_url, params=search_params, headers=headers)
    print(response.text)
    data = response.json()

    pages = data['query']['pages']
    for page_id in pages:
        if 'thumbnail' in pages[page_id]:
            return pages[page_id]['thumbnail']['source']

    return None


@client.tree.command(name="dino-fact", description="Get a cool dino fact!")
async def get_dino_fact(interaction: Interaction):
    data = requests.get("https://dinosaur-facts-api.shultzlab.com/dinosaurs/random")
    data = data.json()

    embed = Embed(title=data["Name"], color=discord.Color.greyple(), description=data["Description"])
    embed.set_image(url=get_dino_image_from_wikipedia(data["Name"]))

    await interaction.response.send_message(embed=embed)


# == Running the bot ==
client.run(os.getenv("TOKEN"))
