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
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('TerritorialBot')

# --- PRE-COMPILED REGEX PATTERNS ---
PAYOUT_METHOD1_RE = re.compile(r'([a-zA-Z0-9]{5})\s+[\d.]+')
PAYOUT_METHOD2_RE = re.compile(r'([a-zA-Z0-9]{5})')
PAYOUT_METHOD3_RE = re.compile(r'\b([a-zA-Z0-9]{5})\b')

TIME_PATTERNS = [re.compile(p) for p in [r'Time:\s*(.+)', r'Time:(.+)', r'Time\s*:\s*(.+)']]
CONTEST_PATTERNS = [re.compile(p) for p in [r'Contest:\s*(\w+)', r'Contest:(.+)', r'Contest\s*:\s*(\w+)']]
MAP_PATTERNS = [re.compile(p) for p in [r'Map:\s*(.+)', r'Map:(.+)', r'Map\s*:\s*(.+)']]
COUNT_PATTERNS = [re.compile(p) for p in [r'Player Count:\s*(\d+)', r'Player Count:(\d+)', r'Player\s*Count\s*:\s*(\d+)']]
CLAN_PATTERNS = [re.compile(p) for p in [r'Winning Clan:\s*\[([^\]]+)\]', r'Winning Clan:\s*\[(.+?)\]', r'Winning\s*Clan\s*:\s*\[([^\]]+)\]']]
PREV_PATTERNS = [re.compile(p) for p in [r'Prev\. Points:\s*([\d.]+)', r'Prev Points:\s*([\d.]+)', r'Prev\.?\s*Points\s*:\s*([\d.]+)']]
CURR_PATTERNS = [re.compile(p) for p in [r'Curr\. Points:\s*([\d.]+)', r'Curr Points:\s*([\d.]+)', r'Curr\.?\s*Points\s*:\s*([\d.]+)']]


class WinLogClaimView(discord.ui.View):
    def __init__(self, bot, points, message_id, guild_id, original_message):
        super().__init__(timeout=300)  # 5 minutes
        self.bot = bot
        self.points = points
        self.message_id = message_id
        self.guild_id = guild_id
        self.claimed_users = {}
        self.original_message = original_message
        self.message = None
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
            
            if datetime.now(timezone.utc) - self.creation_time > timedelta(minutes=5):
                try:
                    await interaction.response.send_message("❌ This win log has expired!", ephemeral=True)
                except (discord.InteractionResponded, discord.NotFound):
                    pass
                return
            
            if user_id in self.claimed_users:
                try:
                    await interaction.response.send_message("❌ You already claimed points from this log!", ephemeral=True)
                except (discord.InteractionResponded, discord.NotFound):
                    pass
                return
            
            self.claimed_users[user_id] = multiplier
            final_points = self.points * multiplier
            success = await self.bot.add_winlog_points(user_id, self.guild_id, final_points)
            
            if success:
                try:
                    server_multiplier_data = await self.bot.db.multipliers.find_one({"guild_id": self.guild_id, "active": True})
                    server_multiplier = server_multiplier_data["multiplier"] if server_multiplier_data else 1.0
                    display_points = final_points * server_multiplier
                    
                    try:
                        embed = discord.Embed(
                            title="🏆 Win Log",
                            description=self.original_message,
                            color=0x00ff00
                        )
                        
                        claimed_mentions = [f"<@{uid}> ({mult}x)" for uid, mult in self.claimed_users.items()]
                        
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
                self.claimed_users.pop(user_id, None)
                try:
                    await interaction.response.send_message("❌ Failed to add points!", ephemeral=True)
                except:
                    pass
                
        except Exception as e:
            self.claimed_users.pop(user_id, None)
            try:
                await interaction.response.send_message("❌ An error occurred!", ephemeral=True)
            except:
                pass
            logger.error(f"Error in winlog claim: {e}")
    
    async def on_timeout(self):
        try:
            if self.message:
                embed = discord.Embed(
                    title="⏰ Win Log Expired",
                    description=self.original_message,
                    color=0x808080
                )
                
                if self.claimed_users:
                    claimed_mentions = [f"<@{uid}> ({mult}x)" for uid, mult in self.claimed_users.items()]
                    
                    embed.add_field(
                        name="Claimed by",
                        value="\n".join(claimed_mentions[:10]),
                        inline=False
                    )
                
                embed.set_footer(text="This win log has expired")
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
        self.session = None
        
        self.join_channel_id = int(os.getenv('JOIN_LOG_CHANNEL_ID')) if os.getenv('JOIN_LOG_CHANNEL_ID') else None
        self.leave_channel_id = int(os.getenv('LEAVE_LOG_CHANNEL_ID')) if os.getenv('LEAVE_LOG_CHANNEL_ID') else None
        self.log_channel_id = int(os.getenv('START_LOG_CHANNEL_ID')) if os.getenv('START_LOG_CHANNEL_ID') else None
        self.reward_log_channel_id = int(os.getenv('REWARD_LOG_CHANNEL_ID')) if os.getenv('REWARD_LOG_CHANNEL_ID') else None
        
        self.reward_monitor = None
        self.processed_rewards = set()
        self.last_log_time = 0
        self.winlog_claims = {}
        self.processed_winlogs = set()
        self.winlog_monitor = None
        self.last_winlog_time = None
        
    async def setup_hook(self):
        logger.info("Setting up bot...")
        
        await self.connect_mongodb()
        
        timeout = aiohttp.ClientTimeout(total=5)
        self.session = aiohttp.ClientSession(timeout=timeout)
        
        await self.load_commands()
        
        try:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} slash commands")
            
            if len(self.guilds) == 1:
                guild = self.guilds[0]
                synced_guild = await self.tree.sync(guild=guild)
                logger.info(f"Force synced {len(synced_guild)} commands for guild {guild.name}")
                
        except Exception as e:
            logger.error(f"Failed to sync commands: {e}")
    
    async def connect_mongodb(self):
        try:
            mongodb_uri = os.getenv('MONGODB_URI')
            if not mongodb_uri:
                logger.error("MONGODB_URI not found in environment variables")
                return
                
            self.mongodb_client = AsyncIOMotorClient(mongodb_uri)
            self.db = self.mongodb_client.your_mongodb_database_name
            
            await self.mongodb_client.admin.command('ping')
            logger.info("Successfully connected to MongoDB")
            
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
    
    async def load_commands(self):
        commands_path = Path("commands")
        
        if not commands_path.exists():
            logger.warning("Commands folder not found, creating it...")
            commands_path.mkdir(exist_ok=True)
            return
        
        loaded_count = 0
        
        for py_file in commands_path.rglob("*.py"):
            if py_file.name.startswith("__"):
                continue
                
            module_path = str(py_file.with_suffix("")).replace(os.sep, ".")
            
            try:
                await self.load_extension(module_path)
                logger.info(f"Loaded command module: {module_path}")
                loaded_count += 1
            except Exception as e:
                logger.error(f"Failed to load {module_path}: {e}")
        
        logger.info(f"Loaded {loaded_count} command modules")
    
    async def on_ready(self):
        logger.info(f"Bot logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guilds")
        
        activity = discord.Game(name="Territorial.io Cults")
        await self.change_presence(activity=activity)
        
        if self.log_channel_id:
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
        
        self.start_reward_monitoring()
        self.start_winlog_monitoring()
        self.start_war_monitoring()

    async def on_guild_join(self, guild):
        try:
            async for entry in guild.audit_logs(action=discord.AuditLogAction.bot_add, limit=1):
                if entry.target.id == self.user.id:
                    inviter = entry.user
                    break
            else:
                inviter = None
            
            if self.join_channel_id:
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
        try:
            if self.leave_channel_id:
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
        logger.error(f"Command error in {ctx.command}: {error}")
    
    def start_reward_monitoring(self):
        if self.reward_monitor is None or self.reward_monitor.done():
            self.reward_monitor = asyncio.create_task(self.monitor_rewards())
            logger.info("Started reward monitoring")
    
    async def monitor_rewards(self):
        import time
        while True:
            try:
                if self.db is None:
                    await asyncio.sleep(3)
                    continue
                
                current_time = time.time()
                rewards = await self.db.reward_roles.find({"active": True}).sort("amount", -1).to_list(None)
                
                if current_time - self.last_log_time >= 5:
                    if self.reward_log_channel_id:
                        log_channel = self.get_channel(self.reward_log_channel_id)
                        if log_channel and len(rewards) > 0:
                            embed = discord.Embed(
                                description=f"Reward monitor: {len(self.processed_rewards)} processed, {len(rewards)} active settings",
                                color=0x2b2d31
                            )
                            await log_channel.send(embed=embed)
                    self.last_log_time = current_time
                
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
                        
                        collection = self.db.points if reward_type == "points" else self.db.wins
                        pipeline = [
                            {"$match": {"guild_id": guild_id}},
                            {"$group": {"_id": "$user_id", "total": {"$sum": "$amount"}}}
                        ]
                        users = await collection.aggregate(pipeline).to_list(None)
                        
                        for user_data in users:
                            user_id = user_data["_id"]
                            total = user_data["total"]
                            
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
                            
                            member = guild.get_member(user_id)
                            if not member:
                                try:
                                    member = await guild.fetch_member(user_id)
                                except:
                                    continue
                            
                            key = f"{reward['_id']}_{user_id}"
                            if key in self.processed_rewards:
                                continue
                            
                            if role in member.roles:
                                self.processed_rewards.add(key)
                                continue
                            
                            try:
                                lower_roles_to_remove = []
                                for lower_reward in type_rewards:
                                    if (lower_reward["amount"] < reward["amount"]):
                                        lower_role = guild.get_role(lower_reward["role_id"])
                                        if lower_role and lower_role in member.roles:
                                            lower_roles_to_remove.append(lower_role)
                                
                                if lower_roles_to_remove:
                                    await member.remove_roles(*lower_roles_to_remove, reason=f"Upgraded to higher milestone: {total:,.0f} {reward['type']}")
                                
                                await member.add_roles(role, reason=f"Milestone: {total:,.0f} {reward['type']}")
                                self.processed_rewards.add(key)
                                
                                embed = discord.Embed(
                                    description=f"Congratulations {member.mention}, you have reached {total:,.0f} {reward['type']} and you are rewarded with {role.mention}",
                                    color=0x00ff00
                                )
                                await channel.send(embed=embed)
                                
                                if self.reward_log_channel_id:
                                    log_channel = self.get_channel(self.reward_log_channel_id)
                                    if log_channel:
                                        log_embed = discord.Embed(
                                            description=f"Gave {role.name} to {member.display_name} for {total:,} {reward['type']} in {guild.name}",
                                            color=0x00ff00
                                        )
                                        await log_channel.send(embed=log_embed)
                                
                            except discord.Forbidden:
                                if self.reward_log_channel_id:
                                    log_channel = self.get_channel(self.reward_log_channel_id)
                                    if log_channel:
                                        embed = discord.Embed(
                                            description=f"No permission to give {role.name} to {member.display_name} in {guild.name}",
                                            color=0xff0000
                                        )
                                        await log_channel.send(embed=embed)
                    
                            except Exception as e:
                                if self.reward_log_channel_id:
                                    log_channel = self.get_channel(self.reward_log_channel_id)
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
        pass
    
    async def scrape_territorial_winlogs(self):
        try:
            async with self.session.get('https://territorial.io/clan-results', headers={'User-Agent': 'Mozilla/5.0'}) as response:
                if response.status != 200:
                    logger.warning(f"HTTP {response.status} from territorial.io")
                    return
                
                content = await response.text()
                
                all_lines = content.strip().split('\n')
                lines = [line.strip() for line in all_lines if line.strip()]
                
                if len(lines) < 8:
                    return
                
                time_indices = [i for i, line in enumerate(lines) if line.startswith('Time:')]
                
                if not time_indices:
                    return
                
                start_idx = time_indices[0]
                
                if start_idx + 10 >= len(lines):
                    return
                
                time_line = lines[start_idx]
                contest_line = lines[start_idx + 1]
                map_line = lines[start_idx + 2]
                player_count_line = lines[start_idx + 3]
                winning_clan_line = lines[start_idx + 4]
                prev_points_line = lines[start_idx + 5]
                curr_points_line = lines[start_idx + 7]
                
                payout_accounts = []
                
                for i in range(start_idx + 8, min(len(lines), start_idx + 15)):
                    current_line = lines[i]
                    
                    if 'Payout' in current_line or 'payout' in current_line.lower():
                        payout_text = current_line.split(':', 1)[1].strip() if ':' in current_line else current_line.replace('Payout', '').replace('payout', '').strip()
                        
                        method1 = PAYOUT_METHOD1_RE.findall(payout_text)
                        
                        method2 = []
                        for part in payout_text.split(','):
                            match = PAYOUT_METHOD2_RE.search(part.strip())
                            if match: method2.append(match.group(1))
                            
                        method3 = PAYOUT_METHOD3_RE.findall(payout_text)
                        
                        method4 = []
                        for word in payout_text.replace(',', ' ').split():
                            clean_word = ''.join(c for c in word if c.isalnum())
                            if len(clean_word) == 5: method4.append(clean_word)
                        
                        all_accounts = method1 + method2 + method3 + method4
                        seen = set()
                        for acc in all_accounts:
                            if len(acc) == 5 and acc.isalnum() and acc not in seen:
                                payout_accounts.append(acc)
                                seen.add(acc)
                        break
                
                win_time = None
                for pattern in TIME_PATTERNS:
                    time_match = pattern.search(time_line)
                    if time_match:
                        win_time = time_match.group(1).strip()
                        break
                
                if not win_time or self.last_winlog_time == win_time:
                    return
                
                is_contest = False
                for pattern in CONTEST_PATTERNS:
                    contest_match = pattern.search(contest_line)
                    if contest_match:
                        is_contest = contest_match.group(1).strip().lower() in ['yes', 'true', '1']
                        break
                
                map_name = 'Unknown'
                for pattern in MAP_PATTERNS:
                    map_match = pattern.search(map_line)
                    if map_match:
                        map_name = map_match.group(1).strip()
                        break
                
                player_count = 0
                for pattern in COUNT_PATTERNS:
                    player_count_match = pattern.search(player_count_line)
                    if player_count_match:
                        player_count = int(player_count_match.group(1))
                        break
                
                if player_count == 0:
                    return
                
                base_points = player_count
                points = base_points * 2 if is_contest else base_points
                
                winning_clan = None
                for pattern in CLAN_PATTERNS:
                    clan_match = pattern.search(winning_clan_line)
                    if clan_match:
                        winning_clan = clan_match.group(1).strip()
                        break
                
                if not winning_clan:
                    return
                
                prev_points = '0'
                for pattern in PREV_PATTERNS:
                    prev_points_match = pattern.search(prev_points_line)
                    if prev_points_match:
                        prev_points = prev_points_match.group(1)
                        break
                
                curr_points = '0'
                for pattern in CURR_PATTERNS:
                    curr_points_match = pattern.search(curr_points_line)
                    if curr_points_match:
                        curr_points = curr_points_match.group(1)
                        break
                
                self.last_winlog_time = win_time
                await self.process_winlog_for_guilds(winning_clan, points, map_name, is_contest, player_count, prev_points, curr_points, win_time, payout_accounts)
                    
        except Exception as e:
            logger.error(f"Error scraping territorial.io: {e}")
    
    async def process_winlog_for_guilds(self, winning_clan, points, map_name, is_contest, player_count, prev_points, curr_points, win_time, payout_accounts):
        try:
            if self.db is None:
                return
            
            settings = await self.db.winlog_settings.find({"active": True}).to_list(None)
            
            for setting in settings:
                clan_filter = setting.get("clan_name", "").strip().lower()
                winning_clan_lower = winning_clan.lower()
                
                if clan_filter and clan_filter != winning_clan_lower:
                    continue
                
                guild = self.get_guild(setting["guild_id"])
                if not guild:
                    continue
                
                channel = guild.get_channel(setting["channel_id"])
                if not channel:
                    continue
                
                auto_credited = []
                all_guild_links = await self.db.account_links.find({"guild_id": guild.id}).to_list(None)
                
                for idx, payout_account in enumerate(payout_accounts):
                    linked_user = None
                    p_acc_lower = payout_account.lower()
                    
                    linked_user = next((link for link in all_guild_links if link.get("account_name") == payout_account), None)
                    if not linked_user:
                        linked_user = next((link for link in all_guild_links if link.get("account_name", "").lower() == p_acc_lower), None)
                    if not linked_user:
                        linked_user = next((link for link in all_guild_links if p_acc_lower in link.get("account_name", "").lower() or link.get("account_name", "").lower() in p_acc_lower), None)

                    if linked_user:
                        user_id = linked_user["user_id"]
                        
                        user = guild.get_member(user_id)
                        if not user:
                            try:
                                user = await guild.fetch_member(user_id)
                            except Exception:
                                continue
                        
                        success = await self.add_winlog_points(user_id, guild.id, points)
                        
                        if success:
                            auto_credited.append(f"<@{user_id}>")
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
                            except Exception:
                                pass
                
                if is_contest:
                    description = f"[{winning_clan}] won on {map_name} (Contest)\n{player_count} players x2 = {points} points available to claim!\n[{prev_points} → {curr_points}]"
                else:
                    description = f"[{winning_clan}] won on {map_name}\n{points} points available to claim!\n[{prev_points} → {curr_points}]"
                
                if auto_credited:
                    description += f"\n\n**Auto-credited:** {', '.join(auto_credited)}"
                
                view = WinLogClaimView(self, points, hash(win_time), guild.id, description)
                
                embed = discord.Embed(
                    title="🏆 Win Log",
                    description=description,
                    color=0x00ff00
                )
                embed.set_footer(text="Click to claim points • Expires in 5 minutes")
                
                try:
                    sent_message = await channel.send(embed=embed, view=view)
                    view.message = sent_message
                except (discord.Forbidden, discord.HTTPException):
                    pass
                    
        except Exception as e:
            logger.error(f"Error processing winlog for guilds: {e}")
    
    def start_winlog_monitoring(self):
        if self.winlog_monitor is None or self.winlog_monitor.done():
            self.winlog_monitor = asyncio.create_task(self.monitor_winlogs())
            logger.info("Started territorial.io win log monitoring")

    async def monitor_winlogs(self):
        while True:
            try:
                await self.scrape_territorial_winlogs()                
                await asyncio.sleep(5 + random.uniform(0, 3))
            except Exception as e:
                logger.error(f"Error in winlog monitoring: {e}")
                await asyncio.sleep(10)
    
    def start_war_monitoring(self):
        if not hasattr(self, 'war_monitor') or self.war_monitor is None or self.war_monitor.done():
            self.war_monitor = asyncio.create_task(self.monitor_wars())
            logger.info("Started war monitoring")
    
    async def monitor_wars(self):
        while True:
            try:
                if self.db is None:
                    await asyncio.sleep(10)
                    continue
                
                active_wars = await self.db.cult_wars.find({"active": True}).to_list(None)
                
                for war in active_wars:
                    end_time = war["end_time"]
                    if end_time.tzinfo is None:
                        end_time = end_time.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) >= end_time:
                        await self.end_war_automatically(war)
                
                await asyncio.sleep(10)
                
            except Exception as e:
                logger.error(f"Error in war monitoring: {e}")
                await asyncio.sleep(10)
    
    async def end_war_automatically(self, war):
        try:
            guild = self.get_guild(war["guild_id"])
            if not guild:
                return
            
            from bson import ObjectId
            attacker_cult = await self.db.cults.find_one({"_id": ObjectId(war["attacker_cult_id"])})
            defender_cult = await self.db.cults.find_one({"_id": ObjectId(war["defender_cult_id"])})
            
            if not attacker_cult or not defender_cult:
                return
            
            attacker_score = await self.calculate_war_score(attacker_cult, war)
            defender_score = await self.calculate_war_score(defender_cult, war)
            
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
                winner_cult = None
                winner_score = attacker_score
                loser_score = defender_score
            
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
            
            all_members = set(attacker_cult["members"] + defender_cult["members"])
            ping_mentions = " ".join([f"<@{user_id}>" for user_id in all_members])
            
            channel = None
            for ch in guild.text_channels:
                if ch.permissions_for(guild.me).send_messages:
                    channel = ch
                    break
            
            if channel:
                await channel.send(f"{ping_mentions}\n", embed=embed)
            
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
                ]).to_list(None)
                total_score += points_result[0]["total"] if points_result else 0
            
            if war["race_type"] in ["wins", "both"]:
                wins_result = await self.db.wins.aggregate([
                    {"$match": {
                        "user_id": member_id,
                        "guild_id": war["guild_id"],
                        "timestamp": {"$gte": war["start_time"], "$lte": war["end_time"]}
                    }},
                    {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
                ]).to_list(None)
                total_score += wins_result[0]["total"] if wins_result else 0
        
        return total_score
    
    async def add_winlog_points(self, user_id, guild_id, points):
        try:
            if self.db is None:
                return False
                
            guild = self.get_guild(guild_id)
            if not guild:
                return False
            
            user = guild.get_member(user_id)
            if not user:
                try:
                    user = await guild.fetch_member(user_id)
                except Exception:
                    return False
            
            multiplier = 1.0
            try:
                multiplier_data = await self.db.multipliers.find_one({"guild_id": guild_id, "active": True})
                if multiplier_data and "multiplier" in multiplier_data:
                    multiplier = float(multiplier_data["multiplier"])
            except Exception:
                pass
            
            final_points = points * multiplier
            
            user_cult_data = None
            try:
                user_cult_data = await self.db.cults.find_one({
                    "guild_id": guild_id,
                    "members": user_id,
                    "active": True
                })
            except Exception:
                pass
            
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
            
            try:
                points_result = await self.db.points.insert_one(points_data)
            except Exception as points_error:
                logger.error(f"Failed to save points transaction: {points_error}")
                return False
            
            try:
                wins_result = await self.db.wins.insert_one(wins_data)
            except Exception as wins_error:
                logger.error(f"Failed to save wins transaction: {wins_error}")
                try:
                    await self.db.points.delete_one({"_id": points_result.inserted_id})
                except:
                    pass
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error adding winlog points to user {user_id}: {e}")
            return False
    
    async def close(self):
        try:
            if self.reward_monitor:
                self.reward_monitor.cancel()
        except Exception: pass
        
        try:
            if hasattr(self, 'war_monitor') and self.war_monitor:
                self.war_monitor.cancel()
        except Exception: pass
        
        try:
            if self.winlog_monitor:
                self.winlog_monitor.cancel()
        except Exception: pass
        
        try:
            if self.mongodb_client:
                self.mongodb_client.close()
        except Exception: pass
        
        try:
            if self.session and not self.session.closed:
                await self.session.close()
                logger.info("Closed aiohttp session")
        except Exception: pass

        await super().close()


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
    bot = TerritorialBot()
    
    try:
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
