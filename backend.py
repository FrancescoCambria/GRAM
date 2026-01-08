from flask import Flask, jsonify, request, Response, session
from dotenv import load_dotenv
import os
import sys
import json # Import json module
import csv
import io
import uuid
from flask_cors import CORS

# Add the current directory to the python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from gram import GramxNeo4j

load_dotenv()

app = Flask(__name__)
CORS(app, origins=["https://cerilab.deib.polimi.it/gram", "http://127.0.0.1:25302","http://localhost:3000"], supports_credentials=True)
app.secret_key = os.getenv("SECRET_KEY", "dev_secret_key_fixed_for_stability")

# Replace with your Neo4j connection details
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
DATABASE_TYPE = os.getenv("DATABASE_TYPE", "neo4j")

# if not NEO4J_URI:
#     raise ValueError("NEO4J_URI not found in .env file")
# Replace with your Memgraph connection details
# NEO4J_URI = "bolt://localhost:23004"
# NEO4J_USER = ""
# NEO4J_PASSWORD = ""
# DATABASE_TYPE = "memgraph"
# Global storage for sessions
# Key: session_id (str), Value: GramxNeo4j instance
gramx_sessions = {}

def get_session_id():
    header_uid = request.headers.get('X-Session-ID')
    if header_uid:
        return header_uid
    if 'uid' not in session:
        session['uid'] = str(uuid.uuid4())
    return session['uid']

def get_gramx():
    uid = get_session_id()
    return gramx_sessions.get(uid)

@app.route('/api/schema', methods=['GET'])
def get_schema():
    try:
        gram = GramxNeo4j(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, database_type=DATABASE_TYPE)
        schema = gram.get_schema_for_cytoscape()
        
        # Debugging: Write schema to a file
        with open("debug_schema.json", "w") as f:
            json.dump(schema, f, indent=2)

        cardinality = gram.relationship_cardinality
        gram.close()
        return jsonify({"elements": schema})
    except Exception as e:
        app.logger.error(f"Error in /api/schema: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/analyze', methods=['POST'])
def analyze():
    uid = get_session_id()
    try:
        data = request.json
        node_labels = data.get('node_labels', [])
        edge_types = data.get('edge_types', [])
        anchors = data.get('anchors', [])
        support = data.get('support', 0.1)
        confidence = data.get('confidence', 0.5)
        max_length = data.get('max_length', 3)
        conditions = data.get('conditions', {})  # Extract conditions

        # Close previous session instance if it exists to free resources
        if uid in gramx_sessions:
            try:
                gramx_sessions[uid].close()
            except Exception as e:
                print(f"Error closing previous session: {e}")

        # Pass conditions to GramxNeo4j
        gramx = GramxNeo4j(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, conditions=conditions, database_type=DATABASE_TYPE)
        gramx.connect()
        gramx.extract_schema()
        gramx.trim_schema(chosen_nodes=node_labels, chosen_edges=edge_types)
        gramx.set_anchors(anchors)
        gramx.query_anchor_counts()
        gramx.set_support_and_confidence(support, confidence)
        gramx.build_forest(max_depth=max_length)

        # Store in session
        gramx_sessions[uid] = gramx

        return jsonify({
            "session_id": uid,
            "anchor_counts": gramx.anchor_counts,
            "forest": gramx.forest,
            "patterns": gramx.patterns
        })
    except Exception as e:
        app.logger.error(f"Error in /api/analyze: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/patterns', methods=['POST'])
def get_patterns():
    gramx = get_gramx()
    
    if not gramx:
        return jsonify({"error": "Analysis not initialized. Please run analysis first."}), 400
    try:
        data = request.json
        anchor = data.get('anchor')
        length = data.get('length')

        if not anchor or not length:
            return jsonify({"error": "Missing 'anchor' or 'length' in request."}), 400

        max_len = int(length)
        # Ensure pattern_results is initialized before calling execute_pattern_queries
        if not hasattr(gramx, 'pattern_results'):
            gramx.pattern_results = {}

        for i in range(1, max_len + 1):
            gramx.execute_pattern_queries(length=i, anchor=anchor)
        
        patterns_with_counts = {}
        for pid, p in gramx.patterns.items():
            if p['anchor'] == anchor:
                p_len = 0
                if p['kind'] == 'anyrel':
                    p_len = len(p['rel_types'])
                else:
                    p_len = len(p['node_labels']) - 1
                
                if p_len > 0 and p_len <= max_len:
                    # Get count from pattern_results
                    pattern_count = 0
                    if pid in gramx.pattern_results:
                        # The count is the number of distinct anchor instances that match the pattern
                        pattern_count = len(gramx.pattern_results[pid])
                    
                    # Filter out patterns with 0 count
                    if pattern_count == 0:
                        continue

                    # Augment pattern with count
                    augmented_p = p.copy()
                    augmented_p['count'] = pattern_count
                    patterns_with_counts[pid] = augmented_p

        return jsonify(patterns_with_counts)
    except Exception as e:
        app.logger.error(f"Error in /api/patterns: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/patterns/csv', methods=['GET'])
def get_patterns_csv():
    gramx = get_gramx()
    if not gramx or not hasattr(gramx, 'pattern_results'):
        return jsonify({"error": "No patterns available. Run analysis first."}), 400
    
    try:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Pattern ID', 'Anchor', 'Structure', 'Count', 'Final IDs'])

        for pid, results in gramx.pattern_results.items():
            pattern = gramx.patterns.get(pid)
            if not pattern:
                continue
            
            structure = gramx._get_pattern_structure_str(pattern)
            
            # Aggregate all final IDs for this pattern
            all_final_ids = set()
            for anchor_id, final_ids in results.items():
                all_final_ids.update(final_ids)
            
            writer.writerow([
                pid,
                pattern['anchor'],
                structure,
                len(results), # Count of unique anchors
                ";".join(all_final_ids)
            ])

        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-disposition": "attachment; filename=frequent_patterns.csv"}
        )
    except Exception as e:
        app.logger.error(f"Error in /api/patterns/csv: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/patterns/<pattern_id>/instances', methods=['GET'])
def get_pattern_instances(pattern_id):
    gramx = get_gramx()
    if not gramx or not hasattr(gramx, 'pattern_results'):
        return jsonify({"error": "Analysis not initialized or patterns not queried."}), 400
    
    try:
        if pattern_id not in gramx.pattern_results:
            return jsonify({"instances": []})

        # Collect all final_ids for the given pattern
        final_ids = set()
        for anchor_id, finals in gramx.pattern_results[pattern_id].items():
            final_ids.update(finals)
        
        if not final_ids:
            return jsonify({"instances": []})

        # Fetch names for the final_ids, limit to 4
        # Convert final_ids to list and take the first 4
        final_ids_list = list(final_ids)[:4]
        
        # Assuming _get_node_names can be called directly. 
        # This might need adjustment if it's not intended for direct use.
        # Making sure the driver is connected before calling it.
        if not gramx.driver:
            gramx.connect()

        node_names_map = gramx._get_node_names(final_ids_list)
        
        # The result from _get_node_names is a map {node_id: name}. We just need the names.
        instances = [{"name": name} for name in node_names_map.values()]

        return jsonify({"instances": instances})
    except Exception as e:
        app.logger.error(f"Error in /api/patterns/<pattern_id>/instances: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/combine', methods=['POST'])
def combine_patterns():
    gramx = get_gramx()
    if not gramx:
        return jsonify({"error": "Analysis not initialized."}), 400
    
    try:
        data = request.json
        pattern_ids = data.get('pattern_ids', [])
        max_combination_size = data.get('max_combination_size', 3)

        if not pattern_ids:
            return jsonify({"error": "No pattern IDs provided."}), 400

        combined_results = gramx.combine_selected_patterns(pattern_ids, max_combination_size=max_combination_size)
        
        # Store for CSV download
        gramx.last_combinations = combined_results
        
        return jsonify(combined_results)
    except Exception as e:
        app.logger.error(f"Error in /api/combine: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/combinations/csv', methods=['GET'])
def get_combinations_csv():
    gramx = get_gramx()
    if not gramx:
        return jsonify({"error": "Analysis not initialized."}), 400
    
    # Check for last_combinations (from UI manual flow) or normal combinations (from automated flow)
    combinations_to_export = []
    
    if hasattr(gramx, 'last_combinations') and gramx.last_combinations:
        # This is the list of grouped combinations from combine_selected_patterns
        # We need to flatten it
        combinations_to_export = []
        for group in gramx.last_combinations:
            if 'combinations' in group:
                combinations_to_export.extend(group['combinations'])
    elif hasattr(gramx, 'combinations') and gramx.combinations:
        # This is the dict from combine_results or combine_results_fpgrowth
        combinations_to_export = gramx.combinations.values()
    else:
        return jsonify({"error": "No combinations available. Run combination first."}), 400
    
    try:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Anchor Label', 'Support', 'Patterns', 'Final IDs'])

        # Create a mapping from final_id to pattern IDs if not already present
        # This might be needed if we need to look up pattern structures again
        # But wait, combine_selected_patterns returns items as "pid::fid"
        
        for combo in combinations_to_export:
            # Handle different structures
            # Structure 1 (from last_combinations/combine_selected_patterns): 
            # { "items": ["pid::fid", ...], "support": 10, "anchor_label": "Artist" }
            
            # Structure 2 (from gramx.combinations/combine_results):
            # { "final_ids": ["fid", ...], "support": 10, "anchor_label": "Artist", ... }
            
            anchor_label = combo.get('anchor_label', 'N/A')
            support = combo.get('support', 0)
            
            involved_patterns_strs = set()
            final_ids = []
            
            if 'items' in combo: # Structure 1
                for item in combo['items']:
                    pid, fid = item.split('::', 1)
                    final_ids.append(fid)
                    if pid in gramx.patterns:
                        involved_patterns_strs.add(gramx._get_pattern_structure_str(gramx.patterns[pid]))
            elif 'final_ids' in combo: # Structure 2
                # We need to find pids for these final_ids
                # This is expensive to re-scan. 
                # Let's assume for now we just list IDs or try best effort.
                final_ids = combo['final_ids']
                # Try to find at least one pattern for each final_id?
                # For CSV export from automated flow, maybe we skip pattern detail or do a heavy lookup.
                # Given user context is likely the UI flow (Structure 1), let's focus on that.
                pass

            writer.writerow([
                anchor_label,
                support,
                "; ".join(involved_patterns_strs),
                ";".join(final_ids)
            ])

        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-disposition": "attachment; filename=frequent_combinations.csv"}
        )
    except Exception as e:
        app.logger.error(f"Error in /api/combinations/csv: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/rules', methods=['POST'])
def get_rules():
    gramx = get_gramx()
    if not gramx:
        return jsonify({"error": "Analysis not initialized."}), 400
    
    try:
        data = request.json
        combinations = data.get('combinations', [])

        if not combinations:
            return jsonify({"error": "No combinations provided."}), 400

        # Flatten combinations if they are in the categorized format (from /api/combine)
        flat_combinations = []
        for item in combinations:
            if 'combinations' in item and isinstance(item['combinations'], list):
                flat_combinations.extend(item['combinations'])
            else:
                flat_combinations.append(item)

        rules = gramx.generate_rules_for_selection(flat_combinations)
        
        # Store for CSV download
        gramx.last_generated_rules = rules
        
        return jsonify(rules)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/rules/csv', methods=['GET'])
def get_rules_csv():
    gramx = get_gramx()
    # Allow if either association_rules OR last_generated_rules exists
    if not gramx:
         return jsonify({"error": "Analysis not initialized."}), 400
    
    if not hasattr(gramx, 'association_rules') and not hasattr(gramx, 'last_generated_rules'):
        return jsonify({"error": "No rules available. Run rule generation first."}), 400
    
    try:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Anchor Label', 'Body', 'Head', 'Support', 'Confidence', 'Body Node IDs', 'Head Node IDs'])

        # Since generate_rules_for_selection returns a list but doesn't store it in self.association_rules
        # unless generate_association_rules was called (which is the automated full path).
        # The frontend calls /api/rules which returns a list. 
        # If we want to download what was just generated for the selection, we need to persist it or re-send it.
        # However, the user request implies downloading "results... csv containing the rule instances".
        # If the user follows the manual flow: Patterns -> Combine -> Rules, the backend might not have `self.association_rules` populated
        # because `generate_rules_for_selection` returns the list but doesn't set `self.association_rules`.
        
        # FIX: The user likely wants to download the rules currently visible or generated.
        # But a GET request can't easily carry the "current selection".
        # If the standard flow is used, `self.association_rules` might be empty.
        # Let's assume for now that if `self.association_rules` is empty, we can't download.
        # BUT, the user flow is manual.
        # To make this work for the manual flow, we might need to store the last generated rules in `gramx`.
        
        rules_to_download = []
        if hasattr(gramx, 'last_generated_rules') and gramx.last_generated_rules:
             rules_to_download = gramx.last_generated_rules
        elif hasattr(gramx, 'association_rules') and gramx.association_rules:
             rules_to_download = gramx.association_rules
        
        if not rules_to_download:
             return jsonify({"error": "No rules generated yet."}), 404

        for rule in rules_to_download:
            # Handle both structures: generate_association_rules (has 'body' as list of IDs) vs generate_rules_for_selection (has 'body' as list of strings/structures)
            
            # generate_rules_for_selection returns:
            # { "body": [structs...], "head": [structs...], "body_fids": [...], "head_fids": [...], ... }
            
            # generate_association_rules returns:
            # { "body": [ids...], "head": [ids...], ... } and saves to file.
            
            # We will try to normalize.
            body_str = ""
            head_str = ""
            body_ids = ""
            head_ids = ""

            if 'body_fids' in rule: # From generate_rules_for_selection
                body_str = "; ".join(rule['body'])
                head_str = "; ".join(rule['head'])
                body_ids = ";".join(map(str, rule['body_fids']))
                head_ids = ";".join(map(str, rule['head_fids']))
            else: # From generate_association_rules
                # We need to reconstruct structures if possible, or just dump IDs
                body_ids = ";".join(map(str, rule['body']))
                head_ids = ";".join(map(str, rule['head']))
                body_str = "See IDs"
                head_str = "See IDs"

            writer.writerow([
                rule.get('anchor_label', 'N/A'),
                body_str,
                head_str,
                rule.get('support', 0),
                rule.get('confidence', 0),
                body_ids,
                head_ids
            ])

        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-disposition": "attachment; filename=association_rules.csv"}
        )
    except Exception as e:
        app.logger.error(f"Error in /api/rules/csv: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/node-names', methods=['POST'])
def get_node_names():
    gramx = get_gramx()
    if not gramx:
        return jsonify({"error": "Analysis not initialized."}), 400
    try:
        data = request.json
        node_ids = data.get('node_ids', [])
        if not node_ids:
            return jsonify({})
        
        names = gramx._get_node_names(node_ids)
        return jsonify(names)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    try:
        app.run(debug=True, port=8081)
    except Exception as e:
        app.logger.error(f"Failed to start Flask application: {e}", exc_info=True)
