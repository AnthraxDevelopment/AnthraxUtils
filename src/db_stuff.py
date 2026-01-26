import asyncio
import datetime
import os

from supabase import Client as SupabaseClient


class DBClient(SupabaseClient):
    listened_channels: list[int] = []
    stickied_messages: list = []
    shutdowns: list[dict] = []

    def __init__(self, console, cache_refresh_interval):
        # Setting up database connection
        url: str = os.getenv("SUPABASE_URL")
        key: str = os.getenv("SUPABASE_KEY")
        super().__init__(url, key)

        self.console = console
        self.cache_refresh_interval = cache_refresh_interval

        self.refresh_cache()

    async def start_cache_refresh(self):
        asyncio.create_task(self.refresh_cache_task())

    async def refresh_cache_task(self):
        print("Starting cache refresh task...")
        while True:
            self.console.log("Refreshing cache...")
            self.refresh_cache()
            await asyncio.sleep(self.cache_refresh_interval)  # Refresh every 60 seconds

    def refresh_cache(self):
        self.listened_channels = self.fetch_listened_channels()
        self.stickied_messages = self.fetch_sticky_messages()
        self.shutdowns = self.fetch_shutdowns()

    def fetch_sticky_messages(self):
        try:
            data = self.table("sticky_messages").select("*").execute()
            return data.data
        except Exception as e:
            self.console.print(f"Error fetching sticky messages: {e}", style="red")
            return []

    def fetch_listened_channels(self):
        try:
            data = self.fetch_sticky_messages()
            return list(set([msg["channel_id"] for msg in data]))
        except Exception as e:
            self.console.print(f"Error fetching listened channels: {e}", style="red")
            return []

    def fetch_shutdowns(self):
        try:
            data = self.table("shutdowns").select("*").execute()
            return data.data
        except Exception as e:
            self.console.print(f"Error fetching shutdowns: {e}", style="red")
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
            self.console.print(f"Error posting sticky message: {e}", style="red")
            return None

    def refresh_sticky_message(self, old_id: int, new_id: int):
        try:
            response = self.table("sticky_messages").update({"message_id": new_id}).eq("message_id", old_id).execute()
            return response.data
        except Exception as e:
            self.console.print(f"Error refreshing sticky message: {e}", style="red")
            return None

    def delete_sticky_message(self, message_id: int):
        try:
            response = self.table("sticky_messages").delete().eq("message_id", message_id).execute()
            return response.data
        except Exception as e:
            self.console.print(f"Error deleting sticky message: {e}", style="red")
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
            self.console.print(f"Error posting shutdown: {e}", style="red")
            return None

    def delete_shutdown(self, shutdown_id: int):
        try:
            response = self.table("shutdowns").delete().eg("id", shutdown_id).execute()
            return response.data
        except Exception as e:
            self.console.print(f"Error deleting shutdown: {e}", style="red")
            return None

    def get_AID_from_discord_id(self, discord_id: int):
        try:
            data = self.table("players").select("*").eq("discord_id", discord_id).execute()
            return data.data[0]["alderon_id"] if data.data else None
        except (ValueError, TypeError) as e:
            self.console.print(f"AID is in wrong format: {e}", style="red")
        except Exception as e:
            self.console.print(f"Error fetching AID from Discord ID: {e}", style="red")
            return None
