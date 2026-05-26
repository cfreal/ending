"""ending – SQL injection framework.

Submodules:

- `ending.ast`: SQL AST nodes and types. Build DBMS-agnostic SQL expressions using
  composable Python objects (Query, Identifier, Value, Substring, ...).
- `ending.db`: DBMS-specific implementations. Each sub-package (mysql, postgresql,
  mssql, oracle, sqlite) provides a Compiler (AST → SQL strings), Injection Methods
  (UNION, blind, error-based, time-based), and a Mapper (schema discovery).
- `ending.cli`: Command-line interface. Defines the Design base class that users
  subclass to describe their target, and exposes the entry-point commands (configure,
  map, query, ...).
- `ending.configuration`: Automatic injection setup. Probes the target to detect quoting
  syntax, fingerprint the DBMS, and select the best injection method.
- `ending.validation`: Runtime sanity checks. Verifies that a Design, its injection
  method, and user-supplied queries actually work on the target.
- `ending.struct`: Shared data structures: result sets, injection state/metadata, and
  serialisable objects used across the framework.
- `ending.util`: Utility helpers: logging, quoting functions, random identifier
  generation, HTTP session wrappers, and type aliases.
- `ending.exception`: Framework-wide exception hierarchy (InjectionError,
  CompilationError, NodeTypeError, ...).
"""
