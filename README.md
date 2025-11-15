# 🏰 Territorial Cults Discord Bot

A Discord bot for managing competitive Territorial.io gaming communities. Create cults, declare wars, track points automatically, and reward your top players with roles.

## Features

### 🎮 Cult Management
- Create and manage cults (guilds) with your friends
- Invite members and manage roles within cults
- View cult stats and member profiles
- Alliance system for temporary cooperation

### ⚔️ War System
- Declare wars between cults
- Automatic score calculation based on points/wins during war period
- Auto-end wars with winner determination
- War notifications to all participants

### 📊 Point Tracking
- Automatic point tracking from Territorial.io wins
- Win log claiming with multipliers (1x, 1.3x, 1.5x)
- Auto-credit linked accounts
- Server-wide multipliers for events
- Leaderboards (global and weekly)

### 🎖️ Reward System
- Automatic role rewards at point/win milestones
- Customizable milestone thresholds per server
- Role hierarchy management

### 🔗 Account Linking
- Link Discord accounts to Territorial.io accounts
- Auto-credit points when your account wins
- Admin tools for managing links

## Quick Start

### Prerequisites
- Python 3.8+
- MongoDB (local or cloud)
- Discord Bot Token

### Installation

1. Clone the repository:
```bash
git clone https://github.com/viktorexe/Territorial-Cults.git
cd Territorial-Cults
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set up environment variables:
```bash
cp .env.example .env
```

4. Edit `.env` with your credentials:
```env
DISCORD_TOKEN=your_bot_token_here
MONGODB_URI=your_mongodb_connection_string
```

5. Configure MongoDB collection name:
   - Open `main.py` and go to **line 220**
   - Replace `your_mongo_collection_name` with your desired database name:
   ```python
   self.db = self.mongodb_client.your_mongo_collection_name
   ```

6. Run the bot:
```bash
python main.py
```

## Configuration

### Discord Bot Setup
1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application
3. Go to "Bot" section and create a bot
4. Copy the token and add to `.env`
5. Enable these intents:
   - Message Content Intent
   - Server Members Intent
   - Guild Intent

### MongoDB Setup
- **Local**: Install MongoDB and use `mongodb://localhost:27017`
- **Cloud**: Use MongoDB Atlas and get your connection string from the dashboard

### Bot Permissions
The bot needs these permissions in your Discord server:
- Send Messages
- Embed Links
- Manage Roles (for reward system)
- Read Message History
- View Channels

## Deployment

### Docker
```bash
docker build -t territorial-bot .
docker run -e DISCORD_TOKEN=your_token -e MONGODB_URI=your_uri territorial-bot
```

### Railway
Included `railway.json` and `Procfile` for easy Railway deployment.

### Traditional Server
Run on any Linux/Windows server with Python 3.8+

## Commands

### Cult Commands
- `/cult create` - Create a new cult
- `/cult join` - Join an existing cult
- `/cult info` - View cult information
- `/cult list` - List all cults
- `/cult war` - Declare war on another cult
- `/cult alliance` - Form an alliance

### Economy Commands
- `/profile` - View your stats
- `/leaderboard` - Global leaderboard
- `/leaderboard week` - Weekly leaderboard
- `/addwin` - Add a win (admin)
- `/addscore` - Add points (admin)

### Admin Commands
- `/rewardrole` - Set up milestone rewards
- `/set_multiplier` - Set server point multiplier
- `/account_linking` - Link Discord to Territorial.io account
- `/cleanup_roles` - Remove old reward roles

## Database Structure

The bot uses MongoDB with these collections:
- `cults` - Cult information and members
- `cult_wars` - War records and results
- `points` - Point transactions
- `wins` - Win transactions
- `reward_roles` - Milestone role settings
- `multipliers` - Server multiplier settings
- `account_links` - Discord ↔ Territorial.io mappings
- `guild_events` - Bot activity logs

## Troubleshooting

### Bot not responding
- Check if bot is online in Discord
- Verify `DISCORD_TOKEN` is correct
- Check bot permissions in server

### Points not tracking
- Verify MongoDB connection
- Check if account is linked correctly
- Ensure bot has access to the channel

### Roles not assigning
- Check bot role hierarchy (bot role must be above reward roles)
- Verify reward role settings are configured
- Check bot permissions for "Manage Roles"

## Support

Join the [Discord server](https://discord.gg/HvF5QnqtHN) for help and updates.

## License

Free to use and modify. Just don't claim you made it.

---

Made with ❤️ by viktorexe
