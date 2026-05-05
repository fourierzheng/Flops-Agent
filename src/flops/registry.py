from typing import Dict, Generic, TypeVar

T = TypeVar("T")


class Registry(Generic[T]):
    def __init__(self):
        self._items: Dict[str, T] = {}

    def register(self, name: str, item: T) -> None:
        """注册一个对象"""
        self._items[name] = item

    def get(self, name: str) -> T:
        """获取对象（不存在会抛 KeyError）"""
        return self._items[name]

    def keys(self) -> list[str]:
        return list(self._items.keys())

    def values(self) -> list[T]:
        return list(self._items.values())

    def __iter__(self):
        return iter(self._items.items())

    def __len__(self):
        return len(self._items)

    def __contains__(self, name: str):
        return name in self._items
