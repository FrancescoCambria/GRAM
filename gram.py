from neo4j import GraphDatabase
import json
import os
import time
from typing import List, Dict, Any, Optional, Tuple, Set
from collections import defaultdict
import itertools
import uuid
import pandas as pd
import math

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


class GramxNeo4j:
    """
    GRAMXNeo4j connects to a Neo4j database and extracts graph schema information.
    It supports:
      1. Connecting to Neo4j.
      2. Querying and saving the schema.
      3. Trimming the schema by removing nodes/edges.
      4. Storing anchor labels for rule extraction.
    """

    def __init__(self, uri: str, user: str, password: str, output_dir: str = "./data/output", conditions: Dict[str, List[str]] = None, database_type: str = "neo4j"):
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
        self.relationship_cardinality: Set[str] = set()
        self.conditions = conditions if conditions else {}
        self.database_type = database_type.lower()
        print(f"[INFO] Initialized GramxNeo4j with database_type='{self.database_type}'")

    # 1️⃣ Connect to Neo4j
    def connect(self):
        """Open a connection to the Neo4j database."""
        if not self.driver:
            try:
                self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
                self.driver.verify_connectivity()
                print("[INFO] Connection to Neo4j established.")
            except Exception as e:
                raise ConnectionError(f"Could not connect to Neo4j at {self.uri}. "
                                      f"Please check if the database is running and if the credentials are correct. Error: {e}")
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

        if self.database_type == "memgraph":
             self._extract_schema_memgraph()
        else:
             self._extract_schema_neo4j()

        print(self.schema)
        self.get_relationship_cardinality()
        print(f"[INFO] Schema extracted ({self.database_type} mode)")

    def _extract_schema_neo4j(self):
        """Native Neo4j schema extraction."""
        with self.driver.session() as session:
            result = session.run("CALL db.schema.visualization()")
            record = result.single()
            if not record:
                self.schema = {"nodes": [], "relationships": []}
                return

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

    def _extract_schema_memgraph(self):
        """
        Custom schema extraction for Memgraph (or generic Cypher).
        Reconstructs the schema by querying existing patterns.
        """
        with self.driver.session() as session:
            # 1. Get Nodes (Labels)
            # We assign arbitrary IDs to these "schema nodes" since Memgraph doesn't return schema objects with IDs
            nodes_query = "MATCH (n) UNWIND labels(n) as l RETURN distinct l"
            nodes_res = session.run(nodes_query)
            
            schema_nodes = []
            label_to_id = {}
            current_id = 0
            
            for record in nodes_res:
                label = record["l"]
                # Create a synthetic ID
                s_id = f"s_node_{current_id}"
                schema_nodes.append({
                    "id": s_id,
                    "label": label
                })
                label_to_id[label] = s_id
                current_id += 1
                
            # 2. Get Relationships
            # MATCH (a)-[r]->(b) RETURN distinct labels(a), type(r), labels(b)
            # This can be slow on huge graphs. 
            # Optimization: Use `CALL db.relationshipTypes()` and sample? 
            # Memgraph usually handles `MATCH (a)-[r]->(b) RETURN distinct ...` okay-ish if not massive. 
            # Ideally we'd use `SHOW SCHEMA` but parsing that output text is harder. 
            
            rels_query = """
            MATCH (a)-[r]->(b) 
            RETURN distinct labels(a)[0] as start_lbl, type(r) as type, labels(b)[0] as end_lbl
            """
            rels_res = session.run(rels_query)
            
            schema_rels = []
            r_id_counter = 0
            
            for record in rels_res:
                start_lbl = record["start_lbl"]
                end_lbl = record["end_lbl"]
                r_type = record["type"]
                
                if start_lbl in label_to_id and end_lbl in label_to_id:
                    schema_rels.append({
                        "id": f"s_rel_{r_id_counter}",
                        "type": r_type,
                        "start": label_to_id[start_lbl],
                        "end": label_to_id[end_lbl]
                    })
                    r_id_counter += 1

            self.schema = {
                "nodes": schema_nodes,
                "relationships": schema_rels
            }

    def get_relationship_cardinality(self):
        """
        Identifies relationship types that are NOT many-to-one and stores them in a set.
        A relationship is considered many-to-one if a node can have multiple
        incoming relationships of that same type.
        """
        if not self.driver:
            raise ConnectionError("You must connect to Neo4j before getting relationship cardinality.")
        
        if not self.schema:
            raise ValueError("Schema not extracted yet. Run extract_schema() first.")

        self.relationship_cardinality = set()

        with self.driver.session() as session:
            all_rel_types = set(r['type'] for r in self.schema['relationships'])
            
            for rel_type in all_rel_types:
                query = f"""
                MATCH ()-[r:`{rel_type}`]->(m)
                WITH m, count(r) AS incoming_count
                RETURN max(incoming_count) AS max_incoming
                """
                result = session.run(query)
                record = result.single()
                max_incoming = record["max_incoming"] if record and record["max_incoming"] is not None else 0

                if max_incoming <= 1:
                    self.relationship_cardinality.add(rel_type)

        print(f"[DEBUG] Relationship types identified as NOT many-to-one: {self.relationship_cardinality}")

    def get_schema_for_cytoscape(self):
        """
        Get the schema in a format that Cytoscape.js can understand.
        """
        if not self.driver:
            self.connect()
        
        if not self.schema:
            self.extract_schema()

        # The schema is now in self.schema, so we can use it directly
        nodes = self.schema["nodes"]
        relationships = self.schema["relationships"]

        cytoscape_elements = []
        colors = ["#c6e9af", "#aaeeff", "#ffeeaa", "#ffd5d5", "#87aade"]
        label_colors = {}
        
        unique_labels = list(set([n['label'] for n in nodes]))
        for i, label in enumerate(unique_labels):
            label_colors[label] = colors[i%len(colors)]

        for node in nodes:
            label = node['label']
            cytoscape_elements.append({
                "data": {"id": f"n{node['id']}",
                          "label": label,
                          "color": label_colors.get(label, "#666")}
            })

        for rel in relationships:
            cytoscape_elements.append({
                "data": {
                    "id": f"r{rel['id']}",
                    "source": f"n{rel['start']}",
                    "target": f"n{rel['end']}",
                    "label": rel["type"],
                    "cardinality": "not-many-to-one" if rel["type"] in self.relationship_cardinality else "many-to-one"
                }
            })

        return cytoscape_elements
            
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
            self.threshold_support_counts[anchor_label] = int(math.ceil(count * support))
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
                     rel_type_seq: List[Optional[str]], parent: Optional[str] = None, dependent_on: Optional[str] = None):
        pid = str(uuid.uuid4())
        pat = {
            "id": pid,
            "kind": kind,
            "anchor": anchor_label,
            "node_labels": node_label_seq,
            "rel_types": rel_type_seq,
            "parent": parent,
            "dependent_on": dependent_on
        }
        self.patterns[pid] = pat
        self.forest[anchor_label].append(pid)
        return pid

    def _check_forced_path(self, node_id: str) -> bool:
        """
        Check if a node has only one outgoing edge type in the trimmed schema.
        """
        outgoing_edge_types = set()
        for rel in self.trimmed_schema['relationships']:
            if rel['start'] == node_id:
                outgoing_edge_types.add(rel['type'])
        return len(outgoing_edge_types) == 1

    def are_all_paths_not_many_to_one(self, anchor_label: str, length: int) -> bool:
        """
        Checks if all paths of a given length from an anchor label have their final
        relationship as NOT many-to-one.
        """
        if not self.trimmed_schema:
            raise ValueError("Trimmed schema missing. Run trim_schema() first.")

        nodes_by_id = {n["id"]: n["label"] for n in self.trimmed_schema["nodes"]}
        adj = defaultdict(list)
        for r in self.trimmed_schema["relationships"]:
            adj[r["start"]].append((r["type"], r["end"]))

        start_node_ids = [n["id"] for n in self.trimmed_schema["nodes"] if n["label"] == anchor_label]

        if not start_node_ids:
            return True

        found_any_path = False

        for start_node_id in start_node_ids:
            queue = [([start_node_id], [])]

            while queue:
                path, rel_types_in_path = queue.pop(0)

                if len(path) - 1 == length:
                    if not rel_types_in_path:
                        continue
                    
                    found_any_path = True
                    last_rel_type = rel_types_in_path[-1]
                    
                    if last_rel_type not in self.relationship_cardinality:
                        return False
                    continue

                if len(path) - 1 < length:
                    last_node_id = path[-1]
                    for r_type, next_node_id in adj.get(last_node_id, []):
                        if next_node_id not in path:
                            new_path = path + [next_node_id]
                            new_rel_types = rel_types_in_path + [r_type]
                            queue.append((new_path, new_rel_types))
        
        return True

    def check_pattern_cardinality(self, pattern: Dict[str, Any]) -> bool:
        """
        Checks the cardinality of a pattern based on its type.
        - For 'anyrel', checks if all paths of that length are not many-to-one at the last step.
        - For 'normal', checks if the last relationship in the pattern is not many-to-one.
        """
        if pattern['kind'] == 'anyrel':
            anchor = pattern['anchor']
            length = len(pattern['rel_types'])
            return self.are_all_paths_not_many_to_one(anchor, length)
        
        elif pattern['kind'] == 'normal':
            rel_types = pattern.get('rel_types')
            if not rel_types:
                return True  # No relationships, so no cardinality to check
            
            last_rel_type = rel_types[-1]
            return last_rel_type in self.relationship_cardinality
            
        return True

    def build_forest(self, max_depth: int = 3):
        """
        Build a forest of patterns for each anchor using the trimmed_schema.
        max_depth: maximum path length (# of relationships).
        Result stored in self.patterns (dict pattern_id -> pattern metadata)
        and self.forest (anchor_label -> list of pattern_ids for that anchor).
        """
        if not self.trimmed_schema:
            raise ValueError("Trimmed schema missing. Run trim_schema() first.")

        nodes_by_id = {n["id"]: n["label"] for n in self.trimmed_schema["nodes"]}
        adj = defaultdict(list)
        for r in self.trimmed_schema["relationships"]:
            adj[r["start"]].append((r["type"], r["end"]))

        self.patterns: Dict[str, Dict[str, Any]] = {}
        self.forest: Dict[str, List[str]] = defaultdict(list)
        pattern_lookup: Dict[Tuple, str] = {}

        for anchor_label in self.anchors:
            start_node_ids = [n["id"] for n in self.trimmed_schema["nodes"] if n["label"] == anchor_label]

            for sid in start_node_ids:
                queue = [([sid], [])]
                
                while queue:
                    path, rel_types_in_path = queue.pop(0)
                    rel_len = len(path) - 1

                    if rel_len > max_depth:
                        continue

                    node_label_seq = [nodes_by_id[nid] for nid in path]
                    
                    if rel_len >= 1:
                        rel_type_seq = rel_types_in_path
                        
                        # Normal pattern
                        if all(rt is not None for rt in rel_type_seq):
                            parent_pid = None
                            if rel_len > 1:
                                parent_key = (anchor_label, "normal", tuple(node_label_seq[:-1]), tuple(rel_type_seq[:-1]))
                                parent_pid = pattern_lookup.get(parent_key)

                            # Create a temporary pattern to check cardinality
                            temp_pattern = {
                                "kind": "normal",
                                "anchor": anchor_label,
                                "rel_types": rel_type_seq
                            }
                            
                            dependent_on = parent_pid if self.check_pattern_cardinality(temp_pattern) else None
                            
                            pid = self._new_pattern("normal", anchor_label, node_label_seq, rel_type_seq, parent=parent_pid, dependent_on=dependent_on)
                            pattern_lookup[(anchor_label, "normal", tuple(node_label_seq), tuple(rel_type_seq))] = pid
                            if dependent_on:
                                print(f"[DEBUG] Created normal pattern {pid} (dependent on {dependent_on}) because last rel is NOT many-to-one.")
                            else:
                                print(f"[DEBUG] Created normal pattern {pid} (no dependency) because last rel IS many-to-one.")


                        # Anyrel pattern
                        anyrel_node_labels = [node_label_seq[-1]]
                        anyrel_key = (anchor_label, "anyrel", tuple(anyrel_node_labels), tuple([None] * rel_len))

                        if anyrel_key not in pattern_lookup:
                            pid = self._new_pattern("anyrel", anchor_label, anyrel_node_labels, [None] * rel_len, parent=None, dependent_on=None)
                            pattern_lookup[anyrel_key] = pid

                    if rel_len < max_depth:
                        last_node_id = path[-1]
                        for r_type, next_node_id in adj.get(last_node_id, []):
                            if next_node_id not in path:
                                new_path = path + [next_node_id]
                                queue.append((new_path, rel_types_in_path + [r_type]))
        
        print(f"[INFO] Built forest with {len(self.patterns)} patterns for anchors {self.anchors}")

    def _get_element_id_str(self, alias):
        """Helper to get ID selection string based on DB type."""
        if self.database_type == "memgraph":
            return f"toString(id({alias}))"
        else:
            return f"elementId({alias})"

    # -------------------------
    # Cypher builders and query
    # -------------------------
    def _build_cypher_for_pattern(self, pattern: Dict[str, Any], anchor_threshold: int) -> Tuple[str, Dict[str, Any]]:
        """
        Build a Cypher query that returns the elementId() of the final node and the anchor node id
        for final_ids that are frequent (i.e., supported by enough anchors).
        Returns (cypher_string, params)
        The query returns rows: { final_id: elementId(final), anchor_id: elementId(anchor) }
        """
        anchor_label = pattern["anchor"]
        node_labels = pattern["node_labels"]
        rel_types = pattern["rel_types"]

        aliases = [f"n{i}" for i in range(len(node_labels))]
        
        conditions = self.conditions if self.conditions else {}
        where_conditions = []

        # Build match path, starting with the anchor
        match_str = f"MATCH p = ({aliases[0]}:{anchor_label})"
        
        # Check conditions for anchor (n0)
        if anchor_label in conditions:
            for cond in conditions[anchor_label]:
                where_conditions.append(f"{aliases[0]}.{cond}")

        for i in range(len(rel_types)):
            rt = rel_types[i]
            # Use directed relationship as implied by the path structure
            rel_alias = f"r{i}"
            rel_part = f"-[{rel_alias}:{rt}]->" if rt else "-->"
            match_str += rel_part + f"({aliases[i+1]}:{node_labels[i+1]})"

            # Conditions for relationship
            if rt and rt in conditions:
                 for cond in conditions[rt]:
                    where_conditions.append(f"{rel_alias}.{cond}")

            # Conditions for next node
            next_label = node_labels[i+1]
            if next_label in conditions:
                for cond in conditions[next_label]:
                    where_conditions.append(f"{aliases[i+1]}.{cond}")
        
        cypher = match_str

        if where_conditions:
            cypher += "\nWHERE " + " AND ".join(where_conditions)

        # ID extraction logic
        final_id_expr = self._get_element_id_str("final")
        anchor_id_expr = self._get_element_id_str("anchor")

        cypher += (
            # Extract final and anchor nodes from each path
            "\nWITH nodes(p)[-1] as final, nodes(p)[0] as anchor"
            # Group by final node and collect all its anchors
            + "\nWITH final, collect(DISTINCT anchor) as unique_anchors"
            # Filter for final nodes that meet the support threshold
            + "\nWHERE size(unique_anchors) >= $anchor_threshold"
            # Unwind the qualifying anchors to create the (final, anchor) pairs for the result
            + "\nUNWIND unique_anchors as anchor"
            + f"\nRETURN {final_id_expr} AS final_id, {anchor_id_expr} AS anchor_id"
        )
        
        params = {"anchor_threshold": anchor_threshold}
        return cypher, params

    def _build_cypher_for_anyrel_pattern(self, pattern: Dict[str, Any], anchor_threshold: int) -> Tuple[str, Dict[str, Any]]:
        """
        Build a Cypher query that returns the elementId() of the final node and the anchor node id for an anyrel pattern,
        ensuring the final_ids are frequent.
        """
        anchor_label = pattern["anchor"]
        node_label = pattern["node_labels"][0]
        rel_len = len(pattern["rel_types"])

        conditions = self.conditions if self.conditions else {}
        where_conditions = []

        match_str = f"MATCH (anchor:{anchor_label})-[*1..{rel_len}]->(final:{node_label})"

        if anchor_label in conditions:
            for cond in conditions[anchor_label]:
                where_conditions.append(f"anchor.{cond}")
        
        if node_label in conditions:
            for cond in conditions[node_label]:
                where_conditions.append(f"final.{cond}")

        cypher = match_str

        if where_conditions:
            cypher += "\nWHERE " + " AND ".join(where_conditions)

        # ID extraction logic
        final_id_expr = self._get_element_id_str("final")
        anchor_id_expr = self._get_element_id_str("anchor")

        cypher += (
            # After matching, we have pairs of (anchor, final)
            "\nWITH final, anchor"
            # Group by final node and collect its anchors
            + "\nWITH final, collect(DISTINCT anchor) as unique_anchors"
            # Filter for final nodes that meet the support threshold
            + "\nWHERE size(unique_anchors) >= $anchor_threshold"
            # Unwind the qualifying anchors to create the (final, anchor) pairs
            + "\nUNWIND unique_anchors as anchor"
            + f"\nRETURN {final_id_expr} AS final_id, {anchor_id_expr} AS anchor_id"
        )

        params = {"anchor_threshold": anchor_threshold}
        return cypher, params

    def execute_pattern_queries(self, length: int, count_threshold: int = 2, anchor: Optional[str] = None):
        """
        Execute all patterns of given length with dependency logic.
        If an anchor is provided, only patterns for that anchor will be executed.
        Stores results in self.pattern_results and self.final_to_anchors.
        """
        if not self.driver:
            raise ConnectionError("Connect to Neo4j before executing queries.")
        if not hasattr(self, "patterns"):
            raise ValueError("Patterns not built. Run build_forest() first.")

        patterns_to_execute = {pid: p for pid, p in self.patterns.items()
                               if (p['anchor'] == anchor if anchor else True) and 
                                  (len(p["node_labels"]) == length + 1 or 
                                   (p["kind"] == "anyrel" and len(p["rel_types"]) == length))}

        if not hasattr(self, "pattern_results"):
            self.pattern_results = {}

        # New: Track successful patterns for dependency checks
        if not hasattr(self, "_successful_patterns"):
            self._successful_patterns = set()

        with self.driver.session() as session:
            for pid, p in patterns_to_execute.items():
                # Dependency check
                if p.get('dependent_on') and p['dependent_on'] not in self._successful_patterns:
                    print(f"[DEBUG] Skipping pattern {pid} (kind: {p['kind']}) dependent on {p['dependent_on']} "
                          f"because parent was not successful.")
                    continue
                
                print(f"[DEBUG] Executing pattern {pid} (kind: {p['kind']}). Parent: {p.get('dependent_on', 'None')}")

                anchor_threshold = self.threshold_support_counts.get(p['anchor'], 0)
                if p["kind"] == "count":
                    continue
                elif p["kind"] == "anyrel":
                    cypher, params = self._build_cypher_for_anyrel_pattern(p, anchor_threshold=anchor_threshold)
                else:
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
                    self._successful_patterns.add(pid) # Add to successful patterns
                    print(f"[DEBUG] Pattern {pid} successful. Added to successful_patterns.")
                else:
                    print(f"[DEBUG] Pattern {pid} found no results.")

        print(f"[INFO] Executed {len(self.pattern_results)} patterns (length={length}, anchor={anchor or 'all'})")

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
            support_threshold = int(math.ceil(self.anchor_counts.get(anchor_label, 0) * self.support))
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
            support_threshold = int(math.ceil(self.anchor_counts.get(anchor_label, 0) * self.support))
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
                if len(prefix) >= max_combination_size:
                    return
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
                
                # 5. Write the Head Patterns section
                f.write("\n  Head Patterns:\n")
                if not head_pids:
                    f.write("    (No patterns for head)\n")
                else:
                    for pid in sorted(list(head_pids)):
                        pattern = self.patterns.get(pid)
                        structure = self._get_pattern_structure_str(pattern)
                        f.write(f"    - Pattern: {structure} (ID: {pid})\n")

                # 6. Keep the full list of nodes for context
                f.write("\n  Full List of Final Node Instances in Combination:\n")
                for final_id in sorted(rule['full_combination']):
                    name = node_names.get(final_id, "N/A")
                    f.write(f"        - {name} (ID: {final_id})\n")
                
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

    def combine_selected_patterns(self, selected_pattern_ids: List[str], max_combination_size: int = 3):
        """
        Finds frequent itemsets of pattern instances using the FP-Growth algorithm,
        adapted for selected patterns.
        """
        if not hasattr(self, "pattern_results"):
            raise ValueError("No pattern results found. Run execute_pattern_queries() first.")

        # Filter pattern_results to only include selected patterns
        filtered_pattern_results = {pid: self.pattern_results[pid] for pid in selected_pattern_ids if pid in self.pattern_results}

        if not filtered_pattern_results:
            return []

        # Group patterns by anchor label, as combinations are only valid within the same anchor
        anchor_to_pids = defaultdict(list)
        for pid in filtered_pattern_results.keys():
            if pid in self.patterns:
                anchor_to_pids[self.patterns[pid]['anchor']].append(pid)

        all_frequent_item_sets = []

        # For each anchor label, run the FP-Growth algorithm
        for anchor_label, pids in anchor_to_pids.items():
            support_threshold = int(math.ceil(self.anchor_counts.get(anchor_label, 0) * self.support))
            print(f"[INFO] Running FP-Growth for anchor '{anchor_label}' with support threshold {support_threshold}")

            # 1. Build transactions using a composite "pattern-instance-id"
            anchor_to_pattern_instances = defaultdict(set)
            for pid in pids:
                if pid in filtered_pattern_results:
                    for anchor_id, final_ids in filtered_pattern_results[pid].items():
                        for final_id in final_ids:
                            pattern_instance_id = f"{pid}::{final_id}"
                            anchor_to_pattern_instances[anchor_id].add(pattern_instance_id)

            transactions = [frozenset(instances) for instances in anchor_to_pattern_instances.values()]
            if not transactions:
                continue

            # 2. First pass: find frequent items and their support
            item_support = defaultdict(int)
            for transaction in transactions:
                for item in transaction:
                    item_support[item] += 1
            
            frequent_items = {item for item, support in item_support.items() if support >= support_threshold}
            if not frequent_items:
                continue

            # 3. Build FP-tree
            header_table = {item: [support, None] for item, support in item_support.items() if item in frequent_items}
            def get_support(item):
                return header_table[item][0]

            root = FPTreeNode(None, 1, None)
            for transaction in transactions:
                frequent_transaction = sorted([item for item in transaction if item in frequent_items], key=get_support, reverse=True)
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
                            node = header_table[item][1]
                            while node.node_link is not None:
                                node = node.node_link
                            node.node_link = new_node
                    current_node = current_node.children[item]

            # 4. Mine FP-tree
            def mine_tree(prefix, header, current_frequent_item_sets):
                if len(prefix) >= max_combination_size:
                    return
                sorted_items = sorted(list(header.keys()), key=lambda i: header[i][0])
                for item in sorted_items:
                    new_prefix = prefix.copy()
                    new_prefix.add(item)
                    
                    support = header[item][0]
                    
                    current_frequent_item_sets.append({
                        "items": list(new_prefix),
                        "support": support,
                        "anchor_label": anchor_label
                    })

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
                    
                    if conditional_pattern_base:
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
                            
                            mine_tree(new_prefix, cond_header, current_frequent_item_sets)

            anchor_frequent_sets = []
            mine_tree(set(), header_table, anchor_frequent_sets)
            all_frequent_item_sets.extend(anchor_frequent_sets)


        # --- Categorization Step ---
        categorized_combinations = defaultdict(lambda: {"count": 0, "combinations": []})
        for combo_data in all_frequent_item_sets:
            # We only care for combinations of 2 or more patterns
            if len(combo_data["items"]) < 2:
                continue

            structure_counts = defaultdict(int)
            for pattern_instance_id in combo_data["items"]:
                pid, _ = pattern_instance_id.split("::", 1)
                if pid in self.patterns:
                    structure_str = self._get_pattern_structure_str(self.patterns[pid])
                    structure_counts[structure_str] += 1
            
            structure_signature = frozenset(structure_counts.items())
            signature_key = str(sorted(list(structure_signature)))

            categorized_combinations[signature_key]["count"] += 1
            categorized_combinations[signature_key]["combinations"].append(combo_data)
            if "structure" not in categorized_combinations[signature_key]:
                 categorized_combinations[signature_key]["structure"] = dict(structure_counts)

        # Convert to list of objects for the frontend
        result_list = [
            {
                "structure": value["structure"],
                "count": value["count"],
                "support": value["combinations"][0]["support"] if value["combinations"] else 0,
                "combinations": value["combinations"]
            }
            for key, value in categorized_combinations.items() if value["combinations"]
        ]

        return result_list

    def _get_node_names(self, node_ids: List[str]) -> Dict[str, str]:
        """Helper to fetch names for a list of node IDs."""
        if not self.driver or not node_ids:
            return {}
        
        id_expr = self._get_element_id_str("n")
        query = f"""
        MATCH (n)
        WHERE {id_expr} IN $ids
        RETURN {id_expr} AS node_id, n.name AS name
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
            self.inspect_instances([anchor] + list(finals), 'name')

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

    

            id_expr = self._get_element_id_str("n")
            query = f"""

            MATCH (n)

            WHERE {id_expr} IN $ids

            RETURN {id_expr} AS node_id, n.{property_name} AS property

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

    

    def generate_rules_for_selection(self, selected_combinations: List[Dict]):
        """
        Generates association rules from a specific selection of combinations,
        typically belonging to a single "combination type" selected in the UI.
        """
        if not hasattr(self, "pattern_results") or not self.pattern_results:
            print("[WARN] Rule generation called without pattern results.")
            return []

        # 1. Build the inverted index of item (pid::final_id) -> set(anchor_ids)
        # This allows precise support calculation for specific pattern instances.
        item_to_anchors = defaultdict(set)
        for pid, presults in self.pattern_results.items():
            for anchor_id, final_ids in presults.items():
                for final_id in final_ids:
                    item_key = f"{pid}::{final_id}"
                    item_to_anchors[item_key].add(anchor_id)
        
        if not item_to_anchors:
            print("[WARN] Rule generation called, but item_to_anchors map is empty.")
            return []

        # 2. Generate rules from the provided combinations
        all_rules = []
        for combo_data in selected_combinations:
            if 'items' not in combo_data or 'support' not in combo_data or 'anchor_label' not in combo_data:
                continue

            items_in_combo = frozenset(combo_data['items'])
            support_of_combo = combo_data['support']
            anchor_label = combo_data['anchor_label']
            total_anchors = self.anchor_counts.get(anchor_label, 1)

            # Generate all non-empty, proper subsets for the rule body based on ITEMS, not just final IDs
            for i in range(1, len(items_in_combo)):
                for body_items_tuple in combinations(items_in_combo, i):
                    body_items = frozenset(body_items_tuple)
                    head_items = items_in_combo - body_items

                    if not body_items: continue
                    
                    # Calculate support for the body (intersection of anchors for all items in body)
                    body_anchor_sets = [item_to_anchors[item] for item in body_items if item in item_to_anchors]
                    
                    # If any item in the body has no anchors (shouldn't happen if valid), support is 0
                    if len(body_anchor_sets) != len(body_items): 
                        continue

                    support_of_body = len(set.intersection(*body_anchor_sets))
                    if support_of_body == 0: continue

                    confidence = support_of_combo / support_of_body

                    if confidence >= self.confidence:
                        # Helper to extract structure and final_id from item string "pid::final_id"
                        def get_struct(item):
                            pid = item.split("::")[0]
                            if pid in self.patterns:
                                return self._get_pattern_structure_str(self.patterns[pid])
                            return "Unknown"

                        def get_fid(item):
                            return item.split("::")[1]

                        body_structs = list(set([get_struct(item) for item in body_items]))
                        head_structs = list(set([get_struct(item) for item in head_items]))
                        
                        body_fids = list(set([get_fid(item) for item in body_items]))
                        head_fids = list(set([get_fid(item) for item in head_items]))
  
                        all_rules.append({
                            "anchor_label": anchor_label,
                            "body": body_structs,
                            "head": head_structs,
                            "body_fids": body_fids,
                            "head_fids": head_fids,
                            "confidence": confidence,
                            "support": support_of_combo / total_anchors if total_anchors > 0 else 0,
                        })

        return all_rules
