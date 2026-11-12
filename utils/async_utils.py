import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Any, List

class AsyncUtils:
    _executor = ThreadPoolExecutor(max_workers=4)

    @classmethod
    async def run_in_thread(cls, func: Callable, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(cls._executor, lambda: func(*args, **kwargs))

    @classmethod
    async def parallel_map(cls, func: Callable, items: List[Any]) -> List[Any]:
        tasks = [cls.run_in_thread(func, item) for item in items]
        return await asyncio.gather(*tasks)
