from enum import Enum


class AnsiColour(Enum):
    black = 30
    red = 31
    green = 32
    yellow = 33
    blue = 34
    magenta = 35
    cyan = 36
    white = 37


def coloured(colour: AnsiColour, *args, sep=" "):
    startColour = "\x1b[1;" + str(colour.value) + "m"
    endColour = "\x1b[0m"  # reset
    if len(args) == 1:
        if isinstance(args[0], str):
            return startColour + args[0] + endColour
        return startColour + sep.join(map(str, args[0])) + endColour
    else:
        return startColour + sep.join(map(str, args)) + endColour
