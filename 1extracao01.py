import json
import os

input_file = "all_preds-Verified-rag-gpt4.jsonl"
output_dir = "patches_extraidos"

os.makedirs(output_dir, exist_ok=True)

salvos, ignorados = 0, 0

with open(input_file, 'r', encoding='utf-8') as f:
    for i, line in enumerate(f, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            print(f"[linha {i}] JSON inválido, ignorada.")
            ignorados += 1
            continue

        instance_id = data.get("instance_id")
        model_patch = data.get("model_patch")

        if not instance_id or not model_patch:
            ignorados += 1
            continue

        safe_id = str(instance_id).replace("/", "_").replace("\\", "_")
        patch_filename = os.path.join(output_dir, f"{safe_id}.patch")
        with open(patch_filename, 'w', encoding='utf-8') as patch_file:
            patch_file.write(model_patch)
        salvos += 1

print(f"Processamento concluído. {salvos} patches salvos em '{output_dir}' ({ignorados} linhas ignoradas).")