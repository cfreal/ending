"""Produces randomized strings."""

import random
import string as _string

from ending.util.misc import SQL_KEYWORDS

__all__ = [
    "string",
    "alpha",
    "lower",
    "hexa",
    "digits",
    "tautology",
    "contradiction",
    "filename",
]


def string(size: int = 8, charset: str = _string.ascii_letters + _string.digits) -> str:
    """Generates a random string.

    Args:
        size (int): Size of the string
        charset (str): Charset to extract characters from

    Returns:
        str: Random string
    """
    return "".join(random.choice(charset) for _ in range(size))


def alpha(size: int = 8) -> str:
    """Generates a random alphabetic string.

    Args:
        size (int): Size of the string

    Returns:
        str: Random alphabetic string
    """
    return string(size, charset=_string.ascii_letters)


def lower(size: int = 8) -> str:
    """Generates a random lowercase string.

    Args:
        size (int): Size of the string

    Returns:
        str: Random lowercase string
    """
    return string(size, charset=_string.ascii_lowercase)


def hexa(size: int = 40) -> str:
    """Generates a random hexadecimal string.

    Args:
        size (int): Size of the string

    Returns:
        str: Random hexadecimal string
    """
    return string(size, charset="0123456789abcdef")


def digits(size: int = 4) -> int:
    """Generates a random int of `size` digits.

    Args:
        size (int): Number of digits in the integer

    Returns:
        int: Random integer
    """
    return random.randrange(10**size, 10 ** (size + 1))


def tautology(size: int = 4) -> str:
    """Returns an SQL tautology in the form `N=N`, with N a number of `size` digits."""
    number = digits(size)
    return f"{number}={number}"


def contradiction(size: int = 4) -> str:
    """Returns an SQL tautology in the form `N=M`, with N and M disctint numbers of
    `size` digits.
    """
    number = digits(size)
    while (other := digits(size)) == number:
        pass
    return f"{number}={other}"


def filename(separator="-"):
    """Generates a filename from an adjective and an animal."""
    return random.choice(_name_left) + separator + random.choice(_name_right)


def identifier(size: int = 4) -> str:
    """Generates a random identifier, garanteed not to be an SQL keyword.

    Args:
        size (int): Size of the identifier

    Returns:
        str: Random identifier
    """
    while identifier := alpha(size):
        if identifier.upper() not in SQL_KEYWORDS:
            break
    return identifier


_name_left = [
    "admiring",
    "adoring",
    "affectionate",
    "agitated",
    "amazing",
    "angry",
    "awesome",
    "beautiful",
    "blissful",
    "bold",
    "boring",
    "brave",
    "busy",
    "charming",
    "clever",
    "cool",
    "compassionate",
    "competent",
    "condescending",
    "confident",
    "cranky",
    "crazy",
    "dazzling",
    "determined",
    "distracted",
    "dreamy",
    "eager",
    "ecstatic",
    "elastic",
    "elated",
    "elegant",
    "eloquent",
    "epic",
    "exciting",
    "fervent",
    "festive",
    "flamboyant",
    "focused",
    "friendly",
    "frosty",
    "funny",
    "gallant",
    "gifted",
    "goofy",
    "gracious",
    "great",
    "happy",
    "hardcore",
    "heuristic",
    "hopeful",
    "hungry",
    "infallible",
    "inspiring",
    "intelligent",
    "interesting",
    "jolly",
    "jovial",
    "keen",
    "kind",
    "laughing",
    "loving",
    "lucid",
    "magical",
    "mystifying",
    "modest",
    "musing",
    "naughty",
    "nervous",
    "nice",
    "nifty",
    "nostalgic",
    "objective",
    "optimistic",
    "peaceful",
    "pedantic",
    "pensive",
    "practical",
    "priceless",
    "quirky",
    "quizzical",
    "recursing",
    "relaxed",
    "reverent",
    "romantic",
    "sad",
    "serene",
    "sharp",
    "silly",
    "sleepy",
    "stoic",
    "strange",
    "stupefied",
    "suspicious",
    "sweet",
    "tender",
    "thirsty",
    "trusting",
    "unruffled",
    "upbeat",
    "vibrant",
    "vigilant",
    "vigorous",
    "wizardly",
    "wonderful",
    "xenodochial",
    "youthful",
    "zealous",
    "zen",
]

_name_right = [
    "aardvark",
    "albatross",
    "alligator",
    "alpaca",
    "ant",
    "anteater",
    "antelope",
    "ape",
    "armadillo",
    "donkey",
    "baboon",
    "badger",
    "barracuda",
    "bat",
    "bear",
    "beaver",
    "bee",
    "bison",
    "boar",
    "buffalo",
    "butterfly",
    "camel",
    "capybara",
    "caribou",
    "cassowary",
    "cat",
    "caterpillar",
    "cattle",
    "chamois",
    "cheetah",
    "chicken",
    "chimpanzee",
    "chinchilla",
    "chough",
    "clam",
    "cobra",
    "cockroach",
    "cod",
    "cormorant",
    "coyote",
    "crab",
    "crane",
    "crocodile",
    "crow",
    "curlew",
    "deer",
    "dinosaur",
    "dog",
    "dogfish",
    "dolphin",
    "dotterel",
    "dove",
    "dragonfly",
    "duck",
    "dugong",
    "dunlin",
    "eagle",
    "echidna",
    "eel",
    "eland",
    "elephant",
    "elk",
    "emu",
    "falcon",
    "ferret",
    "finch",
    "fish",
    "flamingo",
    "fly",
    "fox",
    "frog",
    "gaur",
    "gazelle",
    "gerbil",
    "giraffe",
    "gnat",
    "gnu",
    "goat",
    "goldfinch",
    "goldfish",
    "goose",
    "gorilla",
    "goshawk",
    "grasshopper",
    "grouse",
    "guanaco",
    "gull",
    "hamster",
    "hare",
    "hawk",
    "hedgehog",
    "heron",
    "herring",
    "hippopotamus",
    "hornet",
    "horse",
    "human",
    "hummingbird",
    "hyena",
    "ibex",
    "ibis",
    "jackal",
    "jaguar",
    "jay",
    "jellyfish",
    "kangaroo",
    "kingfisher",
    "koala",
    "kookabura",
    "kouprey",
    "kudu",
    "lapwing",
    "lark",
    "lemur",
    "leopard",
    "lion",
    "llama",
    "lobster",
    "locust",
    "loris",
    "louse",
    "lyrebird",
    "magpie",
    "mallard",
    "manatee",
    "mandrill",
    "mantis",
    "marten",
    "meerkat",
    "mink",
    "mole",
    "mongoose",
    "monkey",
    "moose",
    "mosquito",
    "mouse",
    "mule",
    "narwhal",
    "newt",
    "nightingale",
    "octopus",
    "okapi",
    "opossum",
    "oryx",
    "ostrich",
    "otter",
    "owl",
    "oyster",
    "panther",
    "parrot",
    "partridge",
    "peafowl",
    "pelican",
    "penguin",
    "pheasant",
    "pig",
    "pigeon",
    "pony",
    "porcupine",
    "porpoise",
    "quail",
    "quelea",
    "quetzal",
    "rabbit",
    "raccoon",
    "rail",
    "ram",
    "rat",
    "raven",
    "reindeer",
    "rhinoceros",
    "rook",
    "salamander",
    "salmon",
    "sandpiper",
    "sardine",
    "scorpion",
    "seahorse",
    "seal",
    "shark",
    "sheep",
    "shrew",
    "skunk",
    "snail",
    "snake",
    "sparrow",
    "spider",
    "spoonbill",
    "squid",
    "squirrel",
    "starling",
    "stingray",
    "stinkbug",
    "stork",
    "swallow",
    "swan",
    "tapir",
    "tarsier",
    "termite",
    "tiger",
    "toad",
    "trout",
    "turkey",
    "turtle",
    "viper",
    "vulture",
    "wallaby",
    "walrus",
    "wasp",
    "weasel",
    "whale",
    "wildcat",
    "wolf",
    "wolverine",
    "wombat",
    "woodcock",
    "woodpecker",
    "worm",
    "wren",
    "yak",
    "zebra",
]
