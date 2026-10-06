import pandas as pd
import json

# =====================================================================
# CONSOLIDAÇÃO DA MATRIZ MULTIDIMENSIONAL (Fase 4)
# Fontes: SonarQube (estrutural) + LLM-as-a-Judge (semântica) + pass@1 (funcional)
#
# Métricas estruturais mantidas (alinhadas à seção 4.4 revisada):
#   - Complexidade Ciclomática (CC)      -> 'complexity'
#   - Complexidade Cognitiva              -> 'cognitive_complexity'
#   - Linhas de Código (LOC)              -> 'ncloc'
#   - Índice de Manutenibilidade (MI)     -> 'sqale_index'  (débito técnico, minutos)
#   - Violações por Build (VPB)           -> 'violations'
#
# Removidas por não constarem na definição de variáveis do estudo (4.4)
# ou por redundância estatística (ver revisão): 'code_smells', 'bugs'.
#
# Esforço de Halstead e Profundidade de Aninhamento foram removidas do
# texto da seção 4.4 por não existirem como métricas exportáveis do
# SonarQube (não há coluna equivalente no CSV de origem).
# =====================================================================

# 1. Carregar Dados Estruturais (SonarQube)
df_sonar = pd.read_csv('metricas_completas_swebench.csv')

assert not df_sonar['instance_id'].duplicated().any(), (
    "instance_id duplicado encontrado em metricas_completas_swebench.csv — "
    "verifique se não há reexecuções sobrepostas antes de prosseguir."
)
df_sonar.set_index('instance_id', inplace=True)

# 2. Carregar Dados Semânticos (LLM Judge - JSONL)
semantic_data = []
with open('metricas_estruturais_extraidas.jsonl', 'r', encoding='utf-8') as f:
    for line in f:
        record = json.loads(line)
        instance_id = record.get('instance_id')
        metrics = record.get('LLM_Judge_Metrics', {})
        semantic_data.append({
            'instance_id': instance_id,
            'M_profundidade': metrics.get('M_profundidade', pd.NA),
            'M_integridade': metrics.get('M_integridade', pd.NA),
            'M_defesa': metrics.get('M_defesa', pd.NA)
        })

df_semantic = pd.DataFrame(semantic_data)
assert not df_semantic['instance_id'].duplicated().any(), (
    "instance_id duplicado encontrado em metricas_estruturais_extraidas.jsonl."
)
df_semantic.set_index('instance_id', inplace=True)

# 3. Carregar Dados Funcionais (Eficácia pass@1)
with open('results-20240402_rag_gpt4.json', 'r', encoding='utf-8') as f:
    results_data = json.load(f)

assert 'resolved_instances' in results_data or 'resolved' in results_data, (
    "Estrutura inesperada em results-20240402_rag_gpt4.json: nenhuma chave "
    "'resolved_instances' ou 'resolved' encontrada. Ajuste a leitura antes de prosseguir."
)
resolved_ids = set(results_data.get('resolved_instances', results_data.get('resolved', [])))

# pass@1 = 1 se resolvido, 0 caso contrário (inclui falha de geração, conforme
# convenção padrão do SWE-bench). Definida para a união Sonar ∪ Semântico, já
# que o inner join abaixo é quem efetivamente decide o corte final da amostra.
todas_instancias = set(df_sonar.index).union(set(df_semantic.index))
pass_at_1_data = [{'instance_id': iid, 'pass@1': 1 if iid in resolved_ids else 0} for iid in todas_instancias]

df_funcional = pd.DataFrame(pass_at_1_data)
df_funcional.set_index('instance_id', inplace=True)

# 4. Consolidação da Matriz (Merge)
# NOTA: como df_funcional cobre a união Sonar ∪ Semântico por construção, quem
# de fato limita a amostra final é a interseção entre Sonar e Semântico. O
# join com df_funcional é feito para trazer a coluna pass@1 e por clareza de
# pipeline, não porque ele restrinja adicionalmente as instâncias.
n_sonar, n_semantic = len(df_sonar), len(df_semantic)
df_matriz = df_sonar.join([df_semantic, df_funcional], how='inner')
print(f"[Diagnóstico] Instâncias no SonarQube: {n_sonar} | no LLM-Judge: {n_semantic} "
      f"| após interseção (join): {len(df_matriz)}")

# Selecionar apenas as colunas de interesse para a análise estatística
colunas_interesse = [
    'pass@1', 'M_profundidade', 'M_integridade', 'M_defesa',
    'cognitive_complexity', 'complexity', 'ncloc',
    'sqale_index', 'violations'
]
colunas_ausentes = [c for c in colunas_interesse if c not in df_matriz.columns]
if colunas_ausentes:
    print(f"[Aviso] Colunas esperadas e não encontradas no dataset: {colunas_ausentes}")

df_matriz = df_matriz.reindex(columns=[col for col in colunas_interesse if col in df_matriz.columns])

# Converter para numérico e remover linhas com dados ausentes/ inválidos
n_antes = len(df_matriz)
nulos_por_coluna = df_matriz.apply(pd.to_numeric, errors='coerce').isna().sum()
if nulos_por_coluna.sum() > 0:
    print("[Diagnóstico] Valores não numéricos/ausentes por coluna antes do dropna:")
    print(nulos_por_coluna[nulos_por_coluna > 0].to_string())

df_matriz = df_matriz.apply(pd.to_numeric, errors='coerce').dropna()
n_depois = len(df_matriz)
print(f"[Diagnóstico] Linhas antes do dropna: {n_antes} | após dropna: {n_depois} "
      f"| descartadas: {n_antes - n_depois}")

print(f"\nMerge concluído. Dimensão da Matriz Final: {df_matriz.shape}")

# Exportar matriz estabilizada
df_matriz.to_csv('matriz_consolidada.csv')
print("Arquivo 'matriz_consolidada.csv' gerado com sucesso.")
