import asyncio
import inspect
import time
from argparse import Namespace

from ending.cli.design import Design, DesignDirectory
from ending.cli.misc import *
from ending.configuration import ConfigurationException, DesignConfigurator

__all__ = [
    "do_configure",
]


def check_design_is_configurable(cls: type[Design], ns: Namespace) -> None:
    if not hasattr(cls, "send") or cls.send is Design.send:
        console.print(f"{PFX_ERROR} Design must have a [b]send()[/] method")
        console.print(f"{PFX_INFO} Prototype: [i]send(self, payload: str) -> bytes[/]")
        raise ConfigurationException()

    if not inspect.signature(cls.send).parameters:
        console.print(
            f"{PFX_ERROR} The [b]send()[/] method needs at least one parameter, the payload"
        )
        console.print(
            f'{PFX_INFO} Prototype: [i]send(self, payload: str="base value") -> bytes[/]'
        )
        raise ConfigurationException()

    if not ns.force and cls.inject is not Design.inject:
        console.print(f"{PFX_ERROR} Design already has an [b]inject()[/] method")
        console.print(f"{PFX_INFO} Remove it or [i]--force[/i] to override")
        raise ConfigurationException()


def _get_matching_value(
    status: ConsoleLiveStatus, type: str, choices: list[str], value: str
) -> str:
    if value is None:
        return choices[0]

    value = value.lower()
    candidates = [v for v in choices if v.lower().startswith(value)]

    match candidates:
        case []:
            potential = ", ".join(choices)
            status.failure(f"Unable to find {type} **{value}** in {potential}")
            raise ValueError
        case [match]:
            status.info(f"Setting {type} to **{match}**")
            return match
        case _:
            potential = ", ".join(candidates)
            status.failure(
                f"Multiple matches found for {type} **{value}**: {potential}"
            )
            raise ValueError


async def _configure_manual(
    status: ConsoleLiveStatus, configurator: DesignConfigurator, ns: Namespace
) -> None:
    status.section("Manual configuration")
    try:
        dbms = _get_matching_value(
            status,
            "DBMS",
            ["MySQL", "PostgreSQL", "MSSQL", "sqlite", "Oracle"],
            ns.dbms,
        ).lower()
        if ns.method:
            method = _get_matching_value(
                status,
                "injection method",
                ["unionbased", "errorbased", "blind", "testbased", "timebased"],
                ns.method,
            ).lower()
        else:
            method = None
    except ValueError:
        message_error("[b]ERROR[/]")
        return

    await configurator.setup_manual(dbms, method)

    status.success("Created skeleton code")
    status.done()
    message_success("[b]SUCCESS[/]")


async def do_configure(design_dir: DesignDirectory, ns: Namespace) -> None:
    Design = load_design_or_exit(design_dir)
    design = Design()
    status = ConsoleLiveStatus()
    configurator = DesignConfigurator(design, risky=ns.risky, status=status)

    console.print()

    if ns.manual:
        try:
            check_design_is_configurable(Design, ns)
        except ConfigurationException as e:
            # The message has been displayed already
            message_error("FAILURE")
            return
        except (asyncio.CancelledError, KeyboardInterrupt):
            message_error("INTERRUPTED")
            return

        await _configure_manual(status, configurator, ns)
        return

    try:
        check_design_is_configurable(Design, ns)
        await configurator.configure()
    except ConfigurationException as e:
        # The message has been displayed already
        message_error("FAILURE")
    except (asyncio.CancelledError, KeyboardInterrupt):
        message_error("INTERRUPTED")
    else:
        message_success("[b]CONFIGURED[/]")
    finally:
        status.done()
