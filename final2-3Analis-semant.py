import pandas as pd
import numpy as np
import statsmodels.api as sm
from statsmodels.stats.outliers_influence import variance_inflation_factor

# =====================================================================
# SCRIPT CONFIRMATÓRIO — Regressão Logística Binária (pass@1 ~ preditores)
#
# As variáveis abaixo já foram decididas com base na evidência produzida
# por diagnostico_selecao_variaveis.py (rodar aquele script para ver os
# números brutos por trás de cada decisão). Este script não reargumenta
# a escolha — só documenta, em comentário, o que foi decidido e por quê,
# e executa o modelo final.
#
# Decisões tomadas (evidência em diagnostico_selecao_variaveis.py):
#
#   REMOVIDA 'M_integridade': nas categorias 1 e 2, a taxa de pass@1 é
#   exatamente 0% (0/120 e 0/54); as 14 resoluções do dataset estão 100%
#   concentradas em M_integridade=3 — separação quase-perfeita, impede
#   convergência estável do MLE.
#
#   REMOVIDAS 'complexity', 'sqale_index', 'violations': (a) nenhuma
#   correlaciona-se significativamente com pass@1 (Spearman, p > 0.05 —
#   mesmo critério que também não distingue 'cognitive_complexity', mantida
#   por razão teórica, não estatística); (b) VIF de 15-18 entre as métricas
#   de complexidade (log1p) quando as 5 estruturais entram juntas no modelo,
#   confirmando redundância. 'cognitive_complexity' e 'ncloc' retidas como
#   representantes teóricos de fluxo e volume.
#
#   log1p aplicado a cognitive_complexity e ncloc: reduz o VIF entre elas de
#   126 (escala bruta, distorcida por outliers de pipeline já corrigidos)
#   para ~3.5, sem alterar a correlação de Spearman (invariante a
#   transformação monotônica).
# =====================================================================

df = pd.read_csv('matriz_consolidada.csv', index_col='instance_id')

n_total = len(df)
n_resolvidas = int(df['pass@1'].sum())
print(f"[Nota de limitação] pass@1: {n_resolvidas}/{n_total} resolvidas "
      f"({100 * n_resolvidas / n_total:.1f}%). EPV ≈ {n_resolvidas / 4:.1f} "
      f"(abaixo do mínimo prático recomendado de ~10). Ver seção 4.6 do artigo.\n")

df['cognitive_complexity_log'] = np.log1p(df['cognitive_complexity'])
df['ncloc_log'] = np.log1p(df['ncloc'])

variaveis_preditoras = ['M_profundidade', 'M_defesa', 'cognitive_complexity_log', 'ncloc_log']
X = df[variaveis_preditoras]
y = df['pass@1']

print("--- 4.1 TESTE DE MULTICOLINEARIDADE (VIF) — MODELO FINAL ---")
print("Critério: VIF < 5 (ideal) | 5 ≤ VIF < 10 (aceitável, com ressalva) | VIF ≥ 10 (problemático)\n")

X_vif = sm.add_constant(X)
vif_data = pd.DataFrame()
vif_data["Variável"] = X_vif.columns
vif_data["VIF"] = [variance_inflation_factor(X_vif.values, i) for i in range(X_vif.shape[1])]


def classificar_vif(v):
    if v < 5:
        return "OK (<5)"
    elif v < 10:
        return "Atenção (5–10)"
    else:
        return "Problemático (≥10)"


vif_data["Status"] = vif_data["VIF"].apply(classificar_vif)
# A constante (intercepto) não é uma variável preditora real — seu VIF não é
# interpretável sob os mesmos critérios e é mantida na tabela só por
# completude do procedimento (sm.add_constant exige a coluna para o cálculo).
print(vif_data.to_string(index=False))

vif_predictors = vif_data[vif_data["Variável"] != "const"]
if (vif_predictors["VIF"] >= 10).any():
    print("\n[Alerta] Ao menos uma variável preditora excede VIF = 10 — revisar antes de "
          "interpretar os coeficientes da regressão abaixo.")
elif (vif_predictors["VIF"] >= 5).any():
    print("\n[Nota] Todas as variáveis preditoras estão abaixo de VIF = 10, mas ao menos "
          "uma está na faixa 5–10 — aceitável, reportar no texto com essa ressalva.")
else:
    print("\n[OK] Todas as variáveis preditoras estão abaixo de VIF = 5 — sem indício de "
          "multicolinearidade problemática no modelo final.")

print("\n--- 4.2 REGRESSÃO LOGÍSTICA BINÁRIA ---")
X_reg = sm.add_constant(X)
modelo_logit = sm.Logit(y, X_reg)
resultado = modelo_logit.fit(disp=0)
print(resultado.summary())

print("\n--- 4.3 ODDS RATIO (RAZÃO DE CHANCES) ---")
odds_ratios = pd.DataFrame(
    {
        "Odds Ratio": resultado.params.apply(lambda x: np.exp(x)),
        "IC Inferior (2.5%)": resultado.conf_int()[0].apply(lambda x: np.exp(x)),
        "IC Superior (97.5%)": resultado.conf_int()[1].apply(lambda x: np.exp(x)),
        "P-value": resultado.pvalues
    }
)
print(odds_ratios.drop('const').to_string())
