"""Accessible accent colours for the shared dark interface."""

THEMES = {
    "Orange": (255, 139, 110),
    "Blue": (111, 177, 255),
    "Violet": (183, 152, 255),
    "Mint": (111, 218, 181),
    "Rose": (255, 149, 191),
    "Gold": (242, 202, 112),
}


def accent_for(name):
    return THEMES.get(name, THEMES["Orange"])
