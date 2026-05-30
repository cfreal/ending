"""Parses CLI arguments and runs the corresponding code."""

import asyncio
import os
import subprocess
import sys
from argparse import (
    ArgumentParser,
    BooleanOptionalAction,
    Namespace,
    RawDescriptionHelpFormatter,
    _SubParsersAction,
)


from rich import get_console
from rich.prompt import Prompt
from rich.text import Text
from rich.traceback import install as install_traceback

from ending.cli.configure import do_configure
from ending.cli.design import Design, DesignDirectory
from ending.cli.import_ import do_import
from ending.cli.map import do_map
from ending.cli.misc import *
from ending.cli.misc import PFX_WARNING, ConsoleLiveStatus, message_success
from ending.cli.query import do_query
from ending.db.generic.map import MapDepth
from ending.util import logging
from ending.validation import *

__all__ = ["main"]

DESCRIPTION = """\
AST-based SQL injection tool

https://cfreal.github.io/ending/
"""


def build_parser():
    parser = ArgumentParser("ending", description=DESCRIPTION)
    parser.add_argument("design", help="Name of the design file")
    parser.add_argument(
        "--debug", "-D", help="Increase log level to SQL", action="store_true"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _build_query_parser(subparsers)
    _build_map_parser(subparsers)
    _build_configure_parser(subparsers)
    _build_validate_parser(subparsers)
    _build_create_edit_parser(subparsers)
    # "import" parser is registered inside _build_create_edit_parser

    return parser


def _build_configure_parser(subparsers: _SubParsersAction):
    parser = subparsers.add_parser(
        "configure",
        help="Automatically configure the SQL injection design",
        description=("Determine injection payload, DBMS, quoting, and method."),
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--risky",
        "-r",
        action="store_true",
        help="Allow tautologies during configuration. This flag should not be used if "
        "you are injecting in an UPDATE or a DELETE statement.",
    )
    parser.add_argument(
        "--force", "-f", action="store_true", help="Override existing design"
    )
    parser.add_argument(
        "--manual",
        "-m",
        action="store_true",
        help="Create template for manual configuration",
    )
    parser.add_argument(
        "--dbms",
        "-d",
        help="DBMS to set in the design.",
        default=None,
    )
    parser.add_argument("--method", "-M", help="Injection method to use.", default=None)


def _build_validate_parser(subparsers: _SubParsersAction):
    parser = subparsers.add_parser(
        "validate",
        help="Verify that the design works",
        description=(
            "Verify that the SQL injection design works. "
            "Performs several test queries for different types (text, int, bool) and reports errors."
        ),
        formatter_class=RawDescriptionHelpFormatter,
    )


def _build_query_parser(subparsers: _SubParsersAction):
    parser = subparsers.add_parser(
        "query",
        help="Run an SQL query",
        description="Run an SQL query.",
        formatter_class=RawDescriptionHelpFormatter,
        epilog="""\
Examples:

    SELECT username, password FROM wp_users
    $ ending my-injection query -t wp_users -f username password
    
    SELECT sessionid FROM joomla.session WHERE userid=1 OR usertype LIKE '%admin%'
    $ ending my-injection query -t joomla.session -f sessionid -w 'userid=1 OR usertype LIKE {}' '%admin%'
    
    SELECT id, path FROM files LIMIT 1, 10
    $ ending my-injection query -t files -f id path -c 10
    
Field types (-T):

    You can set the type for each column using --field-types with:
    
    - T: text
    - I: int
    - B: bool 
    - X: blob (bytes)
    - H: hexadecimal
    - 6: Base64 (and Base64URL)
    - U: Unknown (default)
    
    For instance, to get an ID, a username, and a password hash, use:
    
    $ ending my-injection query -t users -f id username password -T ITH

Automatic restoration (cache):

    If the progress gets interrupted (Ctrl-C), the partial results are saved, so that if
    you runthe exact same command again, the data that has already been retrieved will
    not be fetched again, unless you use the `--no-restore` (`-N`) flag.
""",
    )
    parser.add_argument("--fields", "-f", nargs="+", help="Field names")
    parser.add_argument("--table", "-t", help="Table name")
    parser.add_argument("--start", "-s", help="First row to dump", type=int)
    parser.add_argument("--count", "-c", help="Number of rows to dump", type=int)
    parser.add_argument(
        "--distinct",
        "-d",
        help="Fetch distinct rows",
        action=BooleanOptionalAction,
        default=False,
    )
    parser.add_argument("--where", "-w", nargs="+", help="Conditions (WHERE)")
    parser.add_argument("--field-types", "-T", help="Field types")
    parser.add_argument(
        "--dump-count",
        "-C",
        help="Displays the number of rows instead of the results",
        action=BooleanOptionalAction,
        default=False,
    )
    parser.add_argument("--order", "-o", help="Order (ORDER BY)")
    parser.add_argument(
        "--order-reverse",
        "-r",
        help="Order in reverse (DESC instead of ASC)",
        action="store_true",
    )
    parser.add_argument("--output", "-O", help="Save results in path")
    parser.add_argument(
        "--validate",
        "-V",
        help="Validate the query instead of running it",
        action=BooleanOptionalAction,
        default=False,
    )
    parser.add_argument(
        "--no-restore",
        "-N",
        help="Do not use restore data.",
        default=False,
        action="store_true",
    )


def _build_map_parser(subparsers: _SubParsersAction):
    parser: ArgumentParser = subparsers.add_parser(
        "map",
        help="Map the DBMS schema",
        description=(
            "Map the DBMS schema. "
            "The DBMS schema is the list of databases, tables, and columns, and their types.\n\n"
            "Filters support wildcards: `*` for any number of characters, `?` for any character."
            ""
        ),
        formatter_class=RawDescriptionHelpFormatter,
        epilog="""\
Examples:

    Get name of databases:
    $ ending my-injection map databases
    
    Get tables from database `wordpress`
    $ ending my-injection map tables -d wordpress 
    
    Get columns from table `wordpress.wp_users`
    $ ending my-injection map columns -d wordpress -t wp_users
    
    Get tables containing a column whose name contains `pass`
    $ ending my-injection map tables -c '*pass*'
    
    Get the whole schema along with column types
    $ ending my-injection map types
    
    Display tables that have been dumped already
    $ ending my-injection map tables --show    
""",
    )
    parser.add_argument("level", help="What to dump", choices=MapDepth.__args__)
    parser.add_argument("--database", "-d", help="Database filter")
    parser.add_argument("--table", "-t", help="Table filter")
    parser.add_argument("--column", "-c", help="Column filter")
    parser.add_argument("--output", "-o", help="Save results in path")
    parser.add_argument(
        "--show", "-s", help="Show previously dumped results", action="store_true"
    )


def _build_create_edit_parser(subparsers) -> None:
    subparsers.add_parser("create", help="Create design")
    subparsers.add_parser("edit", help="Edit design")
    subparsers.add_parser("delete", help="Delete design")
    p = subparsers.add_parser(
        "import",
        help="Create design from an HTTP request on stdin",
        description=(
            "Read a raw HTTP request from stdin and create a new design "
            "with the corresponding send() method.\n\n"
            "Example:\n\n    ending my-design import < request.txt"
        ),
        formatter_class=RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--force", "-f", action="store_true", help="Overwrite existing design"
    )


# Actions


async def do_create(design_dir: DesignDirectory, namespace: Namespace) -> None:
    if design_dir.exists():
        console.print(f"{PFX_ERROR} Design [b]{design_dir.name}[/] already exists.")
        return
    design_dir.create()
    console.print(
        f"{PFX_SUCCESS} Design [b]{design_dir.name}[/] created in [i]{design_dir.get_module_path()}[/i]"
    )
    await do_edit(design_dir, namespace)


async def do_edit(design_dir: DesignDirectory, namespace: Namespace) -> None:
    path = str(design_dir.get_module_path())

    def _run_to_null(args: list[str]) -> bool:
        try:
            process = subprocess.run(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return False
        return process.returncode == 0

    success = True

    if editor := os.environ.get("EDITOR"):
        result = subprocess.run([editor, "--", path])
        success = result.returncode == 0
    elif sys.platform == "win32":
        try:
            os.startfile(path)
        except OSError:
            success = False
    elif sys.platform == "darwin":
        success = _run_to_null(["open", path])
    else:
        success = _run_to_null(["xdg-open", path])

    if not success:
        console.print(
            f"{PFX_ERROR} Unable to open file, please do so manually: [i]{path}[/i]"
        )


async def do_delete(design_dir: DesignDirectory, namespace: Namespace) -> None:
    choice = Prompt.ask(
        f"Are you sure you want to delete design [b]{design_dir.name}[/]?",
        choices=["y", "n"],
        default="n",
    )
    if choice == "y":
        design_dir.remove()


async def do_validate(design_dir: DesignDirectory, namespace: Namespace) -> None:
    Design = load_design_or_exit(design_dir)
    check_design_configured(Design)

    design = Design({})

    status = ConsoleLiveStatus()
    status.status("Running validations")

    console.print()

    try:
        await DesignSetupValidator(design, status).validate()
        await DesignSetConfigurationValidator(design, status).validate()
        if CurrentMethodValidator := design.method.get_validator():
            await CurrentMethodValidator(design.method, status).validate()
        await TypedQueriesValidator(design.method, status).validate()
        await DesignTeardownValidator(design, status).validate()
    except ValidationError as e:
        display_validation_error(e)
    else:
        message_success("SUCCESS")
    finally:
        status.done()


def main() -> None:
    install_traceback()
    namespace = build_parser().parse_args()

    if namespace.debug:
        logging.set_level("SQL")

    design_path = DesignDirectory(namespace.design)
    if namespace.command not in ("create", "import") and not design_path.exists():
        get_console().print(
            Text.from_markup(
                f"{PFX_ERROR} Design [b]{namespace.design}[/] does not exist"
            )
        )
        return

    func = f"do_{namespace.command}"
    func = globals()[func]

    try:
        asyncio.run(func(design_path, namespace))
    except CLIError as e:
        error_panel(str(e))
    except KeyboardInterrupt:
        pass
    finally:
        console.show_cursor()
