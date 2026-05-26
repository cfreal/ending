# pip

requests
aiohttp

## test

docker
pyodbc
oracledb
mysql-connector-python
psycopg2-binary
imp

# article

- parler de la dichotomie en // (genre toutes les reqs sont faites en //)
    - parler polytomie
- décrire archi des méthodes ? rows row cell part test

part -> returns more than one ?
    can use this to do multiple bytes at once and THEN guess

# TODO

- Au lieu de has() et get(), simplement restore() qui retourne data


- polytomy fix coroutine type item_is_in_section...
- polytomy: it's not an iterable, it needs to be hashable as well (lists don't work)
- when fetching length, it compares to 50, and then does IS NULL which makes no sense
    -> need a flag in type to say that it CANNOT BE NULL

# python 3.14

https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Executor.map
Changed in version 3.14: Added the buffersize parameter.

https://docs.python.org/3/library/asyncio-task.html#asyncio.TaskGroup

# Polymorphism

Possible surface-level polymorphism :

- Keywords case
- list rendering

Le .compiler n'est pas directement nécessaire du coup car c'est lazy evaluated

# Caching

## Objectif

- charset adapté à chaque nouvelle cell avec priorité des lettres
- lorsqu'on reçoit N chars, on peut guess N prochains / tous
    - via des modules de guess (dico, IA)
    - liés au type de la node ?
- pas de redondance des résultats entre le listener et les vrais

## Problèmes

- feedback() et le transmitter font un peu la mm chose dans le cas des résultats
- comment ajouter les modules de guess ?
    - quand peuvent-ils être trigger ?
    - seulement en blind ?
- comment conserver les résultats si on a pas de emitter ?

- Tout se marche un peu dessus: type.feedback, liveemitter qui stocke une copie des résultats, sortie lorsqu'il y a une exception alors que ca semble pas être son rôle
    - qui fait les propositions de charset / autre ?

## idées

- intégrer Partial() aux résultats live ?
- Method -> event based
    - Comment ça marcherait ?
    - Need to match query to events ?

```python

class Method:
    async def on(self, event: str, **data):
        match event:
            case "bounds":
                await self.fetch_results_bounded(query, **data)
            case "rows":
                await self.fetch_rows(query, **data)

    async def fetch_results_bounded(
        self, query: Query, bounds: Bounds
    ) -> Table:
        for p in range(omin, omax, self.nb_rows):
            # Create tasks only when they can start, to avoid having too many in
            # the pipe
            async with semaphore:
                pass

            nb_rows = min(self.nb_rows, omax - p)
            row_query = query.limit(p, nb_rows) if not query.single else query
            rows_emitter = emitter.with_info(row=p - omin, nb_rows=nb_rows)
            task = asyncio.create_task(self.fetch_rows(row_query, rows_emitter))
            tasks.append(task)

```

## implem avec querydescriptor

```python
meta.with_(col=)
metatransmitter
```

toute la logique chiante sera dans les _synced, qu'on peut décorer ou définir via une func

### types

- Type Char() et Byte() qui ont un parent et un offset, et qui ainsi lors d'un feedback
    le transmettent au parent
- Node.feedback()

détacher le feedback du type et tout mettre dans le transmitter

```python
meta.()
```

- implement with feedback ?

```python
```


# Idee de .meta


L'idée est de lier des metadonnées à la requête, mais le code devient moche :

```python

query.meta.update(other, cell=toto, titi=toto)

```

comment marcherait le emit après ?

```py
query.emit("length", length)
```

## MetaQuery

Ici ca doit être split dans certains cas
Genre quand on cherche pr chaque caractère

```py

MetaQuery(), si trouvée dans les nodes les infos sont remontées


```

