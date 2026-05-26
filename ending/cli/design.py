from __future__ import annotations

import inspect
import os.path
import re
import shutil
import sys
from abc import ABC, abstractmethod
from ast import *
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Type, TypeVar

import aiohttp

from ending.ast import Node
from ending.db.generic.compiler import Compiler
from ending.db.generic.map import Mapper
from ending.db.generic.method import Method
from ending.util.misc import ENDING_PATH
from ending.util.requests import AsyncSession

__all__ = [
    "Design",
    "HTTPDesign",
    "AIOHTTPDesign",
    "DesignDirectory",
    "DesignEditor",
    "DesignLoadException",
]


class DesignLoadException(Exception):
    """The design cannot be loaded."""


class Design(ABC):
    """SQL injection design.

    A design contains the logic required to perform an SQL injection. In addition to
    the `Design.inject` method, that sends the payload and obtains a response, it sets
    up a compiler, method, and mapper, and allows you to run queries and mappings.

    In order:

    - `Design.setup` gets called to initialize the design.
    - `Design.set_configuration` gets called to set the configuration.
        It sets up the compiler, injection method, and mapper.
    - `Design.method` and `Design.mapper` can be used at will to perform injections
    - `Design.teardown` gets called to perform cleanup
    """

    compiler: Compiler
    """SQL injection compiler."""
    method: Method
    """The SQL injection method to use to inject."""
    mapper: Mapper
    """The database mapper."""

    def __init__(self, options: dict = {}):
        self.options = options

    async def setup(self) -> None:
        """Sets up the design for SQL injections."""

    async def set_configuration(self) -> None:
        """Sets up the compiler, method, and mapper."""
        self.compiler = await self.set_compiler()
        self.method = await self.set_method()
        self.mapper = await self.set_mapper()

    async def send(self, payload: str) -> bytes:
        """Injects a value and returns the response.
        If payload has a default value, it is assumed to be the base value for the
        parameter.

        For instance, to inject in `http://target.com/news?id=7`, you'd have:

            async def send(self, payload: str="7") -> bytes:
                return request.get("http://target.com/news", params={"id": payload}).response
        """
        raise NotImplementedError()

    async def inject(self, payload: Node) -> bytes:
        """Injects a payload and returns the response."""
        raise NotImplementedError()

    async def set_compiler(self) -> Compiler:
        """Creates a compiler object."""
        raise NotImplementedError()

    async def set_method(self) -> Method:
        """Creates an injection method."""
        raise NotImplementedError()

    async def set_mapper(self) -> Mapper:
        """Creates a mapper."""
        raise NotImplementedError()

    async def teardown(self) -> None:
        """Tears down the design."""

    @classmethod
    def is_configurable(cls) -> bool:
        """Returns whether the design can be configured.
        Namely, checks that the send method is defined and has at least one parameter.
        """
        return (
            cls is not Design
            and cls.send is not Design.send
            and len(inspect.signature(cls.send).parameters) >= 2
        )

    @classmethod
    def is_configured(cls) -> bool:
        """Returns whether the design's configuration methods are set.
        This method does not ensure that the design works, only that the methods are
        defined.
        """
        return (
            cls is not Design
            and cls.inject is not Design.inject
            and cls.set_compiler is not Design.set_compiler
            and cls.set_method is not Design.set_method
            and cls.set_mapper is not Design.set_mapper
        )


class DesignLoadException(Exception):
    """The design cannot be loaded due to an exception."""


T = TypeVar("T")


class BaseHTTPDesign(Design, Generic[T]):
    """Base class for HTTP designs.

    This design does not create an `AsyncSession` instance, but it provides the
    `send` method to send payloads.
    """

    PROXY: str = None
    """Optional proxy to use, such as `http://localhost:8080`. Defaults to none."""
    WORKERS: int = 2
    """Number of workers (concurrent connections) to use. Defaults to 2."""

    session: T
    """Asynchronous HTTP session."""

    @abstractmethod
    async def create_session(self) -> T:
        """Creates an HTTP session."""
        ...

    @abstractmethod
    async def close_session(self) -> None:
        """Closes the HTTP session."""
        ...

    async def setup(self) -> None:
        """Sets up the design."""
        await super().setup()
        self.session = await self.create_session()

    async def teardown(self) -> None:
        """Tears down the design."""
        await super().teardown()
        await self.close_session()


class HTTPDesign(BaseHTTPDesign[AsyncSession]):
    """A design for web injections.

    This design creates an `AsyncSession` instance to perform HTTP requests.
    """

    PROXY: str = None
    """Optional proxy to use, such as `http://localhost:8080`. Defaults to none."""
    WORKERS: int = 2
    """Number of workers (concurrent connections) to use. Defaults to 2."""

    session: AsyncSession
    """Asynchronous HTTP session. The session has the same API as `requests.Session`.
    """

    async def create_session(self) -> AsyncSession:
        """Creates an HTTP session."""
        workers = int(self.options.get("workers", self.WORKERS))
        session = AsyncSession(workers=workers)
        session.verify = False
        proxy = self.options.get("proxy", self.PROXY)
        if proxy:
            session.proxies = {"all": proxy}
        return session

    async def close_session(self) -> None:
        """Closes the HTTP session."""
        self.session.close()


class AIOHTTPDesign(BaseHTTPDesign[aiohttp.ClientSession]):
    """A design for web injections using aiohttp."""

    session: aiohttp.ClientSession
    """Asynchronous HTTP session using aiohttp."""

    async def create_session(self) -> aiohttp.ClientSession:
        workers = int(self.options.get("workers", self.WORKERS))
        connector = aiohttp.TCPConnector(limit=workers)
        session = aiohttp.ClientSession(connector=connector)
        return session

    async def close_session(self) -> None:
        await self.session.close()


class DesignDirectory:
    """Controls a design directory. A design directory stores a python module, the
    design, along with the results of the injections.

    Directory layout:

        - Design `name` is stored in `~/ending/<name>/`;
        - Design files are stored in `~/ending/<name>/design.py`;
        - Results from SQL injections are stored in `~/ending/<name>/queries/`;
        - Results from map dumps are stored in `~/ending/<name>/map/`.
    """

    _DIR_MODE = 0o700
    _DESIGN_MODULE_NAME = "design"
    path: Path
    """Path to the design directory."""
    name: str
    """Name of the design."""

    def __init__(self, name: str) -> None:
        if ".." in name:
            raise ValueError("Design name cannot contain '..'")
        self.name = name
        self.path = ENDING_PATH / name

    def get_module_path(self) -> Path:
        """Gets the path to the python module associated with this design."""
        return self.get_sub_path(f"{self._DESIGN_MODULE_NAME}.py")

    def get_sub_path(self, path: str) -> Path:
        """Gets the path to a subdirectory of the design."""
        return self.path / path

    def exists(self) -> bool:
        """Whether the design exists."""
        return self.get_module_path().exists()

    def create(self) -> None:
        """Creates the design directory and the design template."""
        self.path.mkdir(self._DIR_MODE, parents=True)
        with open(self.get_module_path(), "w") as handle:
            handle.write(self.TEMPLATE)
        self.get_sub_path("queries").mkdir(self._DIR_MODE)
        self.get_sub_path("map").mkdir(self._DIR_MODE)

    def remove(self) -> None:
        """Deletes the directory and its contents."""
        path = str(self.path)
        shutil.rmtree(path)

    def load(self) -> Type[Design]:
        """Loads the module and returns its `Design` class.


        Raises:

            DesignLoadException: If the design cannot be loaded.

        """
        sys.path.insert(0, str(self.path))

        try:
            design = __import__(self._DESIGN_MODULE_NAME)
        except BaseException:
            raise DesignLoadException("Unable to import design file")
        finally:
            sys.path.pop(0)

        try:
            Design = design.Design
        except AttributeError:
            raise DesignLoadException("Design class is not declared")

        return Design

    TEMPLATE = '''\
#!/usr/bin/env python3
# Ending design file

from __future__ import annotations

from ending import *
from ending.ast import *
from ending.cli.design import HTTPDesign
from ending.util import quoting


from ending.db.mysql import *


class Design(HTTPDesign):
    async def send(self, payload: str="1") -> bytes:
        """Sends given payload to the target and returns the response as bytes.
        """
        # TODO If you know a correct value, add it as the default argument of
        # this function
        response = await self.session.post(
            "http://target.com/...",
            data={
                "id": payload,
            }
        )
        return response.content
'''


@dataclass
class ReplacementDescriptor:
    start: int
    end: int
    indent: int
    prefix: str = ""
    suffix: str = ""

    @classmethod
    def single(cls, ast: int, **kwargs) -> ReplacementDescriptor:
        return ReplacementDescriptor(ast, ast, ast, **kwargs)

    @classmethod
    def replace(cls, ast: AST, **kwargs) -> ReplacementDescriptor:
        return ReplacementDescriptor(
            ast.lineno, ast.end_lineno + 1, ast.lineno, **kwargs
        )

    @classmethod
    def after(cls, ast: AST, **kwargs) -> ReplacementDescriptor:
        return cls(ast.end_lineno + 1, ast.end_lineno + 1, ast.lineno, **kwargs)

    @classmethod
    def before(cls, line: AST, **kwargs) -> ReplacementDescriptor:
        return cls.single(line.lineno, **kwargs)


class DesignEditor:
    """Helper to modify the code of a *design.py* file."""

    file: Path
    code: bytes
    ast: AST

    def __init__(self, design: Design):
        self.file = Path(inspect.getfile(design.__class__))
        self._reload()

    def _reload(self) -> None:
        self.code = self.file.read_bytes()
        self.ast = parse(self.code)

        for item in self.ast.body:
            match item:
                case ClassDef(name="Design"):
                    self.cls = item
                    break
        else:
            raise RuntimeError("Design class not found")

    def _save(self, code: str) -> None:
        self.file.write_bytes(code)
        self._reload()

    def _get_line_indent(self, line: str) -> bytes:
        return re.match(rb"^([\t ]*)", line).group(1)

    def _replace_code_at(self, replacement: ReplacementDescriptor, code: str) -> None:
        lines = self.code.splitlines()

        if code is None:
            code = []
        else:
            code = code.encode()
            indent = self._get_line_indent(lines[replacement.indent - 1])
            code = re.sub(rb"^", indent, code, flags=re.MULTILINE)
            if replacement.prefix:
                code = replacement.prefix.encode() + code
            if replacement.suffix:
                code += replacement.suffix.encode()
            code = [code]

        lines = lines[: replacement.start - 1] + code + lines[replacement.end - 1 :]
        self._save(b"\n".join(lines))

    def set_attribute(
        self, name: str, type: str, value: str, formatter: str = "{value!r}"
    ) -> None:
        """Sets an attribute of the given class. If the attribute exists, it is
        replaced. Otherwise, it is added after other attributes, but before method
        declarations.
        """
        formatted = formatter.format(value=value)
        code = f"{name}: {type} = {formatted}"

        replacement = ReplacementDescriptor.before(self.cls.body[0])
        # We put the attribute after other attributes, but before any method
        seen_method = False
        seen_vars = False

        for item in self.cls.body:
            match item:
                case Expr() if isinstance(item.value, Constant):
                    replacement = ReplacementDescriptor.after(item)
                case AsyncFunctionDef() | FunctionDef():
                    if not seen_method:
                        seen_method = True
                        if not seen_vars:
                            replacement = ReplacementDescriptor.before(
                                item, suffix="\n"
                            )
                case AnnAssign() if item.target.id == name:
                    replacement = ReplacementDescriptor.replace(item)
                    break
                case AnnAssign() | Assign() if not seen_method:
                    seen_vars = True
                    replacement = ReplacementDescriptor.after(item)

        self._replace_code_at(replacement, code)

    def strip_triple_comments(self):
        """Removes every comment which starts with `### `."""
        lines = self.code.splitlines()
        lines = [
            line
            for line in lines
            if not line.lstrip().startswith(b"### ") and line.lstrip() != b"###"
        ]
        self._save(b"\n".join(lines))

    def delete_all_methods(self, *names: str) -> None:
        """Deletes every async method with name `name`."""
        lines = self.code.splitlines()
        to_remove = [
            (item.lineno, item.end_lineno)
            for item in self.cls.body
            if isinstance(item, AsyncFunctionDef) and item.name in names
        ]

        offset = 0
        kept = []

        for start, end in to_remove:
            kept.extend(lines[offset : start - 1])
            try:
                while lines[end].strip() == b"":
                    end += 1
            except IndexError:
                pass
            offset = end

        kept.extend(lines[offset:])
        self._save(b"\n".join(kept))

    def set_method(self, name: str, code: str, after: str = None) -> None:
        """Sets the code for a method of the `Design` class. If the method exists, it is
        replaced. Otherwise, it is put after the last statement of the class, of after
        `after` if specified.
        """
        code = code and code.strip()
        replacement = None

        for item in self.cls.body:
            match item:
                case AsyncFunctionDef():
                    if item.name == name:
                        replacement = ReplacementDescriptor.replace(item)
                        break
                    if item.name == after:
                        replacement = ReplacementDescriptor.after(item, prefix="\n")

        if not replacement:
            replacement = ReplacementDescriptor.after(item, prefix="\n")

        self._replace_code_at(replacement, code)

    def set_import(self, module: str) -> None:
        """Adds an `import` statement."""
        line = f"import {module}"
        replacement = None

        for item in self.ast.body:
            match item:
                case Import():
                    if any(
                        alias.name == module and not alias.asname
                        for alias in item.names
                    ):
                        return
                    replacement = ReplacementDescriptor.after(item)
                case ImportFrom() if not replacement and item.module != "__future__":
                    replacement = ReplacementDescriptor.before(item, suffix="\n")

        assert replacement, "No import statement is present in the design file"

        self._replace_code_at(replacement, line)

    def replace_or_add_import_from_star(self, module_prefix: str, new_module: str):
        """Replaces an `from {} import *` statement by another."""
        for item in self.ast.body:
            match item:
                case ImportFrom(
                    full_module, names=[alias(name="*")]
                ) if full_module.startswith(module_prefix):
                    replacement = ReplacementDescriptor.replace(item)
                    break
                case ImportFrom() | Import():
                    replacement = ReplacementDescriptor.after(item)
        self._replace_code_at(replacement, f"from {new_module} import *")

    def set_import_from(self, module: str, *names: str, replace: bool = False) -> None:
        """Adds an `import from` statement."""

        for item in self.ast.body:
            match item:
                case ImportFrom(import_module) if import_module == module:
                    merged_names = [
                        (
                            f"{alias.name} as {alias.asname}"
                            if alias.asname
                            else alias.name
                        )
                        for alias in item.names
                    ]
                    if len(names) == len(merged_names) == 1 and replace:
                        merged_names = names
                    else:
                        # We could work with sets, but we want to preserve order
                        merged_names += [
                            name for name in names if name not in merged_names
                        ]

                    replacement = ReplacementDescriptor.replace(item)
                    line = f"from {item.module} import {', '.join(merged_names)}"
                    break
                case ImportFrom() | Import():
                    replacement = ReplacementDescriptor.after(item)
        else:
            line = f"from {module} import {', '.join(names)}"

        self._replace_code_at(replacement, line)
