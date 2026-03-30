import discord
from discord.ext import commands
import asyncio
import logging
import os
from pathlib import Path
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from aiohttp import web
import aiohttp
import re
import random
from datetime import datetime, timezone, timedelta


# Load environment variables
load_dotenv()

# Configure logging 
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('TerritorialBot')

class WinLogClaimView(discord.ui.View):
    def __init__(self, bot, points, message_id, guild_id, original_message):
        super().__init__(timeout=300)  # 5 minutes
        self.bot = bot
        self.points = points
        self.message_id = message_id
        self.guild_id = guild_id
        self.claimed_users = {}  # Store user_id: multiplier
        self.original_message = original_message
        self.message = None  # Store message reference
        self.creation_time = datetime.now(timezone.utc)
    
    @discord.ui.button(label="Claim (1x)", style=discord.ButtonStyle.secondary, emoji="🎯")
    async def claim_1x(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.claim_points(interaction, 1.0)
    
    @discord.ui.button(label="DUO win (x1.3)", style=discord.ButtonStyle.primary, emoji="🤝")
    async def claim_13x(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.claim_points(interaction, 1.3)
    
    @discord.ui.button(label="SOLO win (x1.5)", style=discord.ButtonStyle.success, emoji="👑")
    async def claim_15x(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.claim_points(interaction, 1.5)
    
    async def claim_points(self, interaction: discord.Interaction, multiplier: float):
        try:
            user_id = interaction.user.id
            
            # Check if 5 minutes have passed
            if datetime.now(timezone.utc) - self.creation_time > timedelta(minutes=5):
                try:
                    await interaction.response.send_message("❌ This win log has expired!", ephemeral=True)
                except (discord.InteractionResponded, discord.NotFound):
                    pass
                return
            
            # Check if user already claimed
            if user_id in self.claimed_users:
                try:
                    await interaction.response.send_message("❌ You already claimed points from this log!", ephemeral=True)
                except (discord.InteractionResponded, discord.NotFound):
                    pass
                return
            
            # Add to claimed users with multiplier
            self.claimed_users[user_id] = multiplier
            
            # Add points to user account with multiplier
            final_points = self.points * multiplier
            success = await self.bot.add_winlog_points(user_id, self.guild_id, final_points)
            
            if success:
                try:
                    # Get server multiplier for display
                    server_multiplier_data = await self.bot.db.multipliers.find_one({"guild_id": self.guild_id, "active": True})
                    server_multiplier = server_multiplier_data["multiplier"] if server_multiplier_data else 1.0
                    display_points = final_points * server_multiplier
                    
                    # Update original embed with claimed user
                    try:
                        embed = discord.Embed(
                            title="🏆 Win Log",
                            description=self.original_message,
                            color=0x00ff00
                        )
                        
                        claimed_mentions = []
                        for uid, mult in self.claimed_users.items():
                            claimed_mentions.append(f"<@{uid}> ({mult}x)")
                        
                        if claimed_mentions:
                            embed.add_field(
                                name="Claimed by",
                                value="\n".join(claimed_mentions[:10]),
                                inline=False
                            )
                        
                        embed.set_footer(text="Click to claim points • Expires in 5 minutes")
                        
                        await interaction.message.edit(embed=embed, view=self)
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        pass
                    
                    # Send success message
                    embed = discord.Embed(
                        title="✅ Points Claimed!",
                        description=f"You received **{display_points:,.1f} points** and **1 win**!",
                        color=0x00ff00
                    )
                    
                    if multiplier > 1.0:
                        embed.description += f"\n*Base: {self.points} x {multiplier} = {final_points} points*"
                    if server_multiplier > 1.0:
                        embed.description += f"\n*Server multiplier: {server_multiplier}x*"
                    
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                    
                except Exception as e:
                    try:
                        await interaction.response.send_message("✅ Points added successfully!", ephemeral=True)
                    except:
                        pass
            else:
                self.claimed_users.pop(user_id, None)  # Remove from claimed if failed
                try:
                    await interaction.response.send_message("❌ Failed to add points!", ephemeral=True)
                except:
                    pass
                
        except Exception as e:
            self.claimed_users.pop(user_id, None)  # Remove from claimed if error
            try:
                await interaction.response.send_message("❌ An error occurred!", ephemeral=True)
            except:
                pass
            logger.error(f"Error in winlog claim: {e}")
    
    async def on_timeout(self):
        try:
            if self.message:
                # Create embed preserving claimed users
                embed = discord.Embed(
                    title="⏰ Win Log Expired",
                    description=self.original_message,
                    color=0x808080
                )
                
                # Add claimed users if any
                if self.claimed_users:
                    claimed_mentions = []
                    for uid, mult in self.claimed_users.items():
                        claimed_mentions.append(f"<@{uid}> ({mult}x)")
                    
                    embed.add_field(
                        name="Claimed by",
                        value="\n".join(claimed_mentions[:10]),
                        inline=False
                    )
                
                embed.set_footer(text="This win log has expired")
                
                # Edit message to remove buttons
                await self.message.edit(embed=embed, view=None)
        except Exception as e:
            logger.error(f"Error in winlog timeout: {e}")

class TerritorialBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True
        
        super().__init__(
            command_prefix='!',
            intents=intents,
            help_command=None
        )
        
        self.mongodb_client = None
        self.db = None
        self.join_channel_id = 1391460533982462023
        self.leave_channel_id = 1391460563401576659
        self.log_channel_id = 1391463375304921108
        self.reward_monitor = None
        self.processed_rewards = set()
        self.last_log_time = 0
        self.winlog_claims = {}
        self.processed_winlogs = set()
        self.winlog_monitor = None
        self.last_winlog_time = None
        
    async def setup_hook(self):
        """Called when the bot is starting up"""
        logger.info("Setting up bot...")
        
        # Connect to MongoDB
        await self.connect_mongodb()
        
        # Load all commands from subfolders
        await self.load_commands()
        
        # Sync slash commands
        try:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} slash commands")
            
            # Force sync for current guild if in development
            if len(self.guilds) == 1:
                guild = self.guilds[0]
                synced_guild = await self.tree.sync(guild=guild)
                logger.info(f"Force synced {len(synced_guild)} commands for guild {guild.name}")
                
        except Exception as e:
            logger.error(f"Failed to sync commands: {e}")
    
    async def connect_mongodb(self):
        """Connect to MongoDB"""
        try:
            mongodb_uri = os.getenv('MONGODB_URI')
            if not mongodb_uri:
                logger.error("MONGODB_URI not found in environment variables")
                return
                
            self.mongodb_client = AsyncIOMotorClient(mongodb_uri)
            self.db = self.mongodb_client.your_mongodb_database_name
            
            # Test connection
            await self.mongodb_client.admin.command('ping')
            logger.info("Successfully connected to MongoDB")
            
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
    
    async def load_commands(self):
        """Load all commands from commands folder and subfolders"""
        commands_path = Path("commands")
        
        if not commands_path.exists():
            logger.warning("Commands folder not found, creating it...")
            commands_path.mkdir(exist_ok=True)
            return
        
        loaded_count = 0
        
        # Walk through all Python files in commands folder and subfolders
        for py_file in commands_path.rglob("*.py"):
            if py_file.name.startswith("__"):
                continue
                
            # Convert path to module format
            module_path = str(py_file.with_suffix("")).replace(os.sep, ".")
            
            try:
                await self.load_extension(module_path)
                logger.info(f"Loaded command module: {module_path}")
                loaded_count += 1
            except Exception as e:
                logger.error(f"Failed to load {module_path}: {e}")
        
        logger.info(f"Loaded {loaded_count} command modules")
    
    async def on_ready(self):
        """Called when bot is ready"""
        logger.info(f"Bot logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guilds")
        
        # Set bot status
        activity = discord.Game(name="Territorial.io Cults")
        await self.change_presence(activity=activity)
        logger.info("Bot status set to 'Playing Territorial.io Cults'")
        
        # Send startup log
        log_channel = self.get_channel(self.log_channel_id)
        if log_channel:
            embed = discord.Embed(
                title="🟢 Bot Started",
                color=0x00ff00,
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(name="Status", value="Online", inline=True)
            embed.add_field(name="Guilds", value=str(len(self.guilds)), inline=True)
            embed.add_field(name="Commands", value=str(len(self.tree.get_commands())), inline=True)
            await log_channel.send(embed=embed)
        
        # Start reward monitoring
        self.start_reward_monitoring()
        
        # Start win log monitoring
        self.start_winlog_monitoring()
        
        # Start war monitoring
        self.start_war_monitoring()
        

    
    async def on_guild_join(self, guild):
        """Called when bot joins a guild"""
        try:
            # Get the user who added the bot
            async for entry in guild.audit_logs(action=discord.AuditLogAction.bot_add, limit=1):
                if entry.target.id == self.user.id:
                    inviter = entry.user
                    break
            else:
                inviter = None
            
            # Send notification to join channel
            channel = self.get_channel(self.join_channel_id)
            if channel:
                embed = discord.Embed(
                    title="🎉 Bot Added to New Server",
                    color=0x00ff00,
                    timestamp=discord.utils.utcnow()
                )
                embed.add_field(name="Server", value=f"{guild.name} (ID: {guild.id})", inline=False)
                embed.add_field(name="Members", value=str(guild.member_count), inline=True)
                embed.add_field(name="Owner", value=str(guild.owner), inline=True)
                
                if inviter:
                    embed.add_field(name="Added by", value=f"{inviter} (ID: {inviter.id})", inline=False)
                
                embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
                
                await channel.send(embed=embed)
            
            # Log to database
            if self.db:
                await self.db.guild_events.insert_one({
                    "event": "join",
                    "guild_id": guild.id,
                    "guild_name": guild.name,
                    "member_count": guild.member_count,
                    "inviter_id": inviter.id if inviter else None,
                    "inviter_name": str(inviter) if inviter else None,
                    "timestamp": datetime.now(timezone.utc)
                })
            
            logger.info(f"Joined guild: {guild.name} (ID: {guild.id}) - Added by: {inviter}")
            
        except Exception as e:
            logger.error(f"Error handling guild join: {e}")
    
    async def on_guild_remove(self, guild):
        """Called when bot leaves a guild"""
        try:
            # Send notification to leave channel
            channel = self.get_channel(self.leave_channel_id)
            if channel:
                embed = discord.Embed(
                    title="❌ Bot Removed from Server",
                    color=0xff0000,
                    timestamp=discord.utils.utcnow()
                )
                embed.add_field(name="Server", value=f"{guild.name} (ID: {guild.id})", inline=False)
                embed.add_field(name="Members", value=str(guild.member_count), inline=True)
                embed.add_field(name="Owner", value=str(guild.owner), inline=True)
                
                embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
                
                await channel.send(embed=embed)
            
            # Log to database
            if self.db:
                await self.db.guild_events.insert_one({
                    "event": "leave",
                    "guild_id": guild.id,
                    "guild_name": guild.name,
                    "member_count": guild.member_count,
                    "timestamp": datetime.now(timezone.utc)
                })
            
            logger.info(f"Left guild: {guild.name} (ID: {guild.id})")
            
        except Exception as e:
            logger.error(f"Error handling guild leave: {e}")
    
    async def on_command_error(self, ctx, error):
        """Global error handler"""
        logger.error(f"Command error in {ctx.command}: {error}")
    
    def start_reward_monitoring(self):
        """Start reward monitoring task"""
        if self.reward_monitor is None or self.reward_monitor.done():
            self.reward_monitor = asyncio.create_task(self.monitor_rewards())
            logger.info("Started reward monitoring")
    
    async def monitor_rewards(self):
        """Monitor rewards every 3 seconds"""
        import time
        while True:
            try:
                if self.db is None:
                    await asyncio.sleep(3)
                    continue
                
                current_time = time.time()
                
                # Get all active reward settings sorted by amount (highest first)
                rewards = await self.db.reward_roles.find({"active": True}).sort("amount", -1).to_list(None)
                
                # Log every 5 seconds to Discord
                if current_time - self.last_log_time >= 5:
                    log_channel = self.get_channel(1392150722698809374)
                    if log_channel and len(rewards) > 0:
                        embed = discord.Embed(
                            description=f"Reward monitor: {len(self.processed_rewards)} processed, {len(rewards)} active settings",
                            color=0x2b2d31
                        )
                        await log_channel.send(embed=embed)
                    self.last_log_time = current_time
                
                # Group rewards by guild and type to find highest eligible roles
                guild_rewards = {}
                for reward in rewards:
                    guild_id = reward["guild_id"]
                    reward_type = reward["type"]
                    if guild_id not in guild_rewards:
                        guild_rewards[guild_id] = {"points": [], "wins": []}
                    guild_rewards[guild_id][reward_type].append(reward)
                
                for guild_id, types in guild_rewards.items():
                    guild = self.get_guild(guild_id)
                    if not guild:
                        continue
                    
                    for reward_type, type_rewards in types.items():
                        if not type_rewards:
                            continue
                        
                        # Get all users' totals for this type in this guild
                        collection = self.db.points if reward_type == "points" else self.db.wins
                        pipeline = [
                            {"$match": {"guild_id": guild_id}},
                            {"$group": {"_id": "$user_id", "total": {"$sum": "$amount"}}}
                        ]
                        users = await collection.aggregate(pipeline).to_list(None)
                        
                        for user_data in users:
                            user_id = user_data["_id"]
                            total = user_data["total"]
                            
                            # Find highest eligible reward for this user
                            highest_reward = None
                            for reward in type_rewards:
                                if total >= reward["amount"]:
                                    if not highest_reward or reward["amount"] > highest_reward["amount"]:
                                        highest_reward = reward
                            
                            if not highest_reward:
                                continue
                            
                            reward = highest_reward
                            channel = guild.get_channel(reward["channel_id"])
                            role = guild.get_role(reward["role_id"])
                            
                            if not channel or not role:
                                continue
                            # Check if user is actually in THIS guild
                            member = guild.get_member(user_id)
                            if not member:
                                try:
                                    member = await guild.fetch_member(user_id)
                                except:
                                    continue
                            
                            # Create unique key
                            key = f"{reward['_id']}_{user_id}"
                            
                            if key in self.processed_rewards:
                                continue
                            
                            # Check if user already has this exact role
                            if role in member.roles:
                                self.processed_rewards.add(key)
                                continue
                            
                            # Remove lower milestone roles of same type
                            try:
                                lower_roles_to_remove = []
                                for lower_reward in type_rewards:
                                    if (lower_reward["amount"] < reward["amount"]):
                                        lower_role = guild.get_role(lower_reward["role_id"])
                                        if lower_role and lower_role in member.roles:
                                            lower_roles_to_remove.append(lower_role)
                                
                                # Remove lower roles first
                                if lower_roles_to_remove:
                                    await member.remove_roles(*lower_roles_to_remove, reason=f"Upgraded to higher milestone: {total:,.0f} {reward['type']}")
                                
                                # Give new role
                                await member.add_roles(role, reason=f"Milestone: {total:,.0f} {reward['type']}")
                                self.processed_rewards.add(key)
                                
                                # Send milestone notification
                                embed = discord.Embed(
                                    description=f"Congratulations {member.mention}, you have reached {total:,.0f} {reward['type']} and you are rewarded with {role.mention}",
                                    color=0x00ff00
                                )
                                await channel.send(embed=embed)
                                
                                # Send log to specific channel
                                log_channel = self.get_channel(1392150722698809374)
                                if log_channel:
                                    log_embed = discord.Embed(
                                        description=f"Gave {role.name} to {member.display_name} for {total:,} {reward['type']} in {guild.name}",
                                        color=0x00ff00
                                    )
                                    await log_channel.send(embed=log_embed)
                                
                            except discord.Forbidden:
                                log_channel = self.get_channel(1392150722698809374)
                                if log_channel:
                                    embed = discord.Embed(
                                        description=f"No permission to give {role.name} to {member.display_name} in {guild.name}",
                                        color=0xff0000
                                    )
                                    await log_channel.send(embed=embed)
                    
                            except Exception as e:
                                log_channel = self.get_channel(1392150722698809374)
                                if log_channel:
                                    embed = discord.Embed(
                                        description=f"Failed to give {role.name} to {member.display_name}: {str(e)}",
                                        color=0xff0000
                                    )
                                    await log_channel.send(embed=embed)
                
                await asyncio.sleep(3)
                
            except Exception as e:
                logger.error(f"Error in reward monitoring: {e}")
                await asyncio.sleep(3)
    
    async def trigger_reward_check(self, user_id, guild_id):
        """Trigger immediate reward check for a user"""
        # Reward system runs automatically every 3 seconds
        pass
    
    async def scrape_territorial_winlogs(self):
        """Scrape territorial.io for new win logs with comprehensive detection"""
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get('https://territorial.io/clan-results', headers={'User-Agent': 'Mozilla/5.0'}) as response:
                    if response.status != 200:
                        logger.warning(f"HTTP {response.status} from territorial.io")
                        return
                    
                    content = await response.text()
                    logger.info(f"Fetched {len(content)} characters from territorial.io")
                    
                    # Split and clean lines
                    all_lines = content.strip().split('\n')
                    lines = [line.strip() for line in all_lines if line.strip()]
                    logger.info(f"Found {len(lines)} non-empty lines")
                    
                    if len(lines) < 8:
                        logger.warning(f"Not enough lines: {len(lines)}")
                        return
                    
                    # Find ALL Time: entries
                    time_indices = []
                    for i, line in enumerate(lines):
                        if line.startswith('Time:'):
                            time_indices.append(i)
                    
                    logger.info(f"Found {len(time_indices)} Time entries at indices: {time_indices}")
                    
                    if not time_indices:
                        logger.warning("No Time: entries found")
                        return
                    
                    # Process the first (most recent) entry
                    start_idx = time_indices[0]
                    logger.info(f"Processing entry starting at line {start_idx}")
                    
                    # Ensure we have enough lines for a complete entry
                    if start_idx + 10 >= len(lines):
                        logger.warning(f"Not enough lines after start_idx {start_idx}")
                        return
                    
                    time_line = lines[start_idx]
                    contest_line = lines[start_idx + 1]
                    map_line = lines[start_idx + 2]
                    player_count_line = lines[start_idx + 3]
                    winning_clan_line = lines[start_idx + 4]
                    prev_points_line = lines[start_idx + 5]
                    gain_line = lines[start_idx + 6]
                    curr_points_line = lines[start_idx + 7]
                    
                    # Find payout line and extract account names - FORCED EXTRACTION
                    payout_accounts = []
                    logger.info(f"\n=== PAYOUT EXTRACTION START ===")
                    logger.info(f"Looking for payout line in lines {start_idx + 8} to {start_idx + 15}")
                    
                    for i in range(start_idx + 8, min(len(lines), start_idx + 15)):
                        current_line = lines[i]
                        logger.info(f"Line {i}: '{current_line}'")
                        
                        if 'Payout' in current_line or 'payout' in current_line.lower():
                            logger.info(f"\n*** FOUND PAYOUT LINE ***: {current_line}")
                            
                            # Extract everything after "Payout:"
                            payout_text = ""
                            if ':' in current_line:
                                payout_text = current_line.split(':', 1)[1].strip()
                            else:
                                payout_text = current_line.replace('Payout', '').replace('payout', '').strip()
                            
                            logger.info(f"Payout text extracted: '{payout_text}'")
                            
                            # METHOD 1: Find all 5-char sequences followed by numbers
                            method1 = re.findall(r'([a-zA-Z0-9]{5})\s+[\d.]+', payout_text)
                            logger.info(f"Method 1 (account + number): {method1}")
                            
                            # METHOD 2: Split by comma and extract first 5 chars
                            method2 = []
                            parts = payout_text.split(',')
                            for part in parts:
                                part = part.strip()
                                # Find first 5 alphanumeric characters
                                match = re.search(r'([a-zA-Z0-9]{5})', part)
                                if match:
                                    method2.append(match.group(1))
                            logger.info(f"Method 2 (comma split): {method2}")
                            
                            # METHOD 3: Find ALL 5-character alphanumeric sequences
                            method3 = re.findall(r'\b([a-zA-Z0-9]{5})\b', payout_text)
                            logger.info(f"Method 3 (all 5-char): {method3}")
                            
                            # METHOD 4: Manual character-by-character parsing
                            method4 = []
                            words = payout_text.replace(',', ' ').split()
                            for word in words:
                                clean_word = ''.join(c for c in word if c.isalnum())
                                if len(clean_word) == 5:
                                    method4.append(clean_word)
                            logger.info(f"Method 4 (word parsing): {method4}")
                            
                            # Combine all methods and deduplicate
                            all_accounts = method1 + method2 + method3 + method4
                            seen = set()
                            for acc in all_accounts:
                                if len(acc) == 5 and acc.isalnum() and acc not in seen:
                                    payout_accounts.append(acc)
                                    seen.add(acc)
                            
                            logger.info(f"\n*** FINAL PAYOUT ACCOUNTS ***: {payout_accounts}")
                            logger.info(f"Total accounts found: {len(payout_accounts)}")
                            break
                    
                    if not payout_accounts:
                        logger.warning("\n!!! NO PAYOUT ACCOUNTS FOUND !!!")
                    
                    logger.info(f"=== PAYOUT EXTRACTION END ===\n")
                    
                    # Extract and validate time with multiple patterns
                    time_patterns = [r'Time:\s*(.+)', r'Time:(.+)', r'Time\s*:\s*(.+)']
                    win_time = None
                    for pattern in time_patterns:
                        time_match = re.search(pattern, time_line)
                        if time_match:
                            win_time = time_match.group(1).strip()
                            break
                    
                    if not win_time:
                        logger.error(f"Could not parse time from: {time_line}")
                        return
                    
                    logger.info(f"Parsed time: {win_time}")
                    
                    # Check if this is a new win log
                    if self.last_winlog_time == win_time:
                        logger.info(f"Already processed log at time: {win_time}")
                        return
                    
                    # Extract contest status with multiple patterns
                    contest_patterns = [r'Contest:\s*(\w+)', r'Contest:(.+)', r'Contest\s*:\s*(\w+)']
                    is_contest = False
                    for pattern in contest_patterns:
                        contest_match = re.search(pattern, contest_line)
                        if contest_match:
                            contest_value = contest_match.group(1).strip().lower()
                            is_contest = contest_value in ['yes', 'true', '1']
                            break
                    
                    logger.info(f"Contest status: {is_contest} from line: {contest_line}")
                    
                    # Extract map with multiple patterns
                    map_patterns = [r'Map:\s*(.+)', r'Map:(.+)', r'Map\s*:\s*(.+)']
                    map_name = 'Unknown'
                    for pattern in map_patterns:
                        map_match = re.search(pattern, map_line)
                        if map_match:
                            map_name = map_match.group(1).strip()
                            break
                    
                    logger.info(f"Map: {map_name}")
                    
                    # Extract player count with multiple patterns
                    count_patterns = [r'Player Count:\s*(\d+)', r'Player Count:(\d+)', r'Player\s*Count\s*:\s*(\d+)']
                    player_count = 0
                    for pattern in count_patterns:
                        player_count_match = re.search(pattern, player_count_line)
                        if player_count_match:
                            player_count = int(player_count_match.group(1))
                            break
                    
                    if player_count == 0:
                        logger.error(f"Could not parse player count from: {player_count_line}")
                        return
                    
                    base_points = player_count
                    points = base_points * 2 if is_contest else base_points
                    logger.info(f"Player count: {player_count}, Base points: {base_points}, Final points: {points}, Contest: {is_contest}")
                    
                    # Extract winning clan with multiple patterns
                    clan_patterns = [r'Winning Clan:\s*\[([^\]]+)\]', r'Winning Clan:\s*\[(.+?)\]', r'Winning\s*Clan\s*:\s*\[([^\]]+)\]']
                    winning_clan = None
                    for pattern in clan_patterns:
                        clan_match = re.search(pattern, winning_clan_line)
                        if clan_match:
                            winning_clan = clan_match.group(1).strip()
                            break
                    
                    if not winning_clan:
                        logger.error(f"Could not parse winning clan from: {winning_clan_line}")
                        return
                    
                    logger.info(f"Winning clan: {winning_clan}")
                    
                    # Extract prev points with multiple patterns
                    prev_patterns = [r'Prev\. Points:\s*([\d.]+)', r'Prev Points:\s*([\d.]+)', r'Prev\.?\s*Points\s*:\s*([\d.]+)']
                    prev_points = '0'
                    for pattern in prev_patterns:
                        prev_points_match = re.search(pattern, prev_points_line)
                        if prev_points_match:
                            prev_points = prev_points_match.group(1)
                            break
                    
                    # Extract curr points with multiple patterns
                    curr_patterns = [r'Curr\. Points:\s*([\d.]+)', r'Curr Points:\s*([\d.]+)', r'Curr\.?\s*Points\s*:\s*([\d.]+)']
                    curr_points = '0'
                    for pattern in curr_patterns:
                        curr_points_match = re.search(pattern, curr_points_line)
                        if curr_points_match:
                            curr_points = curr_points_match.group(1)
                            break
                    
                    logger.info(f"Points change: {prev_points} -> {curr_points}")
                    
                    # Update last processed time
                    self.last_winlog_time = win_time
                    
                    # Process for all configured guilds
                    await self.process_winlog_for_guilds(winning_clan, points, map_name, is_contest, player_count, prev_points, curr_points, win_time, payout_accounts)
                    
        except Exception as e:
            logger.error(f"Error scraping territorial.io: {e}")
    
    async def process_winlog_for_guilds(self, winning_clan, points, map_name, is_contest, player_count, prev_points, curr_points, win_time, payout_accounts):
        """Process win log for all configured guilds"""
        try:
            if self.db is None:
                return
            
            # Get all active winlog settings
            settings = await self.db.winlog_settings.find({"active": True}).to_list(None)
            
            for setting in settings:
                clan_filter = setting.get("clan_name", "").strip().lower()
                winning_clan_lower = winning_clan.lower()
                
                # Multiple matching strategies
                clan_matches = False
                if clan_filter:
                    # Exact match
                    if clan_filter == winning_clan_lower:
                        clan_matches = True
                    # Contains match (for debugging)
                    elif clan_filter in winning_clan_lower:
                        logger.info(f"Partial match found: '{clan_filter}' in '{winning_clan_lower}'")
                        # Uncomment next line to allow partial matches
                        # clan_matches = True
                
                logger.info(f"Clan filter '{clan_filter}' vs winning clan '{winning_clan_lower}': matches={clan_matches}")
                
                if not clan_matches:
                    continue
                
                guild = self.get_guild(setting["guild_id"])
                if not guild:
                    continue
                
                channel = guild.get_channel(setting["channel_id"])
                if not channel:
                    continue
                
                # Auto-credit linked accounts - FORCED PROCESSING
                auto_credited = []
                logger.info(f"\n\n=== AUTO-CREDIT START ===")
                logger.info(f"Guild: {guild.name} (ID: {guild.id})")
                logger.info(f"Payout accounts to process: {payout_accounts}")
                logger.info(f"Total accounts: {len(payout_accounts)}")
                
                # First, get ALL account links for this guild
                all_guild_links = await self.db.account_links.find({"guild_id": guild.id}).to_list(None)
                logger.info(f"\nTotal account links in guild: {len(all_guild_links)}")
                for link in all_guild_links:
                    logger.info(f"  - Account: {link.get('account_name')} -> User ID: {link.get('user_id')}")
                
                # Process each payout account
                for idx, payout_account in enumerate(payout_accounts):
                    logger.info(f"\n--- Processing account {idx+1}/{len(payout_accounts)}: '{payout_account}' ---")
                    
                    # Try EXACT match first
                    linked_user = await self.db.account_links.find_one({
                        "account_name": payout_account,
                        "guild_id": guild.id
                    })
                    logger.info(f"Exact match result: {linked_user}")
                    
                    # Try case-insensitive match
                    if not linked_user:
                        logger.info(f"Trying case-insensitive match for '{payout_account}'")
                        for link in all_guild_links:
                            if link["account_name"].lower() == payout_account.lower():
                                linked_user = link
                                logger.info(f"Case-insensitive match found: {link['account_name']}")
                                break
                    
                    # Try partial match (contains)
                    if not linked_user:
                        logger.info(f"Trying partial match for '{payout_account}'")
                        for link in all_guild_links:
                            if payout_account.lower() in link["account_name"].lower() or link["account_name"].lower() in payout_account.lower():
                                linked_user = link
                                logger.info(f"Partial match found: {link['account_name']}")
                                break
                    
                    if linked_user:
                        user_id = linked_user["user_id"]
                        logger.info(f"\n*** LINKED USER FOUND ***")
                        logger.info(f"Account: {payout_account} -> User ID: {user_id}")
                        
                        # Verify user exists in guild
                        user = guild.get_member(user_id)
                        if not user:
                            logger.info(f"User not in cache, fetching from API...")
                            try:
                                user = await guild.fetch_member(user_id)
                                logger.info(f"User fetched: {user.display_name}")
                            except Exception as fetch_err:
                                logger.error(f"FAILED to fetch user {user_id}: {fetch_err}")
                                continue
                        else:
                            logger.info(f"User found in cache: {user.display_name}")
                        
                        logger.info(f"\n>>> ADDING POINTS TO {user.display_name} <<<")
                        logger.info(f"Points to add: {points}")
                        logger.info(f"Contest: {is_contest}")
                        
                        # Add points with maximum validation
                        success = await self.add_winlog_points(user_id, guild.id, points)
                        
                        if success:
                            auto_credited.append(f"<@{user_id}>")
                            logger.info(f"\n*** SUCCESS! Auto-credited {user.display_name} ***\n")
                            
                            # Send DM notification
                            try:
                                multiplier_data = await self.db.multipliers.find_one({"guild_id": guild.id, "active": True})
                                server_multiplier = multiplier_data["multiplier"] if multiplier_data else 1.0
                                final_points = points * server_multiplier
                                
                                embed = discord.Embed(
                                    title="🎉 Auto-Credited Points!",
                                    description=f"You received **{final_points:,.1f} points** and **1 win** from [{winning_clan}] win on {map_name}!\n\nAccount: `{payout_account}`",
                                    color=0x00ff00
                                )
                                if is_contest:
                                    embed.description += "\n*Contest game - double points!*"
                                
                                await user.send(embed=embed)
                                logger.info(f"DM sent successfully to {user.display_name}")
                            except Exception as dm_error:
                                logger.warning(f"DM failed (user may have DMs disabled): {dm_error}")
                        else:
                            logger.error(f"\n!!! FAILED to add points to {user.display_name} !!!\n")
                    else:
                        logger.warning(f"\n!!! NO LINKED USER for account '{payout_account}' !!!")
                        logger.warning(f"This account is NOT linked in guild {guild.name}\n")
                
                logger.info(f"\n=== AUTO-CREDIT COMPLETE ===")
                logger.info(f"Successfully auto-credited: {len(auto_credited)} users")
                logger.info(f"Users: {auto_credited}")
                logger.info(f"=== END ===\n\n")
                
                # Create formatted description
                if is_contest:
                    description = f"[{winning_clan}] won on {map_name} (Contest)\n{player_count} players x2 = {points} points available to claim!\n[{prev_points} → {curr_points}]"
                else:
                    description = f"[{winning_clan}] won on {map_name}\n{points} points available to claim!\n[{prev_points} → {curr_points}]"
                
                if auto_credited:
                    description += f"\n\n**Auto-credited:** {', '.join(auto_credited)}"
                
                # Create claim button view  
                view = WinLogClaimView(self, points, hash(win_time), guild.id, description)
                
                # Send message with claim buttons
                embed = discord.Embed(
                    title="🏆 Win Log",
                    description=description,
                    color=0x00ff00
                )
                embed.set_footer(text="Click to claim points • Expires in 5 minutes")
                
                try:
                    sent_message = await channel.send(embed=embed, view=view)
                    view.message = sent_message
                except (discord.Forbidden, discord.HTTPException) as e:
                    logger.error(f"Failed to send winlog to {guild.name}: {e}")
                    
        except Exception as e:
            logger.error(f"Error processing winlog for guilds: {e}")
    
    def start_winlog_monitoring(self):
        """Start win log monitoring task"""
        if self.winlog_monitor is None or self.winlog_monitor.done():
            self.winlog_monitor = asyncio.create_task(self.monitor_winlogs())
            logger.info("Started territorial.io win log monitoring")
    


    async def monitor_winlogs(self):
        """Monitor territorial.io for new win logs"""
        while True:
            try:
                await self.scrape_territorial_winlogs()                
                # Wait 5–8 seconds (randomized to avoid blocking)
                await asyncio.sleep(5 + random.uniform(0, 3))
                
            except Exception as e:
                logger.error(f"Error in winlog monitoring: {e}")
                
                # Cooldown on error
                await asyncio.sleep(10)
    
    def start_war_monitoring(self):
        """Start war monitoring task"""
        if not hasattr(self, 'war_monitor') or self.war_monitor is None or self.war_monitor.done():
            self.war_monitor = asyncio.create_task(self.monitor_wars())
            logger.info("Started war monitoring")
    
    async def monitor_wars(self):
        """Monitor active wars and end them when time expires"""
        while True:
            try:
                if self.db is None:
                    await asyncio.sleep(10)
                    continue
                
                # Get all active wars
                active_wars = await self.db.cult_wars.find({"active": True}).to_list(None)
                
                for war in active_wars:
                    end_time = war["end_time"]
                    if end_time.tzinfo is None:
                        end_time = end_time.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) >= end_time:
                        await self.end_war_automatically(war)
                
                await asyncio.sleep(10)  # Check every 10 seconds
                
            except Exception as e:
                logger.error(f"Error in war monitoring: {e}")
                await asyncio.sleep(10)
    
    async def end_war_automatically(self, war):
        """Automatically end a war and determine winner"""
        try:
            guild = self.get_guild(war["guild_id"])
            if not guild:
                return
            
            # Get cult data
            from bson import ObjectId
            attacker_cult = await self.db.cults.find_one({"_id": ObjectId(war["attacker_cult_id"])})
            defender_cult = await self.db.cults.find_one({"_id": ObjectId(war["defender_cult_id"])})
            
            if not attacker_cult or not defender_cult:
                return
            
            # Calculate scores
            attacker_score = await self.calculate_war_score(attacker_cult, war)
            defender_score = await self.calculate_war_score(defender_cult, war)
            
            # Determine winner
            if attacker_score > defender_score:
                winner_cult = attacker_cult
                loser_cult = defender_cult
                winner_score = attacker_score
                loser_score = defender_score
            elif defender_score > attacker_score:
                winner_cult = defender_cult
                loser_cult = attacker_cult
                winner_score = defender_score
                loser_score = attacker_score
            else:
                winner_cult = None  # Tie
                winner_score = attacker_score
                loser_score = defender_score
            
            # Update war record
            await self.db.cult_wars.update_one(
                {"_id": war["_id"]},
                {
                    "$set": {
                        "active": False,
                        "ended_at": datetime.now(timezone.utc),
                        "attacker_score": attacker_score,
                        "defender_score": defender_score,
                        "winner_cult_id": str(winner_cult["_id"]) if winner_cult else None,
                        "auto_ended": True
                    }
                }
            )
            
            # Create result embed
            if winner_cult:
                embed = discord.Embed(
                    title="🏆 WAR ENDED - VICTORY!",
                    description=f"{winner_cult['cult_icon']} **{winner_cult['cult_name']}** has won the war against {loser_cult['cult_icon']} **{loser_cult['cult_name']}**!",
                    color=0x00ff00
                )
                embed.add_field(name="Final Scores", value=f"{winner_cult['cult_name']}: {winner_score:,.0f}\n{loser_cult['cult_name']}: {loser_score:,.0f}", inline=True)
            else:
                embed = discord.Embed(
                    title="🤝 WAR ENDED - TIE!",
                    description=f"The war between {attacker_cult['cult_icon']} **{attacker_cult['cult_name']}** and {defender_cult['cult_icon']} **{defender_cult['cult_name']}** ended in a tie!",
                    color=0xffa500
                )
                embed.add_field(name="Final Scores", value=f"{attacker_cult['cult_name']}: {attacker_score:,.0f}\n{defender_cult['cult_name']}: {defender_score:,.0f}", inline=True)
            
            embed.add_field(name="War Type", value=war["race_type"].title(), inline=True)
            
            # Get all members to notify
            all_members = set(attacker_cult["members"] + defender_cult["members"])
            ping_mentions = " ".join([f"<@{user_id}>" for user_id in all_members])
            
            # Find a channel to send the message (try to find the original channel or use first available)
            channel = None
            for ch in guild.text_channels:
                if ch.permissions_for(guild.me).send_messages:
                    channel = ch
                    break
            
            if channel:
                await channel.send(f"{ping_mentions}\n", embed=embed)
            
            # Send DMs to all members
            for user_id in all_members:
                try:
                    user = guild.get_member(user_id)
                    if user:
                        await user.send(embed=embed)
                except:
                    pass
            
        except Exception as e:
            logger.error(f"Error ending war automatically: {e}")
    
    async def calculate_war_score(self, cult, war):
        """Calculate cult's score for the war period"""
        total_score = 0
        
        for member_id in cult["members"]:
            if war["race_type"] in ["points", "both"]:
                points_result = await self.db.points.aggregate([
                    {"$match": {
                        "user_id": member_id,
                        "guild_id": war["guild_id"],
                        "timestamp": {"$gte": war["start_time"], "$lte": war["end_time"]}
                    }},
                    {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
                ]).to_list(1)
                total_score += points_result[0]["total"] if points_result else 0
            
            if war["race_type"] in ["wins", "both"]:
                wins_result = await self.db.wins.aggregate([
                    {"$match": {
                        "user_id": member_id,
                        "guild_id": war["guild_id"],
                        "timestamp": {"$gte": war["start_time"], "$lte": war["end_time"]}
                    }},
                    {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
                ]).to_list(1)
                total_score += wins_result[0]["total"] if wins_result else 0
        
        return total_score
    
    async def add_winlog_points(self, user_id, guild_id, points):
        """Add points from winlog claim with extensive validation"""
        try:
            logger.info(f"Starting add_winlog_points for user {user_id}, guild {guild_id}, points {points}")
            
            if self.db is None:
                logger.error("Database is None")
                return False
                
            guild = self.get_guild(guild_id)
            if not guild:
                logger.error(f"Guild {guild_id} not found")
                return False
            
            user = guild.get_member(user_id)
            if not user:
                try:
                    user = await guild.fetch_member(user_id)
                    logger.info(f"Fetched user {user_id} from API")
                except Exception as fetch_error:
                    logger.error(f"Failed to fetch user {user_id}: {fetch_error}")
                    return False
            
            logger.info(f"Processing points for user {user.display_name} ({user_id})")
            
            # Get multiplier setting with validation
            multiplier = 1.0
            try:
                multiplier_data = await self.db.multipliers.find_one({"guild_id": guild_id, "active": True})
                if multiplier_data and "multiplier" in multiplier_data:
                    multiplier = float(multiplier_data["multiplier"])
                    logger.info(f"Server multiplier: {multiplier}")
                else:
                    logger.info("No server multiplier found, using 1.0")
            except Exception as mult_error:
                logger.warning(f"Error getting multiplier: {mult_error}")
            
            # Calculate final points
            final_points = points * multiplier
            logger.info(f"Final points calculation: {points} * {multiplier} = {final_points}")
            
            # Get user's cult with validation
            user_cult_data = None
            try:
                user_cult_data = await self.db.cults.find_one({
                    "guild_id": guild_id,
                    "members": user_id,
                    "active": True
                })
                if user_cult_data:
                    logger.info(f"User is in cult: {user_cult_data['cult_name']}")
                else:
                    logger.info("User is not in any cult")
            except Exception as cult_error:
                logger.warning(f"Error getting cult data: {cult_error}")
            
            # Prepare transaction data
            timestamp = datetime.now(timezone.utc)
            points_data = {
                "user_id": user_id,
                "user_name": str(user),
                "guild_id": guild_id,
                "guild_name": guild.name,
                "amount": final_points,
                "base_amount": points,
                "multiplier_used": multiplier,
                "cult_id": str(user_cult_data["_id"]) if user_cult_data else None,
                "cult_name": user_cult_data["cult_name"] if user_cult_data else None,
                "type": "winlog_auto",
                "timestamp": timestamp
            }
            
            wins_data = {
                "user_id": user_id,
                "user_name": str(user),
                "guild_id": guild_id,
                "guild_name": guild.name,
                "amount": 1,
                "cult_id": str(user_cult_data["_id"]) if user_cult_data else None,
                "cult_name": user_cult_data["cult_name"] if user_cult_data else None,
                "type": "winlog_auto",
                "timestamp": timestamp
            }
            
            # Save points transaction with validation
            try:
                points_result = await self.db.points.insert_one(points_data)
                logger.info(f"Points transaction saved with ID: {points_result.inserted_id}")
            except Exception as points_error:
                logger.error(f"Failed to save points transaction: {points_error}")
                return False
            
            # Save win transaction with validation
            try:
                wins_result = await self.db.wins.insert_one(wins_data)
                logger.info(f"Wins transaction saved with ID: {wins_result.inserted_id}")
            except Exception as wins_error:
                logger.error(f"Failed to save wins transaction: {wins_error}")
                # Try to rollback points transaction
                try:
                    await self.db.points.delete_one({"_id": points_result.inserted_id})
                    logger.info("Rolled back points transaction")
                except:
                    logger.error("Failed to rollback points transaction")
                return False
            
            logger.info(f"Successfully added {final_points} points and 1 win to {user.display_name}")
            return True
            
        except Exception as e:
            logger.error(f"Error adding winlog points to user {user_id}: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False
    
    async def close(self):
        """Clean shutdown"""
        try:
            if self.reward_monitor:
                self.reward_monitor.cancel()
                logger.info("Reward monitoring stopped")
        except Exception as e:
            logger.error(f"Error stopping reward monitoring: {e}")
        
        try:
            if hasattr(self, 'war_monitor') and self.war_monitor:
                self.war_monitor.cancel()
                logger.info("War monitoring stopped")
        except Exception as e:
            logger.error(f"Error stopping war monitoring: {e}")
        
        try:
            if self.winlog_monitor:
                self.winlog_monitor.cancel()
                logger.info("Win log monitoring stopped")
        except Exception as e:
            logger.error(f"Error stopping winlog monitoring: {e}")
        
        try:
            if self.mongodb_client:
                self.mongodb_client.close()
                logger.info("MongoDB connection closed")
        except Exception as e:
            logger.error(f"Error closing MongoDB: {e}")
        
        await super().close()

# Health check server for Railway
async def health_check(request):
    return web.Response(text="Bot is running", status=200)

async def start_health_server():
    app = web.Application()
    app.router.add_get('/health', health_check)
    app.router.add_get('/', health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.getenv('PORT', 8000)))
    await site.start()
    print(f"Health server started on port {os.getenv('PORT', 8000)}")

async def main():
    """Main function to run the bot"""
    bot = TerritorialBot()
    
    try:
        # Start health check server
        await start_health_server()
        
        discord_token = os.getenv('DISCORD_TOKEN')
        if not discord_token:
            logger.error("DISCORD_TOKEN not found in environment variables")
            return
        
        await bot.start(discord_token)
        
    except KeyboardInterrupt:
        logger.info("Bot shutdown requested")
    except Exception as e:
        logger.error(f"Bot error: {e}")
    finally:
        await bot.close()

if __name__ == "__main__":
    asyncio.run(main())





    # Send Messages - All commands
    # Use Slash Commands - Command system
    # Embed Links - All embeds
    # Read Message History - Context awareness
    # View Channels - Access channels
    # Manage Roles - Reward role system
    # Attach Files - Profile graphs/charts
    # Add Reactions - Interactive features
