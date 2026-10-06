import pandas as pd
import numpy as np
from scipy.stats import shapiro, spearmanr
import seaborn as sns
import matplotlib.pyplot as plt

# =====================================================================
# ETAPA 2 e 3 — Fase 4: Normalidade (Shapiro-Wilk) e Correlação (Spearman)
#
# Métricas estruturais alinhadas à seção 4.4 revisada do artigo:
#   CC (complexity), Complexidade Cognitiva (cognitive_complexity),
#   LOC (ncloc), MI (sqale_index), VPB (violations).
# =====================================================================

df = pd.read_csv('matriz_consolidada.csv', index_col='instance_id')

var_continuas = ['cognitive_complexity', 'complexity', 'ncloc', 'sqale_index', 'violations']
var_ordinais_binarias = ['pass@1', 'M_profundidade', 'M_integridade', 'M_defesa']

# --- Nota de limitação (reportar também na seção 4.6 do artigo) ---
# A variável pass@1 é fortemente desbalanceada: poucas instâncias resolvidas
# frente ao total. Isso não invalida a correlação de Spearman (que é robusta
# a essa assimetria), mas deve ser considerado na interpretação de qualquer
# modelo preditivo subsequente (ver final2Analis-semant.py).
n_total = len(df)
n_resolvidas = int(df['pass@1'].sum())
print(f"[Nota de limitação] pass@1: {n_resolvidas}/{n_total} instâncias resolvidas "
      f"({100 * n_resolvidas / n_total:.1f}%). Amostra desbalanceada — "
      f"ver seção 4.6 do artigo.\n")

print("--- ETAPA 2: TESTE DE NORMALIDADE (SHAPIRO-WILK) ---")
print("H0: A variável possui distribuição normal (p > 0.05)")
print("H1: A variável não possui distribuição normal (p <= 0.05)\n")

reprovadas = []
for col in var_continuas:
    if col in df.columns:
        stat, p_value = shapiro(df[col])
        normal = "Sim" if p_value > 0.05 else "Não"
        if p_value <= 0.05:
            reprovadas.append(col)
        print(f"Variável: {col:<22} | p-value: {p_value:.5e} | Normal: {normal}")

# Conclusão derivada do resultado real do teste, não assumida a priori.
print(f"\nConclusão da Etapa 2: {len(reprovadas)}/{len(var_continuas)} variáveis estruturais "
      f"rejeitaram H0 (não seguem distribuição normal). Combinado à natureza ordinal/binária "
      f"das demais variáveis (pass@1, M_profundidade, M_integridade, M_defesa), adota-se a "
      f"Correlação de Postos de Spearman — não-paramétrica — para a Etapa 3.\n")

print("--- ETAPA 3: MATRIZ DE CORRELAÇÃO DE SPEARMAN ---")

colunas_finais = var_ordinais_binarias + var_continuas
df_calc = df[colunas_finais].copy()

corr_matrix = pd.DataFrame(index=df_calc.columns, columns=df_calc.columns, dtype=float)
p_matrix = pd.DataFrame(index=df_calc.columns, columns=df_calc.columns, dtype=float)

for col1 in df_calc.columns:
    for col2 in df_calc.columns:
        coef, p = spearmanr(df_calc[col1], df_calc[col2])
        corr_matrix.loc[col1, col2] = coef
        p_matrix.loc[col1, col2] = p

corr_matrix.to_csv('matriz_correlacao_spearman.csv')
p_matrix.to_csv('matriz_p_values_spearman.csv')

# --- Diagnóstico de multicolinearidade entre as métricas estruturais ---
# Nota: Spearman é invariante a transformações monotônicas (ex.: log1p),
# então os coeficientes abaixo não mudam se aplicarmos log nas variáveis
# antes da regressão em final2Analis-semant.py — a transformação log lá
# serve para estabilizar o VIF (efeito de escala/outliers), não a
# correlação de postos em si.
print("\nCorrelação entre as métricas estruturais (checar redundância):")
print(corr_matrix.loc[var_continuas, var_continuas].round(2).to_string())

plt.figure(figsize=(10, 8))
mask = np.triu(np.ones_like(corr_matrix, dtype=bool))
sns.heatmap(
    corr_matrix,
    mask=mask,
    annot=True,
    fmt=".2f",
    cmap="coolwarm",
    vmin=-1, vmax=1,
    cbar_kws={"shrink": .8},
    linewidths=.5
)
plt.title("Matriz de Correlação de Spearman - Métricas de Código Gerado (LLM)", pad=20, fontsize=14)
plt.tight_layout()
plt.savefig('heatmap_correlacao.png', dpi=300, bbox_inches='tight')

print("\nArtefatos gerados:")
print("- matriz_correlacao_spearman.csv")
print("- matriz_p_values_spearman.csv")
print("- heatmap_correlacao.png (Imagem para o artigo)")

print("\nCorrelações em relação à Eficácia Funcional (pass@1):")
pass_corr = corr_matrix['pass@1'].drop('pass@1').sort_values(ascending=False)
for var, coef in pass_corr.items():
    p_val = p_matrix.loc['pass@1', var]
    sig = "*" if p_val < 0.05 else ""
    print(f"{var:<22}: {coef:+.3f} (p-value: {p_val:.3f}) {sig}")

