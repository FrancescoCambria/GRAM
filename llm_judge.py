import argparse
import requests
import json
import os
from dotenv import load_dotenv
import sys
import time
import datetime
import google.generativeai as genai

load_dotenv()

SYSTEM_PROMPT = """
Role: You are an expert in Graph Theory and Association Rule Mining (ARM).
Technical Skills:
1. Graph Database Engineering: You understand the structure of a graph, with nodes and their labels, edges and their types, properties, and traversals language (Cypher). You visualize data not as tables, but as a high-dimensional connected topology.
2. Association Rule Mining (ARM): You are an expert in extracting patterns (Apriori, FP-Growth) and, crucially, you understand how to translate these "transactional" concepts into "graph" contexts.
3. You understand how to meaure Association Rule Mining:
    * Support: In a graph, this is the frequency of a pattern's appearance (the relative number of nodes with the anchor label that match the pattern).
    * Confidence: The conditional probability that if the antecedent set of patterns exists, the consequent set of patterns also exists.


Additional Role: You are also a Musicologist and Music Historian, expert in music knowledge and its semantics.
 You have studied song artist, and why they are categorized in a certain genre. 

1. You understand the graph constructed upon a dataset on Spotify Playlists with entities:
    * Nodes: Artist, Genre, Song, Playlist, User
    * Edges: SING (between Artist and Song), IN (between Song and Playlist), CREATED_BY (between Playlist and User), OF (between either Artist of Playlist and Genre)
2. You evaluate the meaning of the patterns found with association rules mining.
3. You try to look for interesting results with your music expertise
"""

# SYSTEM_PROMPT = """
# Role: You are an expert in Graph Theory and Association Rule Mining (ARM).
# Technical Skills:
# 1. Graph Database Engineering: You understand the structure of a graph, with nodes and their labels, edges and their types, properties, and traversals language (Cypher). You visualize data not as tables, but as a high-dimensional connected topology.
# 2. Association Rule Mining (ARM): You are an expert in extracting patterns (Apriori, FP-Growth) and, crucially, you understand how to translate these "transactional" concepts into "graph" contexts.
# 3. You understand how to meaure Association Rule Mining:
#     * Support: In a graph, this is the frequency of a pattern's appearance (the relative number of nodes with the anchor label that match the pattern).
#     * Confidence: The conditional probability that if the antecedent set of patterns exists, the consequent set of patterns also exists.


# Additional Role: You are a Senior Researcher in Italian Law and Italian Legal Informatics. You possess deep expertise in the Italian legal system, viewing the corpus of laws not merely as text, but as a graph of articles. Your core competency lies in analyzing the "Legislative Network." You understand the structural dynamics of how article and laws are constructed, and how they evolve through specific connection types such as: explicit citations, amendments and abrogations. You are specifically focused on the semantics of these connections. Your goal is to investigate how distinct departments are linked by shared citations or topics.


# 1. You understand the graph constructed upon Italian legislation with entities:
#     * Nodes: Legislature, Government, Law, Article, Topic, Department, Attachment and LawUnit
#     * Edges: CITES, IN_NOTES, INTRODUCES, AMENDS, ABROGATES, HAS_ARTICLE, HAS_ATTACHMENT, IS_LEGAL_BASIS_OF, HAS_TOPIC, FROM_DEPARTMENT, UNDER_LEGISLATURE, UNDER_GOVERNMENT
# 2. You evaluate the meaning of the patterns found with association rules mining.
# 3. You try to look for interesting results with your law expertise
# """

PROMPTS = {
    "analyze_params": """
        You are testing a Graph Mining application.
        Here is the schema of the graph:
        Node Labels: {node_labels}
        Relationship Types: {edge_types}

        Please decide on parameters for the mining task and choose a specific anchor to focus on for pattern exploration.
        Return a JSON object with these keys:
        - \"node_labels\": list of strings (subset of available labels, the nodes used for pattern creation)
        - \"edge_types\": list of strings (subset of available types, the edges used for pattern creation) - include in the list only the edge type, do not include the source and destination nodes labels
        - \"anchors\": list of strings (labels to use as anchors, it will be the subject of the rule)
        - \"support\": float (between 0.0 and 1.0 (e.g. 0.0001), lower supports will produce more rules)
        - \"confidence\": float (between 0.0 and 1.0 (e.g. 0.1), lower confidence will produce more rules)
        - \"max_length\": int (how many nodes in a pattern maximum (e.g. 4), the longer the more different patterns it will show)
        - \"max_combination_size\": int (number of patterns to combine (e.g. 5), the higher the more patterns will appear in a rule)
        - \"selected_anchor\": string (the best anchor has a high outdegree based on the schema (discard labels that according to the schema and direction of edges, do not have paths going 'out' from them))
        
        Choose parameters and an anchor that are likely to find interesting patterns but not be too computationally expensive.
        """,
    "select_patterns_for_combination": """
        Here are some discovered frequent patterns, including example instances found in the graph:
        {patterns_summary}

        Analyze the 'examples' to understand the semantic meaning of each pattern.
        Then, select a set of pattern IDs to combine into larger rules. 
        Focus on patterns that, when combined, might represent a meaningful relationship (for example the citations to other laws). 
        Longer patterns might lead to unexpected and more interesting results as they will create more complex topological structures.
        
        Select the patterns (up to 5) that are in line with your interest and you think could lead to good Association Rules.
        Return JSON:
        {{
            \"pattern_ids\": [\"id1\", \"id2\", ...] 
        }}
        """,
    "select_combination": """
        Here are the generated combination types:
        {combinations_summary}

        Each combination type describes a specific structure of patterns that co-occur.
        Analyze these types. Select the ONE that seems most promising for rule generation.
        Think about whether the combined patterns make sense together semantically and could lead to unexpected and interesting results. Combinations with higher numerosity (both in the cardinality of patterns and in the total count) might lead to more interesting results. Combinations with a variety of patterns might associate different concepts making it more interesting. 
        
        Return JSON:
        {{
            \"selected_type_index\": 0
        }}
        """,
    "evaluate_rules": """
        You have completed the graph mining process.
        Initial Parameters: {initial_params}
        
        Here are the top generated Association Rules:
        {rules_json}

        Please evaluate the quality of these rules. Assign a score from 1 (low) to 5 (high) to the following statements:
        1. The extracted rules match my interest. They are in scope with my work or line of research.
        2. The extracted rules are not trivial and they are not common knowledge.
        3. The extracted rules are useful. They could be used as a direct application or lead to interesting further analysis.
        
        Assign a score for each statement, based on how much you agree with the statement and based on the reported examples of Association Rules. Please when citing examples put the full rule. Put the score on top of everything (Like Score 1: ## Score 2: ## Score 3: ##).

        """
}

class LLMJudge:
    def __init__(self, backend_url="http://localhost:8081", dummy_mode=False):
        self.backend_url = backend_url
        self.dummy_mode = dummy_mode
        self.history = []
        self.timings = {}
        self.data_store = {}
        self.model = None
        self.setup_llm()

    def setup_llm(self):
        print("--- LLM Setup ---")
        
        if self.dummy_mode:
            print("DUMMY MODE ENABLED: Skipping real LLM connection. Mock responses will be used.")
            return
        
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            # Fallback for demo purposes if env var not set, though user should set it
            print("Warning: GEMINI_API_KEY environment variable not set.")
            api_key = input("Please enter your Gemini API Key: ").strip()
        
        genai.configure(api_key=api_key)
        # import pprint
        # for model in genai.list_models():
        #     pprint.pprint(model.name)
        # self.model = genai.GenerativeModel('gemini-2.5-flash')
        self.model = genai.GenerativeModel('gemini-3-flash-preview')
        print("LLM Configured successfully.")

    def log_interaction(self, role, content):
        entry = {
            "role": role,
            "content": content,
            "timestamp": datetime.datetime.now().isoformat()
        }
        self.history.append(entry)
        # print(f"\n[{role.upper()}]: {content}\n")

    def get_dummy_response(self, prompt):
        """Returns a valid JSON or text response based on the prompt content for testing."""
        prompt_lower = prompt.lower()
        if "decide on parameters" in prompt_lower:
            # Mocking step_analyze
            return json.dumps({
                "node_labels": ['Artist', 'Song', 'Playlist', 'Genre'],
                "edge_types": ['SING', 'IN', 'OF'],
                "anchors": ['Artist'], 
                "support": 0.01,
                "confidence": 0.00,
                "max_length": 3,
                "max_combination_size": 3,
                "selected_anchor": "Artist"
            })
        elif "select a set of pattern ids" in prompt_lower:
            # Mocking step_combine
            return json.dumps({
                "pattern_ids": [] 
            })
        elif "analyze these types" in prompt_lower or "analyze these combinations" in prompt_lower:
             # Mocking step_select_combination
             return json.dumps({
                 "selected_type_index": 0
             })
        elif "evaluate the quality" in prompt_lower:
            # Mocking step_evaluate
            return "DUMMY EVALUATION: The rules generated in this test run appear structurally consistent, though no semantic analysis was performed."
        
        return "{}"

    def query_llm(self, prompt, json_mode=False):
        full_prompt = f"{SYSTEM_PROMPT}\n\n{prompt}"
        if json_mode:
            full_prompt += "\n\nIMPORTANT: Return ONLY a raw JSON object. No markdown formatting, no backticks."
        
        self.log_interaction("user", prompt) # Log the specific prompt, system prompt is implicit context
        
        if self.dummy_mode:
            print("(Mocking LLM Response...)")
            text = self.get_dummy_response(prompt)
            self.log_interaction("model", text)
            return text

        try:
            response = self.model.generate_content(full_prompt)
            text = response.text.strip()
            # Clean up potential markdown code blocks if the model ignores instruction
            if text.startswith("```json"):
                text = text[7:]
            if text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            
            self.log_interaction("model", text)
            return text
        except Exception as e:
            print(f"LLM Interaction Error: {e}")
            return None

    def get_schema(self):
        start_time = time.time()
        print("\n[Step 1] Fetching Graph Schema...")
        try:
            resp = requests.get(f"{self.backend_url}/api/schema")
            resp.raise_for_status()
            data = resp.json()
            
            self.timings['get_schema'] = time.time() - start_time
            self.data_store['schema'] = data
            return data
        except Exception as e:
            print(f"Failed to connect to backend: {e}")
            sys.exit(1)

    def step_analyze(self, schema):
        start_time = time.time()
        print("\n[Step 2] determining Mining Parameters...")
        
        # Prepare context for LLM
        # 1. Map Node IDs to Labels
        nodes_map = {n['data']['id']: n['data']['label'] for n in schema['elements'] if 'source' not in n['data']}
        
        # 2. Build enriched edge types
        unique_edges = set()
        for n in schema['elements']:
            if 'source' in n['data']:
                s_id = n['data']['source']
                t_id = n['data']['target']
                r_type = n['data']['label']
                
                s_label = nodes_map.get(s_id, "Unknown")
                t_label = nodes_map.get(t_id, "Unknown")
                
                unique_edges.add(f"{r_type} ({s_label} -> {t_label})")

        schema_summary = {
            "node_labels": list(set(nodes_map.values())),
            "edge_types": list(unique_edges)
        }

        prompt = PROMPTS["analyze_params"].format(
            node_labels=schema_summary['node_labels'],
            edge_types=schema_summary['edge_types']
        )

        response_text = self.query_llm(prompt, json_mode=True)
        try:
            params = json.loads(response_text)
            print(f"LLM decided parameters: {params}")
            self.data_store['parameters'] = params
            
            # Call backend
            resp = requests.post(f"{self.backend_url}/api/analyze", json=params)
            resp.raise_for_status()
            result = resp.json()
            print("Analysis initialized successfully.")
            
            self.timings['step_analyze'] = time.time() - start_time
            return params, result
        except json.JSONDecodeError:
            print("Failed to parse LLM JSON response.")
            sys.exit(1)
        except Exception as e:
            print(f"Analysis Error: {e}")
            sys.exit(1)

    def step_get_patterns(self, params):
        start_time = time.time()
        print("\n[Step 3] Exploring Patterns...")
        
        selected_anchor = params.get('selected_anchor')

        if self.dummy_mode:
            print("(Dummy Mode: Fetching patterns for the selected anchor...)")
            all_patterns = {}
            anchors_to_test = [selected_anchor] if selected_anchor else params.get('anchors', [])
            
            # Fallback to all labels if still empty
            if not anchors_to_test and 'schema' in self.data_store:
                anchors_to_test = list(set(n['data']['label'] for n in self.data_store['schema']['elements'] if 'source' not in n['data']))
            
            for anchor in anchors_to_test:
                print(f"  Fetching for anchor: {anchor}...")
                try:
                    resp = requests.post(f"{self.backend_url}/api/patterns", json={
                        "anchor": anchor,
                        "length": params['max_length']
                    })
                    if resp.status_code == 200:
                        all_patterns.update(resp.json())
                except Exception as e:
                    print(f"  Failed to fetch for {anchor}: {e}")
            
            print(f"Retrieved {len(all_patterns)} patterns total.")
            self.data_store['patterns'] = all_patterns
            self.timings['step_get_patterns'] = time.time() - start_time
            return all_patterns

        if selected_anchor:
            print(f"Using anchor decided in Step 2: {selected_anchor}")
            choice = {"anchor": selected_anchor, "length": params['max_length']}
        else:
            print("No anchor selected in Step 2. Using the first from the 'anchors' list...")
            choice = {"anchor": params['anchors'][0], "length": params['max_length']}

        try:
            print(f"Querying patterns for: {choice}")
            self.data_store['pattern_choice'] = choice
            
            resp = requests.post(f"{self.backend_url}/api/patterns", json=choice)
            resp.raise_for_status()
            patterns = resp.json()
            print(f"Retrieved {len(patterns)} patterns.")
            
            self.data_store['patterns'] = patterns
            self.timings['step_get_patterns'] = time.time() - start_time
            return patterns
        except Exception as e:
            print(f"Pattern Retrieval Error: {e}")
            return {}

    def step_combine(self, patterns, params):
        start_time = time.time()
        print("\n[Step 4] Selecting Patterns for Combination...")
        
        if not patterns:
            print("No patterns to combine.")
            self.timings['step_combine'] = time.time() - start_time
            return []

        # Summarize patterns for LLM
        patterns_summary = []
        for pid, p in list(patterns.items())[:20]: # Limit to top 20 to save tokens
            structure = f"({p['anchor']})"
            
            nodes = p.get('node_labels', [])
            rels = p.get('rel_types', [])
            
            # Logic to construct Cypher-like string based on list lengths
            if len(nodes) == len(rels) + 1:
                # Case: node_labels includes the anchor at index 0
                # e.g. Nodes: [A, B], Rels: [R1]. Structure: (A)-[R1]->(B)
                # We already added (A). Iterate rels.
                for i in range(len(rels)):
                    r_type = rels[i] if rels[i] else ""
                    next_node = nodes[i+1]
                    structure += f"-[:{r_type}]->({next_node})"
            elif len(nodes) == len(rels):
                # Case: node_labels are just the targets
                # e.g. Nodes: [B], Rels: [R1]. Anchor: A. Structure: (A)-[R1]->(B)
                for i in range(len(rels)):
                    r_type = rels[i] if rels[i] else ""
                    next_node = nodes[i]
                    structure += f"-[:{r_type}]->({next_node})"
            else:
                 # Fallback: Just dump what we have
                 structure += f" rels:{rels} nodes:{nodes}"

            # Fetch instances for this pattern
            examples = []
            try:
                resp = requests.get(f"{self.backend_url}/api/patterns/{pid}/instances")
                if resp.status_code == 200:
                    data = resp.json()
                    examples = [item['name'] for item in data.get('instances', [])]
            except Exception as e:
                print(f"Failed to fetch instances for pattern {pid}: {e}")

            patterns_summary.append({
                "id": pid,
                "structure": structure,
                "count": p.get('count', 0),
                "examples": examples
            })

        prompt = PROMPTS["select_patterns_for_combination"].format(
            patterns_summary=json.dumps(patterns_summary, indent=2)
        )
        
        response_text = self.query_llm(prompt, json_mode=True)
        try:
            selection = json.loads(response_text)
            
            if self.dummy_mode:
                selection['pattern_ids'] = list(patterns.keys())
                print(f"Dummy Mode: Overriding selection to use all {len(selection['pattern_ids'])} patterns.")

            print(f"LLM selected {len(selection['pattern_ids'])} patterns.")
            self.data_store['pattern_selection'] = selection
            
            max_combo = params.get('max_combination_size', 3)
            resp = requests.post(f"{self.backend_url}/api/combine", json= {
                "pattern_ids": selection['pattern_ids'],
                "max_combination_size": max_combo
            })
            resp.raise_for_status()
            combinations_groups = resp.json()
            print(f"Generated {len(combinations_groups)} combination groups.")
            
            if not combinations_groups:
                print("No combinations generated.")
                return []

            # 1. Summarize combination types for LLM
            group_summaries = []
            
            # To fetch node names efficiently, collect all unique FIDs from examples first
            all_fid_examples = set()
            for group in combinations_groups[:20]: # Limit to 20 types
                for combo in group.get('combinations', [])[:1]: # Just 1 example combo per type
                     for item in combo.get('items', []):
                         all_fid_examples.add(item.split('::')[1])
            
            node_names_map = {}
            if all_fid_examples:
                try:
                    resp_names = requests.post(f"{self.backend_url}/api/node-names", json={"node_ids": list(all_fid_examples)})
                    if resp_names.status_code == 200:
                        node_names_map = resp_names.json()
                except Exception as e:
                    print(f"Failed to fetch node names for examples: {e}")

            for i, group in enumerate(combinations_groups[:20]):
                structure_desc = []
                for struct_str, cardinality in group.get('structure', {}).items():
                    structure_desc.append({
                        "pattern": struct_str,
                        "cardinality": cardinality
                    })
                
                # Format example
                examples = []
                for combo in group.get('combinations', [])[:1]:
                    combo_example = []
                    for item in combo.get('items', []):
                        fid = item.split('::')[1]
                        name = node_names_map.get(fid, fid)
                        combo_example.append(name)
                    examples.append(combo_example)

                group_summaries.append({
                    "type_index": i,
                    "structure": structure_desc,
                    "total_combinations_of_this_type": group.get('count', 0),
                    "representative_support": group.get('support', 0),
                    "examples": examples
                })

            print("\n[Step 4.5] Selecting Combination Type...")
            prompt_combos = PROMPTS["select_combination"].format(
                combinations_summary=json.dumps(group_summaries, indent=2)
            )

            response_text_combos = self.query_llm(prompt_combos, json_mode=True)
            try:
                selection_combos = json.loads(response_text_combos)
                idx = selection_combos.get('selected_type_index')
                
                selected_combinations = []
                if idx is not None and idx < len(combinations_groups):
                    selected_combinations = combinations_groups[idx].get('combinations', [])
                
                if not selected_combinations and combinations_groups:
                    print("LLM made no selection or invalid selection. Falling back to the first type.")
                    selected_combinations = combinations_groups[0].get('combinations', [])

                print(f"LLM selected combination type {idx}, total {len(selected_combinations)} combinations.")
                
                self.data_store['combinations'] = selected_combinations
                self.timings['step_combine'] = time.time() - start_time
                return selected_combinations

            except Exception as e:
                print(f"Combination Selection Error: {e}")
                # Fallback to the first type if selection fails
                return combinations_groups[0].get('combinations', []) if combinations_groups else []

        except Exception as e:
            print(f"Combination Error: {e}")
            return []

    def step_rules(self, combinations):
        start_time = time.time()
        print("\n[Step 5] Generating Rules...")
        
        if not combinations:
            print("No combinations available for rule generation.")
            self.timings['step_rules'] = time.time() - start_time
            return []
            
        try:
            resp = requests.post(f"{self.backend_url}/api/rules", json={"combinations": combinations})
            resp.raise_for_status()
            rules = resp.json()
            print(f"Generated {len(rules)} rules.")
            
            if combinations and not rules:
                print("WARNING: Combinations were found, but no rules were generated. This might indicate an issue with confidence thresholds or logic.")

            self.data_store['rules'] = rules
            self.timings['step_rules'] = time.time() - start_time
            return rules
        except Exception as e:
            print(f"Rule Generation Error: {e}")
            return []

    def step_evaluate(self, rules, initial_params):
        start_time = time.time()
        print("\n[Step 6] Final Evaluation...")
        
        if not rules:
            print("No rules to evaluate.")
            self.timings['step_evaluate'] = time.time() - start_time
            return

        # 1. Select top rules
        # Sort by support
        top_support = sorted(rules, key=lambda x: x.get('support', 0), reverse=True)[:10]
        # Sort by confidence
        top_confidence = sorted(rules, key=lambda x: x.get('confidence', 0), reverse=True)[:10]
        
        # Combine unique rules
        selected_rules = []
        seen_indices = set()
        
        # We need a way to identify uniqueness. Let's assume the rules objects are distinct in the list.
        # But since we're creating new lists, we can't rely on object identity if we didn't track original indices.
        # However, we can just merge the lists and deduplicate based on content if needed, or simply take the union.
        # A simple way is to iterate both lists and add if not already added.
        # Since we don't have a unique ID for rules, let's use the string representation of body/head as a proxy key.
        
        def get_rule_key(r):
            b_ids = tuple(sorted(r.get('body_fids', r.get('body_node_ids', []))))
            h_ids = tuple(sorted(r.get('head_fids', r.get('head_node_ids', []))))
            return f"{r.get('body')}-{r.get('head')}-{b_ids}-{h_ids}"

        seen_keys = set()
        for r in top_support + top_confidence:
            k = get_rule_key(r)
            if k not in seen_keys:
                seen_keys.add(k)
                selected_rules.append(r)
        
        print(f"Selected {len(selected_rules)} unique top rules for evaluation (Top 10 Support + Top 10 Confidence).")

        # 2. Enrich with node names
        all_node_ids = set()
        for r in selected_rules:
            # Check for different possible keys for IDs
            b_ids = r.get('body_fids', r.get('body_node_ids', []))
            h_ids = r.get('head_fids', r.get('head_node_ids', []))
            
            # Ensure they are lists of ints/strings
            if isinstance(b_ids, list): all_node_ids.update(map(str, b_ids))
            if isinstance(h_ids, list): all_node_ids.update(map(str, h_ids))
        
        node_names_map = {}
        if all_node_ids:
            try:
                print(f"Fetching names for {len(all_node_ids)} nodes...")
                resp = requests.post(f"{self.backend_url}/api/node-names", json={"node_ids": list(all_node_ids)})
                if resp.status_code == 200:
                    node_names_map = resp.json()
            except Exception as e:
                print(f"Failed to fetch node names: {e}")

        # 3. Format for Prompt
        formatted_rules = []
        for r in selected_rules:
            b_ids = r.get('body_fids', r.get('body_node_ids', []))
            h_ids = r.get('head_fids', r.get('head_node_ids', []))
            
            body_names = [node_names_map.get(str(nid), str(nid)) for nid in b_ids]
            head_names = [node_names_map.get(str(nid), str(nid)) for nid in h_ids]
            
            formatted_rules.append({
                "body_structure": r.get('body'),
                "head_structure": r.get('head'),
                "body_instances": body_names,
                "head_instances": head_names,
                "support": r.get('support'),
                "confidence": r.get('confidence')
            })

        self.data_store['formatted_rules'] = formatted_rules

        prompt = PROMPTS["evaluate_rules"].format(
            initial_params=initial_params,
            rules_json=json.dumps(formatted_rules, indent=2)
        )
        
        evaluation = self.query_llm(prompt)
        print("\n--- LLM Evaluation ---")
        print(evaluation)
        print("----------------------")
        
        self.data_store['evaluation'] = evaluation
        self.timings['step_evaluate'] = time.time() - start_time

    def save_report(self):
        # Filter data to save only what's requested: parameters, rules, evaluation
        filtered_data = {
            "parameters": self.data_store.get("parameters"),
            "rules": self.data_store.get("formatted_rules") or self.data_store.get("rules"),
            "evaluation": self.data_store.get("evaluation")
        }

        report = {
            "timestamp": datetime.datetime.now().isoformat(),
            "timings_seconds": self.timings,
            "total_time_seconds": sum(self.timings.values()),
            "data": filtered_data,
            "llm_interaction_history": self.history
        }
        
        # Ensure output directory exists
        os.makedirs("data/output", exist_ok=True)
        filename = f"data/output/llm_judge_report_{int(time.time())}.json"
        
        try:
            with open(filename, 'w') as f:
                json.dump(report, f, indent=2)
            print(f"\nDetailed report saved to {filename}")
        except Exception as e:
            print(f"Failed to save report: {e}")

    def run(self):
        print("Starting LLM Judge for GramXNeo4j...")
        
        # 1. Schema
        schema = self.get_schema()
        
        # 2. Analyze
        params, analyze_result = self.step_analyze(schema)
        
        # 3. Get Patterns
        patterns = self.step_get_patterns(params)
        
        # 4. Combine
        combinations = self.step_combine(patterns, params)
        
        # 5. Rules
        rules = self.step_rules(combinations)

        # 5.5 Select Rules (Removed)
        # selected_rules = self.step_select_rule_structure(rules)
        selected_rules = rules
        
        # 6. Evaluate
        self.step_evaluate(selected_rules, params)
        
        # 7. Save Report
        self.save_report()
        
        print("\nSession Complete.")

if __name__ == "__main__":
    #judge = LLMJudge(dummy_mode=True)
    judge = LLMJudge()
    judge.run()