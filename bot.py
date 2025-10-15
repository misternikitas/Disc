import discord
from discord.ext import commands
import deepl
from dotenv import load_dotenv
import os
import json
import asyncio

load_dotenv()

DEEPL_AUTH_KEY = os.getenv("DEEPL_API_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))  # optional for error alerts

translator = deepl.Translator(DEEPL_AUTH_KEY)

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True
intents.reactions = True

bot = commands.Bot(command_prefix='!', intents=intents)

LINK_FILE = "linked_channels.json"
FLAG_LANG_MAP = {
    "🇫🇷": "FR", "🇪🇸": "ES", "🇯🇵": "JA", "🇩🇪": "DE", "🇨🇳": "ZH",
    "🇷🇺": "RU", "🇮🇹": "IT", "🇰🇷": "KO", "🇺🇸": "EN-US", "🇬🇧": "EN-GB",
    "🇬🇷": "EL", "🇸🇦": "AR",
}

def load_links():
    if os.path.exists(LINK_FILE):
        with open(LINK_FILE, "r") as f:
            return json.load(f)
    return {}

def save_links(data):
    with open(LINK_FILE, "w") as f:
        json.dump(data, f, indent=4)

linked_channels = load_links()

# ===== WEBHOOK SENDER =====
async def send_webhook(channel, user, text, reply_to=None, auto_delete=False, target_lang=None):
    webhooks = await channel.webhooks()
    webhook = discord.utils.get(webhooks, name="TranslatorBot")
    if webhook is None:
        webhook = await channel.create_webhook(name="TranslatorBot")

    if reply_to:
        # Translate the replied-to message if target_lang is provided
        reply_text = reply_to.content
        if target_lang:
            try:
                reply_text = translator.translate_text(reply_to.content, target_lang=target_lang).text
            except Exception as e:
                print(f"Failed to translate replied message: {e}")
        text = f"↪️ Replying to {reply_to.author.display_name}: {reply_text}\n\n{text}"

    sent_msg = await webhook.send(
        content=text,
        username=user.display_name,
        avatar_url=user.display_avatar.url,
        wait=True
    )

    if auto_delete:
        await asyncio.sleep(60)
        await sent_msg.delete()

# ===== SLASH COMMANDS =====
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ Logged in as {bot.user}")

@bot.tree.command(name="link", description="Link a channel to a target language")
@discord.app_commands.describe(channel="Channel to link", lang="Target language (DeepL code)")
async def link(interaction: discord.Interaction, channel: discord.TextChannel, lang: str):
    await interaction.response.defer(ephemeral=True)
    linked_channels[str(channel.id)] = lang.upper()
    save_links(linked_channels)
    await interaction.followup.send(f"✅ Linked {channel.mention} to `{lang.upper()}`", ephemeral=True)

@bot.tree.command(name="unlink", description="Unlink a channel from translations")
@discord.app_commands.describe(channel="Channel to unlink")
async def unlink(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)
    if str(channel.id) in linked_channels:
        del linked_channels[str(channel.id)]
        save_links(linked_channels)
        await interaction.followup.send(f"❌ Unlinked {channel.mention}", ephemeral=True)
    else:
        await interaction.followup.send("⚠️ That channel isn't linked.", ephemeral=True)

@bot.tree.command(name="listlinks", description="List all linked channels")
async def listlinks(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if not linked_channels:
        await interaction.followup.send("No linked channels yet.", ephemeral=True)
        return
    msg = "\n".join([f"<#{cid}> → {lang}" for cid, lang in linked_channels.items()])
    await interaction.followup.send(f"🌐 **Linked Channels:**\n{msg}", ephemeral=True)

@bot.tree.command(name="translatehistory", description="Translate past messages in a channel to all linked channels")
@discord.app_commands.describe(channel="Source channel", limit="Number of past messages to translate (default 100)")
async def translate_history(interaction: discord.Interaction, channel: discord.TextChannel, limit: int = 100):
    await interaction.response.defer(ephemeral=True)

    if not linked_channels:
        await interaction.followup.send("⚠️ There are no linked channels to send translations to.", ephemeral=True)
        return

    count = 0
    async for msg in channel.history(limit=limit, oldest_first=True):
        if msg.author.bot or msg.webhook_id:
            continue
        for target_id, target_lang in linked_channels.items():
            if int(target_id) == channel.id:
                continue  # skip the source channel
            target_channel = bot.get_channel(int(target_id))
            if not target_channel:
                continue
            reply_msg = msg.reference.resolved if msg.reference else None
            try:
                translated = translator.translate_text(msg.content, target_lang=target_lang)
                await send_webhook(target_channel, msg.author, translated.text, reply_to=reply_msg, target_lang=target_lang)
            except Exception as e:
                print(f"History translation failed: {e}")
                if ADMIN_USER_ID:
                    admin = await bot.fetch_user(ADMIN_USER_ID)
                    await admin.send(f"⚠️ History translation failed in {target_channel.mention}: {e}")
        count += 1

    await interaction.followup.send(f"✅ Translated last {count} messages from {channel.mention}", ephemeral=True)

# ===== AUTO TRANSLATION =====
@bot.event
async def on_message(message):
    if message.author.bot or message.webhook_id:
        return

    await bot.process_commands(message)

    if not linked_channels or str(message.channel.id) not in linked_channels:
        return

    for target_id, target_lang in linked_channels.items():
        if int(target_id) == message.channel.id:
            continue

        target_channel = bot.get_channel(int(target_id))
        if not target_channel:
            continue

        reply_msg = message.reference.resolved if message.reference else None
        try:
            translated = translator.translate_text(message.content, target_lang=target_lang)
            await send_webhook(target_channel, message.author, translated.text, reply_to=reply_msg, target_lang=target_lang)
        except Exception as e:
            print(f"Translation failed: {e}")
            if ADMIN_USER_ID:
                admin = await bot.fetch_user(ADMIN_USER_ID)
                await admin.send(f"⚠️ Translation error in {target_channel.mention}: {e}")

# ===== FLAG REACTION =====
@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return

    emoji = str(payload.emoji)
    if emoji not in FLAG_LANG_MAP:
        return

    lang = FLAG_LANG_MAP[emoji]
    channel = bot.get_channel(payload.channel_id)
    message = await channel.fetch_message(payload.message_id)

    if message.author.bot or message.webhook_id:
        return

    reply_msg = message.reference.resolved if message.reference else None
    try:
        translated = translator.translate_text(message.content, target_lang=lang)
        await send_webhook(channel, message.author, translated.text, reply_to=reply_msg, auto_delete=True, target_lang=lang)
    except Exception as e:
        print(f"Flag translation failed: {e}")
        if ADMIN_USER_ID:
            admin = await bot.fetch_user(ADMIN_USER_ID)
            await admin.send(f"⚠️ Flag translation failed in {channel.mention}: {e}")

bot.run(BOT_TOKEN)
