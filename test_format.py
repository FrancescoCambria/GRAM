
import json

def format_rule_new(index, rule):
    body_structures = rule.get('body_structure', [])
    head_structures = rule.get('head_structure', [])
    body_instances = rule.get('body_instances', [])
    head_instances = rule.get('head_instances', [])
    
    out = f"    #{index}\n"
    
    out += "    body:\n"
    for i, struct in enumerate(body_structures):
        inst = str(body_instances[i]) if i < len(body_instances) and body_instances[i] is not None else "null"
        out += f"        {i+1}. {struct} {{{inst}}}\n"
        
    out += "    head:\n"
    for i, struct in enumerate(head_structures):
        inst = str(head_instances[i]) if i < len(head_instances) and head_instances[i] is not None else "null"
        out += f"        {i+1}. {struct} {{{inst}}}\n"

    out += f"    support: {rule.get('support', 0)},\n"
    out += f"    confidence: {rule.get('confidence', 0)},\n"
    return out

# Sample rule with multiple patterns from the JSON
rule_sample = {
    "body_structure": [
        "(Artist)-[SING]->(Song)-[IN]->(Playlist)-[OF]->(Genre)",
        "(Artist)-[OF]->(Genre)"
    ],
    "head_structure": [
        "(Artist)-[SING]->(Song)-[IN]->(Playlist)-[CREATED_BY]->(User)"
    ],
    "body_instances": [
        "Rock",
        "rock"
    ],
    "head_instances": [
        None
    ],
    "support": 0.0027,
    "confidence": 1.0
}

print(format_rule_new(3, rule_sample))
