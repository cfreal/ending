"""State, context, and listeners."""

from __future__ import annotations

from abc import abstractmethod
from collections import namedtuple
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal, Self, final

from ending.ast import Query
from ending.struct.resultset import PartialCell, ResultSet, UnknownCell
from ending.util.typing import Bounds, Cell, Row

if TYPE_CHECKING:
    from ending.db.generic.map import Map

__all__ = [
    "Restorable",
    "Nothing",
    "Data",
    "Context",
    "State",
    "QueryState",
    "MapState",
    "Listener",
    "VoidState",
    "VoidContext",
]


@dataclass
class Restorable:
    """Base type for values returned by `Context.restore`.

    Concrete subclasses indicate whether restore data is available. This
    envelope type makes it easy to test restore results in conditions like:

        if data := ctx.restore("..."):
            return data.data
    """

    @abstractmethod
    def __bool__(self) -> bool:
        """Returns True when restore data is present, False otherwise."""


@dataclass
class Data(Restorable):
    """Wraps restore data returned by `Context.restore`.

    The stored value is available through the `Data.data` attribute and the object
    evaluates as `True` in boolean contexts.
    """

    data: Any

    def __bool__(self) -> Literal[True]:
        return True


@dataclass
class Nothing(Restorable):
    """Represents the absence of restore data.

    This singleton is returned by `Context.restore` when no matching stored
    state exists for the requested key and context.
    """

    def __bool__(self) -> Literal[False]:
        return False


Nothing = Nothing()
"""Singleton that indicates that no restore data is available."""


@dataclass
class Listener:
    """Receives event notifications from a `State` object.

    Subclasses can implement `on_<event>` methods that are dispatched by
    `Listener.on` when the corresponding event is emitted.
    """

    state: State = field(init=False, default=None)

    def link(self, state: State) -> None:
        """Links the listener to a state."""
        if self.state is not None:
            raise RuntimeError("Listener is already linked to a state")
        self.state = state
        self.state._link_listener(self)

    def on(self, event: str, context: Context) -> None:
        """Called when an event is emitted by a `Context` object.

        By default, calls `on_<event>(**context.data)`.

        Args:
            event (str): The event that needs to be handled.
            context (Context): The context from which the event was triggered.

        Returns:
            Nothing.
        """
        event_str = event.replace(":", "_").replace("-", "_")
        method = f"on_{event_str}"

        try:
            handler = getattr(self, method)
        except AttributeError:
            return

        handler(**context.data)


@dataclass
class Context:
    """Execution context for restore lookups and event notification.

    A `Context` carries the current `State` and a small dictionary of
    information about the current operation. It is immutable and can be extended
    with additional data using `with_`.
    """

    state: State
    data: dict[str, Any]

    def with_(self, **data: Any) -> Self:
        """Creates a new `Context` object with additional data."""
        return replace(self, data=self.data | data)

    def restore(self, key: str) -> Data | type[Nothing]:
        return self.state.restore(key, context=self)

    def notify(self, event, *args: Any, **kwargs: Any) -> None:
        """Notifies the state that an event happened."""
        # .notify("test", test="value") is equivalent to .notify("test", "value")
        if args:
            kwargs[event] = args[0]
        if kwargs:
            self = self.with_(**kwargs)

        self.state.notify(event, self)

    def get(self, *keys: str) -> list:
        return [self.data[key] for key in keys]

    def tuple(self) -> namedtuple:
        """Returns a named tuple that contains every key-value pair that the context
        has.
        """
        return namedtuple("ctx", self.data.keys())(*self.data.values())


@dataclass(init=False)
class State:
    """Base state container for retrieval operations.

    A `State` object stores metadata, exposes a `Context`, and manages attached
    `Listener` instances. Specialized subclasses provide restore behavior for
    query results (`QueryState`) and map metadata (`MapState`).
    """

    context: Context
    """Base context associated with this object."""
    listeners: list[Listener]
    """Listeners linked to this object."""

    def __init__(self):
        self.context = Context(self, {})
        self.listeners = []

    def _link_listener(self, listener: Listener) -> None:
        """Adds a listener to the list of listeners.

        The method is not really protected, but it should not be called directly.
        Use `Listener.link` instead.
        """
        if listener in self.listeners or listener.state is not self:
            raise RuntimeError("Listener is already linked to this state object")
        self.listeners.append(listener)

    def notify(self, event: str, context: Context) -> None:
        """Notifies listeners of an event."""
        for listener in self.listeners:
            listener.on(event, context)

    def restore(self, key: str, context: Context) -> Data | type[Nothing]:
        """Restores data relative to a key and a context. If no data is available,
        returns `Nothing`. If a `result` is available, returns `Data(result)`.
        """
        return Nothing


class QueryState(State, Listener):
    """Stores the state of a query retrieval operation.

    `QueryState` tracks the original query, an adjusted query, bounds, and the
    set of complete and partial cell values that have been fetched so far. It
    implements `restore` for rows and individual cells from the collected data.
    """

    query: Query
    adjusted_query: Query
    bounds: Bounds
    results: dict[int, dict[int, Cell | PartialCell]]
    """Holds every obtained result, even if partial, as a scarce map."""

    def __init__(self):
        super().__init__()
        self.query = None
        self.adjusted_query = None
        self.bounds = None
        self.results = {}
        self.link(self)

    def restore(self, key: str, context: Context) -> Data | type[Nothing]:
        """Extracts data from the results to restore them."""

        match key:
            case "rows":
                start, nb = context.get("row", "nb_rows")
                indexes = set(range(start, start + nb))

                if not (indexes <= self.results.keys()):
                    return Nothing

                rows = []
                columns = list(range(len(self.query.q.columns)))

                for i in indexes:
                    row_dict = self.results[i]
                    row = []
                    for j in columns:
                        if j not in row_dict:
                            return Nothing

                        cell = row_dict[j]
                        if isinstance(cell, PartialCell):
                            return Nothing

                        row.append(cell)
                    rows.append(row)
                return Data(rows)
            case "cell":
                row, column = context.get("row", "column")
                cell = self.results.get(row, {}).get(column, Nothing)
                if cell is not Nothing and not isinstance(cell, PartialCell):
                    return Data(cell)
                return Nothing

        return Nothing

    def __getstate__(self) -> dict:
        # Only keep complete cells
        results = {
            i: {j: cell for j, cell in row.items() if not isinstance(cell, PartialCell)}
            for i, row in self.results.items()
        }
        # TODO Storing the query is useless, as it will be filled in immediately ;
        # Storing the adjusted_query could be useful, but for now some nodes cannot be
        # serialized
        return {
            "bounds": self.bounds,
            "results": results,
        }

    def __setstate__(self, data: dict) -> None:
        self.__init__()
        self.bounds = data["bounds"]
        self.results = data["results"]

    def get_partial_results(
        self,
        first: int | None = None,
        last: int | None = None,
        trim: bool = False,
    ) -> ResultSet:
        """Returns the obtained results.

        If `first` is set, returns only the `first` first rows.
        If `last` is set, returns only the `last` last rows.

        If `trim` is set, do not include the topmost and bottommost empty rows.
        """
        if self.query is None or self.bounds is None:
            return None

        if trim:
            if self.results:
                rows = self.results.keys()
                bottom = min(rows)
                top = max(rows) + 1
            else:
                bottom = 0
                top = 0
        else:
            bottom = 0
            top = self.bounds[1] - self.bounds[0]

        if last:
            bottom = max(bottom, top - last)
        if first:
            top = min(top, bottom + first)

        nb_columns = len(self.query.q.columns)
        unknown = UnknownCell()
        rows = [self.results.get(i, {}) for i in range(bottom, top)]
        rows = [[row.get(i, unknown) for i in range(nb_columns)] for row in rows]

        return ResultSet(self.query, rows)

    def on_query(self, query: Query, **_) -> None:
        self.query = query

    def on_adjusted_query(self, adjusted_query: Query, **_) -> None:
        self.adjusted_query = adjusted_query

    def on_bounds(self, bounds: Bounds) -> None:
        self.bounds = bounds

    def on_rows(self, row: int, rows: list[Row], **_) -> None:
        for i, row in enumerate(rows, start=row):
            self.results[i] = {j: cell for j, cell in enumerate(row)}

    def on_cell(self, row: int, column: int, cell: Cell, **_) -> None:
        self.results.setdefault(row, {})[column] = cell

    def on_length(self, row: int, column: int, length: int, **_) -> None:
        self.results.setdefault(row, {})[column] = PartialCell(length)

    def on_partial(self, row: int, column: int, partial: PartialCell, **_) -> None:
        self.results.setdefault(row, {})[column] = partial


class MapState(State, Listener):
    """Stores map metadata during hierarchical schema retrieval.

    `MapState` maintains a nested `Map` structure and a set of completed dump
    markers. It supports restoring database, table, column, and type lists for a
    partially retrieved schema.
    """

    map: Map
    """Current map data."""
    done: set[str]
    """Names of items that have been completely dumped."""

    def __init__(self) -> None:
        super().__init__()
        from ending.db.generic.map import Map

        self.map = Map()
        self.done = set()
        self.link(self)

    def restore(self, key: str, context: Context) -> Data | type[Nothing]:
        if key != "part":
            return Nothing

        ctx = context.tuple()
        items = self.map.items

        # Right now it is impossible to find out if the records have been dumped and are
        # empty or if they have not been dumped yet. When in doubt, return Nothing
        def wrap(items: dict) -> Data | type[Nothing]:
            return Data(list(items.keys()))

        match ctx.depth:
            case "databases" if () in self.done:
                return wrap(items)
            case "tables" if (ctx.database,) in self.done:
                return wrap(items[ctx.database])
            case "columns" if (ctx.database, ctx.table) in self.done:
                return wrap(items[ctx.database][ctx.table])
            case "types" if (ctx.database, ctx.table) in self.done:
                return Data(
                    [column, metadata["type"]]
                    for column, metadata in items[ctx.database][ctx.table].items()
                )

        return Nothing

    def on_dump_done(
        self,
        *,
        depth: str,
        database: str = None,
        table: str = None,
        results: list[str] | list[list[str, str]],
    ) -> None:
        match depth:
            case "databases":
                self.map.radd(results)
                self.done.add(())
            case "tables":
                self.map.radd(database, results)
                self.done.add((database,))
            case "columns" | "types":
                self.map.radd(database, table, results)
                self.done.add(
                    (
                        database,
                        table,
                    )
                )

    def on_map(self, map: Map) -> None:
        self.map.update(map)


class VoidState(State):
    """A no-op state used when no meaningful state object is required."""


@final
@dataclass
class VoidContext(Context):
    """A context bound to an empty `VoidState`.

    This context is useful in situations where a `Context` object is required by
    an API but no real state or event data should be carried.
    """

    state: VoidState = field(default_factory=VoidState)
    data: dict[str, Any] = field(default_factory=dict)
