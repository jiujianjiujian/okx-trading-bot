"""轻量 DI 容器 — 单例注册 + 工厂函数"""

from typing import Any, Callable


class Container:
    def __init__(self):
        self._factories: dict[str, Callable] = {}
        self._instances: dict[str, Any] = {}

    def register(self, name: str, factory: Callable) -> None:
        self._factories[name] = factory

    def resolve(self, name: str) -> Any:
        if name not in self._instances:
            if name not in self._factories:
                raise KeyError(f"未注册的服务: {name}")
            self._instances[name] = self._factories[name]()
        return self._instances[name]

    def override(self, name: str, instance: Any) -> None:
        """测试用 — 替换为 mock"""
        self._instances[name] = instance

    def reset(self) -> None:
        self._instances.clear()
        self._factories.clear()


container = Container()
