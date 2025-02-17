import discord
import requests
import asyncio
import os
from discord.ext import commands
from dotenv import load_dotenv
import sqlite3

# Connect to SQLite database (creates it if it doesn't exist)
conn = sqlite3.connect("bot_data.db")
cursor = conn.cursor()

# Create table to store summoner names and PUUIDs
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    discord_id INTEGER PRIMARY KEY,
    summoner_name TEXT NOT NULL,
    puuid TEXT NOT NULL
)
""")
conn.commit()


# Load API keys from .env file
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
RIOT_API_KEY = os.getenv("RIOT_API_KEY")
REGION = os.getenv("REGION")

# Enable all intents
intents = discord.Intents.all()

# Initialize bot
bot = commands.Bot(command_prefix="!", intents=intents)

# Store summoner names
summoner_names = {}

### FUNCTION TO GET SUMMONER PUUID ###
def get_puuid(summoner_name):
    try:
        if "#" not in summoner_name:
            print(f"❌ Invalid Riot ID format: {summoner_name}")
            return None
        
        name, tag = summoner_name.split("#", 1)  # Correctly handle Name#Tag
        
        # Riot API expects spaces to be encoded as '%20'
        name = name.replace(" ", "%20")

        # Step 1: Get PUUID from Name#Tag
        account_url = f"https://europe.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name}/{tag}?api_key={RIOT_API_KEY}"
        print(f"🔍 Fetching PUUID: {account_url}")  # Debugging

        account_response = requests.get(account_url).json()

        if "puuid" not in account_response:
            print(f"⚠️ Riot API Error: {account_response}")
            return None
        
        return account_response["puuid"]

    except Exception as e:
        print(f"❌ Error getting PUUID: {e}")
        return None


### FUNCTION TO CHECK LAST 2 MATCH RESULTS ###
def get_last_two_match_results(puuid):
    try:
        # Step 1: Get Last 2 Match IDs
        match_url = f"https://europe.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?start=0&count=2&api_key={RIOT_API_KEY}"
        print(f"🔍 Fetching Last 2 Match IDs: {match_url}")  # Debugging
        
        match_ids = requests.get(match_url).json()

        if not match_ids or len(match_ids) < 2:
            return None  # Not enough matches played

        results = []
        
        # Step 2: Get Match Details for each match
        for match_id in match_ids:
            match_details_url = f"https://europe.api.riotgames.com/lol/match/v5/matches/{match_id}?api_key={RIOT_API_KEY}"
            print(f"🔍 Fetching Match Details: {match_details_url}")  # Debugging
            
            match_data = requests.get(match_details_url).json()

            # Step 3: Find the user's result in the match
            for participant in match_data["info"]["participants"]:
                if participant["puuid"] == puuid:
                    results.append("win" if participant["win"] else "loss")
                    break  # Move to next match

        return results  # Returns last 2 matches ["win", "win"] or ["win", "loss"]

    except Exception as e:
        print(f"❌ Error checking match result: {e}")
        return None


### COMMAND: SET SUMMONER NAME ###
@bot.command()
async def setlol(ctx, *, summoner_name: commands.clean_content):
    puuid = get_puuid(summoner_name.strip())

    if puuid:
        # Store in the database
        cursor.execute("INSERT OR REPLACE INTO users (discord_id, summoner_name, puuid) VALUES (?, ?, ?)",
                       (ctx.author.id, summoner_name, puuid))
        conn.commit()
        print(f"Saving to DB: {ctx.author.id}, {summoner_name}, {puuid}")


        await ctx.send(f"{ctx.author.mention}, your League name is set to **{summoner_name}**! ✅")
    else:
        await ctx.send("❌ Error: Could not find that summoner. Check spelling or region.")



### COMMAND: CHECK AND ASSIGN ROLE MANUALLY ###
@bot.command()
async def checkme(ctx):
    # Retrieve the PUUID from the database
    cursor.execute("SELECT puuid FROM users WHERE discord_id = ?", (ctx.author.id,))
    result = cursor.fetchone()

    # If the user hasn't set their summoner name, notify them
    if not result:
        await ctx.send("❌ You haven't set your League name yet. Use `!setlol Name#Tag` first.")
        return

    puuid = result[0]  # Extract the stored PUUID
    results = get_last_two_match_results(puuid)

    # If no match history is found, notify the user
    if not results:
        await ctx.send("⚠️ No match history found.")
        return

    # Get Discord server and member
    guild = ctx.guild
    member = ctx.author

    # Define role names
    role_iron = discord.utils.get(guild.roles, name="🔻 Iron IV")
    role_challenger = discord.utils.get(guild.roles, name="⚡ Challenger")
    role_clutch = discord.utils.get(guild.roles, name="🔥 Clutch Master")

    # Remove existing roles
    await member.remove_roles(role_iron, role_challenger, role_clutch)

    # Assign the correct role based on match results
    if results[0] == "loss":
        await member.add_roles(role_iron)
        role_name = "🔻 Iron IV"
    elif results[0] == "win":
        if results == ["win", "win"]:
            await member.add_roles(role_clutch)
            role_name = "🔥 Clutch Master"
        else:
            await member.add_roles(role_challenger)
            role_name = "⚡ Challenger"

    # Send a message confirming the role update
    await ctx.send(f"✅ Match history searched manually. Your role is now **{role_name}**.")



### CHECK ALL USERS EVERY 30 MIN ###
async def auto_check():
    await bot.wait_until_ready()
    
    while not bot.is_closed():
        cursor.execute("SELECT discord_id, puuid FROM users")
        users = cursor.fetchall()  # Fetch all users from the database

        for discord_id, puuid in users:
            guild = bot.guilds[0]
            member = guild.get_member(discord_id)

            if not member:
                continue  # Skip users who are no longer in the server

            results = get_last_two_match_results(puuid)

            if not results:
                continue

            role_iron = discord.utils.get(guild.roles, name="🔻 Iron IV")
            role_challenger = discord.utils.get(guild.roles, name="⚡ Challenger")
            role_clutch = discord.utils.get(guild.roles, name="🔥 Clutch Master")

            # Remove existing roles safely
            roles_to_remove = [role for role in [role_iron, role_challenger, role_clutch] if role]
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove)

            # Assign the correct role
            if results[0] == "loss" and role_iron:
                await member.add_roles(role_iron)
            elif results[0] == "win":
                if results == ["win", "win"] and role_clutch:
                    await member.add_roles(role_clutch)
                elif role_challenger:
                    await member.add_roles(role_challenger)

        await asyncio.sleep(1800)  # Wait 30 minutes before next check



@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")

    # Load users from database into summoner_names dictionary
    cursor.execute("SELECT discord_id, puuid FROM users")
    rows = cursor.fetchall()
    
    for discord_id, puuid in rows:
        summoner_names[discord_id] = puuid

    # Start the auto-check loop
    bot.loop.create_task(auto_check())




@bot.event
async def on_message(message):
    if message.author == bot.user:
        return  # Ignore bot's own messages
    await bot.process_commands(message)  # Ensures commands still work


@bot.command()
async def ping(ctx):
    await ctx.send("🏓 Pong!")


# Start bot
bot.run(TOKEN)
