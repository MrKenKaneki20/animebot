# bot.py
import discord
from discord.ext import commands
import random
import asyncio
import io
try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
except Exception:
    Image = None
import math
import aiosqlite
import os
from character import random_character, get_character_image, CHARACTERS, RARITY_WEIGHTS


OWNER_ID = 826736555459739648  # replace with your Discord user ID

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

spawned_character = None
spawn_channel = None
message_counter = 0
current_battles = {}  # {challenger_id: {"opponent_id":..., "stage":..., "choices":{}}}
bot_locked = False  # Lock state for admin control

RARITY_EMOJIS = {
    "Common": "",
    "Rare": "🎯",
    "Epic": "💎",
    "Legendary": "✨",
    "Mythic": "🌟"
}

# -------------------- HELPERS --------------------
def generate_stats(rarity):
    base = {"Common":50,"Rare":70,"Epic":90,"Legendary":110,"Mythic":130}
    hp = base[rarity] + random.randint(0,20)
    attack = base[rarity]//2 + random.randint(0,15)
    defense = base[rarity]//2 + random.randint(0,15)
    speed = random.randint(10,50)
    iv = random.randint(0,31)
    return hp, attack, defense, speed, iv

def make_hint(name: str):
    result = []
    new_word = True
    for ch in name:
        if ch == " ":
            result.append("  ")
            new_word = True
        elif ch.isalpha():
            if new_word:
                result.append(ch.upper() + " ")
                new_word = False
            else:
                result.append("_ ")
        else:
            result.append(ch + " ")
    return "".join(result).strip()

def hp_bar(current, total, length=10):
    filled = round(current / total * length)
    empty = length - filled
    return "🟩"*filled + "🟥"*empty + f" {current}/{total}"

def create_spawn_embed(character):
    """Create an embed for a spawned character with rarity-based effects."""
    rarity = character.get("rarity", "Common")
    emoji = RARITY_EMOJIS.get(rarity, "")
    
    if rarity == "Mythic":
        # Mythic: special animation with stars and bold text
        title = f"🌟✨ **MYTHIC SPAWN!** ✨🌟"
        color = discord.Color.from_rgb(255, 215, 0)  # Gold
        description = "**⚡ A LEGENDARY BEING HAS APPEARED! ⚡**\n\nType `!acatch <name>` to catch it!"
    elif rarity == "Legendary":
        # Legendary: bright purple with emphasis
        title = f"✨ **LEGENDARY SPAWN!** ✨"
        color = discord.Color.from_rgb(138, 43, 226)  # Blue-purple
        description = "**A rare legend has appeared!**\n\nType `!acatch <name>` to catch it!"
    else:
        # Normal rarities
        title = f"✨ A wild anime character appeared!"
        color = discord.Color.purple()
        description = f"Type `!acatch <name>` to catch it!"
    
    embed = discord.Embed(title=title, description=description, color=color)
    return embed


def _compose_spawn_image(image_path, rarity=None):
    """Return a BytesIO image (PNG or animated GIF) for spawn with glow/animation based on rarity."""
    if Image is None:
        return None
    try:
        img = Image.open(image_path).convert("RGBA")
        w, h = img.size
        pad = 120
        canvas_size = (w + pad, h + pad)
        base = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
        cx = (canvas_size[0] - w) // 2
        cy = 30

        # rarity color
        rc = {"Common": (120,120,120), "Rare": (30,144,255), "Epic": (147,112,219), "Legendary": (138,43,226), "Mythic": (255,215,0)}
        color = rc.get(rarity, (100,100,100))

        # create colored mask from image alpha
        glow = Image.new("RGBA", img.size, color + (255,))
        mask = img.split()[3]
        glow.putalpha(mask)

        # Legendary/Mythic: animated pulsing glow (GIF)
        if rarity in ("Legendary", "Mythic"):
            frames = []
            for i in range(6):
                radius = 6 + i * 3
                alpha_mul = 120 + int(100 * (0.5 + 0.5 * math.sin(i / 6.0 * 2 * math.pi)))
                g = glow.copy()
                g = g.filter(ImageFilter.GaussianBlur(radius=radius))
                # dim/brighten by multiplying alpha
                alpha = g.split()[3].point(lambda p: min(255, int(p * (alpha_mul / 255.0))))
                g.putalpha(alpha)
                frame = base.copy()
                frame.paste(g, (cx, cy), g)
                frame.paste(img, (cx, cy), img)
                frames.append(frame)
            bio = io.BytesIO()
            frames[0].save(bio, format="GIF", save_all=True, append_images=frames[1:], loop=0, duration=90, disposal=2)
            bio.seek(0)
            return bio
        else:
            g = glow.filter(ImageFilter.GaussianBlur(radius=18))
            base.paste(g, (cx, cy), g)
            base.paste(img, (cx, cy), img)
            bio = io.BytesIO()
            base.save(bio, format="PNG")
            bio.seek(0)
            return bio
    except Exception:
        return None

# -------------------- EVENTS --------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    async with aiosqlite.connect("anime.db") as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS collection (
            user_id INTEGER,
            character_name TEXT,
            anime TEXT,
            rarity TEXT,
            hp INTEGER,
            attack INTEGER,
            defense INTEGER,
            speed INTEGER,
            iv INTEGER
        )
        """)
        # Ensure columns for leveling exist (ALTER will fail if column exists; ignore errors)
        try:
            await db.execute("ALTER TABLE collection ADD COLUMN level INTEGER DEFAULT 1")
            await db.execute("ALTER TABLE collection ADD COLUMN exp INTEGER DEFAULT 0")
        except Exception:
            pass
        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_wallet (
            user_id INTEGER PRIMARY KEY,
            coins INTEGER DEFAULT 0
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_profile (
            user_id INTEGER PRIMARY KEY,
            level INTEGER DEFAULT 1,
            exp INTEGER DEFAULT 0
        )
        """)
        await db.commit()
    print("Bot is ready!")

@bot.event
async def on_message(message):
    global message_counter, spawned_character, spawn_channel, bot_locked

    if message.author.bot:
        return
    
    # Check if bot is locked (commands still process for admin)
    if bot_locked and message.author.id != OWNER_ID:
        return

    message_counter += 1

    if not spawned_character and message_counter >= random.randint(25,40):
        spawned_character = random_character()
        spawn_channel = message.channel
        message_counter = 0

        embed = create_spawn_embed(spawned_character)
        image_path = get_character_image(spawned_character["name"])
        if os.path.exists(image_path):
            composed = _compose_spawn_image(image_path, spawned_character.get("rarity"))
            if composed:
                fname = "spawn.gif" if spawned_character.get("rarity") in ("Legendary","Mythic") else "spawn.png"
                await spawn_channel.send(file=discord.File(fp=composed, filename=fname), embed=embed)
            else:
                file = discord.File(image_path, filename="character.png")
                embed.set_image(url="attachment://character.png")
                await spawn_channel.send(file=file, embed=embed)
        else:
            await spawn_channel.send(embed=embed)

    await bot.process_commands(message)

# -------------------- COMMANDS --------------------
@bot.command(aliases=["ac"])
async def acatch(ctx, *, name: str):
    global spawned_character, spawn_channel
    try:
        if not spawned_character or ctx.channel != spawn_channel:
            return await ctx.send("❌ No character to catch here!")

        user_input = name.strip().lower()
        char_name = spawned_character["name"].strip().lower()
        char_first_word = char_name.split()[0]  # Get first word only

        if user_input != char_first_word and user_input != char_name:
            return await ctx.send("❌ Wrong name!")

        hp, attack, defense, speed, iv = generate_stats(spawned_character["rarity"])

        async with aiosqlite.connect("anime.db") as db:
            await db.execute(
                "INSERT INTO collection (user_id, character_name, anime, rarity, hp, attack, defense, speed, iv) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ctx.author.id, spawned_character["name"], spawned_character["anime"], spawned_character["rarity"], hp, attack, defense, speed, iv)
            )
            await db.execute(
                "INSERT INTO user_wallet (user_id, coins) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET coins = coins + 25",
                (ctx.author.id, 25)
            )
            await db.commit()

            # fetch the ROWID of the just-inserted collection entry
            cursor = await db.execute(
                "SELECT ROWID FROM collection WHERE user_id = ? AND character_name = ? ORDER BY ROWID DESC LIMIT 1",
                (ctx.author.id, spawned_character["name"]) 
            )
            rid_row = await cursor.fetchone()
            rowid = rid_row[0] if rid_row else None

        emoji = RARITY_EMOJIS.get(spawned_character["rarity"], "")
        embed = discord.Embed(title=f"🎉 {emoji} You caught {spawned_character['name']}!", description=f"Anime: {spawned_character['anime']} | Rarity: **{spawned_character['rarity']}**", color=discord.Color.green())
        image_path = get_character_image(spawned_character["name"])
        if os.path.exists(image_path):
            file = discord.File(image_path, filename="character.png")
            embed.set_image(url="attachment://character.png")
            await ctx.send(file=file, embed=embed)
        else:
            await ctx.send(embed=embed)

        # Ask user via message if they want to immediately release this character for +5 coins
        try:
            await ctx.send(f"{ctx.author.mention}, reply with `release` (or `r`) to release **{spawned_character['name']}** and get 💵 5 coins, or `keep` (or `k`) to keep it. You have 30 seconds.")

            def check_msg(m):
                return m.author.id == ctx.author.id and m.channel == ctx.channel and m.content.lower() in ("release", "keep", "r", "k", "y", "n", "yes", "no")

            msg = await bot.wait_for("message", timeout=30.0, check=check_msg)
            resp = msg.content.lower()
            if resp in ("release", "r", "y", "yes"):
                async with aiosqlite.connect("anime.db") as db:
                    if rowid:
                        await db.execute("DELETE FROM collection WHERE ROWID = ?", (rowid,))
                    await db.execute(
                        "INSERT INTO user_wallet (user_id, coins) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET coins = coins + 5",
                        (ctx.author.id, 5)
                    )
                    await db.commit()
                await ctx.send(f"💔 You released **{spawned_character['name']}** and received 💵 5 coins.")
            else:
                await ctx.send("✅ Kept your new character. Enjoy!")
        except asyncio.TimeoutError:
            await ctx.send("⌛ No response. Kept your new character.")

        spawned_character = None
        spawn_channel = None
    except Exception as e:
        print(f"Error in acatch: {e}")
        import traceback
        traceback.print_exc()
        await ctx.send(f"❌ Error: {e}")

@bot.command()
async def hint(ctx):
    if not spawned_character or ctx.channel != spawn_channel:
        return await ctx.send("❌ No character to hint right now.")
    await ctx.send(f"💡 Hint: {make_hint(spawned_character['name'])}")

@bot.command()
async def collection(ctx):
    async with aiosqlite.connect("anime.db") as db:
        cursor = await db.execute(
            "SELECT character_name, rarity, anime, COALESCE(level,1) FROM collection WHERE user_id = ?",
            (ctx.author.id,)
        )
        rows = await cursor.fetchall()
    if not rows:
        return await ctx.send("📦 Your collection is empty.")
    embed = discord.Embed(title=f"📦 {ctx.author.display_name}'s Anime Collection", color=discord.Color.green())
    for idx, (name, rarity, anime, level) in enumerate(rows, start=1):
        emoji = RARITY_EMOJIS.get(rarity, "")
        embed.add_field(name=f"{idx}. {emoji} {name}", value=f"Anime: {anime} | Rarity: **{rarity}** | Lvl: **{level}**", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def info(ctx, index: int):
    async with aiosqlite.connect("anime.db") as db:
        cursor = await db.execute(
            "SELECT character_name, anime, rarity, hp, attack, defense, speed, iv, COALESCE(level,1), COALESCE(exp,0) FROM collection WHERE user_id = ?",
            (ctx.author.id,)
        )
        rows = await cursor.fetchall()
    if not rows: return await ctx.send("📦 Your collection is empty.")
    if index < 1 or index > len(rows): return await ctx.send(f"❌ Invalid number. You have {len(rows)} characters.")
    name, anime, rarity, hp, attack, defense, speed, iv, level, exp = rows[index-1]
    emoji = RARITY_EMOJIS.get(rarity, "")
    embed = discord.Embed(title=f"{emoji} {name}", description=f"Anime: {anime}\nRarity: **{rarity}** | Level: **{level}**", color=discord.Color.blue())
    embed.add_field(name="Stats", value=f"HP:{hp}\nAttack:{attack}\nDefense:{defense}\nSpeed:{speed}\nIV:{iv}", inline=False)
    embed.add_field(name="Experience", value=f"Level: {level}\nExp: {exp}/{level*100}", inline=False)
    image_path = get_character_image(name)
    if os.path.exists(image_path):
        file = discord.File(image_path, filename="character.png")
        embed.set_image(url="attachment://character.png")
        await ctx.send(file=file, embed=embed)
    else:
        await ctx.send(embed=embed)

@bot.command()
async def cc(ctx):
    """Clear your collection with confirmation."""
    async with aiosqlite.connect("anime.db") as db:
        cursor = await db.execute("SELECT COUNT(*) FROM collection WHERE user_id = ?", (ctx.author.id,))
        count_row = await cursor.fetchone()
        count = count_row[0] if count_row else 0
    
    if count == 0:
        return await ctx.send("📦 Your collection is already empty.")
    
    # Ask for confirmation
    await ctx.send(f"{ctx.author.mention}, are you sure you want to clear your entire collection ({count} characters)? **This cannot be undone!** Reply with `yes` (or `y`) to confirm, or `no` (or `n`) to cancel. You have 30 seconds.")
    
    def check_msg(m):
        return m.author.id == ctx.author.id and m.channel == ctx.channel and m.content.lower() in ("yes", "no", "y", "n")
    
    try:
        msg = await bot.wait_for("message", timeout=30.0, check=check_msg)
        resp = msg.content.lower()
        if resp in ("yes", "y"):
            async with aiosqlite.connect("anime.db") as db:
                await db.execute("DELETE FROM collection WHERE user_id = ?", (ctx.author.id,))
                await db.commit()
            await ctx.send(f"🗑️ Your anime collection ({count} characters) has been cleared!")
        else:
            await ctx.send("❌ Clear collection cancelled.")
    except asyncio.TimeoutError:
        await ctx.send("⌛ No response. Clear collection cancelled.")

@bot.command()
async def leaderboard(ctx):
    async with aiosqlite.connect("anime.db") as db:
        # Combine users from collection and wallet, show coins and card counts
        cursor = await db.execute("""
            SELECT u.user_id AS user_id,
                   COALESCE(uw.coins, 0) AS coins,
                   COALESCE(c.total, 0) AS total_cards
            FROM (
                SELECT user_id FROM collection
                UNION
                SELECT user_id FROM user_wallet
            ) u
            LEFT JOIN user_wallet uw ON u.user_id = uw.user_id
            LEFT JOIN (
                SELECT user_id, COUNT(*) AS total FROM collection GROUP BY user_id
            ) c ON u.user_id = c.user_id
            ORDER BY coins DESC, total_cards DESC
            LIMIT 10
        """)
        rows = await cursor.fetchall()
    if not rows:
        return await ctx.send("No collection or wallet data yet!")
    embed = discord.Embed(title="💰 Richest Collectors", color=discord.Color.gold())
    for i, (user_id, coins, total_cards) in enumerate(rows, start=1):
        try:
            user = await bot.fetch_user(user_id)
            name = user.display_name
        except:
            name = f"User ID {user_id}"
        embed.add_field(name=f"{i}. {name}", value=f"Coins: 💵 {coins} | Cards: {total_cards}", inline=False)
    await ctx.send(embed=embed)


@bot.command()
async def r(ctx, index: int):
    """Release a character from your collection by its index and earn 5 coins."""
    async with aiosqlite.connect("anime.db") as db:
        cursor = await db.execute("SELECT ROWID, character_name, rarity FROM collection WHERE user_id = ?", (ctx.author.id,))
        rows = await cursor.fetchall()
        if not rows:
            return await ctx.send("📦 Your collection is empty.")
        if index < 1 or index > len(rows):
            return await ctx.send(f"❌ Invalid number. You have {len(rows)} characters.")
        row = rows[index-1]
        rowid = row[0]
        char_name = row[1]
        rarity = row[2]
        
        # Ask for confirmation
        emoji = RARITY_EMOJIS.get(rarity, "")
        await ctx.send(f"{ctx.author.mention}, are you sure you want to release **{emoji} {char_name}** (Rarity: {rarity})? Reply with `yes` (or `y`) to confirm, or `no` (or `n`) to cancel. You have 30 seconds.")
        
        def check_msg(m):
            return m.author.id == ctx.author.id and m.channel == ctx.channel and m.content.lower() in ("yes", "no", "y", "n")
        
        try:
            msg = await bot.wait_for("message", timeout=30.0, check=check_msg)
            resp = msg.content.lower()
            if resp in ("yes", "y"):
                # Delete the character
                async with aiosqlite.connect("anime.db") as db:
                    await db.execute("DELETE FROM collection WHERE ROWID = ?", (rowid,))
                    # Award 5 coins
                    await db.execute(
                        "INSERT INTO user_wallet (user_id, coins) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET coins = coins + 5",
                        (ctx.author.id, 5)
                    )
                    await db.commit()
                await ctx.send(f"💔 You released **{emoji} {char_name}** and earned 💵 5 coins.")
            else:
                await ctx.send("❌ Release cancelled.")
        except asyncio.TimeoutError:
            await ctx.send("⌛ No response. Release cancelled.")

# -------------------- SPAWN --------------------
@bot.command()
async def spawn(ctx):
    global spawned_character, spawn_channel, message_counter
    if ctx.author.id != OWNER_ID:
        return await ctx.send("❌ You are not allowed to use this command!")
    spawned_character = random_character()
    spawn_channel = ctx.channel
    message_counter = 0
    embed = create_spawn_embed(spawned_character)
    # Add forced spawn note to description
    embed.description = f"**(Forced Spawn)**\n\n{embed.description}"
    image_path = get_character_image(spawned_character["name"])
    if os.path.exists(image_path):
        composed = _compose_spawn_image(image_path, spawned_character.get("rarity"))
        if composed:
            fname = "spawn.gif" if spawned_character.get("rarity") in ("Legendary","Mythic") else "spawn.png"
            await spawn_channel.send(file=discord.File(fp=composed, filename=fname), embed=embed)
        else:
            file = discord.File(image_path, filename="character.png")
            embed.set_image(url="attachment://character.png")
            await spawn_channel.send(file=file, embed=embed)
    else:
        await spawn_channel.send(embed=embed)

# -------------------- 1v1 BATTLE --------------------
@bot.command()
async def battle(ctx, opponent: discord.Member):
    if ctx.author.id == opponent.id:
        return await ctx.send("❌ You cannot battle yourself!")
    if ctx.author.id in current_battles or opponent.id in current_battles:
        return await ctx.send("❌ One of the players is already in a battle!")
    msg = await ctx.send(f"⚔️ {ctx.author.mention} has challenged {opponent.mention}! React ✅ to accept or ❌ to decline.")
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

    def check(reaction, user):
        return user.id == opponent.id and str(reaction.emoji) in ["✅","❌"] and reaction.message.id == msg.id

    try:
        reaction, user = await bot.wait_for("reaction_add", timeout=60.0, check=check)
        if str(reaction.emoji) == "❌":
            return await ctx.send("❌ Battle declined.")
        await ctx.send("✅ Battle accepted! Starting...")
        # Both players choose character
        current_battles[ctx.author.id] = {"opponent_id": opponent.id, "stage": "choose", "choices":{}}
        current_battles[opponent.id] = {"opponent_id": ctx.author.id, "stage": "choose", "choices":{}}
        await ctx.send(f"{ctx.author.mention} and {opponent.mention}, pick your fighter using `!fight <index>` from your collection.")
    except:
        await ctx.send("❌ Battle request timed out.")

@bot.command()
async def fight(ctx, index: int):
    if ctx.author.id not in current_battles:
        return await ctx.send("❌ You are not in a battle.")
    battle = current_battles[ctx.author.id]
    if battle["stage"] != "choose":
        return await ctx.send("❌ You already chose your fighter or battle not started.")
    async with aiosqlite.connect("anime.db") as db:
        cursor = await db.execute(
            "SELECT ROWID, user_id, character_name, anime, rarity, hp, attack, defense, speed, iv, COALESCE(level,1) as level, COALESCE(exp,0) as exp FROM collection WHERE user_id = ?",
            (ctx.author.id,)
        )
        rows = await cursor.fetchall()
    if not rows or index < 1 or index > len(rows):
        return await ctx.send("❌ Invalid index.")
    row = rows[index-1]
    # map row to dict for clarity: ROWID, user_id, character_name, anime, rarity, hp, attack, defense, speed, iv, level, exp
    chosen = {
        "rowid": row[0],
        "user_id": row[1],
        "name": row[2],
        "anime": row[3],
        "rarity": row[4],
        "hp": row[5],
        "attack": row[6],
        "defense": row[7],
        "speed": row[8],
        "iv": row[9],
        "level": row[10],
        "exp": row[11]
    }
    battle["choices"][ctx.author.id] = chosen
    await ctx.send(f"{ctx.author.mention} picked {chosen['name']}!")
    # Mirror choice into opponent's battle dict so both sides can see choices
    opp_id = battle["opponent_id"]
    if opp_id in current_battles:
        current_battles[opp_id]["choices"][ctx.author.id] = chosen

    # If opponent already picked, start battle
    if opp_id in battle["choices"]:
        await start_battle(ctx, ctx.author.id, opp_id)

def _compose_battle_image(p1_path, p2_path, p1_name, p1_hp, p1_max, p2_name, p2_hp, p2_max, p1_rarity=None, p2_rarity=None):
    """Return a BytesIO PNG of two styled character cards side-by-side with health bars above each.

    Adds rounded portraits, rarity-colored frames, level badges and nicer fonts when Pillow is available.
    """
    if Image is None:
        return None
    try:
        def load_or_placeholder(path):
            if path and os.path.exists(path):
                img = Image.open(path).convert("RGBA")
            else:
                img = Image.new("RGBA", (320, 320), (90,90,90,255))
            return img

        img1 = load_or_placeholder(p1_path)
        img2 = load_or_placeholder(p2_path)

        # target heights
        target_h = 260
        def resize_to_height(img, h):
            w, _ = img.size
            new_w = int(w * (h / img.size[1]))
            return img.resize((new_w, h))

        img1 = resize_to_height(img1, target_h)
        img2 = resize_to_height(img2, target_h)

        padding = 28
        bar_h = 30

        # font: prefer Arial, fallback to default
        try:
            font = ImageFont.truetype("arial.ttf", 16)
            font_bold = ImageFont.truetype("arialbd.ttf", 18)
        except Exception:
            font = ImageFont.load_default()
            font_bold = font

        # rarity colors
        rc = {"Common": (120,120,120), "Rare": (30,144,255), "Epic": (147,112,219), "Legendary": (138,43,226), "Mythic": (255,215,0)}
        col1 = rc.get(p1_rarity, (80,80,80))
        col2 = rc.get(p2_rarity, (80,80,80))

        canvas_w = img1.width + img2.width + padding * 3
        canvas_h = target_h + bar_h + 70
        canvas = Image.new("RGBA", (canvas_w, canvas_h), (34,36,40,255))
        draw = ImageDraw.Draw(canvas)

        x1 = padding
        x2 = padding * 2 + img1.width

        # helper: rounded mask
        def rounded(img, radius=30):
            mask = Image.new('L', img.size, 0)
            drawm = ImageDraw.Draw(mask)
            drawm.rounded_rectangle([0,0,img.size[0],img.size[1]], radius=radius, fill=255)
            out = Image.new('RGBA', img.size, (0,0,0,0))
            out.paste(img, (0,0), mask)
            return out

        rim = 6
        r1 = rounded(img1, radius=24)
        r2 = rounded(img2, radius=24)

        # draw health bar function with nicer style
        def draw_health_bar(x, y, width, hp, hp_max, color):
            # background bar
            draw.rounded_rectangle([x, y, x+width, y+bar_h], radius=12, fill=(60,60,60,255))
            ratio = max(0.0, min(1.0, hp / max(1, hp_max)))
            filled = int(width * ratio)
            fill_color = color if isinstance(color, tuple) else (80,200,120)
            draw.rounded_rectangle([x, y, x+filled, y+bar_h], radius=12, fill=fill_color)
            # text
            txt = f"{hp}/{hp_max}"
            tw, th = draw.textsize(txt, font=font_bold)
            draw.text((x+width - tw - 8, y + (bar_h-th)//2), txt, font=font_bold, fill=(255,255,255,255))

        # draw bars above images
        draw_health_bar(x1, 10, img1.width, p1_hp, p1_max, col1)
        draw_health_bar(x2, 10, img2.width, p2_hp, p2_max, col2)

        # paste framed images with border
        # border rectangles
        draw.rounded_rectangle([x1-rim, 10+bar_h+8-rim, x1+img1.width+rim, 10+bar_h+8+img1.height+rim], radius=26, outline=col1+(200,), width=4)
        draw.rounded_rectangle([x2-rim, 10+bar_h+8-rim, x2+img2.width+rim, 10+bar_h+8+img2.height+rim], radius=26, outline=col2+(200,), width=4)

        canvas.paste(r1, (x1, 10 + bar_h + 8), r1)
        canvas.paste(r2, (x2, 10 + bar_h + 8), r2)

        # level badges (use small circles)
        def draw_level_badge(cx, cy, lvl, color):
            badge_r = 18
            draw.ellipse([cx-badge_r, cy-badge_r, cx+badge_r, cy+badge_r], fill=color)
            lvtxt = str(lvl)
            tw, th = draw.textsize(lvtxt, font=font_bold)
            draw.text((cx - tw/2, cy - th/2), lvtxt, font=font_bold, fill=(0,0,0,255))

        # attempt to position badges top-left of each portrait
        draw_level_badge(x1+26, 10 + bar_h + 8 + 26, 1, col1)
        draw_level_badge(x2+26, 10 + bar_h + 8 + 26, 1, col2)

        # draw names under images
        n1y = 10 + bar_h + 8 + img1.height + 6
        n2y = 10 + bar_h + 8 + img2.height + 6
        draw.text((x1, n1y), p1_name, font=font_bold, fill=(255,255,255,255))
        draw.text((x2, n2y), p2_name, font=font_bold, fill=(255,255,255,255))

        bio = io.BytesIO()
        canvas.save(bio, format="PNG")
        bio.seek(0)
        return bio
    except Exception:
        return None

async def start_battle(ctx, player1_id, player2_id):
    p1 = current_battles[player1_id]["choices"][player1_id]
    p2 = current_battles[player2_id]["choices"][player2_id]

    p1_hp, p2_hp = p1["hp"], p2["hp"]
    turn = 0
    # Send one battle message and edit it each turn to animate health bars and logs
    header = f"⚔️ Battle begins between {p1['name']} and {p2['name']}!"
    round_header = f"**(Round {turn+1})**\n"
    stats_block = (
        f"{p1['name']} — Level {p1.get('level',1)} | Speed: {p1.get('speed',0)}\n"
        f"{p2['name']} — Level {p2.get('level',1)} | Speed: {p2.get('speed',0)}\n"
    )

    image_p1 = get_character_image(p1["name"]) if p1.get("name") else None
    image_p2 = get_character_image(p2["name"]) if p2.get("name") else None
    composed = None
    if Image is not None:
        composed = _compose_battle_image(
            image_p1 if image_p1 and os.path.exists(image_p1) else None,
            image_p2 if image_p2 and os.path.exists(image_p2) else None,
            p1["name"], p1_hp, p1["hp"], p2["name"], p2_hp, p2["hp"],
            p1.get("rarity"), p2.get("rarity")
        )

    # initial embed with ascii health bars and polished styling
    rarity_color_p1 = {"Common": discord.Color.greyple(), "Rare": discord.Color.blue(), "Epic": discord.Color.purple(), "Legendary": discord.Color.from_rgb(138, 43, 226), "Mythic": discord.Color.gold()}
    color = rarity_color_p1.get(p1.get("rarity", "Common"), discord.Color.dark_blue())
    
    # Send battle image (composed or individual fallback)
    if composed:
        await ctx.send(file=discord.File(fp=composed, filename="battle.png"))
    else:
        # Fallback: send individual character images
        files_to_send = []
        if image_p1 and os.path.exists(image_p1):
            files_to_send.append(discord.File(image_p1, filename="char1.png"))
        if image_p2 and os.path.exists(image_p2):
            files_to_send.append(discord.File(image_p2, filename="char2.png"))
        if files_to_send:
            await ctx.send(files=files_to_send)
    
    embed = discord.Embed(title=header, color=color)
    embed.description = round_header + stats_block
    embed.add_field(name=f"⚔️ {p1['name']} (Lvl {p1.get('level',1)})", value=hp_bar(p1_hp, p1["hp"]), inline=True)
    embed.add_field(name=f"⚔️ {p2['name']} (Lvl {p2.get('level',1)})", value=hp_bar(p2_hp, p2["hp"]), inline=True)
    embed.set_footer(text="Animating battle... health bars updating")

    battle_msg = await ctx.send(embed=embed)

    # Main loop: edit battle_msg each turn instead of sending new messages
    while p1_hp > 0 and p2_hp > 0:
        attacker, defender = (p1, p2) if turn % 2 == 0 else (p2, p1)
        damage = max(1, attacker["attack"] - defender["defense"])  # simple damage = attack - defense
        # compute target HPs
        if attacker is p1:
            prev_hp = p2_hp
            target_hp = max(p2_hp - damage, 0)
        else:
            prev_hp = p1_hp
            target_hp = max(p1_hp - damage, 0)

        # animate ASCII health bar in 3 frames to smooth the transition
        steps = 3
        for s in range(steps):
            inter_hp = prev_hp - math.ceil((s+1) * (damage / steps))
            inter_hp = max(inter_hp, target_hp)
            if attacker is p1:
                f1 = hp_bar(p1_hp, p1["hp"])
                f2 = hp_bar(inter_hp, p2["hp"])
                embed.clear_fields()
                embed.add_field(name=f"⚔️ {p1['name']} (Lvl {p1.get('level',1)})", value=f1, inline=True)
                embed.add_field(name=f"⚔️ {p2['name']} (Lvl {p2.get('level',1)})", value=f2, inline=True)
            else:
                f1 = hp_bar(inter_hp, p1["hp"])
                f2 = hp_bar(p2_hp, p2["hp"])
                embed.clear_fields()
                embed.add_field(name=f"⚔️ {p1['name']} (Lvl {p1.get('level',1)})", value=f1, inline=True)
                embed.add_field(name=f"⚔️ {p2['name']} (Lvl {p2.get('level',1)})", value=f2, inline=True)

            # update round header and stats in description
            embed.description = f"**(Round {turn+1})**\n" + stats_block
            await battle_msg.edit(embed=embed)
            await asyncio.sleep(0.1)

        # apply final hp after animation
        if attacker is p1:
            p2_hp = target_hp
        else:
            p1_hp = target_hp

        # update action log to footer and final health bars AT THE SAME TIME
        action_line = f"💥 {attacker['name']} attacks → {damage} damage to {defender['name']}!"
        embed.set_footer(text=action_line)
        emoji1 = RARITY_EMOJIS.get(p1.get("rarity", "Common"), "")
        emoji2 = RARITY_EMOJIS.get(p2.get("rarity", "Common"), "")
        embed.clear_fields()
        embed.add_field(name=f"{emoji1} {p1['name']} (Lvl {p1.get('level',1)})", value=hp_bar(p1_hp, p1["hp"]), inline=True)
        embed.add_field(name=f"{emoji2} {p2['name']} (Lvl {p2.get('level',1)})", value=hp_bar(p2_hp, p2["hp"]), inline=True)
        await battle_msg.edit(embed=embed)

        # small pause before next round
        await asyncio.sleep(1.0)
        turn += 1

    winner = p1 if p1_hp > 0 else p2
    loser = p2 if winner is p1 else p1
    
    # Get user objects for pinging
    try:
        winner_user = await bot.fetch_user(winner["user_id"]) if winner.get("user_id") else None
        winner_name = winner_user.mention if winner_user else f"User {winner.get('user_id')}"
    except:
        winner_name = f"User {winner.get('user_id')}"
    
    try:
        loser_user = await bot.fetch_user(loser["user_id"]) if loser.get("user_id") else None
        loser_name = loser_user.mention if loser_user else f"User {loser.get('user_id')}"
    except:
        loser_name = f"User {loser.get('user_id')}"
    
    await ctx.send(f"🏆 Battle Over!\n{winner_name} wins and {loser_name} loses")

    # Award XP: character and user
    char_xp = 20
    user_xp = 10
    # bonus by rarity
    rarity_bonus = {"Common": 0, "Rare": 5, "Epic": 10, "Legendary": 20, "Mythic": 40}
    char_xp += rarity_bonus.get(winner.get("rarity", "Common"), 0)

    async with aiosqlite.connect("anime.db") as db:
        # update character exp and level for winner character (collection)
        # fetch current exp/level for safety
        await db.execute("UPDATE collection SET exp = COALESCE(exp,0) + ? WHERE ROWID = ?", (char_xp, winner["rowid"]))
        cursor = await db.execute("SELECT COALESCE(level,1), COALESCE(exp,0) FROM collection WHERE ROWID = ?", (winner["rowid"],))
        crow = await cursor.fetchone()
        if crow:
            clevel, cexp = crow[0], crow[1]
            leveled = 0
            while cexp >= clevel * 100:
                cexp -= clevel * 100
                clevel += 1
                leveled += 1
            if leveled > 0:
                await db.execute("UPDATE collection SET level = ?, exp = ? WHERE ROWID = ?", (clevel, cexp, winner["rowid"]))
            else:
                await db.execute("UPDATE collection SET exp = ? WHERE ROWID = ?", (cexp, winner["rowid"]))

        # update user_profile for winner user
        # First ensure user exists
        await db.execute("INSERT OR IGNORE INTO user_profile (user_id, level, exp) VALUES (?, 1, 0)", (winner["user_id"],))
        # Then update exp
        await db.execute("UPDATE user_profile SET exp = exp + ? WHERE user_id = ?", (user_xp, winner["user_id"]))
        cursor = await db.execute("SELECT level, exp FROM user_profile WHERE user_id = ?", (winner["user_id"],))
        urow = await cursor.fetchone()
        if urow:
            ulevel, uexp = urow[0], urow[1]
            u_leveled = 0
            while uexp >= ulevel * 100:
                uexp -= ulevel * 100
                ulevel += 1
                u_leveled += 1
            if u_leveled > 0:
                await db.execute("UPDATE user_profile SET level = ?, exp = ? WHERE user_id = ?", (ulevel, uexp, winner["user_id"]))
            else:
                await db.execute("UPDATE user_profile SET exp = ? WHERE user_id = ?", (uexp, winner["user_id"]))

        await db.commit()

    # Inform about XP gains with clean format
    await ctx.send(f"✨ XP Gained:\n{winner['name']} → {char_xp} Char XP +{user_xp} User XP")

    # Clean up
    del current_battles[player1_id]
    del current_battles[player2_id]

@bot.command()
async def bal(ctx):
    async with aiosqlite.connect("anime.db") as db:
        cursor = await db.execute(
            "SELECT coins FROM user_wallet WHERE user_id = ?",
            (ctx.author.id,)
        )
        row = await cursor.fetchone()
    coins = row[0] if row else 0
    embed = discord.Embed(title="💰 Wallet", description=f"**{ctx.author.display_name}**'s Balance", color=discord.Color.gold())
    embed.add_field(name="Coins", value=f"💵 {coins}")
    await ctx.send(embed=embed)

@bot.command()
async def profile(ctx):
    async with aiosqlite.connect("anime.db") as db:
        cursor = await db.execute(
            "SELECT COALESCE(level,1), COALESCE(exp,0) FROM user_profile WHERE user_id = ?",
            (ctx.author.id,)
        )
        row = await cursor.fetchone()
    level = row[0] if row else 1
    exp = row[1] if row else 0
    embed = discord.Embed(title="👤 Profile", description=f"**{ctx.author.display_name}**'s Stats", color=discord.Color.purple())
    embed.add_field(name="Level", value=f"**{level}**", inline=True)
    embed.add_field(name="Experience", value=f"{exp}/{level*100}", inline=True)
    embed.set_thumbnail(url=ctx.author.avatar.url)
    await ctx.send(embed=embed)

# -------------------- ADMIN COMMANDS --------------------
@bot.command()
async def commands(ctx):
    """Display all available commands for playing the bot."""
    embed = discord.Embed(
        title=f"📖 {bot.user.name} - Commands Guide",
        description="Complete guide to all available commands and features",
        color=discord.Color.from_rgb(88, 101, 242)
    )
    embed.set_thumbnail(url=bot.user.avatar.url)
    
    # Catching & Collection
    embed.add_field(
        name="🎯 **Catching & Collection**",
        value=(
            "`!acatch <name>` / `!ac` - Catch spawned character (+25 coins)\n"
            "`!hint` - Get a hint for character name\n"
            "`!collection` - View all your characters\n"
            "`!info <idx>` - View character details & stats\n"
            "`!cc` - Clear collection (confirmation required)"
        ),
        inline=False
    )
    
    # Battles
    embed.add_field(
        name="⚔️ **Battles & Combat**",
        value=(
            "`!battle @user` - Challenge someone to battle\n"
            "`!fight <idx>` - Pick your fighter\n"
            "`!flee` - Give up & lose battle"
        ),
        inline=False
    )
    
    # Economy
    embed.add_field(
        name="💰 **Economy & Profile**",
        value=(
            "`!bal` - Check coin balance\n"
            "`!profile` - View your profile & level\n"
            "`!leaderboard` - Top 10 richest players"
        ),
        inline=False
    )
    
    # Release
    embed.add_field(
        name="💔 **Release Characters**",
        value="`!r <idx>` - Release character (+5 coins, requires confirmation)",
        inline=False
    )
    
    embed.add_field(
        name="ℹ️ **Quick Tips**",
        value=(
            "🌟 Random character spawns every 25-40 messages\n"
            "⭐ Rarity affects stats and XP rewards\n"
            "🏆 Win battles to level up character & account\n"
            "💵 Earn coins by catching and releasing"
        ),
        inline=False
    )
    
    embed.set_footer(text="Use !commands to refresh this guide | Battle your friends and dominate the leaderboard! 🎮")
    await ctx.send(embed=embed)

@bot.command()
async def lock(ctx):
    """Lock the bot (only usable by admin)."""
    global bot_locked
    if ctx.author.id != OWNER_ID:
        return await ctx.send("❌ You are not allowed to use this command!")
    bot_locked = True
    await ctx.send("🔒 Bot is now **locked**. Only you can use commands.")

@bot.command()
async def unlock(ctx):
    """Unlock the bot (only usable by admin)."""
    global bot_locked
    if ctx.author.id != OWNER_ID:
        return await ctx.send("❌ You are not allowed to use this command!")
    bot_locked = False
    await ctx.send("🔓 Bot is now **unlocked**. Everyone can use commands.")

@bot.command()
async def flee(ctx):
    """Flee from an ongoing battle (you lose and opponent wins)."""
    global current_battles
    
    if ctx.author.id not in current_battles:
        return await ctx.send("❌ You are not in a battle!")
    
    battle = current_battles[ctx.author.id]
    opponent_id = battle["opponent_id"]
    
    # Determine winner and loser
    fleeing_user = ctx.author.id
    winning_user = opponent_id
    
    # Get battle choices
    if fleeing_user not in battle["choices"] or winning_user not in battle["choices"]:
        return await ctx.send("❌ Battle hasn't started yet. Use `!fight <index>` first.")
    
    loser_char = battle["choices"][fleeing_user]
    winner_char = battle["choices"][winning_user]
    
    # Get user objects for announcement
    try:
        fleeing_obj = await bot.fetch_user(fleeing_user)
        fleeing_name = fleeing_obj.mention
    except:
        fleeing_name = f"User {fleeing_user}"
    
    try:
        winner_obj = await bot.fetch_user(winning_user)
        winner_name = winner_obj.mention
    except:
        winner_name = f"User {winning_user}"
    
    # Award XP to winner
    char_xp = 20
    user_xp = 10
    rarity_bonus = {"Common": 0, "Rare": 5, "Epic": 10, "Legendary": 20, "Mythic": 40}
    char_xp += rarity_bonus.get(winner_char.get("rarity", "Common"), 0)
    
    async with aiosqlite.connect("anime.db") as db:
        # Update winner character exp and level
        await db.execute("UPDATE collection SET exp = COALESCE(exp,0) + ? WHERE ROWID = ?", (char_xp, winner_char["rowid"]))
        cursor = await db.execute("SELECT COALESCE(level,1), COALESCE(exp,0) FROM collection WHERE ROWID = ?", (winner_char["rowid"],))
        crow = await cursor.fetchone()
        if crow:
            clevel, cexp = crow[0], crow[1]
            leveled = 0
            while cexp >= clevel * 100:
                cexp -= clevel * 100
                clevel += 1
                leveled += 1
            if leveled > 0:
                await db.execute("UPDATE collection SET level = ?, exp = ? WHERE ROWID = ?", (clevel, cexp, winner_char["rowid"]))
            else:
                await db.execute("UPDATE collection SET exp = ? WHERE ROWID = ?", (cexp, winner_char["rowid"]))
        
        # Update winner user profile
        await db.execute("INSERT OR IGNORE INTO user_profile (user_id, level, exp) VALUES (?, 1, 0)", (winning_user,))
        await db.execute("UPDATE user_profile SET exp = exp + ? WHERE user_id = ?", (user_xp, winning_user))
        cursor = await db.execute("SELECT level, exp FROM user_profile WHERE user_id = ?", (winning_user,))
        urow = await cursor.fetchone()
        if urow:
            ulevel, uexp = urow[0], urow[1]
            u_leveled = 0
            while uexp >= ulevel * 100:
                uexp -= ulevel * 100
                ulevel += 1
                u_leveled += 1
            if u_leveled > 0:
                await db.execute("UPDATE user_profile SET level = ?, exp = ? WHERE user_id = ?", (ulevel, uexp, winning_user))
            else:
                await db.execute("UPDATE user_profile SET exp = ? WHERE user_id = ?", (uexp, winning_user))
        
        await db.commit()
    
    # Announce battle end
    await ctx.send(f"⚔️ **Battle Ended!**\n{fleeing_name} fled from battle!\n{winner_name} wins and {fleeing_name} loses")
    await ctx.send(f"✨ XP Gained:\n{winner_char['name']} → {char_xp} Char XP +{user_xp} User XP")
    
    # Clean up battles
    del current_battles[fleeing_user]
    if winning_user in current_battles:
        del current_battles[winning_user]


import os

TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(TOKEN)