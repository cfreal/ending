# Welcome to ending's documentation

**ending** is a flexible SQL injection framework and CLI that works just as well for simple injections as it does for very complex ones.

Like sqlmap, it can be used out of the box for common SQL injection scenarios. However, **ending** is designed so that when the target stops being simple, you don’t need to abandon the tool and write custom scripts — you write python code instead.

You can read about the main concepts [here](main-concepts.md).

!!! warning

    This project is a (albeit pretty advanced) proof of concept that was meant to demonstrate the usefulness of working with the AST instead of writing SQL queries directly. It is robust, but new. It may have bugs or limitations. Please [open an issue](https://github.com/cfreal/ending/issues) if you find a problem. 


## Installation

The project can be installed via pip:

```bash
$ pip install cfreal-ending
```

The tool is then available as **ending**:

```bash
$ ending
```

## Getting started

To get started with the CLI, refer to [this page](cli/design.md). The most common modules and classes of the library are described in the [Library section](library/ast.md).

Tutorials are available in the [Tutorials section](tutorials/first-steps.md).

For the technical documentation, please visit the [pdoc page here](./pdoc/ending.html).

## Supported DBMS

For now, **ending** supports 5 DBMS:

- MySQL
- SQLite3
- PostgreSQL
- Microsoft SQL Server
- Oracle

## Docker usage

Ending is also usable as docker. Designs can be edited through a volume.

```bash
$ docker build -t ending .
$ docker run --rm -it -v "$(pwd)/ending-data:/root/ending" ending
```

It spawns a shell in which you can use the `ending` command:

```bash
root@513d692c82ba:/# ending --help
usage: ending [-h] [--debug] design {query,map,configure,validate,create,edit,delete} ...

SQL injection tool - cfreal https://cfreal.github.io/ending/

positional arguments:
  design                Name of the design file
  {query,map,configure,validate,create,edit,delete}
    query               Run an SQL query
    map                 Map the DBMS schema
    configure           Automatically configure the SQL injection design
    validate            Verify that the design works
    create              Create design
    edit                Edit design
    delete              Delete design

options:
  -h, --help            show this help message and exit
  --debug, -D           Increase log level to SQL
```

Designs and results can be found and edited in `./ending-data`.