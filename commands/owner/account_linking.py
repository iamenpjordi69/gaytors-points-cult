import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone

class AccountLinking(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name="account_linking", description="Link territorial.io account to Discord user")
    @app_commands.describe(
        account_name="Territorial.io account name (5 characters)",
        user="Discord user to link"
    )
    async def account_linking(self, interaction: discord.Interaction, account_name: str, user: discord.Member):
        try:
            # Check if user is authorized
            if interaction.user.id != 780678948949721119:
                await interaction.response.send_message("❌ You are not authorized to use this command!", ephemeral=True)
                return
            
            if self.bot.db is None:
                await interaction.response.send_message("❌ Database not available!", ephemeral=True)
                return
            
            if not interaction.guild:
                await interaction.response.send_message("❌ This command can only be used in servers!", ephemeral=True)
                return
            
            # Validate account name
            if len(account_name) != 5:
                await interaction.response.send_message("❌ Account name must be exactly 5 characters!", ephemeral=True)
                return
            
            # Save account linking
            await self.bot.db.account_links.update_one(
                {"user_id": user.id, "guild_id": interaction.guild.id},
                {
                    "$set": {
                        "user_id": user.id,
                        "guild_id": interaction.guild.id,
                        "account_name": account_name,
                        "linked_by": interaction.user.id,
                        "timestamp": datetime.now(timezone.utc)
                    }
                },
                upsert=True
            )
            
            # Send DM to linked user
            try:
                embed = discord.Embed(
                    title="🔗 Account Linked",
                    description=f"Your territorial.io account `{account_name}` is now linked with {self.bot.user.name}!\n\nIf you are a clan winner, points will be automatically added to your account.",
                    color=0x00ff00
                )
                await user.send(embed=embed)
            except:
                pass
            
            # Confirm to command user
            embed = discord.Embed(
                title="✅ Account Linked",
                description=f"Successfully linked `{account_name}` to {user.mention}",
                color=0x00ff00
            )
            await interaction.response.send_message(embed=embed)
            
        except Exception as e:
            await interaction.response.send_message("❌ An error occurred!", ephemeral=True)
            print(f"Error in account_linking: {e}")

async def setup(bot):
    await bot.add_cog(AccountLinking(bot))