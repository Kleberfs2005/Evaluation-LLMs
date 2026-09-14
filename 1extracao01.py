import json
import os

input_file = "all_preds-Verified-rag-gpt4.jsonl"
output_dir = "patches_extraidos"

os.makedirs(output_dir, exist_ok=True)

with open(input_file, 'r', encoding='utf-8') as f:
    for line in f:
        data = json.loads(line.strip())
        instance_id = data.get("instance_id")
        model_patch = data.get("model_patch")

        if instance_id and model_patch:
            # Salva o diff em um arquivo .patch com o nome da instância
            patch_filename = os.path.join(output_dir, f"{instance_id}.patch")
            with open(patch_filename, 'w', encoding='utf-8') as patch_file:
                patch_file.write(model_patch)

print(f"Processamento concluído. Patches salvos em: {output_dir}")