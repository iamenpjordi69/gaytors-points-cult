import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
import logging
import traceback
import calendar

class CultStatsView(discord.ui.View):
    def __init__(self, bot, guild_id, cult_name):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.cult_name = cult_name
        self.month = None  # None for all time, or (year, month) tuple
    
    async def get_available_months(self):
        try:
            months = set()
            
            # Find cult first
            cult = await self.bot.db.cults.find_one({
                "guild_id": self.guild_id,
                "cult_name": self.cult_name,
                "active": True
            })
            
            if not cult:
                return []
            
            for collection in [self.bot.db.points, self.bot.db.wins]:
                try:
                    pipeline = [
                        {"$match": {
                            "guild_id": self.guild_id,
                            "cult_id": str(cult["_id"])
                        }},
                        {"$group": {
                            "_id": {
                                "year": {"$year": "$timestamp"},
                                "month": {"$month": "$timestamp"}
                            }
                        }}
                    ]
                    
                    results = await collection.aggregate(pipeline).to_list(None)
                    for result in results:
                        if result and result.get("_id"):
                            year = result["_id"].get("year")
                            month = result["_id"].get("month")
                            if year and month:
                                months.add((year, month))
                except Exception as e:
                    logging.error(f"Error getting months from {collection.name}: {e}")
                    continue
            
            return sorted(months, reverse=True)
            
        except Exception as e:
            logging.error(f"Error in get_available_months: {e}")
            return []
    
    async def get_cult_stats(self):
        try:
            # Find cult
            cult = await self.bot.db.cults.find_one({
                "guild_id": self.guild_id,
                "cult_name": self.cult_name,
                "active": True
            })
            
            if not cult or not cult.get("members") or not isinstance(cult["members"], list):
                return None, 0, 0, []
            
            # Build time filter
            time_filter = {}
            if self.month:
                try:
                    year, month = self.month
                    start_date = datetime(year, month, 1, tzinfo=timezone.utc)
                    if month == 12:
                        end_date = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
                    else:
                        end_date = datetime(year, month + 1, 1, tzinfo=timezone.utc)
                    time_filter = {"timestamp": {"$gte": start_date, "$lt": end_date}}
                except Exception as e:
                    logging.error(f"Error building time filter: {e}")
                    time_filter = {}
            
            # Calculate stats
            total_points = 0
            total_wins = 0
            member_stats = []
            
            for member_id in cult["members"]:
                try:
                    # Points query
                    points_match = {
                        "guild_id": self.guild_id,
                        "user_id": member_id,
                        "cult_id": str(cult["_id"])
                    }
                    points_match.update(time_filter)
                    
                    points_result = await self.bot.db.points.aggregate([
                        {"$match": points_match},
                        {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
                    ]).to_list(1)
                    
                    member_points = points_result[0]["total"] if points_result and points_result[0].get("total") else 0
                    total_points += member_points
                    
                    # Wins query
                    wins_match = {
                        "guild_id": self.guild_id,
                        "user_id": member_id,
                        "cult_id": str(cult["_id"])
                    }
                    wins_match.update(time_filter)
                    
                    wins_result = await self.bot.db.wins.aggregate([
                        {"$match": wins_match},
                        {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
                    ]).to_list(1)
                    
                    member_wins = wins_result[0]["total"] if wins_result and wins_result[0].get("total") else 0
                    total_wins += member_wins
                    
                    member_stats.append({
                        "user_id": member_id,
                        "points": member_points,
                        "wins": member_wins
                    })
                except Exception as e:
                    logging.error(f"Error processing member {member_id}: {e}")
                    continue
            
            member_stats.sort(key=lambda x: x["points"], reverse=True)
            return cult, total_points, total_wins, member_stats
            
        except Exception as e:
            logging.error(f"Critical error in get_cult_stats: {e}")
            return None, 0, 0, []
    
    async def update_embed(self, interaction):
        try:
            # Always defer first to prevent timeout
            await interaction.response.defer()
            
            cult, total_points, total_wins, member_stats = await self.get_cult_stats()
            
            if not cult:
                embed = discord.Embed(
                    title="❌ Error",
                    description="Cult not found or invalid data!",
                    color=0xff0000
                )
                await interaction.edit_original_response(embed=embed, view=self)
                return
            
            # Create title with time period
            if self.month:
                year, month = self.month
                month_name = calendar.month_name[month]
                title = f"{cult.get('cult_icon', '🏆')} {cult.get('cult_name', 'Cult')} - {month_name} {year}"
            else:
                title = f"{cult.get('cult_icon', '🏆')} {cult.get('cult_name', 'Cult')} - All Time"
            
            embed = discord.Embed(
                title=title,
                description=cult.get('cult_description', 'No description'),
                color=0x00ff00
            )
            
            # Basic info
            leader_id = cult.get("cult_leader_id")
            guild = interaction.guild
            leader = guild.get_member(leader_id) if leader_id else None
            leader_name = leader.mention if leader else "Unknown"
            
            embed.add_field(name="Leader", value=leader_name, inline=True)
            embed.add_field(name="Members", value=str(len(cult['members'])), inline=True)
            
            # Time period stats
            embed.add_field(name="Total Points", value=f"{total_points:,.0f}", inline=True)
            embed.add_field(name="Total Wins", value=f"{total_wins:,.0f}", inline=True)
            
            member_count = len(cult['members'])
            if member_count > 0:
                avg_points = total_points / member_count
                embed.add_field(name="Avg Points/Member", value=f"{avg_points:,.0f}", inline=True)
            else:
                embed.add_field(name="Avg Points/Member", value="0", inline=True)
            
            # Top 5 members for the period
            if member_stats:
                top_members = ""
                for i, member in enumerate(member_stats[:5], 1):
                    top_members += f"{i}. <@{member['user_id']}> - {member['points']:,.0f} pts, {member['wins']:,.0f} wins\n"
                
                embed.add_field(name="Top Members", value=top_members, inline=False)
            
            # Update month dropdown
            try:
                available_months = await self.get_available_months()
                self.month_select.options = [
                    discord.SelectOption(label="All Time", value="all", default=self.month is None)
                ]
                
                for year, month in available_months:
                    month_name = calendar.month_name[month]
                    label = f"{month_name} {year}"
                    value = f"{year}-{month}"
                    default = self.month == (year, month)
                    self.month_select.options.append(
                        discord.SelectOption(label=label, value=value, default=default)
                    )
            except Exception as e:
                logging.error(f"Error updating month dropdown: {e}")
            
            # Edit the message
            await interaction.edit_original_response(embed=embed, view=self)
                
        except Exception as e:
            logging.error(f"Error in update_embed: {e}")
            try:
                error_embed = discord.Embed(
                    title="❌ Error",
                    description="An error occurred while updating stats.",
                    color=0xff0000
                )
                await interaction.edit_original_response(embed=error_embed, view=None)
            except:
                pass
    
    @discord.ui.select(placeholder="Select month...")
    async def month_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        try:
            if not select.values:
                await interaction.response.defer()
                return
                
            if select.values[0] == "all":
                self.month = None
            else:
                try:
                    year, month = map(int, select.values[0].split("-"))
                    self.month = (year, month)
                except (ValueError, IndexError) as e:
                    logging.error(f"Error parsing month selection: {e}")
                    await interaction.response.defer()
                    return
            
            await self.update_embed(interaction)
            
        except Exception as e:
            logging.error(f"Error in month_select: {e}")
            try:
                await interaction.response.defer()
            except:
                pass

class CultStats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    async def cult_autocomplete(self, interaction: discord.Interaction, current: str):
        if not interaction.guild or self.bot.db is None:
            return []
        
        cults = await self.bot.db.cults.find({
            "guild_id": interaction.guild.id,
            "active": True
        }).to_list(25)
        
        return [
            app_commands.Choice(name=f"{cult['cult_icon']} {cult['cult_name']}", value=cult['cult_name'])
            for cult in cults
            if current.lower() in cult['cult_name'].lower()
        ]
    
    @app_commands.command(name="cult_stats", description="Show detailed stats for a cult")
    @app_commands.describe(cult_name="Select a cult to view stats")
    @app_commands.autocomplete(cult_name=cult_autocomplete)
    async def cult_stats(self, interaction: discord.Interaction, cult_name: str):
        try:
            if self.bot.db is None:
                await interaction.response.send_message("❌ Database not available!", ephemeral=True)
                return
            
            if not interaction.guild:
                await interaction.response.send_message("❌ This command can only be used in servers!", ephemeral=True)
                return
            
            await interaction.response.defer()
            
            # Create view
            view = CultStatsView(self.bot, interaction.guild.id, cult_name)
            
            # Get initial data
            cult, total_points, total_wins, member_stats = await view.get_cult_stats()
            
            if not cult:
                await interaction.followup.send("❌ Cult not found!", ephemeral=True)
                return
            
            # Create initial embed
            embed = discord.Embed(
                title=f"{cult.get('cult_icon', '🏆')} {cult.get('cult_name', 'Cult')} - All Time",
                description=cult.get('cult_description', 'No description'),
                color=0x00ff00
            )
            
            # Basic info
            leader_id = cult.get("cult_leader_id")
            leader = interaction.guild.get_member(leader_id) if leader_id else None
            leader_name = leader.mention if leader else "Unknown"
            
            embed.add_field(name="Leader", value=leader_name, inline=True)
            embed.add_field(name="Members", value=str(len(cult['members'])), inline=True)
            
            # Stats
            embed.add_field(name="Total Points", value=f"{total_points:,.0f}", inline=True)
            embed.add_field(name="Total Wins", value=f"{total_wins:,.0f}", inline=True)
            
            member_count = len(cult['members'])
            if member_count > 0:
                avg_points = total_points / member_count
                embed.add_field(name="Avg Points/Member", value=f"{avg_points:,.0f}", inline=True)
            else:
                embed.add_field(name="Avg Points/Member", value="0", inline=True)
            
            # Top 5 members
            if member_stats:
                top_members = ""
                for i, member in enumerate(member_stats[:5], 1):
                    top_members += f"{i}. <@{member['user_id']}> - {member['points']:,.0f} pts, {member['wins']:,.0f} wins\n"
                
                embed.add_field(name="Top Members", value=top_members, inline=False)
            
            # Setup month dropdown
            available_months = await view.get_available_months()
            view.month_select.options = [
                discord.SelectOption(label="All Time", value="all", default=True)
            ]
            
            for year, month in available_months:
                month_name = calendar.month_name[month]
                label = f"{month_name} {year}"
                value = f"{year}-{month}"
                view.month_select.options.append(
                    discord.SelectOption(label=label, value=value)
                )
            
            await interaction.followup.send(embed=embed, view=view)
                
        except Exception as e:
            error_msg = f"An error occurred in cult_stats: {str(e)}"
            logging.error(error_msg)
            logging.error(traceback.format_exc())
            
            try:
                if interaction.response.is_done():
                    await interaction.followup.send("❌ An error occurred while fetching cult stats!", ephemeral=True)
                else:
                    await interaction.response.send_message("❌ An error occurred while fetching cult stats!", ephemeral=True)
            except Exception:
                pass

async def setup(bot):
    await bot.add_cog(CultStats(bot))