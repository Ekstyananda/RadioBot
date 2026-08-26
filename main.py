import discord
from discord.ext import commands
import asyncio
from config import TOKEN, TARGET_CHANNEL_IDS

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!pndq ', intents=intents, help_command=None)

@bot.event
async def on_ready():
    latency = round(bot.latency * 1000)
    print('\n' + '='*50)
    print(f'SYSTEM ONLINE: {bot.user}')
    print(f'🆔  Bot ID       : {bot.user.id}')
    print('='*50)
    print(f'📊  Status')
    print(f'    • Ping       : {latency}ms')
    print(f'    • Servers    : {len(bot.guilds)} Guilds')
    print(f'    • List Server: {", ".join([g.name for g in bot.guilds])}')
    print('='*50)
    print(f'🎯  Target Monitoring')
    print(f'    • Channel IDs: {TARGET_CHANNEL_IDS}')
    print('='*50)
    
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=f"{len(bot.guilds)} Radio Stations"))
    print('✅  Presence Updated.\n')

import wavelink

async def setup_hook():
    print("🔌  Connecting to Local Lavalink node...")
    node = wavelink.Node(
        identifier="local_lavalink",
        uri="http://lavalink:2333",
        password="RadioBotSecure2026"
    )
    await wavelink.Pool.connect(nodes=[node], client=bot, cache_capacity=100)
    print("✅  Lavalink connected!")

bot.setup_hook = setup_hook

@bot.command()
async def reload(ctx, extension):
    try:
        await bot.reload_extension(extension)
        await ctx.send(f"✅ Berhasil reload: **{extension}**")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

async def main():
    print('🔄  Booting system...')
    for ext in ['cogs.radio', 'cogs.web', 'cogs.music']:
        try:
            await bot.load_extension(ext)
            print(f'📦  Module [{ext}]    : LOADED SUCCESS')
        except Exception as e:
            print(f'❌  Module [{ext}]    : FAILED ({e})')
    
    if TOKEN:
        print('🔑  Token found. Logging in...')
        await bot.start(TOKEN)
    else:
        print("❌ CRITICAL ERROR: Token hilang! Cek file .env")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑  Shutting down system...")