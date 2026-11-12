from utils.async_utils import AsyncUtils

class TaskParallelizer:
    """Wrapper untuk mengeksekusi operasi IO/Sistem secara paralel."""
    @staticmethod
    async def execute_batch(func, items):
        return await AsyncUtils.parallel_map(func, items)
