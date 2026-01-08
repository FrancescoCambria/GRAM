from neo4j import GraphDatabase
import json
import os
import time
from typing import List, Dict, Any, Optional, Tuple, Set
from collections import defaultdict
import itertools
import uuid
import pandas as pd

from bitarray import bitarray
from itertools import combinations


class FPTreeNode:
    """A node in the FP-tree."""
    def __init__(self, item, count, parent):
        self.item = item
        self.count = count
        self.parent = parent
        self.children = {}
        self.node_link = None

    def increment(self, count):
        self.count += count


class GRAMXNeo4j:
    """
    GRAMXNeo4j connects to a Neo4j database and extracts graph schema information.
    It supports:
      1. Connecting to Neo4j.
      2. Querying and saving the schema.
      3. Trimming the schema by removing nodes/edges.
      4. Storing anchor labels for rule extraction.
    """

    def __init__(self, uri: str, user: str, password: str, output_dir: str = "./data/output"):
        self.uri = uri
        self.user = user
        self.password = password
        self.driver = None
        self.schema: Optional[Dict[str, Any]] = None
        self.trimmed_schema: Optional[Dict[str, Any]] = None
        self.anchors: List[str] = []
        self.anchor_counts: Dict[str, int] = {}
        self.output_dir = output_dir
        self.support = 0
        self.confidence = 0
        self.threshold_support_counts: Dict[str, int] = {}
        self.final_to_anchors: Dict[str, Set[str]] = defaultdict(set)

    # 1️⃣ Connect to Neo4j
    def connect(self):
        """Open a connection to the Neo4j database."""
        if not self.driver:
            self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            print("[INFO] Connection to Neo4j established.")
        else:
            print("[INFO] Connection already open.")

    # 2️⃣ Query the schema and save it
    def extract_schema(self):
        """
        Query the database schema and save it to memory and a JSON file.
        This collects labels, relationship types, and property keys.
        """
        if not self.driver:
            raise ConnectionError("You must connect to Neo4j before extracting the schema.")

        with self.driver.session() as session:
            result = session.run("""
                CALL db.schema.visualization()
            """)
            record = result.single()
            nodes = record["nodes"]
            relationships = record["relationships"]

            self.schema = {
                "nodes": [
                    {
                        "id": n.element_id,
                        "label": list(n.labels)[0]
                    }
                    for n in nodes
                ],
                "relationships": [
                    {
                        "id": r.element_id,
                        "type": r.type,
                        "start": r.start_node.element_id,
                        "end": r.end_node.element_id
                    }
                    for r in relationships
                ]
            }

        print(f"[INFO] Schema extracted")

    # 3️⃣ Trim the schema
    def trim_schema(self, chosen_nodes: List[str] = None, chosen_edges: List[str] = None):
        """
        Trim the schema by removing certain node labels or relationship types.
        The trimmed schema is stored and saved as a separate JSON file.
        """
        if not self.schema:
            raise ValueError("Schema not extracted yet. Run extract_schema() first.")

        self.chosen_nodes = chosen_nodes or []
        self.chosen_edges = chosen_edges or []

        chosen_nodes_set = set(self.chosen_nodes)
        chosen_edges_set = set(self.chosen_edges)

        trimmed_nodes = [n for n in self.schema["nodes"]
                         if n["label"] in chosen_nodes_set]
        trimmed_rels = [r for r in self.schema["relationships"]
                        if r["type"] in chosen_edges_set and
                        r["start"] in [n["id"] for n in trimmed_nodes] and
                        r["end"] in [n["id"] for n in trimmed_nodes]]

        self.trimmed_schema = {
            "nodes": trimmed_nodes,
            "relationships": trimmed_rels
        }

        print(f"[INFO] Schema Trimmed")

    # 4️⃣ Save anchor labels
    def set_anchors(self, labels: List[str]):
        """
        Save a list of labels to be used as anchor nodes for rule extraction.
        """
        self.anchors = labels
        print(f"[INFO] Anchors saved")

    def query_anchor_counts(self):
        """
        Query the total number of anchor nodes in the graph and save it in the class.
        """
        if not self.driver:
            raise ConnectionError("You must connect to Neo4j before querying anchor counts.")
        
        with self.driver.session() as session:
            for anchor_label in self.anchors:
                query = f"MATCH (a:{anchor_label}) RETURN count(a) AS count"
                result = session.run(query)
                count = result.single()["count"]
                self.anchor_counts[anchor_label] = count
        
        print(f"[INFO] Anchor counts queried and saved: {self.anchor_counts}")

    def set_support_and_confidence(self, support: float, confidence: float):
        """
        Set the support and confidence values and calculate the threshold support counts.
        """
        self.support = support
        self.confidence = confidence
        for anchor_label, count in self.anchor_counts.items():
            self.threshold_support_counts[anchor_label] = int(count * support)
        print(f"[INFO] Support and confidence set. Threshold support counts: {self.threshold_support_counts}")

    # 🔚 Close connection
    def close(self):
        """Close the Neo4j connection."""
        if self.driver:
            self.driver.close()
            self.driver = None
            print("[INFO] Connection to Neo4j closed.")

    # -------------------------
    # Pattern forest structures
    # -------------------------

    def _new_pattern(self, kind: str, anchor_label: str, node_label_seq: List[str],
                     rel_type_seq: List[Optional[str]], parent: Optional[str] = None,
                     count_edge_idx: Optional[int] = None):
        pid = str(uuid.uuid4())
        pat = {
            "id": pid,
            "kind": kind,  # 'normal'|'count'|'anyrel'
            "anchor": anchor_label,
            "node_labels": node_label_seq,  # length = nodes in path (relationship count +1)
            "rel_types": rel_type_seq,  # may contain None for anyrel
            "parent": parent,  # parent pattern id (for count patterns)
            "count_edge_idx": count_edge_idx  # for count patterns: index of edge to check multiplicity
        }
        self.patterns[pid] = pat
        self.forest[anchor_label].append(pid)
        return pid

    def build_forest(self, max_depth: int = 3):
        """
        Build a forest of patterns for each anchor using the trimmed_schema.
        max_depth: maximum path length (# of relationships).
        Result stored in self.patterns (dict pattern_id -> pattern metadata)
        and self.forest (anchor_label -> list of pattern_ids for that anchor).
        """
        if not self.trimmed_schema:
            raise ValueError("Trimmed schema missing. Run trim_schema() first.")

        # quick helpers from trimmed_schema
        nodes_by_id = {n["id"]: n["label"] for n in self.trimmed_schema["nodes"]}
        rels = self.trimmed_schema["relationships"]

        # adjacency by node id: (from_id -> list of (rel_type, to_id))
        adj = defaultdict(list)
        for r in rels:
            adj[r["start"]].append((r["type"], r["end"]))

        # pattern registry
        self.patterns: Dict[str, Dict[str, Any]] = {}  # pattern_id -> metadata
        self.forest: Dict[str, List[str]] = defaultdict(list)  # anchor_label -> pattern_ids

        # For each anchor label, find all node ids with that label present in trimmed nodes
        anchor_node_ids = [nid for nid, lbl in nodes_by_id.items() if lbl in set(self.anchors)]

        # map label->node_ids (to treat anchors by label)
        label_to_node_ids = defaultdict(list)
        for nid, lbl in nodes_by_id.items():
            label_to_node_ids[lbl].append(nid)

        # BFS-like expansion but by labels using adjacency to produce sequences
        # Start from nodes that carry the anchor label (we work by label sequences, not specific node ids)
        for anchor_label in self.anchors:
            # starting node labels sequences: single node (anchor)
            start_node_ids = label_to_node_ids.get(anchor_label, [])
            if not start_node_ids:
                continue
            anyrel_nodes_label_set = set()
            rel_len_old = 0
            # We'll expand label paths up to max_depth edges
            # Represent a path as list of node_ids (real ids from trimmed_schema)
            queue = []
            for sid in start_node_ids:
                queue.append([sid])

            visited_label_paths = set()  # avoid duplicate label sequences
            while queue:
                path = queue.pop(0)
                rel_len = len(path) - 1
                if rel_len > max_depth:
                    continue

                if rel_len != rel_len_old:
                    rel_len_old = rel_len
                    anyrel_nodes_label_set = set()

                # Convert node id path to label sequence and rel type sequence
                node_label_seq = [nodes_by_id[nid] for nid in path]
                rel_type_seq = []
                for i in range(len(path) - 1):
                    # find rel types between path[i] and path[i+1] in trimmed schema adjacency
                    types = [t for t, toid in adj[path[i]] if toid == path[i + 1]]
                    # if multiple rel types, create separate normal patterns per rel type; here we keep the first
                    rel_type_seq.append(types[0] if types else None)

                # Register patterns for paths with at least 1 relationship (else trivial)
                if len(node_label_seq) >= 2:
                    label_seq_key = (tuple(node_label_seq), tuple(rel_type_seq))
                    if label_seq_key not in visited_label_paths:
                        visited_label_paths.add(label_seq_key)
                        # normal pattern (explicit relationship types). If any rel is None, skip normal.
                        if all(rt is not None for rt in rel_type_seq):
                            self._new_pattern(
                                kind="normal",
                                anchor_label=anchor_label,
                                node_label_seq=node_label_seq,
                                rel_type_seq=rel_type_seq
                            )
                        # anyrel pattern (wildcard rel types)
                        if node_label_seq[-1] not in anyrel_nodes_label_set:
                            anyrel_nodes_label_set.add(node_label_seq[-1])
                            self._new_pattern(
                                kind="anyrel",
                                anchor_label=anchor_label,
                                node_label_seq=[node_label_seq[-1]],
                                rel_type_seq=[None] * rel_len
                            )
                        # count patterns: create one count pattern per edge in path (edge index) derived from normal only
                        if all(rt is not None for rt in rel_type_seq):
                            for edge_idx in range(len(rel_type_seq)):
                                self._new_pattern(
                                    kind="count",
                                    anchor_label=anchor_label,
                                    node_label_seq=node_label_seq,
                                    rel_type_seq=rel_type_seq,
                                    parent=None,  # we'll link parent below
                                    count_edge_idx=edge_idx
                                )

                # expand path
                if len(path) - 1 < max_depth:
                    last = path[-1]
                    for (rtype, toid) in adj.get(last, []):
                        # avoid immediate cycles by not repeating the same node id in path
                        if toid in path:
                            continue
                        new_path = path + [toid]
                        queue.append(new_path)

        # After creating count patterns we need to link each count pattern to a corresponding normal pattern
        # The corresponding normal is the normal pattern with same anchor, same node_labels and same rel_types
        # We'll build a lookup of normal patterns by (anchor, node_labels tuple, rel_types tuple)
        normal_lookup = {}
        for pid, p in self.patterns.items():
            if p["kind"] == "normal":
                key = (p["anchor"], tuple(p["node_labels"]), tuple(p["rel_types"]))
                normal_lookup[key] = pid

        for pid, p in list(self.patterns.items()):
            if p["kind"] == "count":
                key = (p["anchor"], tuple(p["node_labels"]), tuple(p["rel_types"]))
                parent_pid = normal_lookup.get(key)
                p["parent"] = parent_pid  # can be None if no exact normal found
                self.patterns[pid] = p

        # persist patterns and forest metadata as JSON snapshots
        print(f"[INFO] Built forest with {len(self.patterns)} patterns for anchors {self.anchors}")

    # -------------------------
    # Cypher builders and query
    # -------------------------
    def _build_cypher_for_pattern(self, pattern: Dict[str, Any], anchor_threshold: int) -> Tuple[str, Dict[str, Any]]:
        """
        Build a Cypher query that returns the elementId() of the final node and the anchor node id.
        Returns (cypher_string, params)
        The query returns rows: { final_id: elementId(final), anchor_id: elementId(anchor) }
        """
        anchor_label = pattern["anchor"]
        node_labels = pattern["node_labels"]
        rel_types = pattern["rel_types"]  # list len = len(node_labels)-1, contains str or None

        # Build MATCH path piece by piece using aliases n0, n1, ..., nK
        parts = []
        aliases = []
        for i, lbl in enumerate(node_labels):
            aliases.append(f"n{i}")
            parts.append(f"({aliases[-1]}:{lbl})")

        path_expr = aliases[0]
        match_str = f"MATCH p = {parts[0]}"
        # build relationships
        for i in range(len(rel_types)):
            rt = rel_types[i]
            left = aliases[i]
            right = aliases[i + 1]
            if rt is None:
                rel_part = f"-[r{i}]-"
            else:
                rel_part = f"-[r{i}:{rt}]-"
            match_str += rel_part + f"({right}:{node_labels[i + 1]})"

        # final: ensure anchor is first node and anchored by label
        # Build return statement: final node id and anchor id
        cypher = (
            match_str
            + "\nWITH collect(p) as paths"
            + "\nWITH paths, size(apoc.coll.toSet([p in paths | nodes(p)[0]])) as anchor_count"
            + "\nWHERE anchor_count >= $anchor_threshold"
            + "\nUNWIND paths as p"
            + f"\nRETURN elementId(nodes(p)[-1]) AS final_id, elementId(nodes(p)[0]) AS anchor_id"
        )

        params = {"anchor_threshold": anchor_threshold}
        return cypher, params

    def _build_cypher_for_count_pattern(self, pattern: Dict[str, Any], threshold: int, anchor_threshold: int) -> Tuple[str, Dict[str, Any]]:
        """
        Build a Cypher query for a count pattern. Because count patterns depend on a multiplicity of
        relationships between a specific pair of nodes along the path, the query:
          - matches the full path p = (n0)-[r0]->(n1)-...->(nK)
          - identifies the two nodes for the edge index (i and i+1)
          - counts relationships of the same TYPE between those two nodes and enforces >= threshold
        Returns query that yields final node id and anchor id.
        """
        # First build same base match as normal
        anchor_label = pattern["anchor"]
        node_labels = pattern["node_labels"]
        rel_types = pattern["rel_types"]
        edge_idx = pattern["count_edge_idx"]
        if edge_idx is None:
            raise ValueError("count_edge_idx must be set for count patterns")

        # aliases
        aliases = [f"n{i}" for i in range(len(node_labels))]
        match_parts = []
        # start with first node
        match_str = f"MATCH p = ({aliases[0]}:{node_labels[0]})"
        for i in range(len(rel_types)):
            rt = rel_types[i]
            rel_alias = f"r{i}"
            # match specific relationship type between nodes in the path
            match_str += f"-[{rel_alias}:{rt}]-({aliases[i + 1]}:{node_labels[i + 1]})"
            if rt == rel_types[edge_idx]:
                count_alias = rel_alias

        # Now count parallel relationships of type rel_types[edge_idx] between aliases[edge_idx] and aliases[edge_idx+1]
        aliases_str = ", ".join(aliases)

        cypher = (
                match_str
                + f"\nWITH {aliases_str}, p, count({count_alias}) as relCount"
                + f"\nWHERE relCount >= $threshold"
                + "\nWITH collect(p) as paths"
                + "\nWITH paths, size(apoc.coll.toSet([p in paths | nodes(p)[0]])) as anchor_count"
                + "\nWHERE anchor_count >= $anchor_threshold"
                + "\nUNWIND paths as p"
                + f"\nRETURN elementId(nodes(p)[-1]) AS final_id, elementId(nodes(p)[0]) AS anchor_id"
        )

        params = {"threshold": threshold, "anchor_threshold": anchor_threshold}
        return cypher, params

    def _build_cypher_for_anyrel_pattern(self, pattern: Dict[str, Any], anchor_threshold: int) -> Tuple[str, Dict[str, Any]]:
        """
        Build a Cypher query that returns the elementId() of the final node and the anchor node id for a anyrel pattern
        Returns (cypher_string, params)
        The query returns rows: { final_id: elementId(final), anchor_id: elementId(anchor) }
        """
        anchor_label = pattern["anchor"]
        node_label = pattern["node_labels"][0]
        rel_len = len(pattern["rel_types"])

        match_str = f"MATCH p = (anchor:{anchor_label})-[*1..{rel_len}]->(final:{node_label})"

        cypher = (
            match_str
            + "\nWITH collect(p) as paths"
            + "\nWITH paths, size(apoc.coll.toSet([p in paths | nodes(p)[0]])) as anchor_count"
            + "\nWHERE anchor_count >= $anchor_threshold"
            + "\nUNWIND paths as p"
            + f"\nRETURN elementId(nodes(p)[-1]) AS final_id, elementId(nodes(p)[0]) AS anchor_id"
        )

        params = {"anchor_threshold": anchor_threshold}
        return cypher, params

    def execute_pattern_queries(self, length: int, count_threshold: int = 2):
        """
        Execute all patterns of given length with dependency logic.
        Stores results in self.pattern_results and self.final_to_anchors.
        """
        if not self.driver:
            raise ConnectionError("Connect to Neo4j before executing queries.")
        if not hasattr(self, "patterns"):
            raise ValueError("Patterns not built. Run build_forest() first.")

        patterns = {pid: p for pid, p in self.patterns.items()
                    if len(p["node_labels"]) == length + 1 or (p["kind"] == "anyrel" and len(p["rel_types"]) == length)}

        if not hasattr(self, "pattern_results"):
            self.pattern_results = {}
        # self.final_to_anchors.clear() # Removed to allow accumulation

        # record which normal patterns produced results
        normal_success = set()

        with self.driver.session() as session:
            for pid, p in patterns.items():
                anchor_threshold = self.threshold_support_counts.get(p['anchor'], 0)
                if p["kind"] == "count":  # count patterns
                    cypher, params = self._build_cypher_for_count_pattern(p, threshold=count_threshold, anchor_threshold=anchor_threshold)
                elif p["kind"] == "anyrel":  # anyrel patterns
                    cypher, params = self._build_cypher_for_anyrel_pattern(p, anchor_threshold=anchor_threshold)
                else:  # normal
                    cypher, params = self._build_cypher_for_pattern(p, anchor_threshold=anchor_threshold)
                
                res = session.run(cypher, params)
                anchor_to_finals = defaultdict(set)
                for record in res:
                    anchor_id = record["anchor_id"]
                    final_id = record["final_id"]
                    anchor_to_finals[anchor_id].add(final_id)
                    self.final_to_anchors[final_id].add(anchor_id)

                if len(anchor_to_finals) > 0:
                    self.pattern_results[pid] = anchor_to_finals
                if p["kind"] == "normal" and len(anchor_to_finals) > 0:
                    normal_success.add(pid)

            # now execute count patterns whose parents succeeded
            for pid, p in patterns.items():
                if p["kind"] != "count":
                    continue
                parent = p.get("parent")
                if parent not in normal_success:
                    self.pattern_results.pop(pid, None)
                    continue

                anchor_threshold = self.threshold_support_counts.get(p['anchor'], 0)
                cypher, params = self._build_cypher_for_count_pattern(p, threshold=count_threshold, anchor_threshold=anchor_threshold)
                res = session.run(cypher, params)
                anchor_to_finals = defaultdict(set)
                for record in res:
                    anchor_id = record["anchor_id"]
                    final_id = record["final_id"]
                    anchor_to_finals[anchor_id].add(final_id)
                    self.final_to_anchors[final_id].add(anchor_id)
                if len(anchor_to_finals) > 0:
                    self.pattern_results[pid] = anchor_to_finals

        print(f"[INFO] Executed {len(self.pattern_results)} patterns (length={length})")

    def combine_results(self, max_combination_size: int = 3):
        """
        Combines final_ids based on shared anchors for each anchor label separately.

        A combination of final_ids is kept only if the number of common anchors
        is greater than or equal to a dynamic support threshold calculated for each anchor label.
        Combines final_ids iteratively up to max_combination_size.

        Output structure:
            self.combinations = {
                combo_id: {
                    "final_ids": list([...final_ids...]),
                    "anchors": set([...anchor_ids...]),
                    "support": int,
                    "anchor_label": str
                }
            }
        """
        if not hasattr(self, "pattern_results"):
            raise ValueError("No pattern results found. Run execute_pattern_queries() first.")

        self.combinations = {}

        # Group patterns by anchor label
        anchor_to_pids = defaultdict(list)
        for pid, p in self.patterns.items():
            if pid in self.pattern_results:
                anchor_to_pids[p['anchor']].append(pid)

        # For each anchor label, run the combination logic
        for anchor_label, pids in anchor_to_pids.items():
            support_threshold = int(self.anchor_counts.get(anchor_label, 0) * self.support)
            print(f"[INFO] Combining for anchor '{anchor_label}' with support threshold {support_threshold}")

            # Build an inverted index for this anchor label only
            final_to_anchors = defaultdict(set)
            for pid in pids:
                for anchor_id, final_ids in self.pattern_results[pid].items():
                    for final_id in final_ids:
                        final_to_anchors[final_id].add(anchor_id)

            if not final_to_anchors:
                continue

            # Start with individual final_ids (combinations of size 1)
            active_combos = {
                frozenset([final_id]): {
                    "anchors": anchors,
                    "support": len(anchors)
                }
                for final_id, anchors in final_to_anchors.items()
                if len(anchors) >= support_threshold
            }

            # Iteratively generate larger combinations
            iteration = 2
            while iteration <= max_combination_size and active_combos:
                print(f"[INFO] Anchor '{anchor_label}' combination iteration {iteration} with {len(active_combos)} active groups")

                new_combos = {}
                combo_keys = list(active_combos.keys())
                for i in range(len(combo_keys)):
                    for j in range(i + 1, len(combo_keys)):
                        keyA = combo_keys[i]
                        keyB = combo_keys[j]

                        union_key = keyA.union(keyB)
                        if len(union_key) == iteration:
                            if union_key in new_combos:
                                continue

                            all_anchor_sets = [final_to_anchors[final_id] for final_id in union_key]
                            common_anchors = set.intersection(*all_anchor_sets)
                            support = len(common_anchors)

                            if support >= support_threshold:
                                new_combos[union_key] = {
                                    "anchors": common_anchors,
                                    "support": support
                                }

                # Save all new combos into main registry
                for k, data in new_combos.items():
                    combo_id = str(uuid.uuid4())
                    self.combinations[combo_id] = {
                        "final_ids": list(k),
                        "anchors": data["anchors"],
                        "support": data["support"],
                        "anchor_label": anchor_label
                    }

                active_combos = new_combos
                iteration += 1

        print(f"[INFO] Generated {len(self.combinations)} frequent final_id combinations in total")
        return self.combinations

    def combine_results_fpgrowth(self, max_combination_size: int = 3):
        """
        Finds frequent itemsets of final_ids using the FP-Growth algorithm.
        This is more memory-efficient than the Apriori-like `combine_results`.
        """
        if not hasattr(self, "pattern_results"):
            raise ValueError("No pattern results found. Run execute_pattern_queries() first.")

        self.combinations = {}

        # Group patterns by anchor label
        anchor_to_pids = defaultdict(list)
        for pid, p in self.patterns.items():
            if pid in self.pattern_results:
                anchor_to_pids[p['anchor']].append(pid)

        # For each anchor label, run the FP-Growth algorithm
        for anchor_label, pids in anchor_to_pids.items():
            support_threshold = int(self.anchor_counts.get(anchor_label, 0) * self.support)
            print(f"[INFO] Running FP-Growth for anchor '{anchor_label}' with support threshold {support_threshold}")

            # 1. Build anchor-to-final_ids "transactions"
            anchor_to_finals = defaultdict(set)
            final_to_anchors = defaultdict(set)
            for pid in pids:
                for anchor_id, final_ids in self.pattern_results[pid].items():
                    anchor_to_finals[anchor_id].update(final_ids)
                    for final_id in final_ids:
                        final_to_anchors[final_id].add(anchor_id)
            
            transactions = [frozenset(finals) for finals in anchor_to_finals.values()]

            # 2. First pass: find frequent items and their support
            item_support = defaultdict(int)
            for transaction in transactions:
                for item in transaction:
                    item_support[item] += 1
            
            frequent_items = {item for item, support in item_support.items() if support >= support_threshold}

            if not frequent_items:
                continue

            # 3. Build FP-tree
            # Order frequent items by support
            header_table = {item: [support, None] for item, support in item_support.items() if item in frequent_items}
            
            def get_support(item):
                return header_table[item][0]

            root = FPTreeNode(None, 1, None)

            for transaction in transactions:
                # Filter and sort transaction items
                frequent_transaction = [item for item in transaction if item in frequent_items]
                frequent_transaction.sort(key=get_support, reverse=True)
                
                current_node = root
                for item in frequent_transaction:
                    if item in current_node.children:
                        current_node.children[item].increment(1)
                    else:
                        new_node = FPTreeNode(item, 1, current_node)
                        current_node.children[item] = new_node
                        # Link to header table
                        if header_table[item][1] is None:
                            header_table[item][1] = new_node
                        else:
                            # Traverse to the end of the linked list
                            node = header_table[item][1]
                            while node.node_link is not None:
                                node = node.node_link
                            node.node_link = new_node
                    current_node = current_node.children[item]

            # 4. Mine FP-tree
            def mine_tree(prefix, tree_root, header):
                # Get items from header table, sorted by support
                sorted_items = sorted(list(header.keys()), key=lambda i: header[i][0])

                for item in sorted_items:
                    new_prefix = prefix.copy()
                    new_prefix.add(item)
                    
                    # Add to combinations if size >= 2
                    if len(new_prefix) >= 2:
                        all_anchor_sets = [final_to_anchors[final_id] for final_id in new_prefix]
                        common_anchors = set.intersection(*all_anchor_sets)
                        support = len(common_anchors)

                        if support >= support_threshold:
                            combo_id = str(uuid.uuid4())
                            self.combinations[combo_id] = {
                                "final_ids": list(new_prefix),
                                "anchors": common_anchors,
                                "support": support,
                                "anchor_label": anchor_label
                            }

                    # Build conditional pattern base
                    conditional_pattern_base = []
                    node = header[item][1]
                    while node is not None:
                        path = []
                        parent = node.parent
                        while parent.item is not None:
                            path.append(parent.item)
                            parent = parent.parent
                        if path:
                            conditional_pattern_base.extend([path] * node.count)
                        node = node.node_link
                    
                    # Build conditional FP-tree and mine recursively
                    if conditional_pattern_base:
                        # Recalculate support in the conditional base
                        cond_item_support = defaultdict(int)
                        for p in conditional_pattern_base:
                            for i in p:
                                cond_item_support[i] += 1
                        
                        cond_frequent_items = {i for i, s in cond_item_support.items() if s >= support_threshold}

                        if cond_frequent_items:
                            cond_header = {i: [s, None] for i, s in cond_item_support.items() if i in cond_frequent_items}
                            cond_root = FPTreeNode(None, 1, None)
                            for p in conditional_pattern_base:
                                sorted_p = sorted([i for i in p if i in cond_frequent_items], key=lambda i: cond_header[i][0], reverse=True)
                                current_node = cond_root
                                for i in sorted_p:
                                    if i in current_node.children:
                                        current_node.children[i].increment(1)
                                    else:
                                        new_node = FPTreeNode(i, 1, current_node)
                                        current_node.children[i] = new_node
                                        if cond_header[i][1] is None:
                                            cond_header[i][1] = new_node
                                        else:
                                            node_ptr = cond_header[i][1]
                                            while node_ptr.node_link is not None:
                                                node_ptr = node_ptr.node_link
                                            node_ptr.node_link = new_node
                                    current_node = current_node.children[i]
                            
                            mine_tree(new_prefix, cond_root, cond_header)

            mine_tree(set(), root, header_table)

        print(f"[INFO] Generated {len(self.combinations)} frequent final_id combinations in total using FP-Growth")
        return self.combinations


    def generate_association_rules(self):
        """
        Generates association rules from the frequent combinations.

        A rule is of the form body -> head, where body and head are disjoint sets of final_ids.
        The rule is generated from a frequent combination C, where body is a subset of C,
        and head = C - body.

        Confidence = support(C) / support(body).
        A rule is kept if its confidence is >= self.confidence.
        """
        if not hasattr(self, "combinations") or not self.combinations:
            print("[INFO] No combinations available to generate rules from.")
            return

        print(f"[INFO] Generating association rules with min_confidence = {self.confidence}")
        self.association_rules = []

        # Group combinations by anchor label
        anchor_to_combos = defaultdict(dict)
        for combo_id, combo_data in self.combinations.items():
            anchor_to_combos[combo_data['anchor_label']][combo_id] = combo_data

        # Group patterns by anchor label to build final_to_anchors map for each
        anchor_to_pids = defaultdict(list)
        for pid, p in self.patterns.items():
            if pid in self.pattern_results:
                anchor_to_pids[p['anchor']].append(pid)

        # Process rules for each anchor label separately
        for anchor_label, combos in anchor_to_combos.items():
            # Build the final_to_anchors map for this anchor label
            final_to_anchors = defaultdict(set)
            pids = anchor_to_pids.get(anchor_label, [])
            for pid in pids:
                for anchor_id, final_ids in self.pattern_results[pid].items():
                    for final_id in final_ids:
                        final_to_anchors[final_id].add(anchor_id)

            if not final_to_anchors:
                continue

            total_anchors = self.anchor_counts.get(anchor_label, 0)

            for combo_id, combo in combos.items():
                full_combo_final_ids = frozenset(combo['final_ids'])
                full_combo_support = combo['support']
                
                # Generate all non-empty, proper subsets of the combination
                for i in range(1, len(full_combo_final_ids)):
                    for body_tuple in combinations(full_combo_final_ids, i):
                        body = frozenset(body_tuple)
                        
                        # Calculate support of the body
                        body_anchor_sets = [final_to_anchors[fid] for fid in body]
                        if not body_anchor_sets:
                            continue
                        
                        body_common_anchors = set.intersection(*body_anchor_sets)
                        body_support = len(body_common_anchors)

                        if body_support == 0:
                            continue

                        confidence = full_combo_support / body_support

                        if confidence >= self.confidence:
                            head = full_combo_final_ids - body
                            rule = {
                                "anchor_label": anchor_label,
                                "body": list(body),
                                "head": list(head),
                                "support": full_combo_support / total_anchors if total_anchors > 0 else 0,
                                "confidence": confidence,
                                "combo_id": combo_id,
                                "full_combination": list(full_combo_final_ids)
                            }
                            self.association_rules.append(rule)

        print(f"[INFO] Generated {len(self.association_rules)} association rules.")

        # Save rules to file
        output_path = os.path.join(self.output_dir, "association_rules.txt")
        with open(output_path, "w") as f:
            json.dump(self.association_rules, f, indent=2)
        print(f"[INFO] Association rules saved to {output_path}")
        
        # Save detailed rules
        self.save_rule_analysis_file()


    def save_rule_analysis_file(self, num_rules_to_save: int = 20):
        """
        Saves a detailed analysis of association rules, showing which patterns
        contribute to the body and head of each rule.
        """
        if not hasattr(self, "association_rules") or not self.association_rules:
            print("[INFO] No association rules to analyze.")
            return

        output_path = os.path.join(self.output_dir, "rule_analysis.txt")
        print(f"[INFO] Saving detailed rule analysis to {output_path}")

        # Build a reverse map from final_id to pattern_id
        final_to_pids = defaultdict(list)
        for pid, results in self.pattern_results.items():
            for anchor, finals in results.items():
                for final_id in finals:
                    if pid not in final_to_pids[final_id]:
                        final_to_pids[final_id].append(pid)

        # Get node names for all relevant nodes once
        all_final_ids = set()
        rules_to_save = self.association_rules[:num_rules_to_save]
        for rule in rules_to_save:
            all_final_ids.update(rule['full_combination'])
        
        node_names = self._get_node_names(list(all_final_ids))

        with open(output_path, "w") as f:
            for i, rule in enumerate(rules_to_save):
                f.write(f"--- Rule {i+1}/{len(rules_to_save)} ---\n")
                f.write(f"  Support: {rule['support']:.6f}\n")
                f.write(f"  Confidence: {rule['confidence']:.4f}\n")
                f.write(f"  Anchor Label: {rule['anchor_label']}\n")
                f.write(f"  Full Combination ID: {rule['combo_id']}\n\n")

                # --- Detailed Pattern-Node Mapping ---

                # 1. Get all pids for the full combination, filtered by anchor
                all_pids_in_combo = set()
                for final_id in rule['full_combination']:
                    pids = final_to_pids.get(final_id, [])
                    for pid in pids:
                        pattern = self.patterns.get(pid)
                        if pattern and pattern['anchor'] == rule['anchor_label']:
                            all_pids_in_combo.add(pid)

                # 2. Create a map of pid -> list of final_ids it finds within this combo
                pid_to_finals_map = defaultdict(list)
                for final_id in rule['full_combination']:
                    pids = final_to_pids.get(final_id, [])
                    for pid in pids:
                        if pid in all_pids_in_combo:
                            pid_to_finals_map[pid].append(final_id)

                # 3. Separate patterns into body and head
                body_pids = set()
                head_pids = set()
                rule_body_set = set(rule['body'])
                rule_head_set = set(rule['head'])

                for pid, finals in pid_to_finals_map.items():
                    if any(fid in rule_body_set for fid in finals):
                        body_pids.add(pid)
                    if any(fid in rule_head_set for fid in finals):
                        head_pids.add(pid)

                # 4. Write the Body Patterns section
                f.write("  Body Patterns:\n")
                if not body_pids:
                    f.write("    (No patterns for body)\n")
                else:
                    for pid in sorted(list(body_pids)):
                        pattern = self.patterns.get(pid)
                        structure = self._get_pattern_structure_str(pattern)
                        f.write(f"    - Pattern: {structure} (ID: {pid})\n")
                        f.write(f"      - Associated Body Nodes in Combination:\n")
                        associated_body_nodes = [fid for fid in pid_to_finals_map[pid] if fid in rule_body_set]
                        for final_id in sorted(associated_body_nodes):
                            name = node_names.get(final_id, "N/A")
                            f.write(f"        - {name} (ID: {final_id})\n")
                
                # 5. Write the Head Patterns section
                f.write("\n  Head Patterns:\n")
                if not head_pids:
                    f.write("    (No patterns for head)\n")
                else:
                    for pid in sorted(list(head_pids)):
                        pattern = self.patterns.get(pid)
                        structure = self._get_pattern_structure_str(pattern)
                        f.write(f"    - Pattern: {structure} (ID: {pid})\n")
                        f.write(f"      - Associated Head Nodes in Combination:\n")
                        associated_head_nodes = [fid for fid in pid_to_finals_map[pid] if fid in rule_head_set]
                        for final_id in sorted(associated_head_nodes):
                            name = node_names.get(final_id, "N/A")
                            f.write(f"        - {name} (ID: {final_id})\n")

                # 6. Keep the full list of nodes for context
                f.write("\n  Full List of Final Node Instances in Combination:\n")
                for final_id in sorted(rule['full_combination']):
                    name = node_names.get(final_id, "N/A")
                    f.write(f"    - {name} (ID: {final_id})\n")
                
                f.write("\n\n")

    def save_results_to_excel(self):
        """
        Saves all results from the mining process into a single multi-sheet Excel file.
        """
        output_path = os.path.join(self.output_dir, "mining_results.xlsx")
        print(f"[INFO] Saving all results to Excel file: {output_path}")

        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            # Sheet 1: User Inputs
            inputs_data = {
                "Parameter": ["Used Nodes", "Used Edges", "Anchor Labels", "Support", "Confidence"],
                "Value": [
                    ", ".join(self.chosen_nodes),
                    ", ".join(self.chosen_edges),
                    ", ".join(self.anchors),
                    self.support,
                    self.confidence
                ]
            }
            df_inputs = pd.DataFrame(inputs_data)
            df_inputs.to_excel(writer, sheet_name='Inputs', index=False)

            # Sheet 2: Built Patterns
            patterns_data = []
            for pid, p in self.patterns.items():
                patterns_data.append({
                    "Pattern ID": pid,
                    "Type": p['kind'],
                    "Anchor": p['anchor'],
                    "Structure": self._get_pattern_structure_str(p)
                })
            df_patterns = pd.DataFrame(patterns_data)
            df_patterns.to_excel(writer, sheet_name='Built Patterns', index=False)

            # Sheet 3: Frequent Pattern Results (Aggregated by Pattern ID with Anchor Count)
            pattern_summary = defaultdict(lambda: {'anchors': set(), 'final_ids': set()})
            for pid, anchors_map in self.pattern_results.items():
                for anchor_id, final_ids in anchors_map.items():
                    pattern_summary[pid]['anchors'].add(anchor_id)
                    pattern_summary[pid]['final_ids'].update(final_ids)

            results_data = []
            for pid, summary in pattern_summary.items():
                results_data.append({
                    "Pattern ID": pid,
                    "Anchor Count": len(summary['anchors']),
                    "Final IDs": "\n".join(map(str, sorted(list(summary['final_ids']))))
                })
            df_results = pd.DataFrame(results_data)
            df_results.to_excel(writer, sheet_name='Frequent Pattern Results', index=False)

            # Sheet 4: Combinations
            final_to_pids = defaultdict(list)
            for pid, results in self.pattern_results.items():
                for anchor, finals in results.items():
                    for final_id in finals:
                        if pid not in final_to_pids[final_id]:
                            final_to_pids[final_id].append(pid)
            
            combos_data = []
            for combo_id, combo in self.combinations.items():
                for final_id in combo['final_ids']:
                    pids = final_to_pids.get(final_id, [])
                    for pid in pids:
                        pattern = self.patterns.get(pid)
                        if pattern and pattern['anchor'] == combo['anchor_label']:
                            combos_data.append({
                                "Combination ID": combo_id,
                                "Anchor Label": combo['anchor_label'],
                                "Final ID": final_id,
                                "Pattern ID": pid,
                                "Pattern Structure": self._get_pattern_structure_str(pattern)
                            })
            df_combos = pd.DataFrame(combos_data)
            df_combos.to_excel(writer, sheet_name='Combinations', index=False)

            # Sheet 5: Final Rules
            rules_data = []
            for i, rule in enumerate(self.association_rules):
                # Body patterns
                body_pids = set()
                for final_id in rule['body']:
                    body_pids.update(final_to_pids.get(final_id, []))
                
                for pid in body_pids:
                    pattern = self.patterns.get(pid)
                    if pattern and pattern['anchor'] == rule['anchor_label']:
                        rules_data.append({
                            "Rule ID": i + 1,
                            "Combination ID": rule['combo_id'],
                            "Anchor": rule['anchor_label'],
                            "Support": rule['support'],
                            "Confidence": rule['confidence'],
                            "Part": "Body",
                            "Pattern ID": pid,
                            "Pattern Structure": self._get_pattern_structure_str(pattern)
                        })

                # Head patterns
                head_pids = set()
                for final_id in rule['head']:
                    head_pids.update(final_to_pids.get(final_id, []))

                for pid in head_pids:
                    pattern = self.patterns.get(pid)
                    if pattern and pattern['anchor'] == rule['anchor_label']:
                        rules_data.append({
                            "Rule ID": i + 1,
                            "Combination ID": rule['combo_id'],
                            "Anchor": rule['anchor_label'],
                            "Support": rule['support'],
                            "Confidence": rule['confidence'],
                            "Part": "Head",
                            "Pattern ID": pid,
                            "Pattern Structure": self._get_pattern_structure_str(pattern)
                        })
            df_rules = pd.DataFrame(rules_data)
            df_rules.to_excel(writer, sheet_name='Final Rules', index=False)

    def _get_node_names(self, node_ids: List[str]) -> Dict[str, str]:
        """Helper to fetch names for a list of node IDs."""
        if not self.driver or not node_ids:
            return {}
        
        query = """
        MATCH (n)
        WHERE elementId(n) IN $ids
        RETURN elementId(n) AS node_id, n.name AS name
        """
        with self.driver.session() as session:
            res = session.run(query, {"ids": node_ids})
            return {r['node_id']: r['name'] for r in res}
        """Helper to fetch names for a list of node IDs."""
        if not self.driver or not node_ids:
            return {}
        
        query = """
        MATCH (n)
        WHERE elementId(n) IN $ids
        RETURN elementId(n) AS node_id, n.name AS name
        """
        with self.driver.session() as session:
            res = session.run(query, {"ids": node_ids})
            return {r['node_id']: r['name'] for r in res}

    def _get_pattern_structure_str(self, pattern: Dict[str, Any]) -> str:
        """Helper to get a string representation of a pattern's structure."""
        structure = []
        if pattern['kind'] == 'anyrel':
            structure.append(f"({pattern['anchor']})-[*ANYREL*]->({pattern['node_labels'][0]})")
        else:
            for i, lbl in enumerate(pattern["node_labels"]):
                if i < len(pattern["rel_types"]):
                    rel = pattern["rel_types"][i]
                    structure.append(f"({lbl})-[{rel}]->")
                else:
                    structure.append(f"({lbl})")
        return "".join(structure)


    def describe_pattern(self, pattern_id: str):
        """
        Print detailed information about a specific pattern:
          - Kind (normal, count, anyrel)
          - Anchor label
          - Structure (node_labels + rel_types)
          - Instances: anchors and their matched final node IDs
        """
        if not hasattr(self, "patterns") or pattern_id not in self.patterns:
            print(f"[ERROR] Pattern {pattern_id} not found.")
            return

        pattern = self.patterns[pattern_id]
        print(f"\n=== Pattern {pattern_id} ===")
        print(f"Kind: {pattern['kind']}")
        print(f"Anchor: {pattern['anchor']}")
        print("Structure:")
        for i, lbl in enumerate(pattern["node_labels"]):
            if pattern['kind'] == 'anyrel':
                print(f"  ({pattern['anchor']}) -[*ANYREL*]-> ({lbl})")
            elif i < len(pattern["rel_types"]):
                rel = pattern["rel_types"][i]
                print(f"  ({lbl}) -[{rel}]->", end=" ")
            else:
                print(f"({lbl})")

        # Print instances
        if not hasattr(self, "pattern_results") or pattern_id not in self.pattern_results:
            print("[INFO] No query results stored for this pattern yet.")
            return

        results = self.pattern_results[pattern_id]
        print("\nInstances:")
        for anchor, finals in results.items():
            finals_str = ", ".join(map(str, finals)) if finals else "∅"
            print(f"  Anchor {anchor}: {finals_str}")
            gramx.inspect_instances([anchor] + list(finals), 'name')

    def describe_combination(self, combo_id: str):
        """
        Print detailed information about a specific combination:
          - Involved final_ids
          - Common anchors
          - Support (anchor count)
        """
        if not hasattr(self, "combinations") or combo_id not in self.combinations:
            print(f"[ERROR] Combination {combo_id} not found.")
            return

        combo = self.combinations[combo_id]
        print(f"\n=== Combination {combo_id} ===")
        print(f"Support: {combo['support']}")
        print(f"Final IDs: {sorted(list(combo['final_ids']))}")
        print(f"Shared anchors: {sorted(list(combo['anchors']))}")
        
        print("\n--- Involved Nodes ---")
        print("Final Nodes:")
        self.inspect_instances(list(combo['final_ids']), 'name')
        print("\nAnchor Nodes:")
        self.inspect_instances(list(combo['anchors']), 'name')

    def describe_pattern_results_from_file(self, num_instances: int, property_name: str = 'name'):
        """
        Reads pattern results from the output file, and for each pattern,
        describes it and shows a sample of instances.
        """
        results_path = os.path.join(self.output_dir, "patterns_results.txt")
        if not os.path.exists(results_path):
            print(f"[ERROR] File not found: {results_path}")
            return

        with open(results_path, "r") as f:
            pattern_results = json.load(f)

        if not hasattr(self, "patterns"):
            print("[ERROR] Patterns not loaded. Run build_forest() or load them first.")
            return

        print(f"\n--- Describing Pattern Results (showing up to {num_instances} instances per anchor) ---")

        for pid, results in pattern_results.items():
            
            if pid not in self.patterns:
                print(f"\n=== Pattern {pid} (details not found in self.patterns) ===")
                continue

            pattern = self.patterns[pid]
            print(f"\n=== Pattern {pid} ===")
            print(f"Kind: {pattern['kind']}")
            print(f"Anchor: {pattern['anchor']}")
            
            # Building and printing the structure string
            structure = []
            if pattern['kind'] == 'anyrel':
                structure.append(f"({pattern['anchor']})-[*ANYREL*]->({pattern['node_labels'][0]})")
            else:
                for i, lbl in enumerate(pattern["node_labels"]):
                    if i < len(pattern["rel_types"]):
                        rel = pattern["rel_types"][i]
                        structure.append(f"({lbl})-[{rel}]->")
                    else:
                        structure.append(f"({lbl})")
            print("Structure: " + "".join(structure))

            print("\nInstances:")
            if not results:
                print("  No instances found in the results file for this pattern.")
                continue
            i = 0
            for anchor_id_str, final_ids in results.items():
                if i>num_instances:
                    continue
                # anchor_id = int(anchor_id_str)
                anchor_id = anchor_id_str
                
                # Take a sample of final_ids
                sampled_final_ids = final_ids[:num_instances]
                
                if not sampled_final_ids:
                    print(f"  Anchor {anchor_id}: No final nodes to display.")
                    continue

                finals_str = ", ".join(map(str, sampled_final_ids))
                print(f"  Anchor {anchor_id} -> Finals: {{{finals_str}}}")

                # Inspect properties
                nodes_to_inspect = [anchor_id] + sampled_final_ids
                self.inspect_instances(nodes_to_inspect, property_name)
                i = i+1

    def inspect_instances(self, node_ids: List[int], property_name: str):
        """
        Given a list of node IDs, query Neo4j for the chosen property and print results.
        Example:s
            inspect_instances([123,456], 'name')
        """
        if not self.driver:
            raise ConnectionError("You must connect to Neo4j before inspecting instances.")

        if not node_ids:
            print("[INFO] No node IDs provided.")
            return

        query = f"""
        MATCH (n)
        WHERE elementId(n) IN $ids
        RETURN elementId(n) AS node_id, n.{property_name} AS property
        """
        with self.driver.session() as session:
            res = session.run(query, {"ids": node_ids})
            rows = list(res)

        if not rows:
            print("[INFO] No nodes found for given IDs.")
            return

        print(f"\n=== Node inspection for property '{property_name}' ===")
        for r in rows:
            print(f"  Node {r['node_id']}: {r['property']}")


# gramx = GRAMXNeo4j(uri="neo4j://127.0.0.1:7687", user="neo4j", password="password")
gramx = GRAMXNeo4j(uri="neo4j://localhost:37686", user="neo4j", password="mineGraphRule")


gramx.connect()
gramx.extract_schema()
# gramx.trim_schema(chosen_nodes=["Artist", "Song", "Playlist", "Type", "Genre"],
#                   chosen_edges=["SING", "OF", "IN", "LABELLED"])
gramx.trim_schema(chosen_nodes=["Artist", "Song", "Type", "Genre", "Playlist"],
                   chosen_edges=["SING", "OF", "IN", "LABELLED"])

gramx.set_anchors(["Artist", "Song"])
gramx.query_anchor_counts()

gramx.set_support_and_confidence(0.001, 0.1)

gramx.build_forest()

output_dir = "./data/output"
if not os.path.exists(output_dir):
    os.makedirs(output_dir)

with open(os.path.join(output_dir, "forest.txt"), "w") as f:
    json.dump(gramx.forest, f, indent=2)

with open(os.path.join(output_dir, "patterns.txt"), "w") as f:
    json.dump(gramx.patterns, f, indent=2)

print(f"[INFO] Forest and patterns saved to {output_dir}")

# Time the original combine_results function
start_time = time.time()
gramx.execute_pattern_queries(1)
end_time = time.time()
print(f"\n[INFO] Execution of patterns of lenght 1 took: {end_time - start_time:.4f} seconds")

start_time = time.time()
gramx.execute_pattern_queries(2)
end_time = time.time()
print(f"\n[INFO] Execution of patterns of lenght 2 took: {end_time - start_time:.4f} seconds")


with open(os.path.join(output_dir, "patterns_results.txt"), "w") as f:
    serializable_results = {
        pid: {anchor: list(finals) for anchor, finals in res.items()}
        for pid, res in gramx.pattern_results.items()
    }
    json.dump(serializable_results, f, indent=2)

print(f"[INFO] Pattern results saved to {output_dir}")

# gramx.describe_pattern_results_from_file(2)

# Time the original combine_results function
start_time = time.time()
gramx.combine_results()
end_time = time.time()
print(f"\n[INFO] Apriori-like combine_results took: {end_time - start_time:.4f} seconds")

# Reset combinations and time the FP-Growth function
# gramx.combinations = {}
# start_time = time.time()
# gramx.combine_results_fpgrowth()
# end_time = time.time()
# print(f"[INFO] FP-Growth combine_results_fpgrowth took: {end_time - start_time:.4f} seconds")

# if gramx.combinations:
    # Describe the first combination found by FP-Growth
    # first_combo_id = list(gramx.combinations.keys())[0]
    # gramx.describe_combination(first_combo_id)

start_time = time.time()
gramx.generate_association_rules()
end_time = time.time()
print(f"\n[INFO] Generation of Association Rules took: {end_time - start_time:.4f} seconds")

# Save all results to a single Excel file
gramx.save_results_to_excel()

gramx.close()

