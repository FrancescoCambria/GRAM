import json
import os
import re
import argparse
import sys

def extract_rules_from_prompt(prompt_text):
    # Regex to find the JSON array of rules
    # It seems to be between "Here are the top generated Association Rules:" and "Please evaluate"
    pattern = r"Here are the top generated Association Rules:\s*(\[\s*\{.*\}\s*\])\s*Please evaluate"
    match = re.search(pattern, prompt_text, re.DOTALL)
    if match:
        json_str = match.group(1)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON rules: {e}")
            return []
    
    # Fallback: try to find just the first [ and last ]
    start = prompt_text.find('[')
    end = prompt_text.rfind(']')
    if start != -1 and end != -1:
        try:
            return json.loads(prompt_text[start:end+1])
        except:
            return []
    return []

def format_rule(rule):
    # Rule structure can vary.
    # Structure: 'body' or 'body_structure'
    # Instances: 'body_instances'
    
    body_structures = rule.get('body', rule.get('body_structure', []))
    head_structures = rule.get('head', rule.get('head_structure', []))
    
    body_instances = rule.get('body_instances', [])
    head_instances = rule.get('head_instances', [])
    
    def format_part(structures, instances):
        if not instances:
            return "      (empty)\n"
        
        out_part = ""
        current_struct = None
        current_instances = []
        
        for i, instance_val in enumerate(instances):
            # Determine structure for this instance
            struct = "Unknown"
            if structures:
                if i < len(structures):
                    struct = structures[i]
                else:
                    struct = structures[-1]
            
            val_str = "null"
            if instance_val is not None:
                val_str = str(instance_val)
                
            if current_struct is None:
                current_struct = struct
                current_instances.append(val_str)
            elif struct == current_struct:
                current_instances.append(val_str)
            else:
                # Flush previous group
                count = len(current_instances)
                instances_str = ", ".join(current_instances)
                out_part += f"      {current_struct} x{count} {{{instances_str}}}\n"
                
                # Start new group
                current_struct = struct
                current_instances = [val_str]
        
        # Flush final group
        if current_struct:
            count = len(current_instances)
            instances_str = ", ".join(current_instances)
            out_part += f"      {current_struct} x{count} {{{instances_str}}}\n"
            
        return out_part

    out = ""
    out += "    body:\n"
    out += format_part(body_structures, body_instances)
    
    out += "    head:\n"
    out += format_part(head_structures, head_instances)
            
    return out

def get_general_prompt(prompt_text):
    # Remove the specific JSON content to show the template
    pattern = r"(Here are the top generated Association Rules:\s*)(\[\s*\{.*\}\s*\])(\s*Please evaluate)"
    match = re.search(pattern, prompt_text, re.DOTALL)
    if match:
        return match.group(1) + "[ ... RULES JSON ... ]" + match.group(3) + prompt_text[match.end():]
    return prompt_text

def process_files(data_dir, output_file):
    if not os.path.exists(data_dir):
        print(f"Directory not found: {data_dir}")
        return

    # Get files
    files = [f for f in os.listdir(data_dir) if f.startswith('llm_judge_report_') and f.endswith('.json')]
    files.sort() # Ensure deterministic order
    
    full_text = "10 Evaluation tests on Italian Legislation Dataset.\n\n"
    
    first_prompt_captured = False
    
    for i, filename in enumerate(files, 1):
        file_path = os.path.join(data_dir, filename)
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            history = data.get('llm_interaction_history', [])
            
            # Find last user message (prompt) and last model message (answer)
            last_user_msg = None
            last_model_msg = None
            
            for msg in reversed(history):
                if msg['role'] == 'model' and not last_model_msg:
                    last_model_msg = msg
                if msg['role'] == 'user' and not last_user_msg:
                    last_user_msg = msg
                if last_user_msg and last_model_msg:
                    break
            
            if not last_user_msg or not last_model_msg:
                print(f"Skipping {filename}: missing messages")
                continue
            
            prompt_content = last_user_msg['content']
            answer = last_model_msg['content']

            if not first_prompt_captured:
                full_text += "Final prompt to LLM:\n"
                full_text += get_general_prompt(prompt_content)
                full_text += "\n" + "="*80 + "\n\n"
                first_prompt_captured = True
                
            rules = extract_rules_from_prompt(prompt_content)
            
            # Append to full_text
            full_text += f"Report {i} ({filename}):\n"
            full_text += "Rules:\n"
            for r_idx, rule in enumerate(rules, 1):
                full_text += f"  Rule #{r_idx}:\n"
                full_text += format_rule(rule)
            
            full_text += f"\nAnswer:\n"
            full_text += f"{answer}\n\n"
            full_text += "-" * 50 + "\n\n"
            
        except Exception as e:
            print(f"Error processing {filename}: {e}")
            # import traceback
            # traceback.print_exc()

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(full_text)
    print(f"Report written to {output_file}")

if __name__ == "__main__":
    
        
    process_files('./data/output/llm_judge_report_20260104', './data/output/llm_judge_report_20260104/summary_report_20260104.txt')
