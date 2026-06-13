import json
import os

log_path = '/home/simon/.gemini/antigravity-ide/brain/abf0a367-14fb-41a6-9441-8213c4f37a5e/.system_generated/logs/transcript.jsonl'
output_path = '/home/simon/.gemini/antigravity-ide/brain/abf0a367-14fb-41a6-9441-8213c4f37a5e/scratch/extracted_edits.txt'

if not os.path.exists(log_path):
    print("Log path does not exist:", log_path)
    exit(1)

with open(log_path, 'r', encoding='utf-8') as f, open(output_path, 'w', encoding='utf-8') as out:
    for idx, line in enumerate(f):
        try:
            data = json.loads(line)
            tool_calls = data.get('tool_calls', [])
            for tc in tool_calls:
                args = tc.get('args', {})
                args_str = str(args)
                if 'index.html' in args_str:
                    out.write(f"=== Line {idx} ===\n")
                    target = args.get('TargetContent', '')
                    replacement = args.get('ReplacementContent', '')
                    out.write(f"--- TARGET ---\n{target}\n")
                    out.write(f"--- REPLACEMENT ---\n{replacement}\n")
                    out.write("=====================================\n\n")
        except Exception as e:
            pass

print("Extraction completed. Output written to:", output_path)
