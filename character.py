import random
import os

CHARACTERS = [
    {"id": 1, "name": "Naruto Uzumaki", "anime": "Naruto"},
    {"id": 2, "name": "Sasuke Uchiha", "anime": "Naruto"},
    {"id": 3, "name": "Luffy", "anime": "One Piece"},
    {"id": 4, "name": "Gojo Satoru", "anime": "Jujutsu Kaisen"},
    {"id": 5, "name": "Goku", "anime": "Dragon Ball"},
    {"id": 6, "name": "Tanjiro Kamado", "anime": "Demon Slayer"},
    {"id": 7, "name": "Mikasa Ackerman", "anime": "Attack on Titan"},
    {"id": 8, "name": "Light Yagami", "anime": "Death Note"},
    {"id": 9, "name": "Saitama", "anime": "One Punch Man"},
    {"id": 10, "name": "Levi Ackerman", "anime": "Attack on Titan"},
    {"id": 11, "name": "Izuku Midoriya", "anime": "My Hero Academia"},
    {"id": 12, "name": "Itsuki Nakano", "anime": "The Quintessential Quintuplets"},
]

RARITY_WEIGHTS = {"Common":55,"Rare":25,"Epic":12,"Legendary":6,"Mythic":2}

def random_character():
    # Pick a random character
    character = random.choice(CHARACTERS).copy()
    # Assign random rarity based on weights
    rarities = list(RARITY_WEIGHTS.keys())
    weights = list(RARITY_WEIGHTS.values())
    character["rarity"] = random.choices(rarities, weights=weights)[0]
    return character

def get_character_image(name):
    filename = name.lower().replace(" ","_") + ".png"
    return os.path.join("images", filename)
