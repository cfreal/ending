# Typing AST nodes

Due to its architecture, **ending** has great support for typing. This provides the following advantages:

- Fetch back results in their original type: if the column you're dumping is of type `BOOL`, you get a boolean. If it is a `BLOB`, you get bytes.
- Improve injection logic: different types mean different injection logic. Fetching a boolean value using a blind SQL injection requires 1 query, while fetching a string requires to know the length and dump character after character.
- Use statistics: if we dumped 10 rows for column `password` and they all have a length of 32 characters, chances are the next rows are also going to be 32 characters long.
- Bounds: set minimum and maximum values for an integer, charset or length for a string, etc.

Import AST nodes and types using:

```python
from ending.ast import *
```

## Setting a type

To associate a node with a type, either set the type in the constructor, or use the `with_type()` method.

```python
# ID is a positive integer
id = Identifier("id", type=IntType(min=0))
# ID is between 0 and 100
id = id.with_type(IntType(min=0, max=100))

# Password is 32 characters long and stored as hexadecimal
password = Identifier(
    "password", 
    type=TextType(size=32, charset="1234567890abcdef")
)
```

Types are automatically computed from the node if possible. This works for obvious cases, such as hardcoded values:

```python
# TextType(charset='helo wrd', size=IntType(min=11, max=11))
Value("hello").metadata.type
# IntType(min=123, max=123)
Value(123).metadata.type
```

But also for more complex types: here, since `password` is garantied to be 32 characters long, it necessarily has a first character, and the charset is the same as the identifier's:

```python
# TextType(
#   charset='1234567890abcdef',
#   size=IntType(min=1, max=1)
# )
Substring(password, 0, 1).metadata.type
```

## Types

| Node | Description |
|------|-------------|
| `UnknownType` | type not yet determined |
| `BoolType` | boolean (0 / 1) |
| `IntType` | integer with optional min/max bounds |
| `TextType` | string with charset and size hints |
| `BlobType` | binary data with byteset and size hints |

`IntType`, `TextType`, and `BlobType` carry statistical hints that guide blind injection (e.g. which characters to test first). The `feedback(value)` method on a type instance records obtained values so the framework can adapt.

### BoolType

Represents boolean types, such as `BOOL`, `BOOLEAN`.

### IntType

Represents integer types, such as `INT`, `INTEGER`.
The type consists of minimum and maximum values.

```python
# A numeric row ID
id_type = IntType(min=0)
# Timestamp from year 2000 to year 2050
timestamp_type = IntType(min=946684800, max=2524608000)
```

### TextType

Represent text-based types, such as `VARCHAR`, `TEXT`.
The type consists of a length (`size`), which can be either a raw value, or bounds, and a charset.

```python
# Type representing an MD5 hash:
md5_type = TextType(size=32, charset="1234567890abcdef")
```

The `length` is actually an `IntType`, and as such can be a range:

```python
# Type representing an email column, whose size is between 3 and 256
email_type = TextType(
    size=IntType(min=3, max=256),
    charset="a..<cut for brievity>..zA...Z0-9@.-"
)
```

### BlobType

Represent bytes-based types, such as `BLOB` or `BYTEA`.
The type consists of a length (`size`), which can be either a raw value, or bounds, and a `byteset`.

```python
some_type = BlobType(size=32, byteset=b"123456789")
```