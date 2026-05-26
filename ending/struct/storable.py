__all__ = ["Storable"]


class Storable:
    """An object that can be saved to disk under various representations.

    When `store(path)` is called, for every or some of the `store_as_<ext>()`
    method, a file named `<path>.<ext>` will be created.

    For instance, if a subclass has a `store_as_txt()` and a `store_as_json()`
    method, calling `obj.store('/tmp/file')` will save two files,
    `/tmp/file.txt` and `/tmp/file.json`, both containing the appropriate
    content. If `object.store_as_json('/tmp/file.json')` is called, only the
    json file will be created, under the filename `/tmp/file.json`.
    """

    def store(self, path: str) -> None:
        """Calls every `store_as_<ext>` method and saves the output into a file
        with an appropriate extension.

        Args:
            path (str): The prefix of the files that will be generated.

        Returns:
            str: The path of the stored files, without extension

        Example:

            >>> # Stores the object as ./some-object.json and ./some-object.txt
            >>> object.store('some-object')
        """
        STORE_PREFIX = "store_as_"

        methods = [method for method in dir(self) if method.startswith(STORE_PREFIX)]
        methods = {
            method[len(STORE_PREFIX) :]: getattr(self, method) for method in methods
        }

        for extension, method in methods.items():
            method(f"{path}.{extension}")

    def store_as_txt(self, path) -> None:
        """Stores the object as a string."""
        with open(path, "w") as handle:
            handle.write(str(self))
