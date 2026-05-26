"""Allows to perform polytomy methods asynchronously.

Use `run` or `run_positional` to perform the polytomy.

Refer to `blocks` for details about the implementation.

## Implementation details

This module allows to run the dichotomial (resp. polytomial) process on some
item set, concurrently.

The usual dichotomial process is the following: we're looking for a char in a
charset, and can only run binary tests (True/False).
Therefore, we split the charset in half, check which half matches (the char is
in this half), and repeat the process with the matching half. This means that
the tests need to be ran one by one, as we need to know which half to keep
before running another test.

To see how we can improve this, let's use `ABCDEFGH` as an example item set.
Since it has 8 elements, we need `log(8, 2) = 3` tests. Let's assign to each
letter a number in binary corresponding to its position in the charset:
`A` is `0b000`, `B` is `0b001`, `C` is `0b010`, ..., `H` is `0b111`.
Then, for each binary digit, put the letter on the left half if it is `0`, or on
the right side if it is `1`. For instance, since `A` is all zeroes, it will be
on the left half for every of the `3` tests. Since `C` is `0b010`, it will be on
the left half for the first and last test, and right for the second one.
Finally, this yields:

    test left right
       1 ABCD EFGH
       2 ABEF CDGH
       3 ACEG BDFH

Each letter is either on the left side or the right side on each line, and each
has a different combination. `A` is `LLL`, `B` is `LLR`, `C` is `LRL`, etc. It
corresponds to their position in the charset, in binary. Therefore, when testing
each of the three lines, the `L`/`R` combination will yield the value we're
looking for.

With this technique, tests can be ran concurrently. This logic can be extended
to perform trichotomy or any politomy.
"""

from __future__ import annotations

import asyncio
import math
from functools import lru_cache, reduce
from typing import *

__all__ = ["run", "PolytomyNotInSetError", "PolytomyNotSingletonError"]


Item = TypeVar("Item")
"""The unit: a character, a byte, an int, ..."""
ItemSet = Sequence[Item]
"""A sequence of Items: a charset, byteset, list of ints, ..."""
Section = frozenset
"""Part of an item set: a number of items that are contained in another item
set.
"""
PolySection = list[Section]
"""A split of an item set into N distinct parts."""
PolySections = list[PolySection]
"""A list of sections built such that if, for an item I, for each section, we
know in which part I is, then we can infer I.
"""


class PolytomyNotInSetError(Exception):
    """Indicates that the value is not contained in the given set."""

    pass


class PolytomyNotSingletonError(Exception):
    """Indicates that the polytomial process did not yield one result (singleton): it
    resulted in either zero or several, which should never happen.
    """


@lru_cache
def build_polysections(item_set: ItemSet, nb_sections: int) -> PolySections:
    assert nb_sections >= 2, "nb_sections must be >= 2"

    digits = math.ceil(math.log(len(item_set), nb_sections))
    polysections = [[[] for _ in range(nb_sections)] for _ in range(digits)]

    for position, item in enumerate(item_set):
        for digit in range(digits):
            position, pos = divmod(position, nb_sections)
            polysections[digit][pos].append(item)

    return [[frozenset(section) for section in sections] for sections in polysections]


async def get_section(
    polysection: PolySection, item_is_in_section: Callable[[Section], Awaitable[bool]]
) -> Section:
    """Returns the section that contains the item we're looking for.
    Given a PolySection, it first checks the first section, then the second,
    etc. up until the penultimate one. If only one section remains, it means
    that the item is in this section.

    We make the assumption that an item is necessarily contained in the item
    set, and will verify this assumption later.
    """
    # Although recursivity would be cleaner, we'd get a stupid amount of
    # subcalls in some cases, so stick to pythonic code
    *polysection, last = polysection
    for section in polysection:
        if await item_is_in_section(section):
            return section
    return last


async def run(
    item_set: ItemSet,
    nb_sections: int,
    item_is_in_section: Callable[[Section], Awaitable[bool]],
) -> Item:
    """Runs the dichotomial process asynchronously. `item_is_in_section` will be
    called with different versions of the item set, and the output will then be
    processed to determine the result of the dichotomial process. The coroutine
    will be called like this:

        await item_is_in_section(some_item_set)

    with `some_item_set` being a subset of the main item set. If the value is
    is not in the item set, `PolytomyNotInSetError` will be raised.

    Raises:

        PolytomyNotInSetError: if the item set does not contain the value
        PolytomyNotSingletonError: if the resulting set is not a singleton

    Args:

        item_set (iterable): list of possible items (charset)
        nb_sections (int): number of sections
        item_is_in_section (func): returns `True` if the expected value is in
            the given item set

    Notes:

        If your item set contains the same value multiple times, the results
        will be inconsistent. The value is not cast to a `set` because we want
        to preserve order.

    Example:

        Obtain the first 10 letters of `version()`:

            async def is_in_charset(what, index, charset):
                \"""Returns `True` if the letter at index `index` of `what` is
                contained in `charset`.
                \"""
                charset = ', '.join(str(ord(c)) for c in charset)
                payload = f'ORD(SUBSTR({what}, {index+1}, 1)) IN ({charset})'
                return await sql_test(payload)

            charset = string.printable

            for i in range(10):
                partial_is_in_charset = functools.partial(is_in_charset, 'version()', i)
                c = dichotomy.run(charset, partial_is_in_charset)
                msg_success('Character #{} of version(): {}', i, c)
    """
    if not item_set:
        raise PolytomyNotInSetError("Set is empty !")

    if len(item_set) == 1:
        if not await item_is_in_section(frozenset(item_set)):
            raise PolytomyNotInSetError("Item is not in set")
        return item_set[0]

    polysections = build_polysections(item_set, nb_sections)

    tasks = [
        get_section(polysection, item_is_in_section) for polysection in polysections
    ]
    results = await asyncio.gather(*tasks)

    # We previously made the assumption that the item was in the item set: it
    # is not always the case. If our assumption is wrong get_section() returns
    # the last section of every polysection.
    all_last = all(
        section is polysection[-1]
        for section, polysection in zip(results, polysections)
    )
    # If this is True, this means that either the item we're looking for is in
    # the last section of every polysection, or that it is not in the set
    if all_last and not await item_is_in_section(item_set):
        raise PolytomyNotInSetError("Item is not in set")

    items: set[Item] = reduce(set.__and__, map(set, results))
    if len(items) != 1:
        raise PolytomyNotSingletonError(f"Process did not yield one result: {items!r}")

    return items.pop()
