# Mappers

A mapper dumps some (or all) of the database schema: database, table, column names.

## Using a mapper

A mapper only requires an injection method. Most DBMS modules contain a mapper.

```python
method = ...
mapper = mysql.Mapper(method)
```

It can then be used to fetch different levels of information:

```python
map = await mapper.fetch("databases")
```

It supports filters: `*` means any number of characters, while `?` means one character.

```python
# Dumps every table in the wordpress database that contains a column whose name
# contains "pass"
map = await mapper.fetch("tables", database="wordpress", column="*pass*")
```

## Maps

A map is a tree: each database contains tables, which each contains columns.

Once a map has been fetched, it can be displayed using `print()`, or stored as a text file or CSV using `store()`.

```python
print(map)
# Creates /tmp/my-map.rich, /tmp/my-map.txt, and /tmp/my-map.csv
map.store("/tmp/my-map")
```

CSV files are very useful to grep information, as they use a `database,table,column` format. Plaintext is easier to read as a human.