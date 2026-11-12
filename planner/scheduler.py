from typing import List
from .task_graph import TaskGraph, TaskNode

class TaskScheduler:
    """Mengurutkan eksekusi graf tugas secara topologis (berdasarkan dependensi)."""
    @staticmethod
    def get_executable_nodes(graph: TaskGraph) -> List[TaskNode]:
        executable = []
        for node in graph.nodes.values():
            if node.status == "pending":
                # Cek apakah semua dependensinya sudah berstatus 'completed'
                deps_cleared = all(
                    dep_id in graph.nodes and graph.nodes[dep_id].status == "completed"
                    for dep_id in node.dependencies
                )
                if deps_cleared:
                    executable.append(node)
        return executable
