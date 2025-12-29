import datetime
import json
import os

from typing import Literal
from xmlrpc.client import DateTime

import discord
from discord import Client, Intents, app_commands, Object, Interaction, Embed, Message, Member
from discord._types import ClientT
from discord.app_commands import Command
from dotenv import load_dotenv
import asyncio
from rich.console import Console
from supabase import Client as SupabaseClient

load_dotenv()
console = Console()

CACHE_REFRESH_INTERVAL = 300  # seconds


class AnthraxUtilsClient(Client):
    def __init__(self):
        intents = Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)

        self.lifespans = {}
        self.load_configs()

        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        # await self.tree.sync(guild=discord.Object(id=1374722200053088306))
        await self.tree.sync()

    console.print("Commands synced globally", style="green")

    def load_configs(self):
        with open("config/lifespans.json", "r") as f:
            self.lifespans = json.load(f)
        console.print(f"Loaded [yellow i]{len(self.lifespans)}[/] lifespan entries.", style="green")

    async def refresh_cache(self):
        pass


class StickyModal(discord.ui.Modal):
    def __init__(self, callback):
        super().__init__(title="Create Sticky Message")
        self.content = discord.ui.TextInput(label="Message Content", style=discord.TextStyle.paragraph, required=True,
                                            max_length=2000)
        self.add_item(self.content)
        self.callback = callback

    async def on_submit(self, interaction: Interaction, /) -> None:
        await self.callback(self.content.value, interaction)


class DateSelectModal(discord.ui.Modal):
    def __init__(self, callback, context: str):
        super().__init__(title=f"Enter {context.title()} Date")
        self.day = discord.ui.TextInput(label="Day", style=discord.TextStyle.short, required=True)
        self.month = discord.ui.TextInput(label="Month", style=discord.TextStyle.short, required=True)
        self.year = discord.ui.TextInput(label="Year", style=discord.TextStyle.short, required=True)

        self.add_item(self.day)
        self.add_item(self.month)
        self.add_item(self.year)

        self.callback = callback
        self.context = context

    async def on_submit(self, interaction: Interaction[ClientT], /) -> None:
        try:
            day = int(self.day.value)
            month = int(self.month.value)
            year = int(self.year.value)

            date = datetime.datetime.fromisoformat(
                f"{year}-{"0" + str(month) if month < 10 else month}-{"0" + str(day) if day < 10 else day}")
        except ValueError:
            await interaction.response.send_message("You entered an invalid date, please try again", ephemeral=True)
            return

        await self.callback(date.date())
        await interaction.response.send_message("✓", ephemeral=True, delete_after=0.01)


class DescriptionSelectModal(discord.ui.Modal):
    def __init__(self, callback):
        super().__init__(title="Enter Description for Shutdown")
        self.description = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, required=True)
        self.add_item(self.description)

        self.callback = callback

    async def on_submit(self, interaction: Interaction, /) -> None:
        await self.callback(self.description.value)
        await interaction.response.send_message("✓", ephemeral=True, delete_after=0.01)


class AddShutdownView(discord.ui.View):
    def __init__(self, original_interaction: discord.Interaction):
        super().__init__()
        self.original_interaction = original_interaction
        self.start_date: datetime.date | None = None
        self.end_date: datetime.date | None = None
        self.description: str = ""

    @discord.ui.button(label="Enter Start Date", style=discord.ButtonStyle.secondary)
    async def start_date_callback(self, interaction: Interaction, button: discord.ui.button()) -> None:
        await interaction.response.send_modal(DateSelectModal(self.submit_start_date, "start"))

    @discord.ui.button(label="Enter End Date", style=discord.ButtonStyle.secondary)
    async def end_date_callback(self, interaction: Interaction, button: discord.ui.button()) -> None:
        await interaction.response.send_modal(DateSelectModal(self.submit_end_date, "end"))

    @discord.ui.button(label="Description", style=discord.ButtonStyle.secondary)
    async def description_callback(self, interaction: Interaction, button: discord.ui.button()) -> None:
        await interaction.response.send_modal(DescriptionSelectModal(self.submit_description))

    @discord.ui.button(label="Submit", style=discord.ButtonStyle.success)
    async def submit_shutdown_callback(self, interaction: discord.Interaction, button: discord.ui.button()) -> None:
        # Validate that the dates are allowed
        if not self.start_date and not self.end_date and len(self.description) < 5:
            interaction.response.send_message(
                "Please select a start and end date, and enter a description greater than 5 characters", ephemeral=True)
            return

        if self.start_date > self.end_date:
            interaction.response.send_message("The start date cannot be before the end date", ephemeral=True)
            return

        db_client.post_shutdown(self.start_date, self.end_date, self.description)
        db_client.refresh_cache()

        console.log(
            f"Added row to shutdown table:\n{self.start_date.strftime("%d-%m-%Y")}  |  {self.end_date.strftime("%d-%m-%Y")}  |  {self.description}")
        embed = discord.Embed(title="Shutdown Complete", colour=discord.Color.green())
        embed.add_field(name="Start Date", value=self.start_date.strftime("%d-%m-%Y"))
        embed.add_field(name="End Date", value=self.end_date.strftime("%d-%m-%Y"))
        embed.add_field(name="Description", value=self.description)

        await interaction.response.send_message(embed=embed, ephemeral=True)
        original_message = await self.original_interaction.original_response()
        await original_message.delete()

    async def submit_start_date(self, date: datetime.date):
        self.start_date = date
        await self.update_message()

    async def submit_end_date(self, date: datetime.date):
        self.end_date = date
        await self.update_message()

    async def submit_description(self, description: str):
        self.description = description
        await self.update_message()

    async def update_message(self):
        await self.original_interaction.edit_original_response(
            content=f"Fill in the following information:\n**Start Date**:{f"`{self.start_date.strftime("%d-%m-%Y")}`" if self.start_date else ""}\n**End Date**:`{f"`{self.end_date.strftime("%d-%m-%Y")}`" if self.end_date else ""}`\n**Description**: {self.description}", )


class DBClient(SupabaseClient):
    listened_channels: list[int] = []
    stickied_messages: list = []
    shutdowns: list[dict] = []

    def __init__(self):
        # Setting up database connection
        url: str = os.getenv("SUPABASE_URL")
        key: str = os.getenv("SUPABASE_KEY")
        super().__init__(url, key)

        self.refresh_cache()

    async def start_cache_refresh(self):
        asyncio.create_task(self.refresh_cache_task())

    async def refresh_cache_task(self):
        print("Starting cache refresh task...")
        while True:
            console.log("Refreshing cache...")
            self.refresh_cache()
            await asyncio.sleep(CACHE_REFRESH_INTERVAL)  # Refresh every 60 seconds

    def refresh_cache(self):
        self.listened_channels = self.fetch_listened_channels()
        self.stickied_messages = self.fetch_sticky_messages()
        self.shutdowns = self.fetch_shutdowns()

    def fetch_sticky_messages(self):
        try:
            data = self.table("sticky_messages").select("*").execute()
            return data.data
        except Exception as e:
            console.print(f"Error fetching sticky messages: {e}", style="red")
            return []

    def fetch_listened_channels(self):
        try:
            data = self.fetch_sticky_messages()
            return list(set([msg["channel_id"] for msg in data]))
        except Exception as e:
            console.print(f"Error fetching listened channels: {e}", style="red")
            return []

    def fetch_shutdowns(self):
        try:
            data = self.table("shutdowns").select("*").execute()
            return data.data
        except Exception as e:
            console.print(f"Error fetching shutdowns: {e}", style="red")
            return []

    def calculate_shutdown_offset(self, birth_date: datetime.date):
        total_offset = 0

        for shutdown in self.shutdowns:
            start = datetime.date.fromisoformat(shutdown["start_date"])
            end = datetime.date.fromisoformat(shutdown["end_date"])

            if start > birth_date:
                shutdown_duration = (end - start).days
                total_offset += shutdown_duration

        return total_offset

    def post_sticky_message(self, message_id: int, channel_id: int, guild_id: int, content: str):
        try:
            data = {
                "message_id": message_id,
                "channel_id": channel_id,
                "guild_id": guild_id,
                "content": content
            }
            response = self.table("sticky_messages").insert(data).execute()
            return response.data
        except Exception as e:
            console.print(f"Error posting sticky message: {e}", style="red")
            return None

    def refresh_sticky_message(self, old_id: int, new_id: int):
        try:
            response = self.table("sticky_messages").update({"message_id": new_id}).eq("message_id", old_id).execute()
            return response.data
        except Exception as e:
            console.print(f"Error refreshing sticky message: {e}", style="red")
            return None

    def delete_sticky_message(self, message_id: int):
        try:
            response = self.table("sticky_messages").delete().eq("message_id", message_id).execute()
            return response.data
        except Exception as e:
            console.print(f"Error deleting sticky message: {e}", style="red")
            return None

    def post_shutdown(self, start_date: datetime.date, end_date: datetime.date, description: str):
        try:
            data = {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "description": description
            }
            response = self.table("shutdowns").insert(data).execute()
            return response.data
        except Exception as e:
            console.print(f"Error posting shutdown: {e}", style="red")
            return None

    def delete_shutdown(self, shutdown_id: int):
        try:
            response = self.table("shutdowns").delete().eg("id", shutdown_id).execute()
            return response.data
        except Exception as e:
            console.print(f"Error deleting shutdown: {e}", style="red")
            return None


client = AnthraxUtilsClient()
db_client = DBClient()


async def log_command_usage(command_name: str, user: Member, status: Literal["success", "Error", "Not Allowed"]):
    embed = Embed(title=f"`/{command_name}` Used",
                  color=discord.Color.green() if status == "success" else discord.Color.red() if status == "Error" else discord.Color.purple())
    embed.add_field(name="User", value=f"{user.display_name} ({user.id})", inline=False)
    embed.add_field(name=f"{user.display_name}'s Roles",
                    value=", ".join([role.name for role in user.roles if role.name != "@everyone"]), inline=False)
    embed.add_field(name="Timestamp", value=datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
                    inline=False)
    embed.add_field(name="Status", value=status, inline=False)
    try:
        await client.get_channel(1437913445780557835).send(embed=embed)
    # If channel does not exist
    except discord.HTTPException as _:
        pass


# == Add events and commands here! ==
@client.event
async def on_ready():
    console.print(f"Logged in as [green]{client.user.name}[/green]", justify="center")
    await db_client.start_cache_refresh()

    for sticky in db_client.stickied_messages:
        try:
            channel = client.get_channel(sticky["channel_id"])
            message = await channel.fetch_message(sticky["message_id"])
            old_id = message.id
            await message.delete()

            # Sending new sticky message
            new_message = await channel.send(sticky["content"])

            # Update DB and cache
            db_client.refresh_sticky_message(old_id, new_message.id)
            db_client.refresh_cache()
        except Exception as e:
            console.print(
                f"Error in refreshing sticky message ID {sticky['message_id']} in channel ID {sticky['channel_id']}: {e}",
                style="red")


@client.event
async def on_message(message: Message):
    if message.author.id == client.user.id:
        return

    if message.channel.id in db_client.listened_channels:
        for sticky in db_client.stickied_messages:
            if sticky["channel_id"] == message.channel.id:
                # Getting old message and deleting it
                old_message = await message.channel.fetch_message(sticky["message_id"])
                old_id = old_message.id
                await old_message.delete()

                # Sending new sticky message
                new_message = await message.channel.send(sticky["content"] + "\n-# This is a sticky message.")

                # Update DB and cache
                db_client.refresh_sticky_message(old_id, new_message.id)
                db_client.refresh_cache()


@client.event
async def on_app_command_completion(interaction: Interaction, command: Command):
    await log_command_usage(command.name, interaction.user, "success")


# TODO: Add back species to the description and function declaration once elder stuff is done
@client.tree.command(name="calculate-age", description="Calculate the age of your dino, using their birthdate.")
@app_commands.describe(day="The day the dinosaur was born", month="The month the dinosaur was born",
                       year="The year the dinosaur was born")
async def calculate_age(interaction: Interaction, day: int, month: int, year: int):
    try:
        print(f"Calculating age for {interaction.user.display_name}...")

        birth_date = datetime.datetime.fromisoformat(
            f"{year}-{"0" + str(month) if month < 10 else month}-{"0" + str(day) if day < 10 else day}")
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

            async for message in client.get_guild(1374722200053088306).get_channel(1383845771232678071).history(
                    limit=20, around=birth_date):
                # Only look at messages before the birthdate
                if message.created_at.date() > birth_date.date():
                    continue

                for key in seasons.keys():
                    if key in message.content.lower():
                        distance = (birth_date.date() - message.created_at.date()).days

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
Age in Weeks: `{age_in_weeks} week(s)`
Age in in-game years: `{age_in_weeks // 4} year(s)`
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
        await log_command_usage("calculate-age", interaction.user, "success")


    except ValueError as e:
        await interaction.response.send_message("Invalid date format. Please check that your inputs are actual dates!.",
                                                ephemeral=True)
        await log_command_usage("calculate-age", interaction.user, "Error")
        return


@client.tree.command(name="add-shutdown", description="Add a shutdown command to your dinosaur.")
async def add_shutdown_command(interaction: Interaction):
    if not (interaction.user.guild_permissions.administrator or interaction.user.id == 767047725333086209):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        await log_command_usage("add-shutdown", interaction.user, "Not Allowed")
        return

    await interaction.response.send_message(
        "Fill in the following information:\n**Start Date**:\n**End Date**:\n**Description**:",
        view=AddShutdownView(interaction), ephemeral=True)
    await log_command_usage("add-shutdown", interaction.user, "Success")


@client.tree.command(name="delete-shutdown", description="Add a shutdown command to your dinosaur.")
@app_commands.describe(shutdown_id="The ID of the shutdown you want to remove")
async def delete_shutdown_command(interaction: Interaction, shutdown_id: str):
    if not (interaction.user.guild_permissions.administrator or interaction.user.id == 767047725333086209):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        await log_command_usage("delete-shutdown", interaction.user, "Not Allowed")
        return

    shutdown_id = int(shutdown_id)
    await interaction.response.send_message("Removing shutdown from DB")
    db_client.delete_shutdown(shutdown_id)
    db_client.refresh_cache()

    await interaction.edit_original_response(content="Shutdown removed!!!")
    await log_command_usage("delete-shutdown", interaction.user, "Success")


@delete_shutdown_command.autocomplete("shutdown_id")
async def delete_shutdown_autocomplete(interaction: Interaction, current: str):
    filtered = [
        s for s in db_client.shutdowns
        if s["description"].startswith(current)
    ]
    return [
        app_commands.Choice(
            name=s["description"],
            value=str(s["id"])
        )
        for s in filtered[:25]
    ]


@client.tree.command(name="help", description="Lists all available commands")
async def help_command(interaction: Interaction):
    await interaction.response.send_message(embed=help_embed(), ephemeral=True)
    await log_command_usage("help", interaction.user, "success")


@client.tree.command(name="make-sticky", description="Creates a message that stays on the bottom of the discord chat.")
async def make_sticky(interaction: Interaction):
    if not (interaction.user.guild_permissions.administrator or interaction.user.id == 767047725333086209):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        await log_command_usage("make-sticky", interaction.user, "Not Allowed")
        return

    await interaction.response.send_modal(StickyModal(create_sticky_message))


async def create_sticky_message(content: str, interaction: Interaction):
    guild_id = interaction.guild.id
    channel_id = interaction.channel.id
    sticky_msg = await interaction.channel.send(content + "\n-# This is a sticky message.")
    db_client.post_sticky_message(sticky_msg.id, channel_id, guild_id, content)
    db_client.refresh_cache()

    await interaction.response.send_message("Sticky message created!", ephemeral=True)
    await log_command_usage("make-sticky", interaction.user, "success")


@client.tree.command(name="remove-sticky", description="Removes selected sticky message from the channel.")
@app_commands.describe(message_id="The ID of the sticky message to remove")
async def remove_sticky(interaction: Interaction, message_id: str):
    if not (interaction.user.guild_permissions.administrator or interaction.user.id == 767047725333086209):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        await log_command_usage("remove-sticky", interaction.user, "Not Allowed")
        return

    message_id = int(message_id)
    await interaction.response.send_message("Removing sticky message...", ephemeral=True)
    message = await interaction.channel.fetch_message(message_id)
    await message.delete()
    db_client.delete_sticky_message(message_id)
    db_client.refresh_cache()

    await interaction.edit_original_response(content="Sticky message removed!")
    await log_command_usage("remove-sticky", interaction.user, "success")


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


# == Running the bot ==
client.run(os.getenv("TOKEN"))
