import json
import os
from datasets import load_dataset

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURAÇÃO  - VERSÃO APRIMORADA PELA CLAUDE PARA GERAR PROMPTS DE AVALIAÇÃO
# ─────────────────────────────────────────────────────────────────────────────
input_predictions_file = 'all_preds-Verified-rag-gpt4.jsonl'
output_payload_file    = 'evaluation_payload.jsonl'

# ─────────────────────────────────────────────────────────────────────────────
# PROMPTS — LLM-as-a-Judge
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
Você é um Engenheiro de Software Sênior e um juiz rigoroso de qualidade de código.
Sua única função é avaliar se um patch gerado por IA resolve semanticamente o \
problema descrito em uma issue do GitHub.

Critérios de Avaliação obrigatórios:

1. Correção Semântica
   - O patch altera exatamente a lógica exigida pela issue?
   - A solução aborda a causa-raiz do problema ou apenas trata sintomas?

2. Efeitos Colaterais
   - O patch introduz regressões óbvias ou comportamentos inesperados?
   - A mudança viola contratos de interface, convenções arquiteturais \
ou invariantes do sistema descritos na issue ou inferíveis do contexto?

3. Completude
   - Todos os edge cases mencionados explicitamente na issue foram tratados?
   - Casos implícitos críticos (valores nulos, listas vazias, concorrência, \
limites de tipo) foram considerados?

Regras de conduta:
- Baseie-se EXCLUSIVAMENTE no conteúdo fornecido (issue + patch). \
Não invente informações sobre o repositório.
- Seja objetivo e técnico; evite elogios genéricos.
- Se o patch estiver vazio, truncado ou claramente malformado, \
classifique resolve_issue como false e explique.

Formato de saída — responda SOMENTE com o JSON abaixo, sem texto adicional:

{
  "raciocinio_analitico": "<análise passo a passo contrastando issue e patch>",
  "resolve_issue": true,
  "score_confianca": 4
}

Campos:
  raciocinio_analitico : string  — raciocínio detalhado, em português.
  resolve_issue        : boolean — true se o patch resolve a issue, false caso contrário.
  score_confianca      : integer — sua confiança na avaliação, de 1 (mínima) a 5 (máxima).\
"""

USER_PROMPT_TEMPLATE = """\
Avalie o patch abaixo em relação à issue fornecida.

════════════════════════════════════════
ISSUE DO GITHUB (Problema Original)
════════════════════════════════════════
{problem_statement}

════════════════════════════════════════
PATCH GERADO PELA IA (Código Gerado)
════════════════════════════════════════
{model_patch}

════════════════════════════════════════
Retorne apenas o JSON especificado no system prompt.\
"""

# ─────────────────────────────────────────────────────────────────────────────
# VALIDAÇÃO DO ARQUIVO DE ENTRADA
# ─────────────────────────────────────────────────────────────────────────────
if not os.path.isfile(input_predictions_file):
    print(f"Arquivo não encontrado: {input_predictions_file}")
    print(f"Arquivos no diretório atual: {os.listdir('.')}")
    exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# 1. Carregar o dataset oficial do SWE-bench Verified
# ─────────────────────────────────────────────────────────────────────────────
print("Carregando dataset do Hugging Face...")
dataset = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")

# ─────────────────────────────────────────────────────────────────────────────
# 2. Índice O(1) por instance_id
# ─────────────────────────────────────────────────────────────────────────────
swe_dict = {item['instance_id']: item['problem_statement'] for item in dataset}
print(f"Dataset indexado: {len(swe_dict)} instâncias carregadas.")

# ─────────────────────────────────────────────────────────────────────────────
# 3 e 4. Iterar sobre predições e montar payload de avaliação
# ─────────────────────────────────────────────────────────────────────────────
total, gerados, ignorados = 0, 0, 0

with open(input_predictions_file, 'r', encoding='utf-8') as infile, \
     open(output_payload_file,    'w', encoding='utf-8') as outfile:

    for line in infile:
        if not line.strip():
            continue

        total += 1
        pred_data   = json.loads(line)
        instance_id = pred_data.get('instance_id')
        model_patch = pred_data.get('model_patch', '')

        if instance_id not in swe_dict:
            print(f"  [AVISO] instance_id não encontrado no dataset: {instance_id}")
            ignorados += 1
            continue

        problem_statement = swe_dict[instance_id]

        user_prompt = USER_PROMPT_TEMPLATE.format(
            problem_statement=problem_statement,
            model_patch=model_patch if model_patch else "(patch vazio ou ausente)",
        )

        payload = {
            "instance_id":   instance_id,
            "system_prompt": SYSTEM_PROMPT,
            "user_prompt":   user_prompt,
        }

        outfile.write(json.dumps(payload, ensure_ascii=False) + '\n')
        gerados += 1

# ─────────────────────────────────────────────────────────────────────────────
# RELATÓRIO FINAL
# ─────────────────────────────────────────────────────────────────────────────
print(f"\nConcluído.")
print(f"  Total de linhas lidas : {total}")
print(f"  Payloads gerados      : {gerados}")
print(f"  Instâncias ignoradas  : {ignorados}")
print(f"  Arquivo de saída      : {output_payload_file}")
