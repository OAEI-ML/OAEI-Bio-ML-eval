"""
oaei_bioml_eval.hierarchy: the target-ontology hierarchy index.

Parents/children adjacency plus the BFS distance queries the typed metrics use
for hierarchy-aware graded relevance (gain decays as .../(d+1) by shortest-path
depth). Built once from hierarchy edges — rows carrying 'child_id' and
'parent_id'. Dependency-free so the scorer stays light; candi-pool keeps its own
copy with the extra neighbourhood query it needs.
"""
from __future__ import annotations

from collections import defaultdict


class HierarchyIndex:
    def __init__(self, edges: list[dict[str, str]]) -> None:
        self.parents: dict[str, set[str]] = defaultdict(set)
        self.children: dict[str, set[str]] = defaultdict(set)
        for edge in edges:
            child = edge["child_id"]
            parent = edge["parent_id"]
            self.parents[child].add(parent)
            self.children[parent].add(child)

    def ancestors_with_distance(self, entity_id: str, max_distance: int) -> dict[str, int]:
        """BFS by level; shortest path matters (graded gain scales as 1/(d+1))."""
        if max_distance < 1:
            return {}
        distances: dict[str, int] = {}
        current_level: set[str] = self.parents.get(entity_id, set()).copy()
        current_level.discard(entity_id)
        depth = 1
        while current_level and depth <= max_distance:
            next_level: set[str] = set()
            for node in current_level:
                if node not in distances:
                    distances[node] = depth
                    if depth < max_distance:
                        for parent in self.parents.get(node, set()):
                            if parent not in distances and parent != entity_id:
                                next_level.add(parent)
            current_level = next_level
            depth += 1
        return distances

    def descendants_with_distance(self, entity_id: str, max_distance: int) -> dict[str, int]:
        """Symmetric to ancestors_with_distance, walking children."""
        if max_distance < 1:
            return {}
        distances: dict[str, int] = {}
        current_level: set[str] = self.children.get(entity_id, set()).copy()
        current_level.discard(entity_id)
        depth = 1
        while current_level and depth <= max_distance:
            next_level: set[str] = set()
            for node in current_level:
                if node not in distances:
                    distances[node] = depth
                    if depth < max_distance:
                        for child in self.children.get(node, set()):
                            if child not in distances and child != entity_id:
                                next_level.add(child)
            current_level = next_level
            depth += 1
        return distances
