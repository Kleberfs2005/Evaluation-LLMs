import os
import json
from datasets import load_dataset

# 1. Configurações - Altere para o caminho real da sua pasta
pasta_patches = "./patches_extraidos" 

ficheiro_saida = "mapeamento_commits.json"

# 2. Carregar os metadados oficias do Hugging Face
print("A carregar metadados do dataset SWE-bench_Verified...")
dataset = load_dataset("SWE-bench/SWE-bench_Verified", split="test")

# 3. Criar um dicionário em memória para otimizar a busca (evita iterar 500 vezes sobre o dataset)
meta_dict = {
    item["instance_id"]: {
        "repo": item["repo"], 
        "base_commit": item["base_commit"]
    } for item in dataset
}

mapeamento_final = {}
ficheiros_processados = 0

# 4. Ler todos os ficheiros na pasta local
for nome_ficheiro in os.listdir(pasta_patches):
    if nome_ficheiro.endswith(".patch"):
        # Extrair o instance_id (remove os últimos 6 caracteres correspondentes a '.patch')
        instance_id = nome_ficheiro[:-6]
        
        # Verificar se a instância existe nos metadados
        if instance_id in meta_dict:
            mapeamento_final[instance_id] = {
                "repo": meta_dict[instance_id]["repo"],
                "base_commit": meta_dict[instance_id]["base_commit"],
                "caminho_patch": os.path.join(pasta_patches, nome_ficheiro)
            }
            ficheiros_processados += 1
        else:
            print(f"Aviso: Metadados não encontrados no Hugging Face para {instance_id}")

# 5. Guardar o índice gerado
with open(ficheiro_saida, 'w', encoding='utf-8') as f:
    json.dump(mapeamento_final, f, indent=4)

print(f"\nResumo da Extração:")
print(f"- Total de patches processados: {ficheiros_processados}")
print(f"- Ficheiro de índice criado: {ficheiro_saida}")