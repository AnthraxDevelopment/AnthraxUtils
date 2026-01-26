import datetime

import discord
from discord import Interaction
from discord._types import ClientT


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
    def __init__(self, original_interaction: discord.Interaction, db_client):
        super().__init__()
        self.original_interaction = original_interaction
        self.start_date: datetime.date | None = None
        self.end_date: datetime.date | None = None
        self.description: str = ""
        self.db_client = db_client

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

        self.db_client.post_shutdown(self.start_date, self.end_date, self.description)
        self.db_client.refresh_cache()

        print(
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
